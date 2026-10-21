"""Условный GET, лимит запросов на хост и кодировки лент — без сети, на MockTransport."""

import time

import httpx
import pytest

from newsai.ingest.http import Fetcher, HostLimiter
from newsai.ingest.rss import poll_feed
from newsai.schemas import Source

SRC = Source(id="t", name="t", kind="rss", url="https://feed.test/rss", tier=1, weight=0.9)

FEED = """<?xml version="1.0" encoding="windows-1251"?>
<rss version="2.0"><channel><title>Тест</title>
<item><title>Минфин разместил ОФЗ</title><link>https://feed.test/1</link><guid>1</guid>
<pubDate>Wed, 21 Oct 2026 13:06:00 +0300</pubDate></item>
</channel></rss>"""


def _fetcher(handler) -> Fetcher:
    f = Fetcher(rps=1000)
    f.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return f


async def test_conditional_get_and_cp1251():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=FEED.encode("cp1251"),
            headers={"ETag": '"v1"', "Last-Modified": "Wed, 21 Oct 2026 10:00:00 GMT"},
        )

    f = _fetcher(handler)
    first = await poll_feed(f, SRC, None, None)
    assert first.status == 200 and first.etag == '"v1"'
    assert first.entries[0]["title"] == "Минфин разместил ОФЗ"

    second = await poll_feed(f, SRC, first.etag, first.last_modified)
    assert second.status == 304 and second.entries == [] and second.etag == '"v1"'
    assert seen[1]["if-none-match"] == '"v1"'
    assert seen[1]["if-modified-since"] == "Wed, 21 Oct 2026 10:00:00 GMT"
    await f.aclose()


async def test_http_error_raises():
    f = _fetcher(lambda req: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await poll_feed(f, SRC, None, None)
    await f.aclose()


async def test_host_limiter_spacing():
    lim = HostLimiter(rps=10)  # 0,1 с между запросами к одному хосту
    t0 = time.monotonic()
    for _ in range(3):
        await lim.wait("https://a.test/x")
    same_host = time.monotonic() - t0
    t1 = time.monotonic()
    await lim.wait("https://b.test/x")
    other_host = time.monotonic() - t1
    assert same_host >= 0.19
    assert other_host < 0.05
