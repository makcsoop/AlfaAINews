"""Сквозной прогон rate_14: конвейер на LLM-фикстурах -> хранилище -> API (лента, карточка, трасса, ₽ по шагам)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.agents.pipeline import PipelineContext, State, run_pipeline
from backend.agents.steps import make_handlers, run_for_client
from backend.api.main import app
from backend.api.store import get_store, reset_store
from backend.llm import LLMUnavailable, get_client, reset_client
from backend.security.audit import AuditLog, get_audit

BORROWER = "U_DEMO_BORROWER"
DEPOSITOR = "U_DEMO_DEPOSITOR"
STORY = "st_2026-09-11_keyrate"
DEEP_STEPS = {"planner", "analyst_business", "analyst_finance", "card"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("LLM_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CARD_STORE", "pipeline")
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    reset_client()
    reset_store()
    log = AuditLog(tmp_path / "audit")
    app.dependency_overrides[get_audit] = lambda: log
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_audit, None)
    reset_store()
    reset_client()


def login(client, company, role):
    client.cookies.clear()
    r = client.post("/session", json={"company_id": company, "role": role})
    assert r.status_code == 200, r.text


def feed_card(client, company, role):
    login(client, company, role)
    r = client.get("/feed")
    assert r.status_code == 200, r.text
    cards = r.json()
    assert cards and all(c["company_id"] == company for c in cards)
    return cards[0]


def planner_calls() -> int:
    return sum(1 for e in get_client().journal.entries() if e.step == "planner")


def test_feed_card_claims_are_typed_and_grounded(client):
    card = feed_card(client, BORROWER, "cfo")
    assert card["user_role"] == "cfo" and card["event"]["story_id"] == STORY
    claims = card["claims"]
    assert claims
    for c in claims:
        assert c["type"] in {"fact", "bank_view", "market_view", "model_inference", "bank_offer"}
        if c["type"] in {"fact", "bank_view", "market_view"}:
            assert c["evidence"], c["claim_id"]
        else:
            assert c["based_on"], c["claim_id"]
    bank = [c for c in claims if c["type"] == "bank_view"]
    assert bank and all(e["source_id"].startswith("cmi_") for c in bank for e in c["evidence"])
    ids = {c["claim_id"] for c in claims}
    refs = card["what_happened"] + card["why_it_matters"] + card["consequences"] + card["bank_and_market"]
    assert refs and set(refs) <= ids


def test_trace_has_steps_tools_llm_and_cost(client):
    card = feed_card(client, BORROWER, "cfo")
    r = client.get(f"/trace/{card['trace_id']}")
    assert r.status_code == 200
    trace = r.json()
    steps = trace["steps"]
    for state in ("PLANNED", "ANALYZED", "CARD_DRAFTED"):
        assert f"handler:{state}:ok" in steps
    assert steps[-1] == "handler:PUBLISHED:ok"
    assert trace["tool_calls"]
    purposes = [c["purpose"] for c in trace["llm_calls"]]
    assert len(purposes) >= 4 and set(purposes) >= DEEP_STEPS
    assert trace["cost_rub"] == pytest.approx(sum(c["cost_rub"] for c in trace["llm_calls"]))

    cost = client.get(f"/trace/{card['trace_id']}/cost")
    assert cost.status_code == 200
    body = cost.json()
    assert set(body["cost_by_step"]) >= DEEP_STEPS
    assert all(v["calls"] >= 1 and v["tokens_in"] > 0 for v in body["cost_by_step"].values())
    assert body["cost_rub"] == pytest.approx(trace["cost_rub"])
    assert body["budget"]["daily_limit_rub"] > 0 and "spent_rub" in body["budget"]


def test_depositor_gets_different_bank_offer(client):
    def offer(card):
        return [c["text"] for c in card["claims"] if c["type"] == "bank_offer"]

    borrower = feed_card(client, BORROWER, "cfo")
    depositor = feed_card(client, DEPOSITOR, "treasurer")
    assert offer(borrower) and offer(depositor)
    assert offer(borrower) != offer(depositor)
    assert borrower["card_id"] != depositor["card_id"]
    assert [a["text"] for a in depositor["actions"] if a["is_advertising"]] == offer(depositor)


def test_foreign_company_stays_forbidden(client):
    borrower = feed_card(client, BORROWER, "cfo")
    depositor_card = feed_card(client, DEPOSITOR, "treasurer")
    login(client, BORROWER, "cfo")
    assert client.get("/feed", params={"company_id": DEPOSITOR}).status_code == 403
    assert client.get(f"/card/{depositor_card['card_id']}").status_code == 404
    assert client.get(f"/trace/{depositor_card['trace_id']}").status_code == 404
    assert client.get(f"/trace/{depositor_card['trace_id']}/cost").status_code == 404
    assert client.get(f"/card/{borrower['card_id']}").status_code == 200


def test_second_feed_does_not_rerun_analysis(client):
    first = feed_card(client, BORROWER, "cfo")
    calls = len(get_client().journal.entries())
    assert planner_calls() == 1
    again = client.get("/feed").json()[0]
    assert again["card_id"] == first["card_id"] and again["trace_id"] == first["trace_id"]
    assert len(get_client().journal.entries()) == calls
    assert get_store().runs == 1


def test_other_role_reuses_sector_analysis(client):
    first = feed_card(client, BORROWER, "cfo")
    assert planner_calls() == 1
    other = feed_card(client, BORROWER, "ceo")
    assert other["card_id"] != first["card_id"] and other["user_role"] == "ceo"
    assert planner_calls() == 1  # разбор отрасли не пересчитывался
    assert get_store().analysis_cache.hits == 1
    trace = client.get(f"/trace/{other['trace_id']}").json()
    assert any(s.startswith("analysis:cache_hit") for s in trace["steps"])
    assert "planner" not in {c["purpose"] for c in trace["llm_calls"]}


def test_card_comes_from_llm_not_template(client):
    ctx = PipelineContext(
        story_id=STORY, story_version=1, sector="46", company_id=BORROWER, role="cfo", trace_id="t_e2e"
    )
    ctx, trace = asyncio.run(run_pipeline(ctx, make_handlers("rate_14")))
    assert ctx.state == State.PUBLISHED
    art = ctx.artifacts["card"]
    assert art["source"] == "llm" and art["headline"] and not art["fallback_reason"]
    assert "template_card" not in ctx.flags
    assert len(ctx.reports) == 2 and ctx.artifacts["analyst_check_failures"] == 0
    assert [c.purpose for c in trace.llm_calls].count("card") == 1


def _break_analysts(monkeypatch, steps: set[str]) -> None:
    llm = get_client()
    orig = llm.call

    def call(**kw):
        if kw["step"] in steps:
            raise LLMUnavailable(kw["step"], "нет ответа")
        return orig(**kw)

    monkeypatch.setattr(llm, "call", call)


def test_one_analyst_down_continues(client, monkeypatch):
    _break_analysts(monkeypatch, {"analyst_finance"})
    card, trace = run_for_client(STORY, BORROWER, "cfo", sector="46")
    assert card.card_id.endswith("_cfo")
    assert "handler:ANALYZED:ok" in trace.steps and steps_end(trace) == "handler:PUBLISHED:ok"


def test_both_analysts_down_degrades_to_quick_card(client, monkeypatch):
    _break_analysts(monkeypatch, {"analyst_finance", "analyst_business"})
    card, trace = run_for_client(STORY, BORROWER, "cfo", sector="46")
    assert "handler:ANALYZED:error:RuntimeError" in trace.steps
    assert any("DEGRADED" in s for s in trace.steps)
    assert card.company_id == BORROWER  # быстрая карточка вместо разбора


def steps_end(trace) -> str:
    return trace.steps[-1]
