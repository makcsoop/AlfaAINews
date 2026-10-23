"""Порт JsonLLM модулей Р1 поверх LLM-клиента: шаг по назначению, журнал, бюджет, отказ без исключений."""

import json

from backend.extract.llm_port import JsonLLM
from backend.llm.port import ClientJsonLLM
from tests.test_llm_client import make_client

SCHEMA = {"type": "object", "required": ["event_type"], "properties": {"event_type": {"type": "string"}}}


def _fixture(tmp_path, step: str, key: str, body: dict | str) -> None:
    d = tmp_path / "fixtures" / step
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def _llm(tmp_path, **kw) -> ClientJsonLLM:
    return ClientJsonLLM(client=make_client(tmp_path, None, mode="fixture"), **kw)


def test_extract_goes_through_client_and_journal(tmp_path):
    _fixture(tmp_path, "extract", "extract", {"event_type": "rate_decision", "extra": 1})
    llm = _llm(tmp_path)
    port: JsonLLM = llm
    out = port.complete_json(
        model="google/gemma-4-26b-a4b-it", system="s", user="u", json_schema=SCHEMA, purpose="extract"
    )
    assert out == {"event_type": "rate_decision", "extra": 1}
    entries = llm.client.journal.entries()
    assert entries and entries[-1].step == "extract" and entries[-1].dev == "r1"
    assert llm.usage.calls == 1 and llm.usage.failures == 0


def test_purpose_selects_budget_step(tmp_path):
    _fixture(tmp_path, "bank_registry", "bank_positions", {"event_type": "x"})
    _fixture(tmp_path, "knowledge_archive", "consensus", {"event_type": "y"})
    llm = _llm(tmp_path)
    assert llm.complete_json(system="s", user="u", json_schema=SCHEMA, purpose="bank_positions") == {
        "event_type": "x"
    }
    assert llm.complete_json(system="s", user="u", json_schema=SCHEMA, purpose="consensus") == {
        "event_type": "y"
    }
    assert [e.step for e in llm.client.journal.entries()][-2:] == ["bank_registry", "knowledge_archive"]


def test_missing_required_field_returns_none(tmp_path):
    _fixture(tmp_path, "extract", "extract", {"other": 1})
    llm = _llm(tmp_path)
    assert llm.complete_json(system="s", user="u", json_schema=SCHEMA, purpose="extract") is None
    assert llm.usage.failures == 1


def test_call_cap_stops_without_calls(tmp_path):
    _fixture(tmp_path, "extract", "extract", {"event_type": "a"})
    llm = _llm(tmp_path, max_calls=1)
    assert llm.complete_json(system="s", user="u", json_schema=SCHEMA, purpose="extract")
    assert llm.complete_json(system="s", user="u", json_schema=SCHEMA, purpose="extract") is None
    assert llm.usage.calls == 1


def test_other_allowed_model_is_probe_and_unknown_model_refused(tmp_path):
    llm = _llm(tmp_path)
    assert llm._route("extract", "qwen/qwen3.6-35b-a3b") == ("extract", None)  # запасная шага
    assert llm._route("extract", "z-ai/glm-5.3-flash") == ("probe", "z-ai/glm-5.3-flash")
    assert (
        llm.complete_json(model="openai/gpt-x", system="s", user="u", json_schema=SCHEMA, purpose="extract")
        is None
    )
