"""Настоящие обработчики шагов автомата: планировщик, аналитики, карточка.

Остальные шаги (факты, скоринг, быстрая карточка, проверка, публикация) пока
заглушки из `stub_steps`; `make_handlers` собирает реестр, `run_for_client` гонит один прогон.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from newsai.agents.analysts import AnalystResult, run_analysts_detailed
from newsai.agents.arbiter import arbitrate
from newsai.agents.critic import critique
from newsai.agents.dossier import Dossier
from newsai.agents.pipeline import Handlers, PipelineContext, State, run_pipeline
from newsai.agents.planner import build_dossier, replan
from newsai.agents.stub_steps import make_stub_handlers
from newsai.agents.tools import FixtureDataSource, ToolContext, ToolDataSource
from newsai.cards import CardResult, assemble_card, template_draft, verify_card, write_card
from newsai.cards.card import retry_feedback
from newsai.cards.features import allowed_features
from newsai.cards.prompt import prompt_version
from newsai.llm import LLMClient, get_client
from newsai.schemas import InsightCard, RunTrace, ToolCall
from newsai.schemas.common import UserRole

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


# --- журнал вызовов модели в трассе ---------------------------------------------------------------


def _llm_mark(ctx: PipelineContext, client: LLMClient) -> int:
    return len(client.journal.to_llm_calls(ctx.trace_id))


def _llm_sync(ctx: PipelineContext, client: LLMClient, before: int) -> None:
    """Дописывает в трассу вызовы модели, сделанные шагом, и пересчитывает ₽."""
    if ctx.trace is None:
        return
    ctx.trace.llm_calls.extend(client.journal.to_llm_calls(ctx.trace_id)[before:])
    ctx.trace.cost_rub = round(sum(c.cost_rub for c in ctx.trace.llm_calls), 6)


# --- планировщик ----------------------------------------------------------------------------------


def planner_step(ctx: PipelineContext, source: ToolDataSource | None = None) -> PipelineContext:
    """PLANNED: строит досье (или достраивает по задаче критика) и кладёт его в ctx.dossier."""
    if ctx.story is None or ctx.facts is None:
        raise ValueError("планировщику нужны сюжет и факты (шаг FACTS_READY не выполнен)")
    tctx = ToolContext(
        company_id=ctx.company_id or SECTOR_RUN_COMPANY,
        role=ctx.role or "analysis",
        story_id=ctx.story.id,
        story_published_at=ctx.story.last_seen,
        trace_id=ctx.trace_id,
        source=source or _get_source(),
    )
    client = get_client()
    before = _llm_mark(ctx, client)
    calls: list[ToolCall] = []
    try:
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
    finally:
        if ctx.trace is not None:
            ctx.trace.tool_calls.extend(calls)
        _llm_sync(ctx, client, before)
    return ctx


# --- аналитики ------------------------------------------------------------------------------------


def analysts_step(ctx: PipelineContext) -> PipelineContext:
    """ANALYZED: два аналитика на досье; один упал — идём дальше, оба упали — исключение (DEGRADED)."""
    if not ctx.dossier:
        raise ValueError("аналитикам нужно досье (шаг PLANNED не выполнен)")
    dossier = Dossier.model_validate(ctx.dossier)
    client = get_client()
    before = _llm_mark(ctx, client)
    try:
        results = run_analysts_detailed(
            dossier, client=client, trace_id=ctx.trace_id, priority=ctx.priority
        )
    finally:
        _llm_sync(ctx, client, before)

    ctx.artifacts["analysts"] = {a: r.model_dump(mode="json") for a, r in results.items()}
    ctx.artifacts["analyst_check_failures"] = sum(r.check_failures for r in results.values())
    ctx.artifacts["analyst_notes"] = [f"{a}: {n}" for a, r in results.items() for n in r.notes]
    ctx.reports = [r.report for r in results.values() if r.report is not None]
    failed = {a: r.failure for a, r in results.items() if r.report is None}
    if failed:
        ctx.flags.append("analyst_unavailable:" + ",".join(sorted(failed)))
    if not ctx.reports:
        raise RuntimeError("оба аналитика недоступны: " + "; ".join(f"{a}: {f}" for a, f in failed.items()))
    return ctx


# --- критик и арбитр ------------------------------------------------------------------------------


def _analyst_results(ctx: PipelineContext) -> dict[str, AnalystResult]:
    raw = ctx.artifacts.get("analysts") or {}
    results = {a: AnalystResult.model_validate(r) for a, r in raw.items()}
    if not results:  # отчёты пришли не из analysts_step (кеш, заглушка): оборачиваем как есть
        results = {r.analyst: AnalystResult(analyst=r.analyst, report=r) for r in ctx.reports}
    return results


def critic_step(ctx: PipelineContext) -> PipelineContext:
    """CRITIQUED: проверка кодом; задача планировщику -> verdict "retry" (автомат вернётся в PLANNED)."""
    if not ctx.dossier:
        raise ValueError("критику нужно досье (шаг PLANNED не выполнен)")
    verdict = critique(Dossier.model_validate(ctx.dossier), _analyst_results(ctx))
    rounds: list[dict[str, Any]] = ctx.artifacts.setdefault("critic_rounds", [])
    rounds.append(
        {
            "round": ctx.critic_rounds + 1,
            "ok": verdict.ok,
            "failures": [f.model_dump() for f in verdict.failures],
            "planner_task": verdict.planner_task,
            "notes": verdict.notes,
        }
    )
    ctx.artifacts["critic"] = verdict.model_dump(mode="json")
    if ctx.trace is not None:
        ctx.trace.verify_failures.extend(
            f"critic[{ctx.critic_rounds + 1}] {f.code}: {f.analyst}"
            f"{'/' + f.channel if f.channel else ''}: {f.detail}"
            for f in verdict.failures
        )
    if verdict.planner_task:
        ctx.critic_verdict = "retry"
        ctx.critic_notes = list(verdict.planner_tasks)  # автомат склеит их в planner_task
    else:
        ctx.critic_verdict = "ok"
        ctx.critic_notes = list(verdict.notes)
        if not verdict.ok:
            ctx.flags.append("critic_notes")
    return ctx


def arbiter_step(ctx: PipelineContext) -> PipelineContext:
    """ARBITRATED: споры аналитиков -> on_review, сверка с позициями ЦМИ из досье (не блокирует)."""
    dossier = Dossier.model_validate(ctx.dossier) if ctx.dossier else None
    result = arbitrate(ctx.reports, dossier.by_kind("bank_position") if dossier else [])
    ctx.on_review = result.on_review
    ctx.artifacts["arbiter"] = result.model_dump(mode="json")
    if result.on_review:
        ctx.flags.append("analyst_dispute:" + ",".join(d.channel for d in result.disputes))
    if result.confidence_reasons:
        ctx.flags.append("bank_disagrees:" + ",".join(c.channel for c in result.bank_check if not c.agree))
    return ctx


# --- карточка -------------------------------------------------------------------------------------


def _artifact(res: CardResult) -> dict[str, Any]:
    return {
        "source": res.source,
        "headline": res.headline,
        "fallback_reason": res.fallback_reason,
        "model": res.model,
        "fallback_model_used": res.fallback_model_used,
        "cost_rub": res.cost_rub,
        "prompt_version": res.prompt_version,
    }


def card_step(ctx: PipelineContext, source: ToolDataSource | None = None) -> PipelineContext:
    """CARD_DRAFTED: персональная карточка для ctx.company_id и ctx.role по досье и отчётам."""
    if ctx.company_id is None or ctx.role is None:
        raise ValueError("карточке нужны company_id и роль из сессии")
    if ctx.story is None or ctx.relevance is None or not ctx.dossier:
        raise ValueError("карточке нужны сюжет, скоринг и досье")
    src = source or _get_source()
    profile = src.profile(ctx.company_id)
    if profile is None:
        raise LookupError(f"нет профиля компании {ctx.company_id}")
    role: UserRole = ctx.role  # type: ignore[assignment]
    dossier = Dossier.model_validate(ctx.dossier)
    common: dict[str, Any] = {
        "story": ctx.story,
        "dossier": dossier,
        "relevance": ctx.relevance,
        "profile": profile,
        "role": role,
        "trace_id": ctx.trace_id,
        "analyses": list(ctx.reports),
        "graph": src.counterparties(ctx.company_id),
        "sources": src.sources(),
    }
    client = get_client()
    before = _llm_mark(ctx, client)
    try:
        if ctx.use_template:
            draft = template_draft(
                role=role, story=ctx.story, dossier=dossier, relevance=ctx.relevance,
                profile=profile, analyses=list(ctx.reports), graph=common["graph"],
            )
            res = CardResult(
                card=assemble_card(draft, **common), headline=draft.headline, source="template",
                fallback_reason="принудительный шаблон после провала проверки",
                prompt_version=prompt_version(),
            )
        else:
            key = f"{ctx.company_id}_{ctx.role}"
            feedback = None
            if ctx.regen_count and ctx.verify_errors:  # перегенерация после провала проверки
                feedback = retry_feedback(ctx.verify_errors)
                key = f"{key}_retry" if _retry_fixture(client, key) else key
            res = write_card(client=client, fixture_key=key, feedback=feedback, **common)
    finally:
        _llm_sync(ctx, client, before)
    ctx.card = res.card
    ctx.artifacts["card"] = _artifact(res)
    if res.source == "template":
        ctx.flags.append("template_card")
    return ctx


def _retry_fixture(client: LLMClient, key: str) -> bool:
    root = getattr(getattr(client, "settings", None), "llm_fixtures_dir", None)
    return bool(root) and (Path(root) / "card" / f"{key}_retry.json").exists()


def card_verify_step(ctx: PipelineContext, source: ToolDataSource | None = None) -> PipelineContext:
    """CARD_VERIFIED: финальная проверка карточки кодом; что делать при провале, решает автомат."""
    if ctx.card is None or ctx.company_id is None or not ctx.dossier:
        raise ValueError("проверке нужны карточка, company_id из сессии и досье")
    src = source or _get_source()
    profile = src.profile(ctx.company_id)
    if profile is None:
        raise LookupError(f"нет профиля компании {ctx.company_id}")
    graph = src.counterparties(ctx.company_id)
    check = verify_card(
        ctx.card,
        Dossier.model_validate(ctx.dossier),
        profile_features=allowed_features(profile, graph, ctx.card.relevance),
        profile=profile,
        analyses=list(ctx.reports),
    )
    ctx.verify_ok = check.ok
    ctx.verify_errors = check.messages()
    ctx.artifacts["card_verify"] = {"ok": check.ok, "failures": [f.model_dump() for f in check.failures]}
    return ctx


# --- реестр обработчиков --------------------------------------------------------------------------


@dataclass
class AnalysisCache:
    """Кеш глубокого разбора в памяти: «сюжет × отрасль × версия» -> досье, отчёты, замечания."""

    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    hits: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            found = self.items.get(key)
            if found is not None:
                self.hits += 1
            return copy.deepcopy(found) if found is not None else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.items[key] = copy.deepcopy(value)


def make_handlers(
    bundle: str = "rate_14",
    *,
    source: ToolDataSource | None = None,
    cache: AnalysisCache | None = None,
) -> Handlers:
    """Заглушки для ещё не сделанных шагов + настоящие planner, analysts, card."""
    src = source or FixtureDataSource(bundle)

    def planned(ctx: PipelineContext) -> PipelineContext:
        hit = cache.get(ctx.analysis_key) if cache is not None and not ctx.planner_task else None
        if hit is not None:
            ctx.dossier = hit["dossier"]
            ctx.reports = hit["reports"]
            ctx.artifacts.update(hit["artifacts"])
            ctx.artifacts["analysis_cached"] = True
            if ctx.trace is not None:
                ctx.trace.steps.append(f"analysis:cache_hit:{ctx.analysis_key}")
            return ctx
        ctx.artifacts["analysis_cached"] = False
        return planner_step(ctx, src)

    def analyzed(ctx: PipelineContext) -> PipelineContext:
        if ctx.artifacts.get("analysis_cached"):
            return ctx
        ctx = analysts_step(ctx)
        if cache is not None:
            cache.put(
                ctx.analysis_key,
                {
                    "dossier": ctx.dossier,
                    "reports": ctx.reports,
                    "artifacts": {
                        k: ctx.artifacts[k]
                        for k in ("analysts", "analyst_check_failures", "analyst_notes")
                        if k in ctx.artifacts
                    },
                },
            )
        return ctx

    def drafted(ctx: PipelineContext) -> PipelineContext:
        return card_step(ctx, src)

    def verified(ctx: PipelineContext) -> PipelineContext:
        return card_verify_step(ctx, src)

    handlers = dict(make_stub_handlers(bundle))
    handlers[State.PLANNED] = planned
    handlers[State.ANALYZED] = analyzed
    handlers[State.CRITIQUED] = critic_step
    handlers[State.ARBITRATED] = arbiter_step
    handlers[State.CARD_DRAFTED] = drafted
    handlers[State.CARD_VERIFIED] = verified
    return handlers


# --- прогон для клиента ---------------------------------------------------------------------------


def _run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run, а если цикл уже идёт в этом потоке — в отдельном потоке."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: list[Any] = []

    def target() -> None:
        try:
            box.append((True, asyncio.run(coro)))
        except BaseException as e:
            box.append((False, e))

    t = threading.Thread(target=target)
    t.start()
    t.join()
    ok, val = box[0]
    if not ok:
        raise val
    return val  # type: ignore[no-any-return]


class PipelineFailed(Exception):
    """Прогон закончился без карточки (FAILED); причина — в `ctx.errors`."""

    def __init__(self, message: str, trace: RunTrace) -> None:
        super().__init__(message)
        self.trace = trace


async def arun_for_client(
    story_id: str,
    company_id: str,
    role: str,
    *,
    sector: str,
    trace_id: str | None = None,
    story_version: int = 1,
    handlers: Handlers | None = None,
    bundle: str = "rate_14",
    cache: AnalysisCache | None = None,
) -> tuple[InsightCard, RunTrace]:
    """Весь автомат для «сюжет × клиент × роль»; в трассе шаги, tool_calls и llm_calls по trace_id."""
    ctx = PipelineContext(
        story_id=story_id, story_version=story_version, sector=sector, company_id=company_id, role=role
    )
    if trace_id:
        ctx.trace_id = trace_id
    reg = handlers if handlers is not None else make_handlers(bundle, cache=cache)
    ctx, trace = await run_pipeline(ctx, reg)
    trace.cost_rub = round(sum(c.cost_rub for c in trace.llm_calls), 6)
    if ctx.card is None:
        detail = "; ".join(f"{e['state']}: {e['error']}" for e in ctx.errors) or str(ctx.state)
        raise PipelineFailed(f"прогон завершился без карточки ({detail})", trace)
    return ctx.card, trace


def run_for_client(
    story_id: str,
    company_id: str,
    role: str,
    *,
    sector: str,
    trace_id: str | None = None,
    **kwargs: Any,
) -> tuple[InsightCard, RunTrace]:
    """Синхронная обёртка над `arun_for_client`."""
    return _run_sync(arun_for_client(story_id, company_id, role, sector=sector, trace_id=trace_id, **kwargs))


__all__ = [
    "SECTOR_RUN_COMPANY",
    "AnalysisCache",
    "PipelineFailed",
    "analysts_step",
    "arbiter_step",
    "arun_for_client",
    "card_step",
    "card_verify_step",
    "critic_step",
    "make_handlers",
    "planner_step",
    "run_for_client",
    "set_tool_source",
]
