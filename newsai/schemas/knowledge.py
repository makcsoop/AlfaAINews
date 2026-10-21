"""Знания: позиции банка (ЦМИ) и знания аналитиков рынка из Telegram."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from newsai.schemas.common import Evidence, Horizon, Schema, Score01, Signed


class Forecast(Schema):
    metric: str = Field(description="'key_rate_eoy_2026', 'cpi_yoy_2026', 'usdrub_eoy_2026'")
    value: float
    unit: str | None = None
    horizon: str | None = Field(default=None, description="'конец 2026', 'заседание 23.10.2026'")


class BankPosition(Schema):
    """7. Атомарная позиция ЦМИ Альфа-Банка. Цитата сверяется с PDF кодом."""

    id: str = Field(description="'{doc_id}#p{page}#c{n}'")
    doc_id: str
    doc_title: str
    doc_date: date
    page: int
    thesis: str
    channel: str | None = None
    direction: Signed | None = Field(
        default=None, description="куда банк ждёт движения фактора: + вверх, − вниз (не «хорошо/плохо»)"
    )
    forecast: Forecast | None = None
    quote: str
    quote_verified: bool | None = None
    valid_until: date | None = Field(default=None, description="срок актуальности позиции")
    superseded_by: str | None = Field(
        default=None, description="id сюжета, который сделал позицию устаревшей"
    )


class MarketKnowledge(Schema):
    """Знание аналитиков рынка: механизм «событие → фактор → отрасль», ожидание перед плановым
    событием, прогноз с горизонтом или разбор случая после события."""

    id: str
    kind: Literal["mechanism", "expectation", "forecast", "case"]
    source_id: str = Field(description="канал или сайт аналитика (channel_id в контракте)")
    item_id: str = Field(description="raw_item, из которого извлечено")
    published_at: datetime
    event_type: str | None = None
    channel: str | None = None
    sectors_okved: list[str] = Field(default_factory=list)
    statement: str
    direction: Signed | None = Field(default=None, description="направление фактора: + вверх, − вниз")
    horizon: Horizon | None = None
    expected_value: float | None = None
    expected_unit: str | None = None
    target_event_date: date | None = Field(default=None, description="для ожиданий: дата планового события")
    origin: Literal["telegram", "site", "manual"] = Field(
        default="telegram",
        description="откуда знание: из Telegram отключается одной настройкой (п. 1.5 условий API)",
    )
    timing: Literal["before_event", "after_event"] = Field(
        default="before_event", description="в разбор берём только опубликованное до сюжета"
    )
    evidence: list[Evidence] = Field(default_factory=list)
    channel_weight: Score01 | None = None
    one_sided: bool = Field(default=False, description="85%+ «покупать» — вес ×0,5")
    outcome_hit: bool | None = Field(default=None, description="сбылся ли прогноз")
