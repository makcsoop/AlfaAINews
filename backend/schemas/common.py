"""Общие типы для всех схем.

Правило команды: любая правка схемы идёт через владельца (Разработчик 1)
и сразу вместе с фикстурами в fixtures/.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# 0.2.3: промежуточные результаты в RunTrace (план, инструменты, аналитики, арбитр), LlmCall.original_cost_rub,
#        Source.publisher (независимость источников — по издателю)
SCHEMA_VERSION = "0.2.3"  # 0.2.2: Evidence.feed_id; 0.2.1: headline, ссылки у действий и условий, id триггера
# 0.2.0: контракты финальной архитектуры, факторы с направлением (У17), analysis

# Метка утверждения в карточке: факт, позиция банка (только ЦМИ), мнение рынка (аналитики, вкл.
# Альфа-Инвестиции), вывод AI, предложение банка (реклама)
ClaimType = Literal["fact", "bank_view", "market_view", "model_inference", "bank_offer"]

# Позиция банка — только материалы ЦМИ: id позиции начинается с id документа из config/bank_docs.yaml
BANK_SOURCE_PREFIX = "cmi_"

# Статус утверждения внутри события: намерение и слух помечаются явно
EventStatus = Literal["fact", "announced", "planned", "rumor", "forecast"]

# Статус сюжета по подтверждениям
StoryStatus = Literal["single", "rumor", "unconfirmed", "confirmed", "contradictory"]

# Уровень источника: 0 — первоисточник/регулятор, 1 — федеральные ленты,
# 2 — деловые СМИ и аналитика, 3 — агрегаторы
SourceTier = Literal[0, 1, 2, 3]

Polarity = Literal["risk", "opportunity", "neutral", "mixed"]
Confidence = Literal["high", "medium", "low"]
UserRole = Literal["owner", "ceo", "cfo", "chief_accountant", "treasurer", "other"]

Score01 = Annotated[float, Field(ge=0.0, le=1.0)]
Signed = Annotated[float, Field(ge=-1.0, le=1.0)]


class Schema(BaseModel):
    """База: лишние поля запрещены, чтобы ошибки LLM-JSON ловились на валидации."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Evidence(Schema):
    """Ссылка на фрагмент первоисточника. quote — дословная цитата, span — позиции в тексте."""

    source_id: str = Field(description="raw_item id, bank chunk id 'doc#p3#c2' или 'csv:FIELD'")
    quote: str | None = None
    span: tuple[int, int] | None = None
    url: str | None = None
    doc_date: datetime | None = None
    verified: bool | None = Field(default=None, description="цитата найдена в тексте (rapidfuzz ≥ 90)")
    feed_id: str | None = Field(
        default=None, description="лента материала из config/sources.yaml ('cbr_press', 'prime'); у ЦМИ — нет"
    )


class NumberFact(Schema):
    """Число из текста. Проверяется кодом: значение должно быть в цитате."""

    value: float
    unit: str | None = Field(default=None, description="'%', 'руб', 'млрд руб', 'USD/т', 'б.п.'")
    direction: Literal["up", "down", "flat", "level"] | None = None
    period: str | None = None
    quote: str
    verified: bool | None = None


Horizon = Literal["days", "weeks", "months", "quarters", "years"]


class ChannelImpact(Schema):
    """Как событие двигает фактор: «ставка выше», «рубль крепче». Это направление фактора,
    а не «хорошо / плохо» (У17): знак для клиента получается только умножением на его
    чувствительность к фактору (ChannelExposure.sensitivity)."""

    channel: str = Field(description="id фактора из config/channels_matrix.yaml")
    direction: Literal["up", "down", "flat"]
    strength: Score01
    horizon: Horizon | None = None
    rationale: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Timestamped(Schema):
    created_at: datetime | None = None
    updated_at: datetime | None = None
