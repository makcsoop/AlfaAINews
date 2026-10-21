"""Шаг card: вызов модели через единый LLM-клиент, сборка карточки, шаблон при любом сбое."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from newsai.agents.dossier import Dossier
from newsai.cards.build import CardAssemblyError, assemble_card
from newsai.cards.llm_schema import CardDraft
from newsai.cards.prompt import build_messages, prompt_version
from newsai.cards.template import template_draft
from newsai.llm import BudgetExceeded, LLMClient, LLMUnavailable, get_client
from newsai.schemas import (
    AnalystReport,
    CompanyProfile,
    CounterpartyGraph,
    InsightCard,
    RelevanceScore,
    Source,
    Story,
)
from newsai.schemas.common import UserRole


class CardUnavailable(Exception):
    """Не вышло ни через модель, ни по шаблону (например, в досье нет фактов)."""


@dataclass
class CardResult:
    card: InsightCard
    headline: str  # в InsightCard поля заголовка нет: отдаём рядом, пока схему не расширил Р1
    source: Literal["llm", "template"]
    fallback_reason: str | None = None
    model: str | None = None
    fallback_model_used: bool = False
    cost_rub: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    prompt_version: str = ""


def write_card(
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
    client: LLMClient | None = None,
    fixture_key: str | None = None,
) -> CardResult:
    common = {
        "story": story,
        "dossier": dossier,
        "relevance": relevance,
        "profile": profile,
        "analyses": analyses,
        "graph": graph,
    }
    version = prompt_version()
    reason: str
    try:
        res = (client or get_client()).call(
            step="card",
            messages=build_messages(role=role, **common),  # type: ignore[arg-type]
            schema=CardDraft,
            trace_id=trace_id,
            prompt_version=version,
            fixture_key=fixture_key or f"{profile.company_id}_{role}",
        )
        draft = res.value
        card = assemble_card(draft, role=role, trace_id=trace_id, sources=sources, **common)  # type: ignore[arg-type]
        return CardResult(
            card=card,
            headline=draft.headline,
            source="llm",
            model=res.model,
            fallback_model_used=res.fallback_used,
            cost_rub=res.cost_rub,
            tokens_in=res.tokens_in,
            tokens_out=res.tokens_out,
            prompt_version=version,
        )
    except (LLMUnavailable, BudgetExceeded, CardAssemblyError, ValidationError) as e:
        reason = f"{type(e).__name__}: {str(e)[:200]}"

    try:
        draft = template_draft(role=role, **{k: v for k, v in common.items()})  # type: ignore[arg-type]
        card = assemble_card(draft, role=role, trace_id=trace_id, sources=sources, **common)  # type: ignore[arg-type]
    except (CardAssemblyError, ValidationError) as e:
        raise CardUnavailable(f"шаблон не собрался ({e}); до него: {reason}") from e
    return CardResult(
        card=card, headline=draft.headline, source="template", fallback_reason=reason, prompt_version=version
    )
