"""Сюрприз кодом: «рынок ждал X, банк ждал Y, вышло Z» без LLM.

Вышло — число из фактов экстрактора (проверено по цитате). Банк ждал — прогноз позиции ЦМИ к этому событию.
Рынок ждал — консенсус аналитиков: агрегат Р1 (source_id «consensus»), иначе медиана ожиданий постов.
Все ожидания — только опубликованные до события (инвариант 10).
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from statistics import median

from backend.ranking.config import ScoringConfig, SurpriseMetric, load_scoring
from backend.schemas import BankPosition, EventFacts, MarketKnowledge, Story
from backend.schemas.insight import Surprise

CONSENSUS_SOURCE = "consensus"


def _event_day(story: Story, facts: EventFacts | None) -> date:
    at = (facts.event_date if facts and facts.event_date else None) or story.first_seen
    return at.date()


def _before(published: datetime, story: Story) -> bool:
    return published.astimezone(UTC) < story.first_seen.astimezone(UTC)


def actual_value(facts: EventFacts | None, m: SurpriseMetric) -> float | None:
    if facts is None:
        return None
    for n in facts.numbers:
        text = f"{n.period or ''} {n.quote}".casefold()
        if n.unit == m.unit and m.number_hint in text and n.verified is not False:
            return n.value
    return None


def bank_expectation(
    positions: Sequence[BankPosition], m: SurpriseMetric, story: Story, day: date
) -> BankPosition | None:
    """Свежайшая позиция ЦМИ с прогнозом этой метрики к дате события, опубликованная до него.

    Нет прогноза к самому событию — прогноз «без изменений до конца года» того же года (hold_prefix,
    направление 0): банк, ждущий 14% до конца года, ждёт 14% и на каждом заседании до тех пор.
    """
    stamp = day.strftime("%Y_%m_%d")
    published = [
        p
        for p in positions
        if p.forecast and _before(datetime.combine(p.doc_date, datetime.min.time(), tzinfo=UTC), story)
    ]
    found = [
        p
        for p in published
        if p.forecast.metric.startswith(m.forecast_prefix)  # type: ignore[union-attr]
        and (stamp in p.forecast.metric or (p.forecast.horizon or "").endswith(day.strftime("%d.%m.%Y")))  # type: ignore[union-attr]
    ]
    if not found and m.hold_prefix:
        found = [
            p
            for p in published
            if p.forecast.metric == f"{m.hold_prefix}_{day.year}"  # type: ignore[union-attr]
            and p.direction is not None
            and abs(p.direction) < 1e-9
            and p.superseded_by is None
        ]
    return max(found, key=lambda p: p.doc_date, default=None)


def market_expectation(
    knowledge: Sequence[MarketKnowledge], event_type: str, story: Story, day: date
) -> tuple[float, str] | None:
    pool = [
        k
        for k in knowledge
        if k.kind == "expectation"
        and k.event_type == event_type
        and k.expected_value is not None
        and (k.target_event_date is None or k.target_event_date == day)
        and _before(k.published_at, story)
    ]
    if not pool:
        return None
    aggregate = [k for k in pool if k.source_id == CONSENSUS_SOURCE]
    if aggregate:
        k = max(aggregate, key=lambda k: k.published_at)
        return k.expected_value, k.id  # type: ignore[return-value]
    values = [k.expected_value for k in pool if k.expected_value is not None]
    return float(median(values)), min(pool, key=lambda k: k.id).id


def find_surprise(
    story: Story,
    facts: EventFacts | None,
    *,
    bank_positions: Sequence[BankPosition] = (),
    market: Sequence[MarketKnowledge] = (),
    cfg: ScoringConfig | None = None,
) -> Surprise | None:
    cfg = cfg or load_scoring()
    event_type = (facts.event_type if facts else None) or story.event_type or ""
    m = cfg.surprise.metrics.get(event_type)
    if m is None:
        return None
    actual = actual_value(facts, m)
    if actual is None:
        return None
    day = _event_day(story, facts)
    bank = bank_expectation(bank_positions, m, story, day)
    mk = market_expectation(market, event_type, story, day)
    if bank is None and mk is None:
        return None
    reference = mk[0] if mk else bank.forecast.value  # type: ignore[union-attr]
    direction = "in_line" if abs(actual - reference) < 1e-9 else "above" if actual > reference else "below"
    return Surprise(
        metric=m.metric,
        unit=m.unit,
        actual=actual,
        market_expected=mk[0] if mk else None,
        market_ref=mk[1] if mk else None,
        bank_expected=bank.forecast.value if bank and bank.forecast else None,
        bank_ref=bank.id if bank else None,
        direction=direction,
    )
