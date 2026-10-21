"""Схема ответа модели на шаге card. Метки, числа вкладов, скор и знак ставит код, не модель."""

from typing import Literal

from pydantic import Field

from newsai.schemas.common import Schema


class DraftItem(Schema):
    text: str = Field(min_length=3)
    refs: list[str] = Field(min_length=1, description="id из досье")
    client_features: list[str] = Field(
        default_factory=list, description="признаки компании из списка, дословно"
    )
    kind: Literal["bank_view", "market_view"] | None = Field(
        default=None, description="только в bank_and_market: позиция банка или мнение рынка"
    )


class DraftAction(Schema):
    type: Literal["analysis_question", "scenario"]
    text: str = Field(min_length=3)
    refs: list[str] = Field(min_length=1)
    client_features: list[str] = Field(default_factory=list)


class DraftSurprise(Schema):
    """Только ссылки: числа код берёт из досье."""

    metric: str
    unit: str | None = None
    actual_ref: str = Field(description="id факта с фактическим значением")
    market_ref: str | None = Field(default=None, description="id mk_… с ожиданием рынка")
    bank_ref: str | None = Field(default=None, description="id cmi_… с ожиданием банка до события")


class CardDraft(Schema):
    headline: str = Field(min_length=3, max_length=200)
    what_happened: list[DraftItem] = Field(min_length=1, max_length=3)
    why_it_matters: list[DraftItem] = Field(min_length=1, max_length=4)
    consequences: list[DraftItem] = Field(min_length=1, max_length=4)
    actions: list[DraftAction] = Field(default_factory=list, max_length=4)
    bank_and_market: list[DraftItem] = Field(default_factory=list, max_length=4)
    what_would_change: list[DraftItem] = Field(default_factory=list, max_length=3)
    surprise: DraftSurprise | None = None
