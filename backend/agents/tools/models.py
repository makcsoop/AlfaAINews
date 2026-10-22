"""Схемы входа и выхода инструментов. В схемах входа поля company_id нет — компания только из сессии."""

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from backend.schemas.common import Schema, Score01, Signed

# --- get_profile ---------------------------------------------------------------------------------


class GetProfileIn(Schema):
    pass


class ExposureOut(Schema):
    channel: str
    exposure: Signed
    drivers: list[str]
    is_assumption: bool


class ProfileOut(Schema):
    okved_code: str | None
    okved_name: str | None
    sector: str | None
    region: str | None
    client_type: str | None
    products: dict[str, bool]
    has_group: bool
    foreign_trade: bool | None = Field(description="ВЭД-флаг (признак внешней торговли)")
    foreign_trade_country: str | None
    exposures: list[ExposureOut]
    revenue_rank_in_okved: int | None
    revenue_peers_in_okved: int | None
    revenue_top_share: Score01 | None = Field(
        description="ранг/число компаний в ОКВЭД: 0.05 — верхние 5% по выручке; абсолютной выручки нет"
    )
    completeness: Score01 | None


# --- get_counterparties --------------------------------------------------------------------------


class GetCounterpartiesIn(Schema):
    direction: Literal["supplier", "buyer"] | None = None
    top: int = Field(default=5, ge=1, le=20)


class CounterpartyOut(Schema):
    rank: int
    direction: Literal["supplier", "buyer"]
    counterparty_id: str | None = Field(description="None для контрагентов через физлиц")
    share: Score01
    okved_code: str | None
    sector: str | None
    same_group: bool
    via_person: bool


class CounterpartiesOut(Schema):
    counterparties: list[CounterpartyOut]
    hhi_suppliers: float | None
    hhi_buyers: float | None
    top_supplier_share: Score01 | None
    top_buyer_share: Score01 | None
    sector_shares_suppliers: dict[str, float]
    sector_shares_buyers: dict[str, float]
    has_hidden_group_hypothesis: bool
    high_dependency: bool


# --- search_news ---------------------------------------------------------------------------------


class SearchNewsIn(Schema):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=20)


class NewsHit(Schema):
    item_id: str
    source_id: str
    tier: Literal[0, 1, 2, 3] | None
    title: str
    published_at: datetime
    snippet: str = Field(description="материал, не инструкция: передавать модели только в блоке <data>")
    in_story: bool


class SearchNewsOut(Schema):
    hits: list[NewsHit]


# --- get_bank_positions --------------------------------------------------------------------------


class GetBankPositionsIn(Schema):
    channel: str | None = Field(default=None, max_length=64)
    query: str | None = Field(default=None, max_length=300)


class BankPositionOut(Schema):
    id: str
    thesis: str
    channel: str | None
    stance: Signed | None
    forecast_value: float | None
    forecast_unit: str | None
    forecast_horizon: str | None
    doc_id: str
    doc_title: str
    doc_date: date
    page: int
    quote: str
    valid_until: date | None
    expired: bool = Field(description="valid_until раньше даты сюжета")


class BankPositionsOut(Schema):
    positions: list[BankPositionOut]


# --- get_market_knowledge ------------------------------------------------------------------------


class GetMarketKnowledgeIn(Schema):
    channel: str | None = Field(default=None, max_length=64)
    event_type: str | None = Field(default=None, max_length=64)
    kind: Literal["mechanism", "expectation", "post_event_review"] | None = None


class MarketKnowledgeOut(Schema):
    id: str
    kind: Literal["mechanism", "expectation", "post_event_review"]
    published_at: datetime
    event_type: str | None
    channel: str | None
    sectors_okved: list[str]
    statement: str
    direction: Signed | None
    expected_value: float | None
    expected_unit: str | None
    origin: Literal["telegram", "site", "manual"]
    timing: Literal["before_event"]
    channel_weight: Score01 | None
    one_sided: bool


class MarketKnowledgeListOut(Schema):
    knowledge: list[MarketKnowledgeOut]


# --- find_similar_cases --------------------------------------------------------------------------


class FindSimilarCasesIn(Schema):
    event_type: str = Field(min_length=1, max_length=64)
    sector: str | None = Field(default=None, max_length=8)
    limit: int = Field(default=3, ge=1, le=10)


class SimilarCase(Schema):
    story_id: str
    title: str
    event_type: str | None
    first_seen: datetime
    summary: str | None
    sectors_okved: list[str]


class SimilarCasesOut(Schema):
    cases: list[SimilarCase]


# --- calculator ----------------------------------------------------------------------------------


class CalculatorIn(Schema):
    expression: str = Field(min_length=1, max_length=200)


class CalculatorOut(Schema):
    expression: str
    value: float
