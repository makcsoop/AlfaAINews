"""RSS → RawItem: условный GET, разбор ленты, полный текст из ленты или со страницы."""

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import feedparser
import trafilatura

from backend.ingest.http import Fetcher
from backend.ingest.normalize import clean_text, content_hash, item_id, simhash, strip_dateline
from backend.schemas import RawItem, Source
from backend.stories.filters import drop_reason

log = logging.getLogger(__name__)

FUTURE_TOLERANCE = timedelta(minutes=10)


@dataclass
class FeedResult:
    status: int
    entries: list[dict]
    etag: str | None
    last_modified: str | None
    bozo: str | None = None


async def poll_feed(fetcher: Fetcher, src: Source, etag: str | None, last_modified: str | None) -> FeedResult:
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    assert src.url, f"{src.id}: у ленты нет url"
    resp = await fetcher.get(src.url, headers=headers)
    if resp.status_code == 304:
        return FeedResult(304, [], etag, last_modified)
    resp.raise_for_status()
    # Байты, а не строка: кодировку (местами windows-1251) feedparser определит сам
    parsed = feedparser.parse(resp.content)
    bozo = str(parsed.get("bozo_exception")) if parsed.bozo else None
    return FeedResult(
        resp.status_code,
        parsed.entries,
        resp.headers.get("etag") or etag,
        resp.headers.get("last-modified") or last_modified,
        bozo,
    )


def native_id(entry: dict) -> str:
    return str(entry.get("rbc_news_news_id") or entry.get("id") or entry.get("link"))


def _published(entry: dict) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), UTC) if t else None


def entry_to_item(
    src: Source, entry: dict, fetched_at: datetime, page_text: str | None = None
) -> RawItem | None:
    title = clean_text(entry.get("title"))
    published = _published(entry)
    if not title or not published:
        return None
    if published > fetched_at + FUTURE_TOLERANCE:
        log.warning("%s: дата из будущего %s — пропуск: %s", src.id, published, title)
        return None

    lead = clean_text(entry.get("rbc_news_anons") or entry.get("summary"))
    text = clean_text(entry.get("rbc_news_full-text")) if src.full_text == "feed" else page_text
    city = None
    if text:
        text, city = strip_dateline(text)
    if lead and lead == title:
        lead = None

    nid = native_id(entry)
    updated = entry.get("rbc_news_newsmodifdate")
    priority = entry.get("rian_priority")
    item = RawItem(
        id=item_id(src.id, nid),
        source_id=src.id,
        native_id=nid,
        url=entry.get("link"),
        title=title,
        lead=lead,
        text=text,
        is_partial=src.is_partial or not text,
        published_at=published,
        updated_at=parsedate_to_datetime(updated).astimezone(UTC) if updated else None,
        fetched_at=fetched_at,
        source_tags=[t["term"] for t in entry.get("tags", []) if t.get("term")]
        + ([f"city:{city}"] if city else []),
        priority=int(priority) if priority and str(priority).isdigit() else None,
        content_hash=content_hash(title, text or lead),
        simhash=simhash(f"{title}\n{lead or ''}\n{text or ''}"),
        tier=src.tier,
    )
    item.dropped_reason = drop_reason(item)
    return item


def extract_page(html: bytes, url: str | None = None) -> str | None:
    return trafilatura.extract(
        html, url=url, favor_precision=True, include_comments=False, include_tables=False, deduplicate=True
    )


async def fetch_text(fetcher: Fetcher, url: str) -> str | None:
    try:
        resp = await fetcher.get(url)
        resp.raise_for_status()
    except Exception as e:  # сетевые сбои статьи не должны ронять ленту
        log.info("не скачалась статья %s: %s", url, e)
        return None
    return clean_text(extract_page(resp.content, url))
