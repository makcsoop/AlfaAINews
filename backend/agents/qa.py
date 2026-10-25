"""Вопрос-ответ по своей компании (шаг qa).

Контекст ответа — досье карточки и результаты инструментов, компания только из сессии.
Вопрос про другую компанию отсекается кодом до вызова модели и пишется в журнал безопасности.
Ответ модели проходит проверку: ссылки на существующие id, метки bank_view/market_view,
числа из цитат, отсутствие чужих идентификаторов и сумм платежей.
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.dossier import Dossier, DossierItem
from backend.agents.planner import items_from_result, story_and_fact_items
from backend.agents.tools import FixtureDataSource, ToolContext, ToolDataSource, ToolError, run_tool
from backend.cards.verify import _norm_number, _Pool, extract_dates, extract_numbers
from backend.ingest.private import has_paste
from backend.llm import BudgetExceeded, LLMClient, LLMUnavailable, data_block
from backend.schemas.common import BANK_SOURCE_PREFIX
from backend.schemas.insight import InsightCard
from backend.security.audit import AuditLog, get_audit
from backend.security.session import Session

if TYPE_CHECKING:
    from backend.api.store import CardStore

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "qa.md"
MARKET_PREFIX = "mk_"
MAX_TOOL_CALLS = 4
MAX_STATEMENTS = 5

REFUSED_TEXT = (
    "Я отвечаю только по данным вашей компании. Данные других компаний и клиентов банка "
    "показать не могу; если нужен вопрос по вашей компании, задайте его."
)
INJECTION_TEXT = (
    "Я отвечаю на вопросы о событии и о вашей компании и не меняю правила работы. "
    "Сформулируйте, пожалуйста, вопрос по существу."
)
NO_DATA_TEXT = "В данных по вашей компании и в материалах сюжета ответа на этот вопрос нет."
DEGRADED_TEXT = "Сейчас ответить не получилось: сервис разбора временно недоступен. Попробуйте позже."
CLARIFY_TEXT = (
    "Не понял вопрос — уточните, что именно вас интересует по этому событию или по вашей компании. "
    "Например: «как это повлияет на наши кредиты?» или «что это значит для наших поставщиков?»"
)
OFF_TOPIC_TEXT = (
    "Я отвечаю только на вопросы о событии и о вашей компании. Уточните, что именно вас интересует "
    "по этому событию: например, «как это повлияет на наши кредиты?»"
)


# --- схемы ---------------------------------------------------------------------------------------


class QAStatement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1)
    kind: Literal["fact", "bank_view", "market_view", "model_inference"]
    refs: list[str] = Field(default_factory=list)


class QAAnswer(BaseModel):
    """Ответ модели: намерение, текст, ссылки и утверждения с метками.

    `intent`: "answer" — вопрос понятен и по теме; "unclear" — набор символов, обрывок, реплика без вопроса;
    "off_topic" — вопрос не про событие и не про компанию. В двух последних случаях код отвечает просьбой
    уточнить и утверждения модели не показывает.
    """

    model_config = ConfigDict(extra="ignore")

    intent: Literal["answer", "unclear", "off_topic"] = "answer"
    answer: str = Field(min_length=1)
    refs: list[str] = Field(default_factory=list)
    statements: list[QAStatement] = Field(default_factory=list)


class QAResult(BaseModel):
    answer: str
    refs: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    status: Literal["ok", "refused", "degraded", "unclear"]
    trace_id: str


# --- промпт --------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_prompt() -> tuple[str, str, str]:
    """(версия, system, user)."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    m = re.search(r"prompt_version:\s*(\S+)", text.splitlines()[0])
    system = re.search(r"^## SYSTEM\n(.*?)(?=^## USER\n)", text, re.S | re.M)
    user = re.search(r"^## USER\n(.*)\Z", text, re.S | re.M)
    if not (m and system and user):
        raise ValueError("в prompts/qa.md нужны prompt_version и разделы ## SYSTEM и ## USER")
    return f"qa-v{m.group(1)}", system.group(1).strip(), user.group(1).strip()


def build_messages(question: str, dossier: Dossier, role: str) -> list[dict[str, str]]:
    _, system, user = load_prompt()
    values = {
        "role": role,
        "dossier": dossier.render_for_prompt(max_chars=12000),
        "question": data_block(question),
    }
    user = re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), user)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --- отказ на чужую компанию ---------------------------------------------------------------------

_COMPANY_ID = re.compile(r"(?<![\w])(U_[A-Za-z0-9_]+|SYNTHETIC_[A-Za-z0-9_]+)", re.IGNORECASE)
# длинное число — идентификатор, только если рядом маркер id; сумма или ИНН-подобное число без маркера — нет
# «выручкой компании 15000 млн»: за числом единица измерения — это сумма, а не id
_AMOUNT_UNIT = r"(?:млн|млрд|трлн|тыс|руб|р\.|₽|\$|€|usd|eur|долл|евро|%|шт|тонн|т\b|кг|ед\b|чел)"
_UID_WITH_WORD = re.compile(
    rf"(?:uid|\bid\b|инн|клиент\w*|компани\w*)\W{{0,4}}(\d{{5,}})(?!\d)(?![.,]?\s*{_AMOUNT_UNIT})",
    re.IGNORECASE,
)
_OTHER_COMPANY = re.compile(
    r"(?:друг\w+|чуж\w+|иной|иных|остальн\w+|конкурент\w*)\s+(?:\S+\s+){0,2}?"
    r"(?:компани\w*|клиент\w*|организац\w*|фирм\w*|предприяти\w*)|конкурент\w*"
    r"|(?:всех|каждого|любого|любой)\s+(?:\S+\s+)?(?:клиент\w*|компани\w*)",
    re.IGNORECASE,
)
_DATA_OR_SWITCH = re.compile(
    r"данн|платеж|платёж|выруч|контрагент|финанс|оборот|сч[её]т|баланс|прибыл|"
    r"покажи|выведи|выдай|дай\s|переключ|смени|поменя|от\s+имени|представь|сравни|список|id\b|company_id",
    re.IGNORECASE,
)


def foreign_company_mentions(question: str, own_company_id: str) -> list[str]:
    """Идентификаторы чужих компаний в тексте; пустой список — нет. Свой id допустим."""
    own = own_company_id.strip().upper()
    found: list[str] = []
    for m in _COMPANY_ID.finditer(question):
        if m.group(1).upper() != own:
            found.append(m.group(1))
    for rx in (_UID_WITH_WORD,):
        for m in rx.finditer(question):
            if m.group(1) != own_company_id:
                found.append(m.group(1))
    return found


# компания названа по правовой форме: «ООО Ромашка», «ПАО «Север»», «ИП Иванов». Свои профили в демо
# безымянные (id U_…), поэтому названное юрлицо вместе со словами о данных — это вопрос о чужой компании
_LEGAL_ENTITY = re.compile(
    r"(?<![А-ЯЁA-Za-zа-яё])(?:ООО|ОАО|ЗАО|ПАО|НАО|АО|ИП|ГУП|МУП)\s*[«\"'„]?\s*[А-ЯЁA-Z0-9]"
)


# попытка переписать правила модели в самом вопросе. Повелительное «выведи / напиши» сюда не входит:
# в вопросе пользователя это обычная просьба («выведи контрагентов»), в отличие от текста новости
_INJECTION = [
    re.compile(p, re.IGNORECASE | re.MULTILINE)
    for p in (
        r"\b(игнорир|забудь|отмени)\w*\b.{0,40}\b(инструкц|правил|указани|ограничени|сказанн)",
        r"\bignore\b.{0,30}\b(instructions?|previous|above|rules)",
        r"\b(предыдущ|прошл)\w* (инструкц|указани)\w*",
        r"\bнов\w+ (правила|инструкци)\w*",
        r"^\W*(system|assistant|developer|система|ассистент)\s*:",
        r"\bsystem prompt\b|\b(системн\w+|сво\w+) (промпт|инструкц)",
        r"\bты (больше не|теперь|отныне)\b|\byou are now\b|\bact as\b|\bрежим\w* (разработчика|отладки)",
        r"</?\s*(data|system|instructions?|prompt)\s*>",
    )
]


def is_injection_attempt(question: str) -> bool:
    return any(rx.search(question) for rx in _INJECTION)


def asks_about_other_company(question: str, own_company_id: str) -> tuple[bool, str | None]:
    """(чужая компания?, что именно упомянуто: id или None для описания словами)."""
    ids = foreign_company_mentions(question, own_company_id)
    if ids:
        return True, ids[0]
    named = _OTHER_COMPANY.search(question) or _LEGAL_ENTITY.search(question)
    if named and _DATA_OR_SWITCH.search(question):
        return True, None
    return False, None


# --- явная бессмыслица до вызова модели ---------------------------------------------------------

_RU_WORD = re.compile(r"[А-Яа-яЁё]{2,}")
_ABBR = re.compile(r"^[А-ЯЁ]{2,6}$")  # ОФЗ, ЦМИ, НДС: аббревиатуры словарь не знает


@lru_cache(maxsize=1)
def _morph() -> Any:
    # pymorphy2 напрямую не стартует на Python 3.12; обёртка Natasha работает (как в backend/bank/search.py)
    from natasha import MorphVocab

    return MorphVocab()


@lru_cache(maxsize=20_000)
def _in_dictionary(word: str) -> bool:
    try:
        parses = _morph().parse(word)
    except Exception:  # без словаря фильтр не должен мешать вопросу
        return True
    return any(type(p.methods_stack[0][0]).__name__ == "DictionaryAnalyzer" for p in parses)


def _stem(word: str) -> str:
    return word.lower().replace("ё", "е")[:5]


def is_gibberish(question: str, known_text: str = "") -> bool:
    """Явная бессмыслица: нет ни одной буквы или меньше половины русских слов есть в словаре.

    Словом считаются ≥ 2 букв кириллицы. Аббревиатуры (ОФЗ) и слова из карточки (`known_text`: фамилии,
    названия) считаются известными. Вопрос латиницей и пограничные случаи («ну и?», «ок») решает модель
    по полю `intent`.
    """
    if not re.search(r"[^\W\d_]", question):
        return True
    words = _RU_WORD.findall(question)
    if not words:
        return False
    known_stems = {_stem(w) for w in _RU_WORD.findall(known_text)}
    known = sum(
        1 for w in words if _ABBR.match(w) or _stem(w) in known_stems or _in_dictionary(w.lower())
    )
    return known / len(words) < 0.5


def _card_text(card: InsightCard | None) -> str:
    if card is None:
        return ""
    return " ".join([card.event.title, card.headline or "", *(c.text for c in card.claims)])


# --- выбор инструментов по намерению -------------------------------------------------------------

_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("get_counterparties", re.compile(r"контрагент|логист|поставщик|покупател|партн[её]р|зависимост", re.I)),
    ("get_profile", re.compile(r"профил|продукт|оквэд|отрасл|регион|наша компани|о нас\b|о компании", re.I)),
    ("get_bank_positions", re.compile(r"банк|цми|альфа|прогноз", re.I)),
    ("get_market_knowledge", re.compile(r"рынок|рынк|аналитик|консенсус|ожидан", re.I)),
    ("search_news", re.compile(r"новост|что произошло|сообщ|заявил|пресс", re.I)),
    ("find_similar_cases", re.compile(r"похож|аналог|прошл|раньше|прецедент", re.I)),
]


def plan_tools(question: str, event_type: str | None) -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = []
    for tool, rx in _INTENTS:
        if not rx.search(question):
            continue
        args: dict[str, Any] = {}
        if tool == "get_counterparties":
            sup = bool(re.search(r"поставщик|логист", question, re.I))
            buy = bool(re.search(r"покупател", question, re.I))
            if sup != buy:
                args = {"direction": "supplier" if sup else "buyer"}
        elif tool == "search_news":
            args = {"query": question[:300]}
        elif tool == "find_similar_cases":
            if not event_type:
                continue
            args = {"event_type": event_type}
        steps.append((tool, args))
    return steps[:MAX_TOOL_CALLS]


# --- досье ответа --------------------------------------------------------------------------------


def _own_items(tool: str, out: BaseModel) -> list[DossierItem]:
    """Элементы досье для инструментов про компанию: без идентификаторов контрагентов и сумм."""
    d = out.model_dump(mode="json")
    if tool == "get_profile":
        return [
            DossierItem(
                id="profile:self", kind="profile", title="Признаки вашей компании",
                data=d, source_tool=tool,
            )
        ]
    if tool == "get_counterparties":
        items = [
            DossierItem(
                id=f"cp:{c['direction']}:{c['rank']}", kind="counterparties",
                title=f"Контрагент ({'поставщик' if c['direction'] == 'supplier' else 'покупатель'}) "
                f"№{c['rank']}",
                data={
                    k: c[k]
                    for k in ("direction", "share", "okved_code", "sector", "same_group", "via_person")
                },
                source_tool=tool,
            )
            for c in d["counterparties"]
        ]
        summary = {k: v for k, v in d.items() if k != "counterparties"}
        items.append(
            DossierItem(
                id="cp:summary", kind="counterparties", title="Концентрация контрагентов",
                data=summary, source_tool=tool,
            )
        )
        return items
    return items_from_result(tool, out)


def card_items(card: InsightCard, positions: dict[str, Any] | None = None) -> list[DossierItem]:
    """Утверждения карточки как элементы досье: id — claim_id; доказательства банка — их cmi_-id.

    `positions` — позиции ЦМИ из источника по id: из них берутся тезис и дата документа.
    """
    positions = positions or {}
    items: list[DossierItem] = []
    for c in card.claims:
        if c.type == "bank_offer":
            continue
        items.append(
            DossierItem(
                id=c.claim_id, kind="fact", title=f"Утверждение карточки ({c.type})", text=c.text[:600],
                data={"claim_type": c.type, "evidence": [e.source_id for e in c.evidence]},
            )
        )
        for e in c.evidence:
            if c.type == "bank_view" and e.source_id.startswith(BANK_SOURCE_PREFIX):
                pos = positions.get(e.source_id)
                items.append(
                    DossierItem(
                        id=e.source_id, kind="bank_position",
                        title=pos.doc_title if pos else "Позиция ЦМИ",
                        text=((pos.quote if pos else e.quote) or "")[:600],
                        data={"thesis": pos.thesis, "doc_date": str(pos.doc_date)} if pos else {},
                    )
                )
    return items


def _source() -> ToolDataSource:
    try:
        from backend.agents.steps import get_tool_source

        return get_tool_source()
    except ImportError:
        return FixtureDataSource("rate_14")


def build_context(
    question: str, *, session: Session, card: InsightCard | None, trace_id: str,
    source: ToolDataSource,
) -> tuple[Dossier, list[str]]:
    """Досье ответа и список вызванных инструментов. company_id — только из сессии."""
    story_id = card.event.story_id if card else None
    stories = source.stories()
    # без карточки — первый общий сюжет; личные вставки других компаний (/analyze) сюда не попадают
    public = [s for s in stories if not has_paste(s)]
    story = next((s for s in stories if s.id == story_id), None) or (public[0] if public else None)
    if story is None:
        raise ToolError("not_found", "сюжет не найден")
    profile = source.profile(session.company_id)
    facts = next((f for f in source.event_facts() if f.story_id == story.id), None)
    dossier = Dossier(
        story_id=story.id, story_version=story.version, sector=(profile.sector if profile else "") or "",
        event_type=facts.event_type if facts else story.event_type,
        story_published_at=story.first_seen, trace_id=trace_id,
    )
    if facts is not None:
        for item in story_and_fact_items(story, facts):
            dossier.add(item)
    if card is not None:
        positions = {p.id: p for p in source.bank_positions()}
        for item in card_items(card, positions):
            dossier.add(item)
    ctx = ToolContext.for_story(
        source, story_id=story.id, company_id=session.company_id, role=session.role, trace_id=trace_id
    )
    used: list[str] = []
    for tool, args in plan_tools(question, dossier.event_type):
        try:
            out, _call = run_tool(ctx, tool, args)
        except ToolError as e:
            dossier.gaps.append(f"{tool}: {e.code}")
            continue
        used.append(tool)
        for item in _own_items(tool, out):
            dossier.add(item)
    return dossier, used


# --- проверка ответа -----------------------------------------------------------------------------

_MONEY = re.compile(r"\d[\d\s.,]*\s*(?:млн|млрд|трлн|тыс)?\.?\s*(?:₽|руб)", re.IGNORECASE)
_PAY_WORDS = re.compile(r"pay_amt|pay_cnt|company_id", re.IGNORECASE)


def _cited_pool(refs: list[str], dossier: Dossier, *, with_profile: bool) -> _Pool:
    """Числа и даты из элементов, на которые ссылается утверждение, и из их доказательств.

    Для вывода модели (`with_profile`) опорой служат ещё признаки компании. Доли 0.35 и проценты 35%
    считаются одним числом.
    """
    pool = _Pool()
    seen: set[str] = set()

    def add(item_id: str) -> None:
        item = dossier.get(item_id)
        if item is None or item_id in seen:
            return
        seen.add(item_id)
        pool.add_item(item)
        for ev in item.data.get("evidence", []) if item.data.get("claim_type") else []:
            add(ev)

    for r in refs:
        add(r)
    if with_profile:
        add("profile:self")
    for n in list(pool.numbers):
        try:
            v = Decimal(n)
        except InvalidOperation:
            continue
        if 0 < v <= 1 and (pct := _norm_number(str(v * 100))) is not None:
            pool.numbers.add(pct)
    return pool


def _forbidden_content(text: str, session: Session, pool: _Pool) -> bool:
    """Чужие идентификаторы, служебные имена полей и суммы платежей без подтверждения в ссылках."""
    if foreign_company_mentions(text, session.company_id) or _PAY_WORDS.search(text):
        return True
    return any(not extract_numbers(m.group(0)) <= pool.numbers for m in _MONEY.finditer(text))


def _claim_type(item: DossierItem) -> str | None:
    t = item.data.get("claim_type")
    return t if isinstance(t, str) else None


def _backs_opinion(item: DossierItem, prefix: str, claim_type: str) -> bool:
    """Ссылка годится для мнения: сам материал с нужным префиксом или утверждение карточки того же типа
    с доказательствами только такого префикса."""
    if item.id.startswith(prefix):
        return True
    evidence = item.data.get("evidence") or []
    return _claim_type(item) == claim_type and bool(evidence) and all(e.startswith(prefix) for e in evidence)


def verify_statements(
    statements: list[QAStatement], dossier: Dossier, session: Session
) -> list[QAStatement]:
    """Оставляет утверждения со ссылками из досье, правильной меткой и числами/датами из этих ссылок."""
    kept: list[QAStatement] = []
    for st in statements[:MAX_STATEMENTS]:
        refs = list(dict.fromkeys(st.refs))
        items = [dossier.get(r) for r in refs]
        if not refs or any(i is None for i in items):
            continue
        cited = [i for i in items if i is not None]
        opinion = {"bank_view": BANK_SOURCE_PREFIX, "market_view": MARKET_PREFIX}.get(st.kind)
        if opinion and not all(_backs_opinion(i, opinion, st.kind) for i in cited):
            continue
        if st.kind == "fact" and any(
            r.startswith((BANK_SOURCE_PREFIX, MARKET_PREFIX)) or _claim_type(i) not in (None, "fact")
            for r, i in zip(refs, cited, strict=True)
        ):
            continue  # факт, опирающийся на мнение банка, рынка или вывод модели, — это не факт
        pool = _cited_pool(refs, dossier, with_profile=st.kind == "model_inference")
        if _forbidden_content(st.text, session, pool):
            continue
        dates, rest = extract_dates(st.text)
        if not all(pool.has_date(d) for d in dates) or not extract_numbers(rest) <= pool.numbers:
            continue
        kept.append(st.model_copy(update={"refs": refs}))
    return kept


# --- точка входа ---------------------------------------------------------------------------------


def answer_question(
    question: str,
    *,
    session: Session,
    card_id: str | None,
    store: "CardStore",
    client: LLMClient,
    trace_id: str,
    audit: AuditLog | None = None,
    source: ToolDataSource | None = None,
    fixture_key: str | None = None,
) -> QAResult:
    """Ответ на вопрос по своей компании. Чужая компания в вопросе — отказ без вызова модели."""
    foreign, mentioned = asks_about_other_company(question, session.company_id)
    if foreign:
        (audit or get_audit()).record(
            user_id=session.user_id,
            session_company=session.company_id,
            requested_company=(mentioned or "unspecified")[:64],
            endpoint="/ask",
            trace_id=trace_id,
        )
        return QAResult(answer=REFUSED_TEXT, status="refused", trace_id=trace_id)
    if is_injection_attempt(question):  # без вызова модели и без упоминания чужих компаний
        log.warning("qa: попытка сменить правила trace_id=%s", trace_id)
        return QAResult(answer=INJECTION_TEXT, status="refused", trace_id=trace_id)

    card = store.get_card(card_id, session.company_id) if card_id else None  # чужая карточка -> None
    if is_gibberish(question, _card_text(card)):  # бесплатно, без вызова модели
        return QAResult(answer=CLARIFY_TEXT, status="unclear", trace_id=trace_id)
    try:
        dossier, used = build_context(
            question, session=session, card=card, trace_id=trace_id, source=source or _source()
        )
    except ToolError as e:
        log.warning("qa: контекст не собран trace_id=%s: %s", trace_id, e.code)
        return QAResult(answer=DEGRADED_TEXT, status="degraded", trace_id=trace_id)

    key = fixture_key or ("counterparties" if "get_counterparties" in used else None)
    try:
        res = client.call(
            step="qa", messages=build_messages(question, dossier, session.role), schema=QAAnswer,
            trace_id=trace_id, prompt_version=load_prompt()[0], fixture_key=key,
        )
    except (LLMUnavailable, BudgetExceeded) as e:
        log.warning("qa: модель недоступна trace_id=%s: %s", trace_id, type(e).__name__)
        return QAResult(answer=DEGRADED_TEXT, tools_used=used, status="degraded", trace_id=trace_id)

    value = res.value
    if value.intent != "answer":  # вопрос не понят или не по теме: досье не пересказываем
        text = CLARIFY_TEXT if value.intent == "unclear" else OFF_TOPIC_TEXT
        return QAResult(answer=text, tools_used=used, status="unclear", trace_id=trace_id)
    kept = verify_statements(value.statements, dossier, session)
    if not kept:
        return QAResult(answer=NO_DATA_TEXT, tools_used=used, status="ok", trace_id=trace_id)
    # текст ответа собирается только из проверенных утверждений: свободный `answer` модели не доверен
    answer = " ".join(s.text for s in kept)
    refs = list(dict.fromkeys(r for s in kept for r in s.refs))
    return QAResult(answer=answer, refs=refs, tools_used=used, status="ok", trace_id=trace_id)
