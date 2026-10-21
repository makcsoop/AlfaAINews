"""Знания: позиции банка (ЦМИ) и знания аналитиков рынка из Telegram."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from newsai.schemas.common import Evidence, Schema, Score01, Signed


class BankPosition(Schema):
    """7. Атомарная позиция ЦМИ Альфа-Банка. Цитата сверяется с PDF кодом."""

    id: str = Field(description="'{doc_id}#p{page}#c{n}'")
    doc_id: str
    doc_title: str
    doc_date: date
    page: int
    thesis: str
    channel: str | None = None
    stance: Signed | None = Field(default=None, description="знак ожидания по каналу")
    forecast_value: float | None = None
    forecast_unit: str | None = None
    forecast_horizon: str | None = None
    quote: str
    quote_verified: bool | None = None
    valid_until: date | None = Field(default=None, description="срок актуальности позиции")
    superseded_by: str | None = Field(
        default=None, description="id сюжета, который сделал позицию устаревшей"
    )


class MarketKnowledge(Schema):
    """Знание аналитиков рынка: механизм, ожидание (консенсус) или разбор после события."""

    id: str
    kind: Literal["mechanism", "expectation", "post_event_review"]
    source_id: str
    item_id: str = Field(description="raw_item, из которого извлечено")
    published_at: datetime
    event_type: str | None = None
    channel: str | None = None
    sectors_okved: list[str] = Field(default_factory=list)
    statement: str
    direction: Signed | None = None
    expected_value: float | None = None
    expected_unit: str | None = None
    target_event_date: date | None = Field(default=None, description="для ожиданий: дата планового события")
    origin: Literal["before_event", "after_event"] = "before_event"
    evidence: list[Evidence] = Field(default_factory=list)
    channel_weight: Score01 | None = None
    outcome_hit: bool | None = Field(default=None, description="сбылся ли прогноз")
