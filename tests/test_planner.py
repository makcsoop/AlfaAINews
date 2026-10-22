"""Планировщик на фикстуре rate_14: шаблоны, досье с id, достройка моделью только при пробелах."""

import json
import re

import pytest
from pydantic import SecretStr

from backend.agents.dossier import Dossier
from backend.agents.pipeline import PipelineContext, State, run_pipeline
from backend.agents.planner import (
    PLANNER_TOOLS,
    build_dossier,
    compute_gaps,
    expand_template,
    gap_code,
    load_prompt,
    load_templates,
    replan,
    taxonomy_channels,
)
from backend.agents.steps import planner_step, set_tool_source
from backend.agents.stub_steps import make_stub_handlers
from backend.agents.tools import TOOLS, FixtureDataSource, ToolContext
from backend.fixtures import load_bundle
from backend.llm import LLMClient, get_client, reset_client
from backend.llm.budget import BudgetManager, InMemoryBudgetStore
from backend.llm.cache import ResponseCache
from backend.llm.client import CompletionRequest, CompletionResponse, TransportError
from backend.llm.journal import Journal
from backend.llm.settings import LLMSettings
from backend.schemas import NumberFact

STORY = "st_2026-09-11_keyrate"
TOP10 = [
    "rate_decision", "monetary_signal", "fx_move", "inflation_data", "sanctions", "tariff_duty",
    "tax_change_adopted", "commodity_price", "supply_disruption", "state_support",
]  # fmt: skip


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("LLM_JOURNAL_DIR", str(tmp_path / "journal"))
    reset_client()
    yield
    reset_client()
    set_tool_source(None)


@pytest.fixture
def bundle():
    return load_bundle("rate_14")


def tool_ctx(source=None) -> ToolContext:
    source = source or FixtureDataSource("rate_14")
    story = source.stories()[0]
    return ToolContext(
        company_id="U_DEMO_BORROWER", role="cfo", story_id=story.id,
        story_published_at=story.last_seen, trace_id="t1", source=source,
    )  # fmt: skip


def planner_entries(client, trace_id):
    return [e for e in client.journal.entries() if e.step == "planner" and e.trace_id == trace_id]


class StubTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[CompletionRequest] = []

    def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return CompletionResponse(text=r, tokens_in=100, tokens_out=50)


def stub_client(tmp_path, replies) -> tuple[LLMClient, StubTransport]:
    s = LLMSettings(
        _env_file=None, llm_mode="live", llm_api_key=SecretStr("k"), llm_api_base="http://x.invalid/v1",
        llm_journal_dir=str(tmp_path / "j"), llm_cache_dir=str(tmp_path / "c"), llm_cache_enabled=False,
    )  # fmt: skip
    tr = StubTransport(replies)
    client = LLMClient(
        settings=s, budget=BudgetManager(settings=s, store=InMemoryBudgetStore()),
        journal=Journal(None), cache=ResponseCache(s.llm_cache_dir, False), transport=tr,
    )  # fmt: skip
    return client, tr


def steps_json(*steps) -> str:
    return json.dumps({"steps": list(steps)})


# --- шаблоны -------------------------------------------------------------------------------------


def test_templates_cover_top10_and_default():
    tf = load_templates()
    for et in [*TOP10, "default"]:
        assert et in tf.templates
    assert tf.max_steps == 8


@pytest.mark.parametrize("event_type", [*TOP10, "default", "unknown_type"])
def test_templates_at_most_8_steps_and_only_sector_tools(event_type):
    nums = [NumberFact(value=10, unit="%", quote="10%"), NumberFact(value=5, unit="%", quote="5%")]
    chans = taxonomy_channels().get(event_type, [])
    steps = expand_template(
        event_type, title="Заголовок", sector="46", numbers=nums, fact_channels=chans[::-1]
    )
    assert 3 <= len(steps) <= 8
    for s in steps:
        assert s.tool in PLANNER_TOOLS and s.tool in TOOLS
        assert s.origin == "template"
        assert "company_id" not in s.args
        assert not re.search(r"\{\w+\}", json.dumps(s.args))  # все подстановки сделаны
        TOOLS[s.tool].input_model.model_validate(s.args)  # аргументы валидны по схеме инструмента


def test_template_uses_event_type_channels_and_skips_calc_without_numbers():
    steps = expand_template("rate_decision", title="t", sector="46")
    bank = [s.args["channel"] for s in steps if s.tool == "get_bank_positions"]
    assert bank == taxonomy_channels()["rate_decision"]
    steps = expand_template("fx_move", title="t", sector="46", numbers=[NumberFact(value=1, unit="%", quote="q")])
    assert all(s.tool != "calculator" for s in steps)
    mixed = [NumberFact(value=14, unit="%", quote="q"), NumberFact(value=10, unit="снижений", quote="q")]
    assert all(s.tool != "calculator" for s in expand_template("fx_move", title="t", sector="46", numbers=mixed))


def test_prompt_has_version_header():
    version, body = load_prompt()
    assert version.startswith("planner-")
    assert "company_id" in body and "<data>" in body


# --- досье по rate_14 ----------------------------------------------------------------------------


def test_dossier_rate_14_has_facts_bank_market_and_tool_calls(bundle):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    dossier, calls = build_dossier(story, facts, "46", tool_ctx(), trace_id="t1", priority=0.9)

    assert dossier.story_id == STORY and dossier.sector == "46" and dossier.event_type == "rate_decision"
    assert dossier.get(STORY).kind == "story"
    summary = dossier.get(f"fact:{STORY}:1")
    assert summary is not None and "14%" in summary.text
    number = dossier.get(f"fact:{STORY}:2")
    assert number.data["value"] == 14.0 and "14%" in number.text  # число с цитатой
    banks = dossier.by_kind("bank_position")
    assert banks and all(i.id.startswith("cmi_") for i in banks)
    assert [i.id for i in dossier.by_kind("market_knowledge")] == ["mk_consensus_2026-09-11"]
    assert dossier.by_kind("news")
    assert calls and all(c.ok for c in calls)
    assert all(set(c.args) == {"sha256"} for c in calls)  # в трассе только хеш аргументов
    assert len(dossier.plan) <= 8 and len(calls) == len(dossier.plan)
    assert dossier.plan[0].origin == "template"
    assert not {s.tool for s in dossier.plan} & {"get_profile", "get_counterparties"}
    # досье полностью сериализуется и читается обратно
    assert Dossier.model_validate(dossier.model_dump(mode="json")).ids() == dossier.ids()


def test_gap_when_no_similar_cases_triggers_one_llm_call_with_prompt_version(bundle):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    client = get_client()
    dossier, _ = build_dossier(story, facts, "46", tool_ctx(), trace_id="t-gap")
    entries = planner_entries(client, "t-gap")
    assert len(entries) == 1
    assert entries[0].prompt_version == load_prompt()[0]
    assert entries[0].validation == "ok"
    assert any(s.origin == "llm" for s in dossier.plan)
    # блок данных: текст материалов уходит в промпт только внутри <data>
    assert [gap_code(g) for g in compute_gaps(dossier)] == [gap_code(g) for g in dossier.gaps]


class RichSource(FixtureDataSource):
    """Фикстура + прошлый сюжет того же типа: пробелов не остаётся."""

    def stories(self):
        base = super().stories()[0]
        past = base.model_copy(
            update={
                "id": "st_2026-07-24_keyrate",
                "title": "ЦБ снизил ставку до 14%",
                "first_seen": base.first_seen.replace(month=7, day=24),
                "last_seen": base.last_seen.replace(month=7, day=24),
            }
        )
        return [base, past]


def test_no_gaps_means_no_llm_call(bundle):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    client = get_client()
    dossier, calls = build_dossier(story, facts, "46", tool_ctx(RichSource("rate_14")), trace_id="t-nogap")
    assert dossier.gaps == []
    assert [i.id for i in dossier.by_kind("case")] == ["st_2026-07-24_keyrate"]
    assert planner_entries(client, "t-nogap") == []
    assert all(s.origin == "template" for s in dossier.plan)
    assert len(calls) == len(dossier.plan)


# --- проверка предложений модели -----------------------------------------------------------------


def test_invalid_llm_steps_are_dropped(bundle, tmp_path):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    good = {"tool": "search_news", "args": {"query": "ожидания по ставке", "limit": 3}, "why": "консенсус"}
    client, _ = stub_client(
        tmp_path,
        [
            steps_json(
                {"tool": "get_profile", "args": {}, "why": "профиль компании"},  # не разбор отрасли
                {"tool": "drop_database", "args": {}},  # нет в реестре
                {"tool": "search_news", "args": {"query": "x", "company_id": "U_OTHER"}},  # company_id
                {"tool": "get_bank_positions", "args": {"company_id": "U_DEMO_BORROWER"}},
                {"tool": "search_news", "args": {"query": "x", "limit": 999}},  # не по схеме
                {"tool": "search_news", "args": {"query": "x", "bogus": 1}},  # лишний аргумент
                good,
                good,  # повтор
            )
        ],
    )
    dossier, calls = build_dossier(story, facts, "46", tool_ctx(), client=client, trace_id="t-bad")
    llm_steps = [s for s in dossier.plan if s.origin == "llm"]
    assert [(s.tool, s.args) for s in llm_steps] == [(good["tool"], good["args"])]
    assert all(c.ok for c in calls)
    assert len(dossier.plan) <= 8


def test_llm_steps_limited_by_free_slots(bundle, tmp_path):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    many = [{"tool": "search_news", "args": {"query": f"запрос {i}"}} for i in range(10)]
    client, _ = stub_client(tmp_path, [steps_json(*many)])
    dossier, _ = build_dossier(story, facts, "46", tool_ctx(), client=client, trace_id="t-many")
    assert len(dossier.plan) == 8


@pytest.mark.parametrize("budget", [False, True])
def test_llm_unavailable_keeps_template_dossier(bundle, tmp_path, budget):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    client, tr = stub_client(tmp_path, [TransportError("недоступно", budget=budget)])
    dossier, calls = build_dossier(story, facts, "46", tool_ctx(), client=client, trace_id="t-down")
    assert tr.requests  # модель вызывали
    assert all(s.origin == "template" for s in dossier.plan)
    assert dossier.by_kind("bank_position") and dossier.by_kind("market_knowledge")
    assert any(gap_code(g) == "no_similar_cases" for g in dossier.gaps)  # пробел остался
    assert len(calls) == len(dossier.plan)


# --- повторное планирование для критика ----------------------------------------------------------


def test_replan_respects_total_limit(bundle, tmp_path):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    client, _ = stub_client(tmp_path, [steps_json()])  # достройка ничего не предлагает
    ctx = tool_ctx()
    dossier, _ = build_dossier(story, facts, "46", ctx, client=client, trace_id="t-rp")
    assert len(dossier.plan) == 7

    three = [{"tool": "search_news", "args": {"query": f"уточнение {i}"}, "why": "критик"} for i in range(3)]
    client2, _ = stub_client(tmp_path, [steps_json(*three)])
    calls = replan(dossier, "нет объяснения, почему ставка важна для оптовой торговли", ctx, client2)
    assert len(calls) == 1 and len(dossier.plan) == 8
    assert dossier.plan[-1].origin == "critic"

    # лимит исчерпан: модель не вызывается, шагов не прибавляется
    client3, tr3 = stub_client(tmp_path, [steps_json(*three)])
    assert replan(dossier, "ещё задача", ctx, client3) == []
    assert tr3.requests == [] and len(dossier.plan) == 8


def test_replan_on_fixture_adds_critic_steps(bundle, tmp_path):
    story, facts = bundle["story"][0], bundle["event_facts"][0]
    ctx = tool_ctx()
    quiet, _ = stub_client(tmp_path, [steps_json()])
    dossier, _ = build_dossier(story, facts, "46", ctx, client=quiet, trace_id="t-rp2")
    assert len(dossier.plan) == 7
    client = get_client()
    calls = replan(dossier, "нужна позиция ЦМИ по траектории ставки", ctx, client)
    assert len(dossier.plan) == 8 and len(calls) == 1  # из двух предложенных влезает один
    assert dossier.plan[-1].origin == "critic"
    assert len(planner_entries(client, "t-rp2")) == 1


# --- обработчик автомата -------------------------------------------------------------------------


async def test_planner_step_in_pipeline_fills_dossier_and_trace():
    handlers = dict(make_stub_handlers())
    handlers[State.PLANNED] = planner_step
    ctx = PipelineContext(story_id=STORY, story_version=1, sector="46", company_id="U_DEMO_BORROWER", role="cfo")
    ctx, trace = await run_pipeline(ctx, handlers)
    assert ctx.state == State.PUBLISHED
    dossier = Dossier.model_validate(ctx.dossier)
    assert dossier.story_id == STORY and dossier.by_kind("bank_position")
    assert trace.tool_calls and len(trace.tool_calls) == len(dossier.plan)
    assert any(c.purpose == "planner" for c in trace.llm_calls)


async def test_planner_step_replans_on_critic_task():
    handlers = dict(make_stub_handlers())
    handlers[State.PLANNED] = planner_step
    ctx = PipelineContext(story_id=STORY, story_version=1, sector="46", company_id="U_DEMO_BORROWER", role="cfo")
    ctx, _ = await run_pipeline(ctx, handlers, stop_after={State.PLANNED})
    before = len(Dossier.model_validate(ctx.dossier).plan)
    ctx.planner_task = "нужна позиция ЦМИ по траектории ставки"
    ctx = planner_step(ctx)
    after = Dossier.model_validate(ctx.dossier).plan
    assert ctx.planner_task is None
    assert len(after) >= before and len(after) <= 8
