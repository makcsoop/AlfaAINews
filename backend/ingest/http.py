"""HTTP-клиент сбора: вежливый User-Agent, не больше `host_rps` запросов в секунду на хост,
таймаут 10 с и 2 повтора с паузой при сетевом сбое, 429 и 5xx (Retry-After учитывается, не больше 30 с)."""

import asyncio
import logging
import time
from urllib.parse import urlsplit

import httpx

from backend.config import get_settings

log = logging.getLogger(__name__)

RETRIES = 2
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 30.0


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
    def __init__(self, rps: float | None = None, timeout: float = 10.0, retry_pause: float = 2.0):
        s = get_settings()
        self.retry_pause = retry_pause
        self.limiter = HostLimiter(rps or s.ingest_host_rps)
        self.client = httpx.AsyncClient(
            headers={"User-Agent": s.ingest_user_agent, "Accept-Language": "ru"},
            follow_redirects=True,
            timeout=timeout,
        )

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """Ответ сервера; после исчерпания повторов — последний ответ или исключение сети."""
        for attempt in range(RETRIES + 1):
            await self.limiter.wait(url)
            try:
                resp = await self.client.get(url, headers=headers)
            except httpx.TransportError as e:  # таймаут, обрыв, DNS
                if attempt == RETRIES:
                    raise
                log.info("%s: %s — повтор %d", url, type(e).__name__, attempt + 1)
                await asyncio.sleep(self.retry_pause * (attempt + 1))
                continue
            if resp.status_code not in RETRY_STATUS or attempt == RETRIES:
                return resp
            pause = self._retry_after(resp) or self.retry_pause * (attempt + 1)
            log.info("%s: HTTP %d — повтор %d через %.0f с", url, resp.status_code, attempt + 1, pause)
            await asyncio.sleep(pause)
        raise AssertionError("недостижимо")

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        v = resp.headers.get("retry-after", "")
        return min(float(v), MAX_RETRY_AFTER) if v.replace(".", "", 1).isdigit() else None

    async def aclose(self) -> None:
        await self.client.aclose()
