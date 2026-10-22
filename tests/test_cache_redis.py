"""Кеш разбора и карточек в Redis (фейковый клиент) и откат в память."""

import logging

from newsai.agents.cache import RedisJsonStore, make_kv
from newsai.agents.steps import AnalysisCache
from newsai.api.store import PipelineCardStore
from newsai.fixtures import load_bundle


class FakeKV:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttl[key] = ex


def _reports():
    return load_bundle("rate_14")["analyst_report"]


def test_analysis_roundtrip_between_caches():
    kv = FakeKV()
    value = {
        "dossier": {"story_id": "s", "items": [{"id": "x"}]},
        "reports": list(_reports()),
        "artifacts": {"analysts": ["a"], "analyst_notes": []},
    }
    AnalysisCache(backend=RedisJsonStore(kv, 100)).put("s:46:1", value)
    assert kv.ttl["analysis:s:46:1"] == 100

    other = AnalysisCache(backend=RedisJsonStore(kv, 100))
    got = other.get("s:46:1")
    assert got is not None and other.hits == 1
    assert got["dossier"] == value["dossier"]
    assert got["reports"] == value["reports"]
    assert got["artifacts"] == value["artifacts"]
    assert AnalysisCache(backend=RedisJsonStore(kv)).get("нет") is None


def test_second_store_reads_cards_from_redis_without_pipeline():
    kv = FakeKV()
    first = PipelineCardStore(kv=kv)
    cards = first.list_cards("U_DEMO_BORROWER", "cfo")
    assert first.runs == 1 and cards
    assert any(k.startswith("card:") for k in kv.data)
    assert any(k.startswith("trace:") for k in kv.data)

    second = PipelineCardStore(kv=kv)
    again = second.list_cards("U_DEMO_BORROWER", "cfo")
    assert second.runs == 0
    assert [c.card_id for c in again] == [c.card_id for c in cards]
    trace = second.get_trace(cards[0].trace_id)
    assert trace is not None and trace.company_id == "U_DEMO_BORROWER"


def test_fallback_to_memory_when_redis_unreachable(monkeypatch, caplog):
    monkeypatch.setenv("CACHE_BACKEND", "redis")
    with caplog.at_level(logging.WARNING):
        assert make_kv("redis://127.0.0.1:1/0") is None
    assert "в памяти" in caplog.text
    monkeypatch.delenv("CACHE_BACKEND")
    assert make_kv() is None
