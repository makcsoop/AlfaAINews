"""Простой лимит частоты на сессию: скользящее окно в памяти процесса."""

import threading
import time
from collections import defaultdict, deque

from newsai.security.session import Session


class RateLimiter:
    def __init__(self, limit: int = 20, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def key_of(session: Session) -> str:
        return f"{session.company_id}|{session.user_id}|{session.expires_at}"

    def allow(self, key: str, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= t - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(t)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


ask_limiter = RateLimiter(limit=20, window_s=60.0)
