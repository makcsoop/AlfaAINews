"""Сборка InsightCard из черновика модели.

Текст пишет модель, всё остальное — код: claim_id, метки, evidence с цитатами из досье, based_on,
полярность и горизонт (из вкладов скоринга и отчётов аналитиков), уверенность, предложение банка.
Финальную проверку чисел и цитат (день 3) эта функция не делает: только собирает и ловит
структурные ошибки (чужие id, неверные метки, «банк ожидает» вне раздела банка).
"""

import logging
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from rapidfuzz import fuzz

from backend.agents.dossier import Dossier, DossierItem
from backend.cards.analysis import build_analysis
from backend.cards.features import (
    allowed_features,
    canonical,
    channel_label,
    is_sector_feature,
)
from backend.cards.llm_schema import CardDraft, DraftItem, DraftSurprise
from backend.cards.offers import find_offer
from backend.cards.signals import analysts_disagree, channel_horizon, channel_mean
from backend.ranking.confidence import confidence as score_confidence
from backend.ranking.surprise import find_surprise
from backend.schemas import (
    Action,
    Analysis,
    AnalystReport,
    BankPosition,
    Claim,
    CompanyProfile,
    CounterpartyGraph,
    EventFacts,
    Evidence,
    InsightCard,
    MarketKnowledge,
    RelevanceScore,
    Source,
    Story,
)
from backend.schemas.common import BANK_SOURCE_PREFIX, Confidence, Polarity, UserRole
from backend.schemas.insight import CardEvent, Condition, StorySource, Surprise

log = logging.getLogger(__name__)

MARKET_SOURCE_PREFIX = "mk_"

# виды элементов досье, на которые опирается факт или вывод
FACT_KINDS = frozenset({"fact", "news", "story", "calc", "sector", "case"})
WHAT_HAPPENED_KINDS = frozenset({"fact", "news", "story", "calc"})
# элементы про клиента: допустимые refs, но отдельных утверждений не создают
CLIENT_KINDS = frozenset({"profile", "counterparties"})

# «позиция банка» вне раздела банка
# «банк» в единственном числе и не «центробанк»: «Коммерческие банки ожидают…», «Центробанк прогнозирует…» —
# не позиция Альфа-Банка
_BANK_VOICE = re.compile(
    r"((?<!центро)(?<![а-яё])банк\s+(ожида|ждёт|ждет|ждал|прогнозир|считает|полагает|оценива)"
    r"|по\s+(мнению|оценке|прогнозу)\s+(банка|цми)"
    r"|цми\s+(ожида|ждёт|ждет|ждал|прогнозир|считает|полагает|оценива))",
    re.IGNORECASE,
)
# предложение банка добавляет только код
_OFFER_VOICE = re.compile(r"предложени\w+\s+банка", re.IGNORECASE)

HORIZON_TEXT = {
    "days": "1–7 дн.",
    "weeks": "2–4 нед.",
    "months": "1–3 мес.",
    "quarters": "3–9 мес.",
    "years": "от 1 года",
}

_TOKEN = re.compile(r"[\w%]+", re.UNICODE)
NEAR_DUP = 90  # rapidfuzz ratio: «в РФ речи не идёт» и «в России речи не идёт» — одна цитата


class CardAssemblyError(ValueError):
    """Черновик нельзя собрать в карточку: нарушены правила ссылок, меток или разделов."""


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _TOKEN.findall(text.lower()) if len(w) > 1}


_NUM = re.compile(r"\d+(?:[.,]\d+)?")
# сокращения, после которых точка не конец предложения: «2027 г.», «млн.», «т. е.», «Э. Набиуллина»
_ABBR_END = re.compile(
    r"(?:(?<![\w])(?:гг?|т\.\s?[едпк]|т|млн|млрд|трлн|тыс|руб|долл|ст|им|см|др|пр|напр|ок|п|пп|стр|мес|нед"
    r"|кв|ч|мин|б\.\s?п)|(?<![\w])[А-ЯЁA-Z])\.$",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    """Предложения без разрыва на сокращениях: следующий кусок со строчной буквы или цифры — продолжение."""
    out: list[str] = []
    for piece in (p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip())):
        if not piece:
            continue
        if out and (piece[0].islower() or piece[0].isdigit() or _ABBR_END.search(out[-1])):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def _best_sentence(text: str, claim_text: str, limit: int = 300) -> str:
    sentences = _sentences(text)
    if not sentences:
        return text[:limit]
    want = _stems(claim_text)
    nums = set(_NUM.findall(claim_text))

    def found(s: str) -> set[str]:
        return nums & set(_NUM.findall(s))

    def score(k: int) -> int:
        return len(_stems(sentences[k]) & want) + 2 * len(found(sentences[k]))

    i = max(range(len(sentences)), key=score)
    # чисел утверждения в лучшем предложении нет, а в тексте есть — расширяем цитату соседними предложениями
    lo = hi = i
    in_text = found(text)
    while found(" ".join(sentences[lo : hi + 1])) < in_text and (lo > 0 or hi < len(sentences) - 1):
        nxt = hi + 1 if hi < len(sentences) - 1 else hi
        prv = lo - 1 if lo > 0 else lo
        if len(" ".join(sentences[prv : nxt + 1])) > limit:
            break
        lo, hi = prv, nxt
    return " ".join(sentences[lo : hi + 1])[:limit]


def _norm_quote(q: str | None) -> str:
    text = re.sub(r"[«»“”„\"'’.,;:!?…—–-]", " ", (q or "").casefold().replace("ё", "е"))
    return re.sub(r"\s+", " ", text).strip()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _sign(x: float | None) -> int:
    return 0 if x is None else (x > 0) - (x < 0)


class _Builder:
    def __init__(self, dossier: Dossier, features: list[str]) -> None:
        self.dossier = dossier
        self.features = features
        self.claims: list[Claim] = []
        self._count: dict[str, int] = {}
        # id досье -> claim_id утверждения, которое на него ссылается (сначала — с одним этим источником)
        self.by_ref: dict[str, str] = {}
        self._multi_ref: dict[str, str] = {}
        self.bank_items: list[DossierItem] = []  # позиции ЦМИ, попавшие в карточку

    def new_id(self, prefix: str) -> str:
        self._count[prefix] = self._count.get(prefix, 0) + 1
        return f"{prefix}{self._count[prefix]}"

    def resolve(self, refs: Sequence[str], where: str) -> list[DossierItem]:
        items = []
        for r in refs:
            item = self.dossier.get(r)
            if item is None:
                raise CardAssemblyError(f"{where}: в досье нет id {r!r}")
            items.append(item)
        return items

    def feats(self, raw: Sequence[str]) -> list[str]:
        """Только признаки из списка компании (дословно); выдуманные отбрасываем."""
        out = [c for f in raw if (c := canonical(f, self.features))]
        return list(dict.fromkeys(out))

    def evidence(self, item: DossierItem, claim_text: str) -> Evidence:
        if item.kind == "story":
            # сюжет — не документ: цитируем материал, на котором экстрактор построил «суть события»
            head = self.dossier.get(f"fact:{item.id}:1")
            if head is not None and head.data.get("source_id"):
                return self.evidence(head, claim_text)
        d = item.data
        source_id = str(d.get("source_id") or item.id) if item.kind == "fact" else item.id
        quote = d.get("quote") or (item.text and _best_sentence(item.text, claim_text)) or item.title
        doc_date = _as_datetime(d.get("doc_date")) or item.published_at
        # у новости в data.source_id — лента («prime»); у факта — id материала, ленту допишет проверка
        feed = d.get("source_id") if item.kind == "news" else None
        return Evidence(
            source_id=source_id, quote=str(quote)[:400], url=d.get("url"), doc_date=doc_date, feed_id=feed
        )

    def evidences(self, items: Sequence[DossierItem], claim_text: str) -> list[Evidence]:
        """Одна цитата на документ и без почти одинаковых цитат (перепечатки одного текста)."""
        res: list[Evidence] = []
        for it in items:
            ev = self.evidence(it, claim_text)
            q = _norm_quote(ev.quote)
            dup = any(
                ev.source_id == r.source_id or (q and fuzz.ratio(q, _norm_quote(r.quote)) >= NEAR_DUP)
                for r in res
            )
            if not dup:
                res.append(ev)
        return res

    def add(self, claim: Claim, refs: Sequence[str]) -> str:
        self.claims.append(claim)
        if len(refs) == 1:
            self.by_ref.setdefault(refs[0], claim.claim_id)
        elif claim.type in ("fact", "bank_view", "market_view"):
            # утверждение по нескольким источникам тоже опора: иначе вывод породит дубль факта
            for r in refs:
                self._multi_ref.setdefault(r, claim.claim_id)
        return claim.claim_id

    def support(self, item: DossierItem) -> str:
        """claim_id утверждения, на которое опирается вывод; нет такого — создаём из элемента досье."""
        if item.id in self.by_ref:
            return self.by_ref[item.id]
        if item.id in self._multi_ref:
            return self._multi_ref[item.id]
        # у позиции банка и мнения рынка title — название обзора («Макростатистика 7М26: …»), тезис — в text:
        # с названием опорные утверждения одного обзора повторялись, а «7» из «7М26» не находилось в цитате
        if item.kind in ("bank_position", "market_knowledge"):
            text = item.text or item.title
        else:
            text = item.title or item.text
        if item.kind == "bank_position":
            self.bank_items.append(item)
            claim = Claim(
                claim_id=self.new_id("B"), type="bank_view", text=text, evidence=[self.evidence(item, text)]
            )
        elif item.kind == "market_knowledge":
            claim = Claim(
                claim_id=self.new_id("M"), type="market_view", text=text, evidence=[self.evidence(item, text)]
            )
        else:
            claim = Claim(
                claim_id=self.new_id("F"), type="fact", text=text, evidence=[self.evidence(item, text)]
            )
        return self.add(claim, [item.id])


def _bank_voice(text: str) -> bool:
    """«Банк ожидает…» — позиция банка; вне раздела «Банк и рынок» такое утверждение выбрасывается целиком:
    живой прогон 24.10 — одна фраза «Альфа-Банк ожидает…» в последствиях отправляла всю карточку в шаблон."""
    return bool(_BANK_VOICE.search(text))


def _scan(text: str, where: str, *, bank_section: bool = False) -> None:
    if _OFFER_VOICE.search(text):
        raise CardAssemblyError(f"{where}: «предложение банка» добавляет код, не модель")
    if not bank_section and _BANK_VOICE.search(text):
        raise CardAssemblyError(f"{where}: позиция банка вне раздела «Банк и рынок»: {text[:80]!r}")


def _polarity(relevance: RelevanceScore, features: list[str]) -> Polarity:
    """Знак для клиента — из вкладов скоринга по признакам, на которые опирается вывод."""
    fset = {f.casefold() for f in features}
    hits = [c for c in relevance.contributions if c.sign and fset & {f.casefold() for f in c.client_features}]
    if not hits:
        return relevance.polarity
    top = max(hits, key=lambda c: c.value)
    return "risk" if top.sign < 0 else "opportunity"


def _claim_horizon(
    relevance: RelevanceScore, analyses: Sequence[AnalystReport], features: list[str]
) -> str | None:
    fset = {f.casefold() for f in features}
    hits = [c for c in relevance.contributions if fset & {f.casefold() for f in c.client_features}]
    if not hits:
        return None
    h = channel_horizon(analyses, max(hits, key=lambda c: c.value).factor)
    return HORIZON_TEXT.get(h) if h else None


def _event(
    story: Story, dossier: Dossier, sources: Sequence[Source] | None, event_type: str | None
) -> CardEvent:
    tiers = {s.id: s.tier for s in sources or []}
    news = {}
    for it in dossier.by_kind("news"):
        sid = it.data.get("source_id")
        if sid and sid not in news:
            news[sid] = it
    ordered = list(
        dict.fromkeys([*([story.first_source_id] if story.first_source_id else []), *story.source_ids])
    )
    out = []
    for sid in ordered:
        found = news.get(sid)
        tier = tiers.get(sid, found.data.get("tier") if found else None)
        out.append(
            StorySource(
                source_id=sid,
                tier=tier if tier in (0, 1, 2, 3) else 3,
                url=found.data.get("url") if found else None,
                title=found.title if found else None,
            )
        )
    return CardEvent(
        story_id=story.id,
        event_type=event_type or story.event_type,
        status=story.status,
        title=story.title,
        first_seen=story.first_seen,
        sources=out,
        corroboration=story.corroboration,
    )


def _direct_channel(profile: CompanyProfile, relevance: RelevanceScore) -> bool:
    if not relevance.contributions:
        return False
    top = max(relevance.contributions, key=lambda c: c.value)
    if top.factor.startswith("counterparty:") or top.value < 0.3 or not top.client_features:
        return False
    assumed = {d for e in profile.exposures if e.is_assumption for d in e.drivers}
    firm = {d for e in profile.exposures if not e.is_assumption for d in e.drivers}
    return not set(top.client_features) <= (assumed - firm)


def compute_confidence(
    *,
    event: CardEvent,
    relevance: RelevanceScore,
    analyses: Sequence[AnalystReport],
    bank_items: Sequence[DossierItem],
    profile: CompanyProfile,
) -> tuple[Confidence, list[str]]:
    """high — первичный источник + позиция банка того же направления + прямой канал; иначе medium/low."""
    reasons: list[str] = []
    primary = any(s.tier == 0 for s in event.sources)
    reasons.append("первоисточник — регулятор" if primary else "нет первичного источника")

    factor = max(relevance.contributions, key=lambda c: c.value).factor if relevance.contributions else None
    mean = channel_mean(analyses, factor) if factor else None
    bank_same = False
    if factor and mean is not None:
        stances = [
            _sign(i.data.get("direction"))  # позиция ЦМИ уже хранит направление фактора
            for i in bank_items
            if i.data.get("channel") == factor and i.data.get("direction") is not None
        ]
        bank_same = any(s == _sign(mean) for s in stances if s)
        if stances and not bank_same:
            reasons.append("позиция ЦМИ расходится с аналитиками по знаку")
    reasons.append(
        "позиция ЦМИ совпадает по знаку"
        if bank_same
        else "нет позиции ЦМИ того же направления по главному каналу"
    )

    direct = _direct_channel(profile, relevance)
    reasons.append(
        f"прямой канал: {channel_label(factor)}"
        if direct and factor
        else "связь с клиентом косвенная или предположительная"
    )

    score = int(primary) + int(bank_same) + int(direct)
    level: Confidence = "high" if score == 3 else "medium" if score == 2 else "low"
    if factor and analysts_disagree(analyses, factor):
        reasons.append("аналитики расходятся по направлению фактора")
        level = "low" if level == "low" else "medium"
    if event.status in ("rumor", "contradictory"):
        reasons.append(f"статус сюжета: {event.status}")
        level = "low"
    elif event.status in ("unconfirmed", "single") and not primary and level == "high":
        reasons.append("сюжет не подтверждён независимыми источниками")
        level = "medium"
    return level, reasons


def _bank_from_dossier(dossier: Dossier) -> list[BankPosition]:
    """Позиции ЦМИ из досье: в data часть полей, недостающие подставляем."""
    out: list[BankPosition] = []
    for it in dossier.by_kind("bank_position"):
        d = it.data
        doc_date = (it.published_at or _as_datetime(d.get("doc_date")))
        if doc_date is None:
            continue
        try:
            out.append(
                BankPosition(
                    id=it.id, doc_id=it.id.split("#")[0], doc_title=it.title, doc_date=doc_date.date(),
                    page=1, thesis=it.title, channel=d.get("channel"), direction=d.get("direction"),
                    forecast=d.get("forecast"), quote=str(d.get("quote") or it.text or it.title),
                )
            )
        except ValidationError:
            continue
    return out


def _market_from_dossier(dossier: Dossier) -> list[MarketKnowledge]:
    """Знания рынка в досье неполные: берём только те, что собираются в схему целиком."""
    out: list[MarketKnowledge] = []
    for it in dossier.by_kind("market_knowledge"):
        try:
            out.append(MarketKnowledge.model_validate(it.data))
        except ValidationError:
            continue
    return out


def _build_surprise(
    ds: DraftSurprise | None, dossier: Dossier, story_published_at: datetime
) -> Surprise | None:
    """Числа — из досье. Нет ни одного ожидания до события — строки нет."""
    if ds is None:
        return None
    actual_item = dossier.get(ds.actual_ref)
    if actual_item is None or not isinstance(actual_item.data.get("value"), int | float):
        return None

    def expectation(ref: str | None, prefix: str, kind: str, key: str) -> float | None:
        if not ref:
            return None
        it = dossier.get(ref)
        if it is None or it.kind != kind or not it.id.startswith(prefix):
            return None
        pub = it.published_at or _as_datetime(it.data.get("doc_date"))
        if pub is not None and pub.astimezone(UTC) > story_published_at.astimezone(UTC):
            return None  # инвариант 10: ожидание должно быть опубликовано до события
        v: Any = it.data
        for part in key.split("."):  # «forecast.value» — вложенное поле позиции ЦМИ
            v = v.get(part) if isinstance(v, dict) else None
        return float(v) if isinstance(v, int | float) else None

    bank = expectation(ds.bank_ref, BANK_SOURCE_PREFIX, "bank_position", "forecast.value")
    market = expectation(ds.market_ref, MARKET_SOURCE_PREFIX, "market_knowledge", "expected_value")
    if bank is None and market is None:
        return None
    actual = float(actual_item.data["value"])
    ref_value = market if market is not None else bank
    assert ref_value is not None
    direction = "in_line" if abs(actual - ref_value) < 1e-9 else "above" if actual > ref_value else "below"
    return Surprise(
        metric=ds.metric,
        unit=ds.unit or actual_item.data.get("unit"),
        actual=actual,
        market_expected=market,
        market_ref=ds.market_ref if market is not None else None,
        bank_expected=bank,
        bank_ref=ds.bank_ref if bank is not None else None,
        direction=direction,
    )


def assemble_card(
    draft: CardDraft,
    *,
    story: Story,
    dossier: Dossier,
    relevance: RelevanceScore,
    profile: CompanyProfile,
    role: UserRole,
    trace_id: str,
    analyses: Sequence[AnalystReport],
    graph: CounterpartyGraph | None = None,
    sources: Sequence[Source] | None = None,
    with_offer: bool = True,
    facts: EventFacts | None = None,
    analysis: Analysis | None = None,
    bank_positions: Sequence[BankPosition] | None = None,
    market: Sequence[MarketKnowledge] | None = None,
) -> InsightCard:
    if relevance.company_id != profile.company_id or relevance.story_id != story.id:
        raise CardAssemblyError("relevance или профиль относятся к другой паре «сюжет × клиент»")
    b = _Builder(dossier, allowed_features(profile, graph, relevance))
    event = _event(story, dossier, sources, dossier.event_type)

    # 1. что произошло — факты
    what_happened: list[str] = []
    for n, it in enumerate(draft.what_happened):
        where = f"what_happened[{n}]"
        _scan(it.text, where)
        items = b.resolve(it.refs, where)
        bad = [i.id for i in items if i.kind not in WHAT_HAPPENED_KINDS]
        if bad:
            raise CardAssemblyError(f"{where}: факт не может опираться на {bad} (нужны факты и материалы)")
        cid = b.new_id("F")
        what_happened.append(
            b.add(
                Claim(claim_id=cid, type="fact", text=it.text, evidence=b.evidences(items, it.text)), it.refs
            )
        )

    # 2. банк и рынок — метка по ссылкам, не по желанию модели
    bank_and_market: list[str] = []
    for n, it in enumerate(draft.bank_and_market):
        where = f"bank_and_market[{n}]"
        _scan(it.text, where, bank_section=True)
        items = b.resolve(it.refs, where)
        is_bank = all(i.kind == "bank_position" and i.id.startswith(BANK_SOURCE_PREFIX) for i in items)
        is_market = all(i.kind == "market_knowledge" and i.id.startswith(MARKET_SOURCE_PREFIX) for i in items)
        if not (is_bank or is_market):
            raise CardAssemblyError(
                f"{where}: позиция банка — только cmi_…, мнение рынка — только mk_…, получено {it.refs}"
            )
        label = "bank_view" if is_bank else "market_view"
        if it.kind and it.kind != label:
            raise CardAssemblyError(f"{where}: метка {it.kind} не совпадает со ссылками {it.refs}")
        if is_bank:
            b.bank_items.extend(items)
        claim = Claim(
            claim_id=b.new_id("B" if is_bank else "M"),
            type=label,
            text=it.text,
            evidence=b.evidences(items, it.text),
        )
        bank_and_market.append(b.add(claim, it.refs))

    # 3. выводы AI: почему важно и последствия
    dropped: list[str] = []  # утверждения с голосом банка вне раздела банка — не в карточку
    def inferences(section: list[DraftItem], name: str) -> tuple[list[str], list[list[str]]]:
        ids, used = [], []
        for n, it in enumerate(section):
            where = f"{name}[{n}]"
            if _bank_voice(it.text):
                dropped.append(where)
                continue
            _scan(it.text, where)
            items = b.resolve(it.refs, where)
            feats = b.feats(it.client_features)
            based: dict[str, list[str]] = {"facts": [], "bank_views": [], "market_views": []}
            for item in items:
                if item.kind in FACT_KINDS:
                    key = "facts"
                elif item.kind == "bank_position":
                    key = "bank_views"
                elif item.kind == "market_knowledge":
                    key = "market_views"
                else:
                    continue
                cid = b.support(item)
                if cid not in based[key]:
                    based[key].append(cid)
            based = {k: v for k, v in based.items() if v}
            if feats:
                based["client_features"] = feats
            claim = Claim(
                claim_id=b.new_id("I"),
                type="model_inference",
                text=it.text,
                based_on=based,
                polarity=_polarity(relevance, feats),
                horizon=_claim_horizon(relevance, analyses, feats),
            )
            ids.append(b.add(claim, []))
            used.append(feats)
        return ids, used

    why_ids, why_feats = inferences(draft.why_it_matters, "why_it_matters")
    cons_ids, _ = inferences(draft.consequences, "consequences")

    # «почему вы это видите» — не меньше двух признаков компании кроме отрасли
    own = {f for fs in why_feats for f in fs if not is_sector_feature(f)}
    available = [f for f in b.features if not is_sector_feature(f)]
    if len(own) < min(2, len(available)):
        raise CardAssemblyError(
            f"why_it_matters: нужно ≥ 2 признаков компании кроме отрасли, есть {sorted(own)}"
        )

    # 4. действия и что изменит вывод
    actions: list[Action] = []
    for n, a in enumerate(draft.actions):
        where = f"actions[{n}]"
        if _bank_voice(a.text):
            dropped.append(where)
            continue
        _scan(a.text, where)
        b.resolve(a.refs, where)
        # вопрос и сценарий — вывод модели; ссылки — проверенные id досье (инвариант 4)
        refs = list(dict.fromkeys(a.refs))
        actions.append(Action(type=a.type, text=a.text, kind="model_inference", refs=refs))
    conditions: list[Condition] = []
    for n, it in enumerate(draft.what_would_change):
        where = f"what_would_change[{n}]"
        if _bank_voice(it.text):
            dropped.append(where)
            continue
        _scan(it.text, where)
        b.resolve(it.refs, where)
        conditions.append(Condition(text=it.text, kind="model_inference", refs=list(dict.fromkeys(it.refs))))

    # 5. предложение банка — только по триггеру, кодом
    if with_offer:
        offer = find_offer(profile=profile, relevance=relevance, analyses=analyses, story_status=story.status)
        if offer is not None:
            offer_id = b.new_id("O")
            based_on: dict[str, list[str]] = {"client_features": list(offer.client_features)}
            if what_happened:
                based_on["facts"] = what_happened[:1]
            b.claims.append(
                Claim(
                    claim_id=offer_id,
                    type="bank_offer",
                    text=offer.text,
                    based_on=based_on,
                    is_advertising=True,
                    trigger=offer.trigger_id,
                )
            )
            actions.append(
                Action(
                    type="bank_product",
                    text=offer.text,
                    is_advertising=True,
                    kind="bank_offer",
                    refs=[offer_id],
                )
            )

    bank_models = list(bank_positions) if bank_positions is not None else _bank_from_dossier(dossier)
    market_models = list(market) if market is not None else _market_from_dossier(dossier)
    if analysis is None and analyses:
        analysis = build_analysis(story.id, dossier.sector, story.version, analyses)
    try:
        confidence, reasons = score_confidence(
            story=story,
            relevance=relevance,
            profile=profile,
            sources={s.id: s for s in sources or []},
            analysis=analysis,
            claims=b.claims,
            bank_positions=bank_models,
            market=market_models,
        )
    except Exception:  # запасная формула, если скоринг Р2 не смог посчитать
        confidence, reasons = compute_confidence(
            event=event, relevance=relevance, analyses=analyses, bank_items=b.bank_items, profile=profile
        )
    # сюрприз: сначала код по фактам, черновик модели — только если код ничего не нашёл
    surprise = find_surprise(story, facts, bank_positions=bank_models, market=market_models)
    if surprise is None:
        surprise = _build_surprise(draft.surprise, dossier, dossier.story_published_at)
    if dropped:
        log.info("карточка %s/%s: выброшены утверждения с голосом банка вне раздела банка: %s",
                 story.id, profile.company_id, ", ".join(dropped))
    return InsightCard(
        card_id=f"card_{story.id}_{profile.company_id}_{role}",
        company_id=profile.company_id,
        user_role=role,
        version=story.version,
        stage="deep",
        analysis_ref=f"{story.id}:{dossier.sector}:{story.version}",
        headline=draft.headline[:200],
        event=event,
        relevance=relevance.model_copy(update={"deep_analysis": True}),
        what_happened=what_happened,
        why_it_matters=why_ids,
        consequences=cons_ids,
        actions=actions,
        bank_and_market=bank_and_market,
        what_would_change=[c.text for c in conditions],
        conditions=conditions,
        surprise=surprise,
        claims=b.claims,
        confidence=confidence,
        confidence_reasons=reasons,
        review_status="published",
        trace_id=trace_id,
    )
