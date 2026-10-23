"""Инструменты агентов на фикстуре rate_14: схемы, границы дат, изоляция компании."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from backend.agents.tools import (
    TOOLS,
    FixtureDataSource,
    ToolContext,
    ToolError,
    describe_tools,
    run_tool,
)
from backend.schemas import MarketKnowledge, ToolCall

STORY = "st_2026-09-11_keyrate"
COMPANY = "U_DEMO_BORROWER"


class Source(FixtureDataSource):
    """Фикстура + дополнительные знания для проверки фильтров."""

    def __init__(self, extra_mk=()):
        super().__init__("rate_14")
        self._extra = list(extra_mk)

    def market_knowledge(self):
        return super().market_knowledge() + self._extra


def mk(id_, *, published_at, timing="before_event", origin="telegram", kind="mechanism"):
    return MarketKnowledge(
        id=id_,
        kind=kind,
        source_id="tg_x",
        item_id="it",
        published_at=published_at,
        event_type="rate_decision",
        channel="key_rate",
        statement="s",
        origin=origin,
        timing=timing,
    )


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext.for_story(
        FixtureDataSource(), story_id=STORY, company_id=COMPANY, role="cfo", trace_id="tr1"
    )


def ctx_with(source, **kw) -> ToolContext:
    return ToolContext.for_story(
        source, story_id=STORY, company_id=COMPANY, role="cfo", trace_id="tr1", **kw
    )


def test_every_tool_answers_by_schema(ctx):
    calls = {
        "get_profile": {},
        "get_counterparties": {"top": 5},
        "search_news": {"query": "ключевая ставка", "limit": 5},
        "get_bank_positions": {"channel": "key_rate"},
        "get_market_knowledge": {},
        "find_similar_cases": {"event_type": "rate_decision"},
        "calculator": {"expression": "14 - 13.5"},
    }
    assert set(calls) == set(TOOLS)
    for name, args in calls.items():
        out, call = run_tool(ctx, name, args)
        assert isinstance(out, TOOLS[name].output_model)
        assert call.ok and call.tool == name and call.result_hash


def test_describe_tools_has_schemas_without_company_id():
    desc = describe_tools()
    assert {d["name"] for d in desc} == set(TOOLS)
    for d in desc:
        assert "company_id" not in d["input_schema"].get("properties", {})
        json.dumps(d)


def test_profile_has_no_absolute_revenue(ctx):
    out, _ = run_tool(ctx, "get_profile", {})
    dumped = json.dumps(out.model_dump(mode="json"))
    assert "42000000000" not in dumped and "revenue\"" not in dumped
    assert out.revenue_rank_in_okved == 3 and out.revenue_peers_in_okved == 57
    assert out.revenue_top_share == pytest.approx(3 / 57, abs=1e-3)
    assert out.foreign_trade is True and out.foreign_trade_country == "Китай"
    assert out.okved_code == "46.69" and {e.channel for e in out.exposures} >= {"key_rate"}
    assert COMPANY not in dumped


def test_counterparties_without_amounts(ctx):
    out, _ = run_tool(ctx, "get_counterparties", {})
    dumped = json.dumps(out.model_dump(mode="json"))
    for banned in ("pay_amt", "pay_cnt", "5200000000", "7100000000", "412"):
        assert banned not in dumped
    assert [c.share for c in out.counterparties if c.direction == "supplier"] == [0.35, 0.12]
    assert out.hhi_suppliers == 0.16
    only_buyers, _ = run_tool(ctx, "get_counterparties", {"direction": "buyer", "top": 1})
    assert len(only_buyers.counterparties) == 1


def test_counterparty_person_id_hidden():
    class Src(FixtureDataSource):
        def counterparties(self, company_id):
            g = super().counterparties(company_id)
            g.suppliers[0].via_person = True
            return g

    c = ctx_with(Src())
    out, _ = run_tool(c, "get_counterparties", {"direction": "supplier"})
    assert out.counterparties[0].counterparty_id is None
    assert out.counterparties[1].counterparty_id == "U_DEMO_S2"


def test_search_news_respects_story_boundary(ctx):
    out, _ = run_tool(ctx, "search_news", {"query": "ключевая ставка 14%"})
    assert out.hits and all(h.published_at <= ctx.story_published_at for h in out.hits)
    early = ToolContext(
        company_id=COMPANY,
        role="cfo",
        story_id=STORY,
        story_published_at=datetime(2026, 9, 11, 10, 30, 30, tzinfo=UTC),
        trace_id="t",
        source=ctx.source,
    )
    out2, _ = run_tool(early, "search_news", {"query": "ключевая ставка 14%", "limit": 10})
    assert len(out2.hits) == 1 and out2.hits[0].source_id == "cbr_press" and out2.hits[0].tier == 0
    before_all = ToolContext(
        company_id=COMPANY,
        role="cfo",
        story_id="x",
        story_published_at=datetime(2026, 1, 1, tzinfo=UTC),
        trace_id="t",
        source=ctx.source,
    )
    assert run_tool(before_all, "search_news", {"query": "ставка"})[0].hits == []


def test_bank_positions_filtered_by_date_and_channel(ctx):
    out, _ = run_tool(ctx, "get_bank_positions", {"channel": "key_rate"})
    assert len(out.positions) == 5
    assert all(p.quote and p.page >= 1 for p in out.positions)
    q, _ = run_tool(ctx, "get_bank_positions", {"query": "ключевую ставку 13,5%"})
    assert q.positions and all(p.channel == "key_rate" for p in q.positions)
    old = ToolContext(
        company_id=COMPANY,
        role="cfo",
        story_id=STORY,
        story_published_at=datetime(2026, 8, 15, tzinfo=UTC),
        trace_id="t",
        source=ctx.source,
    )
    out_old, _ = run_tool(old, "get_bank_positions", {})
    assert [p.id for p in out_old.positions] == ["cmi_macro_1h26#p1#c1"]
    assert run_tool(ctx, "get_bank_positions", {"channel": "nope"})[0].positions == []


def test_market_knowledge_filters_after_story_and_after_event():
    t0 = datetime(2026, 9, 11, 10, 31, tzinfo=UTC)  # = story.last_seen
    src = Source(
        [
            mk("mk_ok", published_at=t0 - timedelta(hours=1)),
            mk("mk_late", published_at=t0 + timedelta(minutes=1)),
            mk("mk_at_boundary", published_at=t0),
            mk("mk_after_event", published_at=t0 - timedelta(hours=2), timing="after_event"),
            mk("mk_site", published_at=t0 - timedelta(hours=3), origin="site"),
        ]
    )
    out, _ = run_tool(ctx_with(src), "get_market_knowledge", {})
    ids = {k.id for k in out.knowledge}
    assert ids == {"mk_fixture_consensus_2026-09-11", "mk_ok", "mk_site"}
    assert all(k.timing == "before_event" for k in out.knowledge)
    assert all(k.origin for k in out.knowledge)


def test_market_knowledge_telegram_switch():
    t0 = datetime(2026, 9, 11, 10, 31, tzinfo=UTC)
    src = Source([mk("mk_site", published_at=t0 - timedelta(hours=3), origin="site")])
    out, _ = run_tool(ctx_with(src, allow_telegram_origin=False), "get_market_knowledge", {})
    assert [k.id for k in out.knowledge] == ["mk_site"]
    only_kind, _ = run_tool(ctx_with(src), "get_market_knowledge", {"kind": "expectation"})
    assert [k.id for k in only_kind.knowledge] == ["mk_fixture_consensus_2026-09-11"]


def test_similar_cases_empty_on_fixture_and_before_story(ctx):
    out, _ = run_tool(ctx, "find_similar_cases", {"event_type": "rate_decision", "sector": "46"})
    assert out.cases == []  # единственный сюжет — текущий


def test_company_id_in_args_forbidden(ctx):
    with pytest.raises(ToolError) as e:
        run_tool(ctx, "get_profile", {"company_id": "OTHER"})
    assert e.value.code == "forbidden_company"
    assert isinstance(e.value.call, ToolCall) and e.value.call.ok is False
    with pytest.raises(ToolError) as e2:
        run_tool(ctx, "get_counterparties", {"company_id": COMPANY})
    assert e2.value.code == "bad_args" and e2.value.call.ok is False


def test_unknown_tool_and_bad_args(ctx):
    with pytest.raises(ToolError) as e:
        run_tool(ctx, "delete_everything", {})
    assert e.value.code == "unknown_tool" and e.value.call.ok is False
    for name, args in [
        ("get_profile", {"x": 1}),
        ("get_counterparties", {"top": 999}),
        ("search_news", {}),
        ("calculator", {"expression": ""}),
    ]:
        with pytest.raises(ToolError) as e:
            run_tool(ctx, name, args)
        assert e.value.code == "bad_args" and e.value.call.ok is False


def test_missing_company_is_not_found():
    c = ToolContext.for_story(
        FixtureDataSource(), story_id=STORY, company_id="U_NOBODY", role="cfo", trace_id="t"
    )
    with pytest.raises(ToolError) as e:
        run_tool(c, "get_profile", {})
    assert e.value.code == "not_found"


def test_tool_call_contains_hashes_not_raw_args(ctx):
    secret_query = "уникальный запрос 12345"
    out, call = run_tool(ctx, "search_news", {"query": secret_query, "limit": 5})
    blob = call.model_dump_json()
    assert secret_query not in blob and "12345" not in blob
    assert len(call.args["sha256"]) == 64 and len(call.result_hash) == 64
    _, call2 = run_tool(ctx, "search_news", {"limit": 5, "query": secret_query})
    assert call2.args == call.args  # канонический JSON не зависит от порядка ключей
    _, call3 = run_tool(ctx, "search_news", {"query": "другой"})
    assert call3.args != call.args
    assert out is not None
