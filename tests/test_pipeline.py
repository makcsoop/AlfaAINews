import pytest

from newsai.agents.pipeline import (
    TERMINAL,
    TRANSITIONS,
    InvalidTransition,
    PipelineContext,
    State,
    analysis_key,
    card_key,
    check_transition,
    run_pipeline,
)
from newsai.agents.stub_steps import make_stub_handlers

S = State
STORY = "st_2026-09-11_keyrate"
BORROWER = "U_DEMO_BORROWER"

HAPPY = [
    S.NEW, S.FACTS_READY, S.SCORED, S.QUICK_CARD, S.QUEUED_DEEP, S.PLANNED, S.ANALYZED,
    S.CRITIQUED, S.ARBITRATED, S.CARD_DRAFTED, S.CARD_VERIFIED, S.PUBLISHED,
]  # fmt: skip


def make_ctx(**kw) -> PipelineContext:
    return PipelineContext(
        story_id=STORY, story_version=1, sector="46", company_id=BORROWER, role="cfo", **kw
    )


def path(ctx: PipelineContext) -> list[State]:
    return [ctx.history[0].src] + [t.dst for t in ctx.history]


def test_keys():
    k = analysis_key(STORY, "46", 1)
    assert k == f"{STORY}:46:v1"
    assert card_key(k, BORROWER, "cfo") == f"{k}:{BORROWER}:cfo"
    assert card_key(k, BORROWER, None).endswith(":-")


async def test_rate_14_reaches_published_with_trace():
    ctx, trace = await run_pipeline(make_ctx(), make_stub_handlers())
    assert ctx.state == S.PUBLISHED
    assert path(ctx) == HAPPY
    assert ctx.card is not None and ctx.card.company_id == BORROWER
    assert ctx.card_key == f"{STORY}:46:v1:{BORROWER}:cfo"
    assert trace.run_id == ctx.trace_id
    assert trace.story_id == STORY and trace.company_id == BORROWER
    assert trace.finished_at is not None and trace.finished_at >= trace.started_at
    assert trace.verify_rounds == 1 and trace.verify_failures == []
    assert "NEW->FACTS_READY" in trace.steps
    assert "handler:PLANNED:ok" in trace.steps
    assert trace.steps[-1] == "handler:PUBLISHED:ok"


async def test_quick_only_publishes_after_quick_card():
    ctx, _ = await run_pipeline(make_ctx(deep=False), make_stub_handlers())
    assert ctx.state == S.PUBLISHED
    assert path(ctx) == [S.NEW, S.FACTS_READY, S.SCORED, S.QUICK_CARD, S.PUBLISHED]


async def test_stop_after_queue_then_resume():
    handlers = make_stub_handlers()
    ctx, trace = await run_pipeline(make_ctx(), handlers, stop_after={S.QUEUED_DEEP})
    assert ctx.state == S.QUEUED_DEEP and trace.finished_at is None
    assert ctx.quick_card is not None and ctx.card is None
    ctx, trace2 = await run_pipeline(ctx, handlers)
    assert ctx.state == S.PUBLISHED and trace2 is trace and trace.finished_at is not None


async def test_other_client_gets_own_card():
    ctx = PipelineContext(
        story_id=STORY, story_version=1, sector="46", company_id="U_DEMO_DEPOSITOR", role="treasurer"
    )
    ctx, _ = await run_pipeline(ctx, make_stub_handlers())
    assert ctx.card is not None and ctx.card.company_id == "U_DEMO_DEPOSITOR"


def test_invalid_transitions():
    with pytest.raises(InvalidTransition):
        check_transition(S.NEW, S.PUBLISHED)
    with pytest.raises(InvalidTransition):
        check_transition(S.DEGRADED, S.PLANNED)
    check_transition(S.CRITIQUED, S.PLANNED)
    for t in TERMINAL - {S.PUBLISHED, S.ON_REVIEW}:
        assert not TRANSITIONS[t]


async def test_unknown_story_fails():
    ctx = PipelineContext(story_id="st_other", story_version=1, sector="46")
    ctx, trace = await run_pipeline(ctx, make_stub_handlers())
    assert ctx.state == S.FAILED  # быстрой карточки ещё нет — деградировать нечем
    assert ctx.errors[0]["state"] == "FACTS_READY"
    assert trace.finished_at is not None


async def test_critic_loops_back_at_most_two_rounds():
    handlers = dict(make_stub_handlers())
    calls = {"planner": 0}
    base_plan = handlers[S.PLANNED]

    def planner(ctx):
        calls["planner"] += 1
        return base_plan(ctx)

    def critic(ctx):
        ctx.critic_verdict = "retry"
        ctx.critic_notes.append("нет второго признака компании")
        return ctx

    handlers[S.PLANNED] = planner
    handlers[S.CRITIQUED] = critic
    ctx, _ = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.PUBLISHED
    assert calls["planner"] == 3  # первый проход + 2 круга
    assert ctx.critic_rounds == 2
    assert "critic_unresolved" in ctx.flags
    assert ctx.planner_task == "нет второго признака компании; нет второго признака компании"


async def test_critic_returns_once_then_ok():
    handlers = dict(make_stub_handlers())

    def critic(ctx):
        ctx.critic_verdict = "retry" if ctx.critic_rounds == 0 else "ok"
        return ctx

    handlers[S.CRITIQUED] = critic
    ctx, _ = await run_pipeline(make_ctx(), handlers)
    assert ctx.critic_rounds == 1 and "critic_unresolved" not in ctx.flags
    assert path(ctx).count(S.PLANNED) == 2


class LLMUnavailable(Exception):
    pass


async def test_analyst_failure_degrades_to_quick_card():
    handlers = dict(make_stub_handlers())

    async def analysts(ctx):
        raise LLMUnavailable("обе модели не отвечают")

    handlers[S.ANALYZED] = analysts
    ctx, trace = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.DEGRADED
    assert ctx.card is ctx.quick_card and ctx.card is not None
    assert "deep_unavailable" in ctx.flags
    assert ctx.errors[0]["error"] == "LLMUnavailable"
    assert "handler:ANALYZED:error:LLMUnavailable" in trace.steps
    assert trace.finished_at is not None


async def test_fatal_error_fails():
    handlers = dict(make_stub_handlers())

    def boom(ctx):
        exc = RuntimeError("битые данные")
        exc.fatal = True
        raise exc

    handlers[S.PLANNED] = boom
    ctx, _ = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.FAILED


async def test_arbiter_on_review_does_not_block():
    handlers = dict(make_stub_handlers())

    def arbiter(ctx):
        ctx.on_review = True
        return ctx

    handlers[S.ARBITRATED] = arbiter
    ctx, _ = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.ON_REVIEW
    assert ctx.card is not None  # карточка выдана, просто с пометкой
    assert S.CARD_VERIFIED in path(ctx)


async def test_verify_failure_regenerates_once_then_template():
    handlers = dict(make_stub_handlers())
    drafts = []

    def draft(ctx):
        drafts.append(ctx.use_template)
        return ctx

    def verify(ctx):
        ctx.verify_ok = ctx.use_template  # LLM-текст не проходит, шаблон проходит
        ctx.verify_errors = [] if ctx.verify_ok else ["число 15% нет в источнике"]
        return ctx

    handlers[S.CARD_DRAFTED] = draft
    handlers[S.CARD_VERIFIED] = verify
    ctx, trace = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.PUBLISHED
    assert drafts == [False, False, True]  # черновик, одна перегенерация, шаблон
    assert trace.verify_rounds == 3
    assert trace.verify_failures == ["число 15% нет в источнике"] * 2
    assert "template_card" in ctx.flags


async def test_regeneration_fixes_card():
    handlers = dict(make_stub_handlers())

    def verify(ctx):
        ctx.verify_ok = ctx.regen_count >= 1
        return ctx

    handlers[S.CARD_VERIFIED] = verify
    ctx, trace = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.PUBLISHED and trace.verify_rounds == 2
    assert "template_card" not in ctx.flags


async def test_template_failing_too_degrades():
    handlers = dict(make_stub_handlers())

    def verify(ctx):
        ctx.verify_ok = False
        return ctx

    handlers[S.CARD_VERIFIED] = verify
    ctx, trace = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.DEGRADED and trace.verify_rounds == 3
    assert ctx.card is not None


async def test_publish_error_fails():
    handlers = dict(make_stub_handlers())

    def publish(ctx):
        raise OSError("хранилище недоступно")

    handlers[S.PUBLISHED] = publish
    ctx, _ = await run_pipeline(make_ctx(), handlers)
    assert ctx.state == S.FAILED
