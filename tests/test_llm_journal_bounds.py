"""B13: журнал вызовов, журнал разборов и трассы в памяти не растут без предела."""

from backend.llm.journal import Journal, JournalEntry


def _e(trace: str, n: int) -> JournalEntry:
    return JournalEntry(trace_id=trace, step="card", model="m", tokens_in=n)


def test_journal_keeps_last_entries_and_indexes_by_trace():
    j = Journal(max_entries=5)
    for n in range(4):
        j.record(_e("old", n))
    for n in range(4):
        j.record(_e("new", n))
    assert len(j.entries()) == 5
    assert [e.tokens_in for e in j.entries()][:1] == [3]  # самые старые вытеснены
    assert len(j.to_llm_calls("new")) == 4 and len(j.to_llm_calls("old")) == 1
    assert j.cost_by_step("old")["card"]["calls"] == 1
    assert j.by_trace("missing") == []


def test_deep_log_bounded(monkeypatch):
    from backend.api import store as st
    from tests.test_feed_multi import BORROWER, make_store

    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("FEED_MIN_SCORE", "0")
    monkeypatch.setattr(st, "DEEP_LOG_MAX", 3)
    store, _ = make_store(k=0)
    store.list_cards(BORROWER, "cfo")
    feed = store._feeds[(BORROWER, "cfo")]
    for _ in range(10):
        store._log_run(feed, "s_2", "failed", reason="x")
    assert len(store.deep_log) == 3


def test_traces_bounded_but_card_traces_kept(monkeypatch):
    from datetime import UTC, datetime

    from backend.api import store as st
    from backend.schemas import RunTrace
    from tests.test_feed_multi import BORROWER, make_store

    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("FEED_MIN_SCORE", "0")
    monkeypatch.setattr(st, "TRACES_MAX", 10)
    store, _ = make_store(k=0)
    store.list_cards(BORROWER, "cfo")
    card = next(iter(store._cards.values()))
    now = datetime.now(UTC)
    keep = RunTrace(run_id="run_keep", story_id="s", company_id=BORROWER, started_at=now)
    store._add_trace(keep)
    store._cards[card.card_id] = card.model_copy(update={"trace_id": "run_keep"})
    for n in range(30):
        store._add_trace(RunTrace(run_id=f"run_{n}", story_id="s", company_id=BORROWER, started_at=now))
    assert len(store._traces) <= 10 and "run_keep" in store._traces and "run_29" in store._traces
