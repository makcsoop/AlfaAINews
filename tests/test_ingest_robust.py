"""Сбор не падает целиком из-за одного битого элемента; даты без зоны — UTC, а не местное время."""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

from backend.ingest import rss, run, store
from backend.ingest.tg_archive import _dt
from backend.schemas import Source

SRC = Source(id="rss_test", name="Тест", kind="rss", url="https://example.com/rss", tier=1, weight=0.5)


def test_rss_modified_date_tolerant_and_utc():
    assert rss._modified("Fri, 23 Oct 2026 10:00:00 +0300") == datetime(2026, 10, 23, 7, tzinfo=UTC)
    assert rss._modified("Fri, 23 Oct 2026 10:00:00 -0000") == datetime(2026, 10, 23, 10, tzinfo=UTC)
    assert rss._modified("вчера вечером") is None and rss._modified(None) is None


def test_archive_naive_date_is_utc():
    assert _dt("2026-10-23T10:00:00") == datetime(2026, 10, 23, 10, tzinfo=UTC)
    assert _dt("2026-10-23T10:00:00+03:00") == datetime(2026, 10, 23, 7, tzinfo=UTC)


def test_bad_rss_entry_does_not_drop_whole_feed(monkeypatch):
    saved: list = []
    monkeypatch.setattr(store, "get_state", lambda conn, sid: {})
    monkeypatch.setattr(store, "save_state", lambda conn, sid, **f: None)
    monkeypatch.setattr(store, "known_native_ids", lambda conn, sid, ids: set())
    monkeypatch.setattr(store, "insert_items", lambda conn, items: saved.extend(items) or len(items))
    monkeypatch.setattr(run, "get_engine", lambda: SimpleNamespace(begin=lambda: nullcontext(None)))
    entries = [{"id": "a", "link": "https://example.com/a"}, {"id": "bad"}, {"id": "c", "link": "https://example.com/c"}]
    feed = SimpleNamespace(entries=entries, etag=None, last_modified=None, status=200, bozo=None)

    async def poll(fetcher, src, etag, modified):
        return feed

    def to_item(src, entry, fetched_at, page_text):
        if entry["id"] == "bad":
            raise ValueError("битая дата")
        return SimpleNamespace(id=entry["id"])

    monkeypatch.setattr(run, "poll_feed", poll)
    monkeypatch.setattr(run, "native_id", lambda e: e["id"])
    monkeypatch.setattr(run, "entry_to_item", to_item)
    assert asyncio.run(run.ingest_rss(None, SRC.model_copy(update={"full_text": "feed"}))) == 2
    assert [i.id for i in saved] == ["a", "c"]
