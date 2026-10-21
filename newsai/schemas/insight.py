"""Сопоставление, разбор, карточка и трассировка."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from newsai.schemas.common import (
    BANK_SOURCE_PREFIX,
    ChannelImpact,
    ClaimType,
    Confidence,
    Evidence,
    Polarity,
    Schema,
    Score01,
    UserRole,
)


class Contribution(Schema):
    factor: str
    value: float
    client_features: list[str] = Field(default_factory=list)
    sign: Literal[-1, 0, 1] = 0


class RelevanceScore(Schema):
    """8. Значимость пары «сюжет × клиент (× роль)» с вкладами — основа «почему вы это видите»."""

    story_id: str
    company_id: str
    role: UserRole | None = None
    score: Score01
    quality: Score01
    freshness: Score01 | None = None
    contributions: list[Contribution] = Field(default_factory=list)
    polarity: Polarity = "neutral"
    deep_analysis: bool = False


class AnalystReport(Schema):
    """9. Отчёт независимого аналитика (бизнес / финансы) по паре «сюжет × отрасль»."""

    story_id: str
    sector: str
    analyst: Literal["business", "finance"]
    model: str
    channels: list[ChannelImpact]
    summary: str
    open_questions: list[str] = Field(default_factory=list)


class Claim(Schema):
    """10. Утверждение карточки с меткой и обоснованием."""

    claim_id: str
    type: ClaimType
    text: str
    evidence: list[Evidence] = Field(default_factory=list)
    based_on: dict[str, list[str]] = Field(
        default_factory=dict, description="facts / bank_views / client_features, на которые опирается вывод"
    )
    polarity: Polarity | None = None
    horizon: str | None = None
    verified: dict[str, bool | str] = Field(default_factory=dict)
    is_advertising: bool = False

    @model_validator(mode="after")
    def _label_rules(self) -> Self:
        bank_refs = [e.source_id.startswith(BANK_SOURCE_PREFIX) for e in self.evidence]
        if self.type == "fact" and not self.evidence:
            raise ValueError(f"{self.claim_id}: факт без источника")
        if self.type == "bank_view" and not (bank_refs and all(bank_refs)):
            raise ValueError(f"{self.claim_id}: позиция банка должна ссылаться только на материалы ЦМИ")
        if self.type == "market_view" and any(bank_refs):
            raise ValueError(f"{self.claim_id}: мнение рынка ссылается на ЦМИ — это позиция банка")
        if self.type == "model_inference" and not any(self.based_on.values()):
            raise ValueError(f"{self.claim_id}: вывод AI без based_on")
        if self.type == "bank_offer" and not self.is_advertising:
            raise ValueError(f"{self.claim_id}: предложение банка должно быть помечено как реклама")
        return self


class Action(Schema):
    type: Literal["analysis_question", "scenario", "bank_product"]
    text: str
    is_advertising: bool = False

    @model_validator(mode="after")
    def _ads_marked(self) -> Self:
        if self.type == "bank_product" and not self.is_advertising:
            raise ValueError("продукт банка в действиях должен быть помечен как реклама")
        return self


class StorySource(Schema):
    source_id: str
    tier: int
    url: str | None = None
    title: str | None = None


class CardEvent(Schema):
    story_id: str
    event_type: str | None
    status: str
    title: str
    first_seen: datetime
    sources: list[StorySource]
    independent_sources: int


class Surprise(Schema):
    """«Рынок ждал X, банк ждал Y, вышло Z». Ожидания — только опубликованные до события."""

    metric: str
    unit: str | None = None
    actual: float
    market_expected: float | None = None
    market_ref: str | None = Field(default=None, description="id market_knowledge с консенсусом")
    bank_expected: float | None = None
    bank_ref: str | None = Field(default=None, description="id bank_position")
    direction: Literal["above", "below", "in_line"] | None = None


class InsightCard(Schema):
    """11. Персональная карточка: 6 вопросов ТЗ + что изменит вывод."""

    card_id: str
    company_id: str
    user_role: UserRole
    event: CardEvent
    relevance: RelevanceScore
    what_happened: list[str] = Field(description="claim_id типа fact")
    why_it_matters: list[str]
    consequences: list[str]
    actions: list[Action] = Field(default_factory=list)
    bank_and_market: list[str] = Field(
        default_factory=list, description="claim_id bank_view и консенсус рынка"
    )
    what_would_change: list[str] = Field(default_factory=list)
    surprise: Surprise | None = None
    claims: list[Claim]
    confidence: Confidence
    confidence_reasons: list[str] = Field(default_factory=list)
    review_status: Literal["published", "on_review"] = "published"
    trace_id: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        by_id = {c.claim_id: c for c in self.claims}
        if len(by_id) != len(self.claims):
            raise ValueError("повторяющиеся claim_id")
        sections = {
            "what_happened": ({"fact"}, self.what_happened),
            "why_it_matters": (None, self.why_it_matters),
            "consequences": (None, self.consequences),
            "bank_and_market": ({"bank_view", "market_view"}, self.bank_and_market),
        }
        for name, (allowed, refs) in sections.items():
            for ref in refs:
                if ref not in by_id:
                    raise ValueError(f"{name}: нет утверждения {ref}")
                if allowed and by_id[ref].type not in allowed:
                    raise ValueError(f"{name}: {ref} имеет метку {by_id[ref].type}, ожидается {allowed}")
        if self.relevance.company_id != self.company_id or self.relevance.story_id != self.event.story_id:
            raise ValueError("relevance относится к другой паре «сюжет × клиент»")
        return self


class ToolCall(Schema):
    tool: str
    args: dict = Field(default_factory=dict)
    result_hash: str | None = None
    ok: bool = True
    at: datetime


class LlmCall(Schema):
    model: str
    purpose: str
    prompt_hash: str
    tokens_in: int
    tokens_out: int
    cost_rub: float
    at: datetime
    cached: bool = False


class RunTrace(Schema):
    """12. Журнал разбора: шаги, инструменты, вызовы моделей, итог проверки и стоимость."""

    run_id: str
    story_id: str
    company_id: str | None = None
    steps: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    llm_calls: list[LlmCall] = Field(default_factory=list)
    verify_rounds: int = 0
    verify_failures: list[str] = Field(default_factory=list)
    cost_rub: float = 0.0
    started_at: datetime
    finished_at: datetime | None = None


class Feedback(Schema):
    """13. Обратная связь пользователя или эксперта."""

    card_id: str
    user_role: UserRole | None = None
    kind: Literal["useful", "not_relevant", "wrong", "expert_edit", "click_source"]
    comment: str | None = None
    claim_id: str | None = None
    at: datetime
