from datetime import UTC, datetime
from pathlib import Path

import feedparser
import pytest

from newsai.ingest.normalize import clean_text, content_hash, hamming, simhash, strip_dateline
from newsai.ingest.registry import load_sources
from newsai.ingest.rss import entry_to_item, extract_page

DATA = Path(__file__).parent / "data"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def sources():
    return {s.id: s for s in load_sources("config/sources.yaml")}


def test_registry_has_day1_feeds(sources):
    enabled = {s.id for s in sources.values() if s.enabled and s.kind == "rss"}
    assert {"prime", "interfax", "rbc"} <= enabled
    assert sources["cbr_press"].official and sources["cbr_press"].tier == 0


@pytest.mark.parametrize("name", ["prime", "interfax", "rbc"])
def test_feed_entries_become_items(sources, name):
    feed = feedparser.parse((DATA / f"{name}.xml").read_bytes())
    items = [entry_to_item(sources[name], e, NOW) for e in feed.entries]
    items = [i for i in items if i]
    assert len(items) >= 10
    assert len({i.id for i in items}) == len(items)
    assert all(i.title and i.published_at.tzinfo for i in items)
    if name == "rbc":  # полный текст прямо в ленте
        assert sum(1 for i in items if i.text and len(i.text) > 300) >= len(items) // 2


def test_future_dates_dropped(sources):
    entry = {"title": "Из будущего", "id": "x", "published_parsed": (2030, 1, 1, 0, 0, 0, 0, 1, 0)}
    assert entry_to_item(sources["prime"], entry, NOW) is None


@pytest.mark.parametrize(
    ("page", "city", "starts"),
    [("prime_article", "Москва", "Необходимо ввести"), ("interfax_article", "Москва", "В Энергодаре")],
)
def test_page_extraction_and_dateline(page, city, starts):
    text = clean_text(extract_page((DATA / f"{page}.html").read_bytes()))
    body, got_city = strip_dateline(text)
    assert got_city == city
    assert body.startswith(starts)


def test_simhash_near_duplicates():
    a = "Банк России сохранил ключевую ставку на уровне 14% годовых, сообщил регулятор в пятницу"
    b = "Банк России сохранил ключевую ставку на уровне 14% годовых, сообщил регулятор в пятницу."
    c = "Минфин разместил ОФЗ на 50 млрд рублей при спросе в 120 млрд рублей на аукционе"
    assert hamming(simhash(a), simhash(b)) <= 3
    assert hamming(simhash(a), simhash(c)) > 3
    assert content_hash("Т", a) == content_hash("т", a.replace("ё", "е"))
