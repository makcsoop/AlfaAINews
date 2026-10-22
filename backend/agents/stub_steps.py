"""Заглушечные обработчики шагов на фикстуре: пока нет настоящих планировщика, аналитиков и критика.

Берут готовые объекты из `load_bundle(bundle)`; подменяются настоящими обработчиками по одному.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.agents.pipeline import Handlers, PipelineContext, State
from backend.fixtures import load_bundle


@lru_cache(maxsize=4)
def _bundle(name: str) -> dict[str, list[Any]]:
    return load_bundle(name)


def _pick(items: list[Any], ctx: PipelineContext, *, role_attr: str) -> Any:
    """Объект для клиента контекста; без клиента — первый."""
    if ctx.company_id is None:
        return items[0]
    for item in items:
        if item.company_id == ctx.company_id and getattr(item, role_attr, None) in (ctx.role, None):
            return item
    for item in items:
        if item.company_id == ctx.company_id:
            return item
    raise LookupError(f"в фикстуре нет данных для клиента {ctx.company_id}")


def make_stub_handlers(bundle: str = "rate_14") -> Handlers:
    def data() -> dict[str, list[Any]]:
        return _bundle(bundle)

    def facts(ctx: PipelineContext) -> PipelineContext:
        story = data()["story"][0]
        if story.id != ctx.story_id:
            raise LookupError(f"фикстура {bundle} содержит сюжет {story.id}, а не {ctx.story_id}")
        ctx.story = story
        ctx.facts = data()["event_facts"][0]
        return ctx

    def scored(ctx: PipelineContext) -> PipelineContext:
        ctx.relevance = _pick(data()["relevance_score"], ctx, role_attr="role")
        return ctx

    def quick(ctx: PipelineContext) -> PipelineContext:
        ctx.quick_card = _pick(data()["insight_card"], ctx, role_attr="user_role")
        return ctx

    def planned(ctx: PipelineContext) -> PipelineContext:
        ctx.dossier.setdefault("plan", ["profile", "bank_positions", "analyst_knowledge"])
        ctx.dossier["plan_rounds"] = ctx.dossier.get("plan_rounds", 0) + 1
        return ctx

    def analyzed(ctx: PipelineContext) -> PipelineContext:
        ctx.reports = [r for r in data()["analyst_report"] if r.sector == ctx.sector] or list(
            data()["analyst_report"]
        )
        return ctx

    def critiqued(ctx: PipelineContext) -> PipelineContext:
        ctx.critic_verdict = "ok"
        return ctx

    def arbitrated(ctx: PipelineContext) -> PipelineContext:
        ctx.on_review = False
        return ctx

    def drafted(ctx: PipelineContext) -> PipelineContext:
        ctx.card = _pick(data()["insight_card"], ctx, role_attr="user_role")
        return ctx

    def verified(ctx: PipelineContext) -> PipelineContext:
        ctx.verify_ok = True
        ctx.verify_errors = []
        return ctx

    def published(ctx: PipelineContext) -> PipelineContext:
        return ctx

    return {
        State.FACTS_READY: facts,
        State.SCORED: scored,
        State.QUICK_CARD: quick,
        State.PLANNED: planned,
        State.ANALYZED: analyzed,
        State.CRITIQUED: critiqued,
        State.ARBITRATED: arbitrated,
        State.CARD_DRAFTED: drafted,
        State.CARD_VERIFIED: verified,
        State.PUBLISHED: published,
    }
