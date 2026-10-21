"""Общие типы для всех схем.

Правило команды: любая правка схемы идёт через владельца (Разработчик 1)
и сразу вместе с фикстурами в fixtures/.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "0.1.0"

# Метка утверждения в карточке: факт, позиция банка (только ЦМИ), мнение рынка (аналитики, вкл.
# Альфа-Инвестиции), вывод AI, предложение банка (реклама)
ClaimType = Literal["fact", "bank_view", "market_view", "model_inference", "bank_offer"]

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


class NumberFact(Schema):
    """Число из текста. Проверяется кодом: значение должно быть в цитате."""

    value: float
    unit: str | None = Field(default=None, description="'%', 'руб', 'млрд руб', 'USD/т', 'б.п.'")
    direction: Literal["up", "down", "flat", "level"] | None = None
    period: str | None = None
    quote: str
    verified: bool | None = None


class ChannelImpact(Schema):
    """Воздействие на канал влияния (ставка, курс, нефть…). Знак — для отрасли, не для клиента."""

    channel: str = Field(description="id канала из config/taxonomy.yaml")
    impact: Signed = Field(description="−1…1: направление и сила воздействия")
    horizon: Literal["days", "weeks", "months", "quarters", "years"] | None = None
    rationale: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Timestamped(Schema):
    created_at: datetime | None = None
    updated_at: datetime | None = None
