"""Настоящие обработчики шагов автомата (добавляются по одному; пока — планировщик)."""

from __future__ import annotations

from newsai.agents.dossier import Dossier
from newsai.agents.pipeline import PipelineContext
from newsai.agents.planner import build_dossier, replan
from newsai.agents.tools import FixtureDataSource, ToolContext, ToolDataSource
from newsai.llm import get_client

# Разбор считается на отрасль, а не на компанию: инструменты планировщика данных компании не читают.
SECTOR_RUN_COMPANY = "_sector_analysis_"

_source: ToolDataSource | None = None


def set_tool_source(source: ToolDataSource | None) -> None:
    """Подставляет источник данных инструментов (БД Р1/Р2); None — вернуться к фикстуре."""
    global _source
    _source = source


def _get_source() -> ToolDataSource:
    global _source
    if _source is None:
        _source = FixtureDataSource("rate_14")
    return _source


def planner_step(ctx: PipelineContext) -> PipelineContext:
    """PLANNED: строит досье (или достраивает по задаче критика) и кладёт его в ctx.dossier."""
    if ctx.story is None or ctx.facts is None:
        raise ValueError("планировщику нужны сюжет и факты (шаг FACTS_READY не выполнен)")
    tctx = ToolContext(
        company_id=ctx.company_id or SECTOR_RUN_COMPANY,
        role=ctx.role or "analysis",
        story_id=ctx.story.id,
        story_published_at=ctx.story.last_seen,
        trace_id=ctx.trace_id,
        source=_get_source(),
    )
    client = get_client()
    llm_before = len(client.journal.to_llm_calls(ctx.trace_id))

    if ctx.dossier.get("story_id") == ctx.story.id and ctx.planner_task:
        dossier = Dossier.model_validate(ctx.dossier)
        calls = replan(dossier, ctx.planner_task, tctx, client, priority=ctx.priority)
        ctx.planner_task = None
    else:
        dossier, calls = build_dossier(
            ctx.story, ctx.facts, ctx.sector, tctx,
            client=client, trace_id=ctx.trace_id, priority=ctx.priority,
        )
    ctx.dossier = dossier.model_dump(mode="json")

    if ctx.trace is not None:
        ctx.trace.tool_calls.extend(calls)
        ctx.trace.llm_calls.extend(client.journal.to_llm_calls(ctx.trace_id)[llm_before:])
    return ctx
