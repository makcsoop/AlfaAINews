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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.agents.analysts import AnalystResult, run_analysts_detailed
from backend.agents.arbiter import arbitrate
from backend.agents.cache import RedisJsonStore
from backend.agents.critic import critique
from backend.agents.critic_llm import LLM_UNSUPPORTED, run_critic_llm
from backend.agents.dossier import Dossier
from backend.agents.examples import select_examples
from backend.agents.pipeline import Handlers, PipelineContext, State, run_pipeline
from backend.agents.planner import build_dossier, replan
from backend.agents.stub_steps import make_stub_handlers
from backend.agents.tools import FixtureDataSource, ToolContext, ToolDataSource
from backend.cards import CardResult, assemble_card, template_draft, verify_card, write_card
from backend.cards.analysis import build_analysis
from backend.cards.card import retry_feedback
from backend.cards.features import allowed_features
from backend.cards.prompt import prompt_version
from backend.llm import LLMClient, get_client
from backend.profile.exposures import with_exposures
from backend.ranking.fast_card import fast_card
from backend.ranking.score import score_pair
from backend.ranking.surprise import find_surprise
from backend.schemas import CompanyProfile, InsightCard, RunTrace, ToolCall
from backend.schemas.common import UserRole

# Разбор считается на отрасль, а не на компанию: инструменты планировщика данных компании не читают.
SECTOR_RUN_COMPANY = "_sector_analysis_"

_source: ToolDataSource | None = None


def set_tool_source(source: ToolDataSource | None) -> None:
    """Подставляет источник данных инструментов (БД Р1/Р2); None — вернуться к фикстуре."""
    global _source
    _source = source


def get_tool_source() -> ToolDataSource:
    global _source
    if _source is None:
        _source = _make_source()
    return _source


def _make_source() -> ToolDataSource:
    """TOOL_SOURCE=fixture|db (по умолчанию fixture); при db и недоступной БД — предупреждение и фикстура."""
    import logging
    import os

    if os.environ.get("TOOL_SOURCE", "fixture").strip().lower() == "db":
        try:
            from sqlalchemy import text

            from backend.agents.tools.db_source import DbDataSource
            from backend.db.engine import get_engine

            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return DbDataSource(engine)
        except Exception as e:
            logging.getLogger(__name__).warning("TOOL_SOURCE=db, БД недоступна (%s): фикстура", e)
    return FixtureDataSource("rate_14")


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
        source=source or get_tool_source(),
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


def analysts_step(ctx: PipelineContext, source: ToolDataSource | None = None) -> PipelineContext:
    """ANALYZED: два аналитика на досье; один упал — идём дальше, оба упали — исключение (DEGRADED)."""
    if not ctx.dossier:
        raise ValueError("аналитикам нужно досье (шаг PLANNED не выполнен)")
    dossier = Dossier.model_validate(ctx.dossier)
    # Примеры разборов аналитиков рынка: только опубликованные до сюжета, одинаковые для обоих
    examples = select_examples(
        source or get_tool_source(),
        event_type=dossier.event_type,
        sector=dossier.sector,
        story_published_at=dossier.story_published_at,
    )
    ctx.artifacts["examples"] = [e.id for e in examples]
    client = get_client()
    before = _llm_mark(ctx, client)
    try:
        results = run_analysts_detailed(
            dossier, client=client, trace_id=ctx.trace_id, priority=ctx.priority, examples=examples
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


def _critic_llm_pass(ctx: PipelineContext, final_round: bool) -> list[str]:
    """LLM-критик на последнем круге: unsupported -> флаг и заметка, не новый круг; сбой -> пропуск."""
    if not final_round:
        return []
    client = get_client()
    before = _llm_mark(ctx, client)
    try:
        res = run_critic_llm(
            Dossier.model_validate(ctx.dossier), _analyst_results(ctx),
            trace_id=ctx.trace_id, priority=ctx.priority, client=client,
        )
    except Exception as e:  # необязательный шаг не должен ронять конвейер
        ctx.artifacts["critic_llm"] = {"status": "error", "reason": type(e).__name__}
        return []
    finally:
        _llm_sync(ctx, client, before)
    ctx.artifacts["critic_llm"] = res.model_dump(mode="json")
    notes: list[str] = []
    for v in res.verdicts:
        if v.verdict == "unsupported":
            notes.append(f"{LLM_UNSUPPORTED} {v.claim_id}: {v.reason}")
            if ctx.trace is not None:
                ctx.trace.verify_failures.append(f"critic_llm {LLM_UNSUPPORTED}: {v.claim_id}: {v.reason}")
        elif v.verdict == "weak":
            notes.append(f"llm_weak {v.claim_id}: {v.reason}")
    if any(v.verdict == "unsupported" for v in res.verdicts):
        ctx.flags.append("critic_llm_flags")
    return notes


def critic_step(ctx: PipelineContext) -> PipelineContext:
    """CRITIQUED: проверка кодом; задача планировщику -> verdict "retry" (автомат вернётся в PLANNED)."""
    if not ctx.dossier:
        raise ValueError("критику нужно досье (шаг PLANNED не выполнен)")
    rounds: list[dict[str, Any]] = ctx.artifacts.setdefault("critic_rounds", [])
    asked = [r["planner_task"] for r in rounds if r.get("planner_task")]
    verdict = critique(Dossier.model_validate(ctx.dossier), _analyst_results(ctx), asked=asked)
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
    final_round = not verdict.planner_task or ctx.critic_rounds >= ctx.max_critic_rounds
    llm_notes = _critic_llm_pass(ctx, final_round)
    if verdict.planner_task:
        ctx.critic_verdict = "retry"
        ctx.critic_notes = list(verdict.planner_tasks)  # автомат склеит их в planner_task
    else:
        ctx.critic_verdict = "ok"
        ctx.critic_notes = list(verdict.notes) + llm_notes
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


# --- скоринг (Р2) -----------------------------------------------------------------------------------


def profile_v1(src: ToolDataSource, company_id: str) -> CompanyProfile | None:
    """Профиль v1 (с чувствительностями к факторам) для скоринга и карточки."""
    profile = src.profile(company_id)
    if profile is None:
        return None
    return with_exposures(profile, src.counterparties(company_id))


def _now(ctx: PipelineContext, now: datetime | None) -> datetime:
    """Момент оценки свежести: явный параметр; в LLM_MODE=fixture — время сюжета (детерминированные тесты)."""
    import os

    if now is not None:
        return now
    if os.environ.get("LLM_MODE", "").strip().lower() == "fixture" and ctx.story is not None:
        return ctx.story.last_seen
    return datetime.now(UTC)


def _score(ctx: PipelineContext, src: ToolDataSource, now: datetime | None, *, with_analysis: bool) -> None:
    assert ctx.story is not None and ctx.company_id is not None
    profile = profile_v1(src, ctx.company_id)
    if profile is None:
        raise LookupError(f"нет профиля компании {ctx.company_id}")
    surprise = find_surprise(
        ctx.story, ctx.facts, bank_positions=src.bank_positions(), market=src.market_knowledge()
    )
    ctx.relevance = score_pair(
        ctx.story,
        profile,
        now=_now(ctx, now),
        facts=ctx.facts,
        graph=src.counterparties(ctx.company_id),
        sources={s.id: s for s in src.sources()},
        analysis=ctx.analysis if with_analysis else None,
        surprise=surprise,
        role=ctx.role,  # type: ignore[arg-type]
    )


def scored_step(ctx: PipelineContext, src: ToolDataSource, now: datetime | None = None) -> PipelineContext:
    """SCORED: значимость пары «сюжет × клиент» кодом (backend.ranking), без LLM."""
    _score(ctx, src, now, with_analysis=False)
    return ctx


def quick_step(ctx: PipelineContext, src: ToolDataSource) -> PipelineContext:
    """QUICK_CARD: быстрая карточка кодом по вкладам скоринга."""
    assert ctx.story is not None and ctx.relevance is not None and ctx.company_id is not None
    ctx.quick_card = fast_card(
        ctx.story,
        ctx.relevance,
        ctx.role,  # type: ignore[arg-type]
        facts=ctx.facts,
        items={i.id: i for i in src.raw_items()},
        sources={s.id: s for s in src.sources()},
        bank_positions=src.bank_positions(),
        profile=profile_v1(src, ctx.company_id),
        market=src.market_knowledge(),
    )
    return ctx


def rescored_arbiter_step(
    ctx: PipelineContext, src: ToolDataSource, now: datetime | None = None
) -> PipelineContext:
    """ARBITRATED + шаг 11 архитектуры: после арбитра значимость пересчитывается по разбору."""
    ctx = arbiter_step(ctx)
    if ctx.story is not None and ctx.company_id is not None and ctx.reports:
        ctx.analysis = build_analysis(
            ctx.story.id, ctx.sector, ctx.story_version, ctx.reports, on_review=ctx.on_review,
            trace_id=ctx.trace_id,
        )
        _score(ctx, src, now, with_analysis=True)
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
    src = source or get_tool_source()
    profile = profile_v1(src, ctx.company_id)
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
        "facts": ctx.facts,
        "analysis": ctx.analysis,
        "bank_positions": src.bank_positions(),
        "market": src.market_knowledge(),
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
    src = source or get_tool_source()
    profile = profile_v1(src, ctx.company_id)
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
    backend: RedisJsonStore | None = None  # общий кеш (Redis); память — первый уровень

    def get(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            found = self.items.get(key)
            if found is None and self.backend is not None:
                found = self.backend.get_analysis(key)
                if found is not None:
                    self.items[key] = found
            if found is not None:
                self.hits += 1
            return copy.deepcopy(found) if found is not None else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.items[key] = copy.deepcopy(value)
            if self.backend is not None:
                self.backend.put_analysis(key, value)


def make_handlers(
    bundle: str = "rate_14",
    *,
    source: ToolDataSource | None = None,
    cache: AnalysisCache | None = None,
    now: datetime | None = None,
) -> Handlers:
    """Заглушки для ещё не сделанных шагов + настоящие planner, analysts, card."""
    src = source or FixtureDataSource(bundle)
    stub_handlers = make_stub_handlers(bundle)

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
        ctx = analysts_step(ctx, src)
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

    stub_scored = stub_handlers[State.SCORED]
    stub_quick = stub_handlers[State.QUICK_CARD]

    def scored(ctx: PipelineContext) -> PipelineContext:
        if ctx.company_id is None:  # разбор отрасли без клиента: скоринг пары не нужен
            return stub_scored(ctx)
        return scored_step(ctx, src, now)

    def quick(ctx: PipelineContext) -> PipelineContext:
        if ctx.company_id is None or ctx.relevance is None:
            return stub_quick(ctx)
        try:
            return quick_step(ctx, src)
        except Exception as e:  # быстрая карточка не должна ронять разбор
            ctx.flags.append(f"fast_card_failed:{type(e).__name__}")
            return stub_quick(ctx)

    def arbitrated(ctx: PipelineContext) -> PipelineContext:
        return rescored_arbiter_step(ctx, src, now)

    def drafted(ctx: PipelineContext) -> PipelineContext:
        return card_step(ctx, src)

    def verified(ctx: PipelineContext) -> PipelineContext:
        return card_verify_step(ctx, src)

    handlers = dict(stub_handlers)
    handlers[State.SCORED] = scored
    handlers[State.QUICK_CARD] = quick
    handlers[State.PLANNED] = planned
    handlers[State.ANALYZED] = analyzed
    handlers[State.CRITIQUED] = critic_step
    handlers[State.ARBITRATED] = arbitrated
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
    "get_tool_source",
    "make_handlers",
    "planner_step",
    "run_for_client",
    "set_tool_source",
]
