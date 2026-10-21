"""Очередь глубоких разборов по приоритету с дедупликацией по `analysis_key`.

Повторный push того же разбора не создаёт второй копии: приоритет только растёт
(выше число — раньше достанем). В Redis при равном приоритете порядок не гарантирован,
в памяти — по времени постановки.
"""

from __future__ import annotations

import heapq
import itertools
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from newsai.agents.pipeline import analysis_key

log = logging.getLogger(__name__)

KEY_PREFIX = "newsai:queue:"
ZSET_KEY = KEY_PREFIX + "analysis"
JOBS_KEY = KEY_PREFIX + "jobs"


@dataclass(frozen=True)
class AnalysisJob:
    story_id: str
    story_version: int
    sector: str

    @property
    def key(self) -> str:
        return analysis_key(self.story_id, self.sector, self.story_version)

    def dumps(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def loads(cls, raw: str | bytes) -> AnalysisJob:
        return cls(**json.loads(raw))


class AnalysisQueue(Protocol):
    def push(self, job: AnalysisJob, priority: float) -> bool:
        """True — задание новое; False — уже было в очереди (приоритет мог вырасти)."""
        ...

    def pop(self) -> AnalysisJob | None: ...

    def contains(self, key: str) -> bool: ...

    def __len__(self) -> int: ...


class InMemoryQueue:
    def __init__(self) -> None:
        self._heap: list[tuple[float, int, str]] = []  # (-приоритет, порядковый номер, ключ)
        self._live: dict[str, tuple[float, int, AnalysisJob]] = {}
        self._seq = itertools.count()

    def push(self, job: AnalysisJob, priority: float) -> bool:
        cur = self._live.get(job.key)
        if cur is not None:
            if priority > cur[0]:
                n = next(self._seq)
                self._live[job.key] = (priority, n, job)
                heapq.heappush(self._heap, (-priority, n, job.key))
            return False
        n = next(self._seq)
        self._live[job.key] = (priority, n, job)
        heapq.heappush(self._heap, (-priority, n, job.key))
        return True

    def pop(self) -> AnalysisJob | None:
        while self._heap:
            _, n, key = heapq.heappop(self._heap)
            cur = self._live.get(key)
            if cur is None or cur[1] != n:  # устаревшая запись (приоритет обновили или уже достали)
                continue
            del self._live[key]
            return cur[2]
        return None

    def contains(self, key: str) -> bool:
        return key in self._live

    def __len__(self) -> int:
        return len(self._live)


def _s(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else value


class RedisQueue:
    """Sorted set (ключ разбора → приоритет) + hash с телом задания."""

    def __init__(self, client: Any, zset_key: str = ZSET_KEY, jobs_key: str = JOBS_KEY) -> None:
        self._r = client
        self._z = zset_key
        self._h = jobs_key

    def push(self, job: AnalysisJob, priority: float) -> bool:
        current = self._r.zscore(self._z, job.key)
        if current is not None:
            if priority > float(current):
                self._r.zadd(self._z, {job.key: priority})
            return False
        self._r.hset(self._h, job.key, job.dumps())
        self._r.zadd(self._z, {job.key: priority})
        return True

    def pop(self) -> AnalysisJob | None:
        while True:
            popped = self._r.zpopmax(self._z, 1)
            if not popped:
                return None
            key = _s(popped[0][0])
            raw = self._r.hget(self._h, key)
            self._r.hdel(self._h, key)
            if raw is not None:
                return AnalysisJob.loads(raw)
            log.warning("в очереди есть %s, но нет тела задания; пропускаю", key)

    def contains(self, key: str) -> bool:
        return self._r.zscore(self._z, key) is not None

    def __len__(self) -> int:
        return int(self._r.zcard(self._z))


def make_queue(redis_url: str | None = None) -> AnalysisQueue:
    """Redis, если он доступен, иначе очередь в памяти (с предупреждением)."""
    if redis_url:
        try:
            import redis

            client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
            client.ping()
            return RedisQueue(client)
        except Exception as exc:
            log.warning("Redis недоступен (%s): очередь разборов в памяти, при перезапуске теряется", exc)
    else:
        log.warning("redis_url не задан: очередь разборов в памяти")
    return InMemoryQueue()
