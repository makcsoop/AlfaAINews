"""Конечный автомат конвейера «от сюжета до карточки».

Один прогон = одна пара «сюжет × отрасль» (глубокий разбор) плюс, если задан `company_id`,
карточка для «сюжет × клиент × роль». Автомат сам ничего не считает и не вызывает модели:
он ведёт состояние, вызывает внедрённые обработчики шагов, пишет журнал (`RunTrace`)
и решает, куда идти дальше (цикл критика, перегенерация карточки, деградация).

Обработчик регистрируется на состояние, В КОТОРОЕ автомат вошёл: вошли в PLANNED — работает
планировщик, вошли в ANALYZED — аналитики и т. д. Обработчик получает и возвращает контекст,
может быть обычной или async-функцией и сообщает автомату итог через поля контекста
(`critic_verdict`, `on_review`, `verify_ok`).
"""

from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from newsai.schemas import (
    AnalystReport,
    EventFacts,
    InsightCard,
    RelevanceScore,
    RunTrace,
    Story,
)

log = logging.getLogger(__name__)


class State(StrEnum):
    NEW = "NEW"
    FACTS_READY = "FACTS_READY"
    SCORED = "SCORED"
    QUICK_CARD = "QUICK_CARD"
    QUEUED_DEEP = "QUEUED_DEEP"
    PLANNED = "PLANNED"
    ANALYZED = "ANALYZED"
    CRITIQUED = "CRITIQUED"
    ARBITRATED = "ARBITRATED"
    CARD_DRAFTED = "CARD_DRAFTED"
    CARD_VERIFIED = "CARD_VERIFIED"
    PUBLISHED = "PUBLISHED"
    ON_REVIEW = "ON_REVIEW"
    DEGRADED = "DEGRADED"  # «разбор недоступен»: отдаём быструю карточку
    FAILED = "FAILED"


S = State
TERMINAL = frozenset({S.PUBLISHED, S.ON_REVIEW, S.DEGRADED, S.FAILED})

# Разрешённые переходы. Из любого нетерминального состояния можно в FAILED;
# в DEGRADED — из тех, где быстрая карточка уже есть или может появиться.
TRANSITIONS: dict[State, frozenset[State]] = {
    S.NEW: frozenset({S.FACTS_READY, S.FAILED}),
    S.FACTS_READY: frozenset({S.SCORED, S.FAILED}),
    S.SCORED: frozenset({S.QUICK_CARD, S.FAILED}),
    S.QUICK_CARD: frozenset({S.QUEUED_DEEP, S.PUBLISHED, S.DEGRADED, S.FAILED}),
    S.QUEUED_DEEP: frozenset({S.PLANNED, S.DEGRADED, S.FAILED}),
    S.PLANNED: frozenset({S.ANALYZED, S.DEGRADED, S.FAILED}),
    S.ANALYZED: frozenset({S.CRITIQUED, S.DEGRADED, S.FAILED}),
    S.CRITIQUED: frozenset({S.PLANNED, S.ARBITRATED, S.DEGRADED, S.FAILED}),
    S.ARBITRATED: frozenset({S.CARD_DRAFTED, S.DEGRADED, S.FAILED}),
    S.CARD_DRAFTED: frozenset({S.CARD_VERIFIED, S.DEGRADED, S.FAILED}),
    S.CARD_VERIFIED: frozenset({S.CARD_DRAFTED, S.PUBLISHED, S.ON_REVIEW, S.DEGRADED, S.FAILED}),
    # ошибка при самой публикации (запись в хранилище) — единственный выход из «конечных»
    S.PUBLISHED: frozenset({S.FAILED}),
    S.ON_REVIEW: frozenset({S.FAILED}),
    S.DEGRADED: frozenset(),
    S.FAILED: frozenset(),
}


class InvalidTransition(Exception):
    def __init__(self, src: State, dst: State):
        super().__init__(f"недопустимый переход {src} -> {dst}")
        self.src = src
        self.dst = dst


def analysis_key(story_id: str, sector: str, version: int) -> str:
    """Ключ кеша глубокого разбора: «сюжет × отрасль × версия»."""
    return f"{story_id}:{sector}:v{version}"


def card_key(analysis: str, company_id: str, role: str | None) -> str:
    """Ключ кеша карточки: «разбор × клиент × роль»."""
    return f"{analysis}:{company_id}:{role or '-'}"


@dataclass
class Transition:
    src: State
    dst: State
    at: datetime
    reason: str = ""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class PipelineContext:
    story_id: str
    story_version: int
    sector: str
    company_id: str | None = None
    role: str | None = None
    priority: float = 0.0
    deep: bool = True  # False — только быстрая карточка
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: State = State.NEW
    max_critic_rounds: int = 2
    max_regenerations: int = 1

    # артефакты шагов
    story: Story | None = None
    facts: EventFacts | None = None
    relevance: RelevanceScore | None = None
    quick_card: InsightCard | None = None
    dossier: dict[str, Any] = field(default_factory=dict)
    reports: list[AnalystReport] = field(default_factory=list)
    critic_notes: list[str] = field(default_factory=list)
    card: InsightCard | None = None

    # сигналы обработчиков автомату
    critic_verdict: str = "ok"  # "ok" | "retry" (CRITIQUED)
    planner_task: str | None = None  # что критик просит от планировщика
    on_review: bool = False  # арбитр: итог «на проверке» (ARBITRATED)
    verify_ok: bool = True  # результат финальной проверки (CARD_VERIFIED)
    verify_errors: list[str] = field(default_factory=list)

    # состояние автомата
    critic_rounds: int = 0
    regen_count: int = 0
    use_template: bool = False  # CARD_DRAFTED должен собрать шаблонный текст
    flags: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    history: list[Transition] = field(default_factory=list)
    trace: RunTrace | None = None

    @property
    def analysis_key(self) -> str:
        return analysis_key(self.story_id, self.sector, self.story_version)

    @property
    def card_key(self) -> str | None:
        if self.company_id is None:
            return None
        return card_key(self.analysis_key, self.company_id, self.role)


class StepHandler(Protocol):
    def __call__(self, ctx: PipelineContext) -> PipelineContext | Awaitable[PipelineContext]: ...


Handlers = Mapping[State, Callable[[PipelineContext], Any]]


def check_transition(src: State, dst: State) -> None:
    if dst not in TRANSITIONS[src]:
        raise InvalidTransition(src, dst)


def _move(ctx: PipelineContext, dst: State, reason: str = "") -> None:
    check_transition(ctx.state, dst)
    ctx.history.append(Transition(ctx.state, dst, _now(), reason))
    if ctx.trace is not None:
        ctx.trace.steps.append(f"{ctx.state}->{dst}" + (f" ({reason})" if reason else ""))
    log.info("run %s: %s -> %s %s", ctx.trace_id, ctx.state, dst, reason)
    ctx.state = dst


def _decide(ctx: PipelineContext) -> tuple[State, str]:
    """Следующее состояние по текущему и сигналам обработчиков."""
    st = ctx.state
    if st == S.NEW:
        return S.FACTS_READY, ""
    if st == S.FACTS_READY:
        return S.SCORED, ""
    if st == S.SCORED:
        return S.QUICK_CARD, ""
    if st == S.QUICK_CARD:
        return (S.QUEUED_DEEP, "") if ctx.deep else (S.PUBLISHED, "только быстрая карточка")
    if st == S.QUEUED_DEEP:
        return S.PLANNED, ""
    if st == S.PLANNED:
        return S.ANALYZED, ""
    if st == S.ANALYZED:
        return S.CRITIQUED, ""
    if st == S.CRITIQUED:
        if ctx.critic_verdict == "retry":
            if ctx.critic_rounds < ctx.max_critic_rounds:
                ctx.critic_rounds += 1
                ctx.planner_task = "; ".join(ctx.critic_notes[-5:]) or None
                return S.PLANNED, f"круг критика {ctx.critic_rounds}/{ctx.max_critic_rounds}"
            ctx.flags.append("critic_unresolved")
            return S.ARBITRATED, "круги критика исчерпаны"
        return S.ARBITRATED, ""
    if st == S.ARBITRATED:
        return S.CARD_DRAFTED, ""
    if st == S.CARD_DRAFTED:
        return S.CARD_VERIFIED, ""
    if st == S.CARD_VERIFIED:
        if ctx.verify_ok:
            return (S.ON_REVIEW, "арбитр: на проверке") if ctx.on_review else (S.PUBLISHED, "")
        if ctx.regen_count < ctx.max_regenerations:
            ctx.regen_count += 1
            return S.CARD_DRAFTED, "перегенерация карточки"
        if not ctx.use_template:
            ctx.use_template = True
            ctx.flags.append("template_card")
            return S.CARD_DRAFTED, "шаблонный текст"
        return S.DEGRADED, "шаблонная карточка не прошла проверку"
    raise InvalidTransition(st, st)  # из конечного состояния идти некуда


async def _call(handler: Callable[[PipelineContext], Any], ctx: PipelineContext) -> PipelineContext:
    result = handler(ctx)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, PipelineContext) else ctx


def _fail_state(ctx: PipelineContext, exc: Exception) -> State:
    """Деградация возможна, если есть что отдать вместо разбора: быстрая карточка."""
    if getattr(exc, "fatal", False) or ctx.quick_card is None:
        return S.FAILED
    return S.DEGRADED


async def run_pipeline(
    ctx: PipelineContext,
    handlers: Handlers,
    *,
    stop_after: Iterable[State] = (),
) -> tuple[PipelineContext, RunTrace]:
    """Гонит контекст по автомату до конечного состояния (или до одного из `stop_after`).

    `stop_after={QUEUED_DEEP}` — остановиться после быстрой карточки, оставив глубокий разбор
    очереди; повторный вызов с тем же контекстом продолжит с места остановки.
    """
    stops = frozenset(stop_after)
    if ctx.trace is None:
        ctx.trace = RunTrace(
            run_id=ctx.trace_id, story_id=ctx.story_id, company_id=ctx.company_id, started_at=_now()
        )
    trace = ctx.trace
    while ctx.state not in TERMINAL:
        dst, reason = _decide(ctx)
        _move(ctx, dst, reason)
        handler = handlers.get(dst)
        if handler is not None:
            try:
                ctx = await _call(handler, ctx)
                ctx.trace = trace
                trace.steps.append(f"handler:{dst}:ok")
            except Exception as exc:
                log.warning("run %s: обработчик %s упал: %r", ctx.trace_id, dst, exc)
                ctx.errors.append({"state": str(dst), "error": type(exc).__name__, "message": str(exc)})
                trace.steps.append(f"handler:{dst}:error:{type(exc).__name__}")
                target = _fail_state(ctx, exc)
                if target not in TRANSITIONS[ctx.state]:
                    target = S.FAILED
                if target == S.DEGRADED:
                    ctx.card = ctx.card or ctx.quick_card
                    ctx.flags.append("deep_unavailable")
                _move(ctx, target, f"{type(exc).__name__} в {dst}")
                break
        if dst == S.CARD_VERIFIED:
            trace.verify_rounds += 1
            if not ctx.verify_ok:
                trace.verify_failures.extend(ctx.verify_errors or ["проверка не пройдена"])
        if dst in stops:
            return ctx, trace
    trace.finished_at = _now()
    return ctx, trace
