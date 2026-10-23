"""Публичные страницы t.me/s: разбор постов на сохранённой странице канала ЦБ (без сети)."""

from datetime import UTC, datetime

from backend.ingest.registry import load_sources
from backend.ingest.telegram import message_to_item
from backend.ingest.tme_web import _count, parse_page
from tests.conftest import DATA

SRC = {s.id: s for s in load_sources("config/sources.yaml")}["tg_centralbank_russia"]


def test_parse_saved_page():
    posts = parse_page((DATA / "tme_centralbank.html").read_bytes(), "centralbank_russia")
    assert len(posts) >= 10
    ids = [p.id for p in posts]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(p.date.tzinfo is not None for p in posts)
    texts = [p for p in posts if p.message]
    assert len(texts) >= len(posts) // 2
    assert any(p.views and p.views > 1000 for p in posts)
    item = message_to_item(SRC, "centralbank_russia", texts[0], datetime(2026, 10, 22, tzinfo=UTC))
    assert item.url.startswith("https://t.me/centralbank_russia/") and item.title and item.tier == 0


def test_other_channel_posts_ignored():
    assert parse_page((DATA / "tme_centralbank.html").read_bytes(), "minfin") == []


def test_count_suffixes():
    assert (
        _count("29.6K") == 29600
        and _count("1.2M") == 1_200_000
        and _count("845") == 845
        and _count(None) is None
    )
