"""Модуль «Банк и рынок» (Р1, для разбора Р3): тезисы ЦМИ и консенсус рынка по сюжету. Без генерации.

Что отдаёт:
- позиции банка по факторам сюжета (поиск BM25 + плотный, RRF), только опубликованные ДО события,
  с цитатой, страницей, датой обзора и пометкой «устарела»;
- консенсус аналитиков рынка к ближайшему заседанию ЦБ (если сюжет про ставку) — только посты до решения;
- сюрприз: «рынок ждал X, банк ждал Y, вышло Z», если в фактах сюжета есть фактическое значение.
Мнение рынка и позиция банка разделены: позиция банка — только материалы ЦМИ (BANK_SOURCE_PREFIX).
"""

from datetime import date, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import Connection, select

from backend.bank.search import BankIndex
from backend.db import models as m
from backend.schemas import EventFacts, MarketKnowledge, Story

RATE_TYPES = {"rate_decision", "monetary_signal"}


class BankView(BaseModel):
    position_id: str
    thesis: str
    quote: str
    doc_title: str
    doc_date: date
    page: int
    channel: str | None
    direction: float | None
    forecast: dict | None
    before_event: bool
    stale: bool
    score: float


class MarketView(BaseModel):
    meeting: date
    statement: str
    expected_value: float | None
    channels: int
    evidence: list[dict]


class Surprise(BaseModel):
    metric: str
    actual: float
    market_expected: float | None
    bank_expected: float | None
    bank_ref: str | None
    market_ref: str | None
    direction: str | None


class BankMarketReport(BaseModel):
    story_id: str
    as_of: date
    channels: list[str]
    bank: list[BankView]
    market: MarketView | None
    surprise: Surprise | None


def _consensus(conn: Connection, day: date, before: datetime | None = None) -> MarketKnowledge | None:
    """Консенсус к заседанию (день события или 3 дня до него), опубликованный раньше сюжета — инвариант 10."""
    rows = conn.execute(
        select(m.market_knowledge.c.data).where(m.market_knowledge.c.id.like("mk_consensus_%"))
    ).scalars()
    best = None
    for r in rows:
        mk = MarketKnowledge.model_validate(r)
        if before and mk.published_at >= before:
            continue
        if (mk.target_event_date and timedelta(0) <= mk.target_event_date - day <= timedelta(days=3)) or (
            mk.target_event_date == day
        ):
            best = mk
    return best


def _actual_rate(facts: EventFacts) -> float | None:
    """Ставка после решения: для сюжета о решении — первое число в % из правдоподобного диапазона."""
    for n in facts.numbers:
        if (
            n.unit == "%"
            and 3 <= n.value <= 30
            and (facts.event_type == "rate_decision" or "ставк" in n.quote.lower())
        ):
            return n.value
    return None


def report(conn: Connection, story_id: str, index: BankIndex | None = None, k: int = 5) -> BankMarketReport:
    story = Story.model_validate(
        conn.execute(select(m.stories.c.data).where(m.stories.c.id == story_id)).scalar_one()
    )
    facts_raw = conn.execute(
        select(m.event_facts.c.data).where(m.event_facts.c.story_id == story_id)
    ).scalar()
    facts = EventFacts.model_validate(facts_raw) if facts_raw else None
    as_of = story.first_seen.date()
    channels = [c.channel for c in facts.channels] if facts else []
    index = index or BankIndex.load(conn)
    query = " ".join(filter(None, [story.title, facts.summary if facts else None]))

    seen, views = set(), []
    for ch in [*channels, None]:  # сначала по каждому фактору сюжета, затем общий поиск
        for h in index.search(query, k=k, channel=ch, as_of=as_of):
            if h.position.id in seen:
                continue
            seen.add(h.position.id)
            p = h.position
            views.append(
                BankView(
                    position_id=p.id,
                    thesis=p.thesis,
                    quote=p.quote,
                    doc_title=p.doc_title,
                    doc_date=p.doc_date,
                    page=p.page,
                    channel=p.channel,
                    direction=p.direction,
                    forecast=p.forecast.model_dump() if p.forecast else None,
                    before_event=p.doc_date < as_of,  # в день события банк уже реагирует на решение
                    stale=h.stale,
                    score=round(h.score, 4),
                )
            )
    views.sort(key=lambda v: (v.stale, -v.score))
    views = views[:k]

    market, surprise = None, None
    etype = facts.event_type if facts else story.event_type
    if etype in RATE_TYPES or "key_rate" in channels:
        mk = _consensus(conn, as_of, before=story.first_seen)
        if mk:
            market = MarketView(
                meeting=mk.target_event_date,
                statement=mk.statement,
                expected_value=mk.expected_value,
                channels=len(mk.evidence),
                evidence=[e.model_dump(mode="json") for e in mk.evidence[:5]],
            )
        actual = _actual_rate(facts) if facts else None
        if actual is not None:
            bank = next(
                (
                    v
                    for v in views
                    if v.forecast
                    and "decision" in str(v.forecast.get("metric", ""))
                    and v.before_event
                    and not v.stale
                ),
                None,
            )
            exp = market.expected_value if market else None
            bank_value = bank.forecast["value"] if bank and bank.forecast else None
            ref = exp if exp is not None else bank_value
            surprise = Surprise(
                metric="key_rate",
                actual=actual,
                market_expected=exp,
                bank_expected=bank_value,
                bank_ref=bank.position_id if bank else None,
                market_ref=f"mk_consensus_{market.meeting.isoformat()}" if market else None,
                direction=None
                if ref is None
                else ("above" if actual > ref else "below" if actual < ref else "in_line"),
            )
    return BankMarketReport(
        story_id=story_id, as_of=as_of, channels=channels, bank=views, market=market, surprise=surprise
    )
