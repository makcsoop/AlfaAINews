"""HTTP-клиент сбора: вежливый User-Agent и не больше `host_rps` запросов в секунду на хост."""

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from backend.config import get_settings


class HostLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._last.get(host, 0.0) + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


class Fetcher:
    def __init__(self, rps: float | None = None, timeout: float = 20.0):
        s = get_settings()
        self.limiter = HostLimiter(rps or s.ingest_host_rps)
        self.client = httpx.AsyncClient(
            headers={"User-Agent": s.ingest_user_agent, "Accept-Language": "ru"},
            follow_redirects=True,
            timeout=timeout,
        )

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        await self.limiter.wait(url)
        return await self.client.get(url, headers=headers)

    async def aclose(self) -> None:
        await self.client.aclose()
