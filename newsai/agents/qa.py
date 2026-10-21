"""Вопрос-ответ по своей компании (шаг qa).

Контекст ответа — досье карточки и результаты инструментов, компания только из сессии.
Вопрос про другую компанию отсекается кодом до вызова модели и пишется в журнал безопасности.
Ответ модели проходит проверку: ссылки на существующие id, метки bank_view/market_view,
числа из цитат, отсутствие чужих идентификаторов и сумм платежей.
"""

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from newsai.agents.dossier import Dossier, DossierItem
from newsai.agents.planner import items_from_result, story_and_fact_items
from newsai.agents.tools import FixtureDataSource, ToolContext, ToolDataSource, ToolError, run_tool
from newsai.llm import BudgetExceeded, LLMClient, LLMUnavailable, data_block
from newsai.schemas.common import BANK_SOURCE_PREFIX
from newsai.schemas.insight import InsightCard
from newsai.security.audit import AuditLog, get_audit
from newsai.security.session import Session

if TYPE_CHECKING:
    from newsai.api.store import CardStore

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "qa.md"
MARKET_PREFIX = "mk_"
MAX_TOOL_CALLS = 4
MAX_STATEMENTS = 5

REFUSED_TEXT = (
    "Я отвечаю только по данным вашей компании. Данные других компаний и клиентов банка "
    "показать не могу; если нужен вопрос по вашей компании, задайте его."
)
NO_DATA_TEXT = "В данных по вашей компании и в материалах сюжета ответа на этот вопрос нет."
DEGRADED_TEXT = "Сейчас ответить не получилось: сервис разбора временно недоступен. Попробуйте позже."


# --- схемы ---------------------------------------------------------------------------------------


class QAStatement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1)
    kind: Literal["fact", "bank_view", "market_view", "model_inference"]
    refs: list[str] = Field(default_factory=list)


class QAAnswer(BaseModel):
    """Ответ модели: текст, ссылки и утверждения с метками."""

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(min_length=1)
    refs: list[str] = Field(default_factory=list)
    statements: list[QAStatement] = Field(default_factory=list)


class QAResult(BaseModel):
    answer: str
    refs: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    status: Literal["ok", "refused", "degraded"]
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
_NUMERIC_UID = re.compile(r"(?<!\d)(\d{8,})(?!\d)")
_UID_WITH_WORD = re.compile(r"(?:uid|\bid\b|инн|клиент\w*|компани\w*)\W{0,4}(\d{5,})", re.IGNORECASE)
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
    for rx in (_NUMERIC_UID, _UID_WITH_WORD):
        for m in rx.finditer(question):
            if m.group(1) != own_company_id:
                found.append(m.group(1))
    return found


def asks_about_other_company(question: str, own_company_id: str) -> tuple[bool, str | None]:
    """(чужая компания?, что именно упомянуто: id или None для описания словами)."""
    ids = foreign_company_mentions(question, own_company_id)
    if ids:
        return True, ids[0]
    if _OTHER_COMPANY.search(question) and _DATA_OR_SWITCH.search(question):
        return True, None
    return False, None


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
        from newsai.agents.steps import get_tool_source

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
    story = next((s for s in stories if s.id == story_id), None) or (stories[0] if stories else None)
    if story is None:
        raise ToolError("not_found", "сюжет не найден")
    profile = source.profile(session.company_id)
    facts = next((f for f in source.event_facts() if f.story_id == story.id), None)
    dossier = Dossier(
        story_id=story.id, story_version=story.version, sector=(profile.sector if profile else "") or "",
        event_type=facts.event_type if facts else story.event_type,
        story_published_at=story.last_seen, trace_id=trace_id,
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

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_MONEY = re.compile(r"\d[\d\s.,]*\s*(?:млн|млрд|трлн|тыс)?\.?\s*(?:₽|руб)", re.IGNORECASE)
_PAY_WORDS = re.compile(r"pay_amt|pay_cnt|company_id", re.IGNORECASE)


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUM.findall(text):
        try:
            v = float(m.replace(",", "."))
        except ValueError:
            continue
        out.add(v)
        if 0 < v <= 1:
            out.add(round(v * 100, 6))  # доли в данных (0.35) и проценты в тексте (35%)
    return out


def _item_text(item: DossierItem) -> str:
    return f"{item.title} {item.text} {json.dumps(item.data, ensure_ascii=False, default=str)}"


def _forbidden_content(text: str, session: Session, dossier_text: str) -> bool:
    """Чужие идентификаторы, служебные имена полей и суммы платежей, которых нет в досье."""
    if foreign_company_mentions(text, session.company_id) or _PAY_WORDS.search(text):
        return True
    known = _numbers(dossier_text)
    for m in _MONEY.finditer(text):
        glued = re.sub(r"(?<=\d)[\s ](?=\d)", "", m.group(0))  # «5 200 000» -> «5200000»
        if not _numbers(glued) <= known:
            return True
    return False


def verify_statements(
    statements: list[QAStatement], dossier: Dossier, session: Session
) -> list[QAStatement]:
    """Оставляет только утверждения со ссылками из досье, правильной меткой и числами из досье."""
    kept: list[QAStatement] = []
    all_text = " ".join(_item_text(i) for i in dossier.items)
    for st in statements[:MAX_STATEMENTS]:
        refs = list(dict.fromkeys(st.refs))
        if not refs or any(dossier.get(r) is None for r in refs):
            continue
        if st.kind == "bank_view" and not all(r.startswith(BANK_SOURCE_PREFIX) for r in refs):
            continue
        if st.kind == "market_view" and not all(r.startswith(MARKET_PREFIX) for r in refs):
            continue
        if st.kind == "fact" and any(r.startswith((BANK_SOURCE_PREFIX, MARKET_PREFIX)) for r in refs):
            continue  # факт, опирающийся на мнение банка или рынка, — это не факт
        if _forbidden_content(st.text, session, all_text):
            continue
        if st.kind != "model_inference":
            cited = " ".join(_item_text(dossier.get(r)) for r in refs if dossier.get(r))  # type: ignore[arg-type]
            if not _numbers(st.text) <= _numbers(cited):
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

    card = store.get_card(card_id, session.company_id) if card_id else None  # чужая карточка -> None
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
    kept = verify_statements(value.statements, dossier, session)
    if not kept:
        return QAResult(answer=NO_DATA_TEXT, tools_used=used, status="ok", trace_id=trace_id)
    # текст ответа собирается только из проверенных утверждений: свободный `answer` модели не доверен
    answer = " ".join(s.text for s in kept)
    refs = list(dict.fromkeys(r for s in kept for r in s.refs))
    return QAResult(answer=answer, refs=refs, tools_used=used, status="ok", trace_id=trace_id)
