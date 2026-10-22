"""Критик кодом на досье и разборах rate_14 (LLM_MODE=fixture)."""

import asyncio

import pytest

from backend.agents.analysts import AnalystResult
from backend.agents.critic import critique, unsupported_numbers
from backend.agents.dossier import Dossier, DossierItem
from backend.agents.pipeline import PipelineContext, State, run_pipeline
from backend.agents.steps import make_handlers

STORY = "st_2026-09-11_keyrate"


@pytest.fixture(scope="module")
def analyzed():
    import os

    os.environ["LLM_MODE"] = "fixture"
    ctx = PipelineContext(
        story_id=STORY, story_version=1, sector="46", company_id="U_DEMO_BORROWER", role="cfo"
    )
    ctx, _ = asyncio.run(run_pipeline(ctx, make_handlers(), stop_after={State.ANALYZED}))
    dossier = Dossier.model_validate(ctx.dossier)
    results = {a: AnalystResult.model_validate(r) for a, r in ctx.artifacts["analysts"].items()}
    return dossier, results


def mutated(results, analyst, fn):
    out = {a: r.model_copy(deep=True) for a, r in results.items()}
    fn(out[analyst])
    return out


def edit_driver(res: AnalystResult, channel: str, **changes):
    """Меняет драйвер (и тот же канал в отчёте) аналитика."""
    for cd in res.drivers:
        if cd.driver.channel == channel:
            cd.driver = cd.driver.model_copy(update=changes)
    for c in res.report.channels:
        if c.channel == channel and "rationale" in changes:
            c.rationale = changes["rationale"]


def test_rate14_passes_first_time(analyzed):
    dossier, results = analyzed
    v = critique(dossier, results)
    assert v.ok, v.failures
    assert v.planner_task is None and v.failures == []


def test_missing_ref_fails_with_task(analyzed):
    dossier, results = analyzed
    bad = mutated(results, "business", lambda r: edit_driver(r, "rate_cost", refs=["fact:ghost:9"]))
    v = critique(dossier, bad)
    assert not v.ok
    f = [x for x in v.failures if x.code == "missing_ref"]
    assert f and f[0].analyst == "business" and f[0].channel == "rate_cost"
    assert v.planner_task and "rate_cost" in v.planner_task


def test_missing_ref_from_dropped_driver_note(analyzed):
    dossier, results = analyzed
    bad = mutated(
        results,
        "finance",
        lambda r: r.notes.append("driver[2] fx_rub: нет в досье ['ghost'], отброшен"),
    )
    assert any(x.code == "missing_ref" and x.channel == "fx_rub" for x in critique(dossier, bad).failures)


def test_number_not_from_quote_fails(analyzed):
    dossier, results = analyzed
    bad = mutated(
        results,
        "finance",
        lambda r: edit_driver(r, "rate_cost", rationale="Ставка сохранена на 17% годовых."),
    )
    v = critique(dossier, bad)
    assert any(x.code == "number_unverified" and "17" in x.detail for x in v.failures)
    assert v.planner_task and "найти материал с числом 17" in v.planner_task


def test_numbers_and_dates_matching():
    item = DossierItem(
        id="n1",
        kind="news",
        title="t",
        text="Решение принято 11 сентября 2026 года, ставка 14,0%",
        data={"v": 13.5},
    )
    assert unsupported_numbers("14% и 13,5%, дата 11.09.2026 и 11.09", [item]) == []
    assert unsupported_numbers("ставка 15% с 12.09", [item]) == ["15", "12.09"]


def test_evaluative_word_fails_without_task(analyzed):
    dossier, results = analyzed
    bad = mutated(
        results,
        "business",
        lambda r: edit_driver(r, "rate_cost", rationale="Это плохо для оптовой торговли."),
    )
    v = critique(dossier, bad)
    assert any(x.code == "evaluative" and x.channel == "rate_cost" for x in v.failures)
    assert v.planner_task is None  # новыми данными не лечится
    assert any("плохо" in n for n in v.notes)


def test_bank_view_without_cmi_ref_fails(analyzed):
    dossier, results = analyzed
    bad = mutated(
        results,
        "business",
        lambda r: edit_driver(
            r,
            "consumer_demand",
            rationale="По мнению банка, спрос восстановится медленнее.",
            refs=["fact:st_2026-09-11_keyrate:3"],
        ),
    )
    v = critique(dossier, bad)
    assert any(x.code == "bank_view_no_cmi_ref" and x.channel == "consumer_demand" for x in v.failures)
    assert v.planner_task and "найти позицию ЦМИ по каналу consumer_demand" in v.planner_task


def test_bank_view_with_cmi_ref_ok(analyzed):
    dossier, results = analyzed
    ok = mutated(
        results,
        "business",
        lambda r: edit_driver(
            r, "consumer_demand", rationale="Банк ожидает паузу.", refs=["cmi_keyrate_2026_09#p1#c3"]
        ),
    )
    assert critique(dossier, ok).ok


def test_channel_coverage(analyzed):
    dossier, results = analyzed
    bad = {a: r.model_copy(deep=True) for a, r in results.items()}
    for res in bad.values():
        res.drivers = [d for d in res.drivers if d.driver.channel != "fx_rub"]
        res.report.channels = [c for c in res.report.channels if c.channel != "fx_rub"]
    v = critique(dossier, bad)
    f = [x for x in v.failures if x.code == "no_channel_coverage"]
    assert [x.channel for x in f] == ["fx_rub"] and "нет оценки канала fx_rub" in f[0].detail
    assert v.planner_task and "fx_rub" in v.planner_task


def test_all_analysts_failed(analyzed):
    dossier, _ = analyzed
    dead = {
        "business": AnalystResult(analyst="business", failure="LLMUnavailable"),
        "finance": AnalystResult(analyst="finance", failure="LLMUnavailable"),
    }
    v = critique(dossier, dead)
    assert not v.ok and v.failures[0].code == "all_analysts_failed" and v.planner_task is None


def test_one_analyst_down_is_note_only(analyzed):
    dossier, results = analyzed
    one = {"business": results["business"], "finance": AnalystResult(analyst="finance", failure="x")}
    v = critique(dossier, one)
    assert any("finance" in n for n in v.notes)


# --- цикл критика в автомате ---


def _inject_ghost_ref(ctx: PipelineContext) -> None:
    for cd in ctx.artifacts["analysts"]["business"]["drivers"]:
        if cd["driver"]["channel"] == "rate_cost":
            cd["driver"]["refs"] = ["fact:ghost:9"]


def _ctx() -> PipelineContext:
    return PipelineContext(
        story_id=STORY, story_version=1, sector="46", company_id="U_DEMO_BORROWER", role="cfo"
    )


async def test_pipeline_rate14_passes_critic_and_arbiter_first_time(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    ctx, trace = await run_pipeline(_ctx(), make_handlers())
    assert ctx.state == State.PUBLISHED and ctx.critic_rounds == 0
    assert ctx.artifacts["critic"]["ok"] and ctx.artifacts["arbiter"]["on_review"] is False
    assert [c["agree"] for c in ctx.artifacts["arbiter"]["bank_check"]] == [True]
    assert not [f for f in trace.verify_failures if f.startswith("critic")]


async def test_pipeline_critic_retries_then_ok(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    handlers = dict(make_handlers())
    real = handlers[State.ANALYZED]
    calls = {"n": 0}

    def analyzed(ctx):
        ctx = real(ctx)
        calls["n"] += 1
        if calls["n"] == 1:
            _inject_ghost_ref(ctx)
        return ctx

    handlers[State.ANALYZED] = analyzed
    ctx, trace = await run_pipeline(_ctx(), handlers)
    assert calls["n"] == 2 and ctx.critic_rounds == 1
    assert "critic_unresolved" not in ctx.flags and ctx.state == State.PUBLISHED
    assert [r["ok"] for r in ctx.artifacts["critic_rounds"]] == [False, True]
    assert any("missing_ref" in f for f in trace.verify_failures)
    assert any(c.purpose == "planner" for c in trace.llm_calls)  # replan по задаче критика


async def test_pipeline_critic_gives_up_after_two_rounds(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    handlers = dict(make_handlers())
    real = handlers[State.ANALYZED]

    def analyzed(ctx):
        ctx = real(ctx)
        _inject_ghost_ref(ctx)
        return ctx

    handlers[State.ANALYZED] = analyzed
    ctx, _ = await run_pipeline(_ctx(), handlers)
    assert ctx.critic_rounds == 2 and len(ctx.artifacts["critic_rounds"]) == 3
    assert "critic_unresolved" in ctx.flags
    assert ctx.state == State.PUBLISHED  # идём дальше с пометкой


def test_repeated_uncovered_channel_gives_no_second_round(analyzed):
    from backend.agents.dossier import PlanStep

    dossier, results = analyzed
    bad = {a: r.model_copy(deep=True) for a, r in results.items()}
    for res in bad.values():
        res.drivers = [d for d in res.drivers if d.driver.channel != "fx_rub"]
        res.report.channels = [c for c in res.report.channels if c.channel != "fx_rub"]
    assert critique(dossier, bad).planner_task  # первый раз задача есть
    again = dossier.model_copy(deep=True)
    again.plan.append(PlanStep(tool="search_news", why="fx_rub", origin="critic"))
    v = critique(again, bad)
    assert any(x.code == "no_channel_coverage" for x in v.failures)
    assert v.planner_task is None and any("fx_rub" in n for n in v.notes)
    gap = dossier.model_copy(deep=True)
    gap.gaps = ["fx_rub: нет материала"]
    assert critique(gap, bad).planner_task is None
    # задача из прошлого круга тоже считается запросом, даже если replan не добавил шагов
    first = critique(dossier, bad).planner_task
    assert critique(dossier, bad, asked=[first]).planner_task is None
