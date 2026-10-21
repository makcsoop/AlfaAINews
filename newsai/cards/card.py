"""Шаг card: вызов модели через единый LLM-клиент, сборка карточки, шаблон при любом сбое."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from newsai.agents.dossier import Dossier
from newsai.cards.build import CardAssemblyError, assemble_card
from newsai.cards.features import allowed_features
from newsai.cards.llm_schema import CardDraft
from newsai.cards.prompt import build_messages, prompt_version
from newsai.cards.template import template_draft
from newsai.cards.verify import CardCheck, verify_card
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


class _ForceTemplate(Exception):
    pass


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
    verify_failures: list[str] = field(default_factory=list)  # провалы проверки черновика модели
    regenerations: int = 0
    checked: bool = False


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
    feedback: str | None = None,
    force_template: bool = False,
) -> CardResult:
    """Черновик модели -> сборка; любой сбой или `force_template` -> шаблон.

    `feedback` — перечень провалов предыдущей проверки: уходит модели отдельным сообщением.
    """
    common = {
        "story": story,
        "dossier": dossier,
        "relevance": relevance,
        "profile": profile,
        "analyses": analyses,
        "graph": graph,
    }
    version = prompt_version()
    reason: str = "принудительный шаблон"
    try:
        if force_template:
            raise _ForceTemplate
        messages = build_messages(role=role, **common)  # type: ignore[arg-type]
        if feedback:
            messages.append({"role": "user", "content": feedback})
        res = (client or get_client()).call(
            step="card",
            messages=messages,
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
    except _ForceTemplate:
        pass
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


def retry_feedback(failures: Sequence[str]) -> str:
    """Сообщение модели при перегенерации: что именно не прошло проверку кодом."""
    lines = "\n".join(f"- {f}" for f in failures[:12])
    return (
        "Предыдущий черновик не прошёл проверку кодом. Исправь только перечисленное, остальные правила "
        "прежние: числа и даты бери дословно из цитат досье, позицию банка пиши только в bank_and_market.\n"
        + lines
    )


def _fixture_exists(client: LLMClient, key: str) -> bool:
    root = getattr(getattr(client, "settings", None), "llm_fixtures_dir", None)
    return bool(root) and (Path(root) / "card" / f"{key}.json").exists()


def check_result(
    res: CardResult, *, dossier: Dossier, profile: CompanyProfile, graph: CounterpartyGraph | None,
    relevance: RelevanceScore, analyses: Sequence[AnalystReport],
) -> CardCheck:
    return verify_card(
        res.card, dossier, profile_features=allowed_features(profile, graph, relevance),
        profile=profile, analyses=analyses,
    )


def write_verified_card(
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
    """Черновик модели -> проверка кодом; провал -> одна перегенерация -> шаблон (он тоже проверяется)."""
    cl = client or get_client()
    key = fixture_key or f"{profile.company_id}_{role}"
    kw: dict[str, Any] = {
        "story": story, "dossier": dossier, "relevance": relevance, "profile": profile, "role": role,
        "trace_id": trace_id, "analyses": analyses, "graph": graph, "sources": sources, "client": cl,
    }
    chk = {
        "dossier": dossier, "profile": profile, "graph": graph, "relevance": relevance, "analyses": analyses,
    }

    first = write_card(fixture_key=key, **kw)
    cost, tin, tout = first.cost_rub, first.tokens_in, first.tokens_out
    failures: list[str] = []
    regens = 0
    if first.source == "llm":
        check = check_result(first, **chk)
        if check.ok:
            return _mark(first, [], 0)
        failures = check.messages()
        regens = 1
        retry_key = f"{key}_retry" if _fixture_exists(cl, f"{key}_retry") else key
        second = write_card(fixture_key=retry_key, feedback=retry_feedback(failures), **kw)
        cost, tin, tout = cost + second.cost_rub, tin + second.tokens_in, tout + second.tokens_out
        if second.source == "llm":
            check2 = check_result(second, **chk)
            if check2.ok:
                second.cost_rub, second.tokens_in, second.tokens_out = cost, tin, tout
                return _mark(second, failures, regens)
            failures = [*failures, *(f"retry: {m}" for m in check2.messages())]
        else:
            failures = [*failures, f"retry: {second.fallback_reason}"]
        tmpl = write_card(force_template=True, fixture_key=key, **kw)
        reason = "проверка карточки не пройдена: " + "; ".join(failures[:6])
    else:
        tmpl = first
        reason = first.fallback_reason or ""
        failures = [reason] if reason else []

    tmpl.fallback_reason = reason or tmpl.fallback_reason
    tmpl.cost_rub, tmpl.tokens_in, tmpl.tokens_out = cost, tin, tout
    check_t = check_result(tmpl, **chk)
    if not check_t.ok:
        raise CardUnavailable("шаблонная карточка не прошла проверку: " + "; ".join(check_t.messages()[:6]))
    return _mark(tmpl, failures, regens)


def _mark(res: CardResult, failures: list[str], regens: int) -> CardResult:
    res.verify_failures, res.regenerations, res.checked = failures, regens, True
    return res
