import json

import pytest
from pydantic import SecretStr

from newsai.llm import LLMClient
from newsai.llm.budget import BudgetManager, InMemoryBudgetStore
from newsai.llm.cache import ResponseCache
from newsai.llm.client import CompletionRequest, CompletionResponse, TransportError
from newsai.llm.journal import Journal
from newsai.llm.probe import MAX_TOKENS, run
from newsai.llm.settings import LLMSettings

SECRET = "test-secret-value-not-a-real-key"


class Stub:
    def __init__(self, fail: set[str] | None = None, reply: str = '{"ok": true}'):
        self.fail = fail or set()
        self.reply = reply
        self.requests: list[CompletionRequest] = []

    def complete(self, req: CompletionRequest) -> CompletionResponse:
        self.requests.append(req)
        if req.model.id in self.fail:
            raise TransportError("HTTP 404: model not found")
        return CompletionResponse(
            text=self.reply, tokens_in=30, tokens_out=5, base="primary", provider_cost_rub=0.01
        )


def settings(tmp_path, mode="live", **kw) -> LLMSettings:
    return LLMSettings(
        _env_file=None, llm_mode=mode, llm_api_key=SecretStr(SECRET), llm_api_base="http://p.invalid/v1",
        llm_journal_dir=str(tmp_path / "journal"), llm_cache_dir=str(tmp_path / "cache"), **kw,
    )


def client(tmp_path, transport, **kw) -> LLMClient:
    s = settings(tmp_path, **kw)
    return LLMClient(
        settings=s, budget=BudgetManager(settings=s, store=InMemoryBudgetStore()),
        journal=Journal(s.llm_journal_dir), cache=ResponseCache(s.llm_cache_dir, True), transport=transport,
    )


def test_probe_refuses_in_fixture_mode(tmp_path, capsys):
    stub = Stub()
    c = client(tmp_path, stub, mode="fixture")
    assert run(client=c, out_dir=tmp_path / "out") == 2
    assert "LLM_MODE=live" in capsys.readouterr().err
    assert stub.requests == []


def test_probe_six_calls_no_fallback_and_report(tmp_path, capsys):
    stub = Stub()
    c = client(tmp_path, stub)
    out = tmp_path / "out"
    assert run(client=c, out_dir=out) == 0
    assert len(stub.requests) == 6
    assert {r.model.id for r in stub.requests} == set(c.config.models)
    assert all(r.step == "probe" and r.max_tokens == MAX_TOKENS for r in stub.requests)
    reports = list(out.glob("probe-*.json"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    data = json.loads(text)
    assert len(data["results"]) == 6
    assert data["total_rub"] == pytest.approx(0.06)
    assert all(r["base"] == "primary" and r["error"] is None for r in data["results"])
    assert SECRET not in text
    assert "primary" in capsys.readouterr().out
    assert all(e.dev == "r3" for e in c.journal.entries())
    assert c.budget.group_spent_rub("system") == pytest.approx(0.06)


def test_probe_failed_model_not_replaced(tmp_path):
    stub = Stub(fail={"kimi-k2.6"})
    c = client(tmp_path, stub)
    out = tmp_path / "out"
    assert run(client=c, out_dir=out) == 1
    assert len(stub.requests) == 6  # запасная модель не вызывалась
    data = json.loads(next(out.glob("probe-*.json")).read_text(encoding="utf-8"))
    bad = [r for r in data["results"] if r["error"]]
    assert [r["model_id"] for r in bad] == ["kimi-k2.6"]


def test_probe_selected_models(tmp_path):
    stub = Stub()
    c = client(tmp_path, stub)
    assert run(models="gemma-4-26b,glm-5.3-flash", client=c, out_dir=tmp_path / "o") == 0
    assert len(stub.requests) == 2


def test_probe_dry_run(tmp_path, capsys):
    stub = Stub()
    c = client(tmp_path, stub)
    assert run(dry_run=True, client=c, out_dir=tmp_path / "o") == 0
    assert stub.requests == []
    assert not (tmp_path / "o").exists()
    assert "6 вызовов" in capsys.readouterr().out


def test_probe_refuses_when_budget_empty(tmp_path):
    stub = Stub()
    c = client(tmp_path, stub, llm_daily_budget_rub=0.5)
    assert run(client=c, out_dir=tmp_path / "o") == 3
    assert stub.requests == []
