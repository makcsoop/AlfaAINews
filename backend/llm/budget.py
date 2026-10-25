"""Бюджет LLM: учёт ₽ и токенов за сутки (сброс в 03:00 МСК), режимы экономии, потолок архива."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal, Protocol

from backend.llm.models import ModelsConfig, default_models_config
from backend.llm.settings import LLMSettings

log = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
RESET_HOUR_MSK = 3

Mode = Literal["normal", "save70", "save90"]

DEEP_STEPS = frozenset({"planner", "analyst_business", "analyst_finance", "critic_llm"})
ARCHIVE_STEPS = frozenset({"knowledge_archive"})
SAVE90_ALLOWED = frozenset({"qa"})
ARCHIVE_GROUP = "archive"

SAVE70_FRACTION = 0.70
SAVE90_FRACTION = 0.90
DEFAULT_PRIORITY_THRESHOLD = 0.5

DEVS = ("r1", "r2", "r3", "system")


class BudgetExceeded(Exception):
    def __init__(self, step: str, mode: str):
        self.step = step
        self.mode = mode
        super().__init__(f"бюджет LLM: шаг {step} запрещён (режим {mode})")


class BudgetStore(Protocol):
    def incr(self, day: str, key: str, value: float) -> None: ...

    def snapshot(self, day: str) -> dict[str, float]: ...


class InMemoryBudgetStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    def incr(self, day: str, key: str, value: float) -> None:
        with self._lock:
            d = self._data.setdefault(day, {})
            d[key] = d.get(key, 0.0) + value

    def snapshot(self, day: str) -> dict[str, float]:
        with self._lock:
            return dict(self._data.get(day, {}))


class RedisBudgetStore:
    """Хранилище в Redis: хеш `llm:budget:<день>` на каждые сутки, живёт 3 дня.

    Redis отвалился после старта — учёт не падает: расход копится в памяти и доезжает в Redis при первой
    удачной записи, а снимок — последний известный из Redis плюс накопленное (консервативно, не ноль).
    """

    def __init__(self, url: str, prefix: str = "llm:budget", client: object | None = None) -> None:
        if client is None:
            import redis

            client = redis.Redis.from_url(url, decode_responses=True)
        self._r = client
        self._prefix = prefix
        self._pending = InMemoryBudgetStore()  # не записанное в Redis за время сбоя
        self._last: dict[str, dict[str, float]] = {}  # последний удачный снимок по суткам
        self._lock = threading.Lock()

    def _key(self, day: str) -> str:
        return f"{self._prefix}:{day}"

    def _write(self, day: str, key: str, value: float) -> None:
        k = self._key(day)
        self._r.hincrbyfloat(k, key, value)  # type: ignore[attr-defined]
        self._r.expire(k, 3 * 24 * 3600)  # type: ignore[attr-defined]

    def _flush(self, day: str) -> None:
        with self._lock:
            pending = self._pending.snapshot(day)
            if not pending:
                return
            for key, value in pending.items():
                self._write(day, key, value)
            self._pending._data.pop(day, None)
        log.warning("бюджет LLM: Redis снова доступен, расход за время сбоя записан")

    def incr(self, day: str, key: str, value: float) -> None:
        try:
            self._flush(day)
            self._write(day, key, value)
        except Exception as e:  # сбой Redis не должен ронять уже оплаченный вызов
            log.warning("бюджет LLM: Redis недоступен (%s), расход копится в памяти", type(e).__name__)
            self._pending.incr(day, key, value)

    def snapshot(self, day: str) -> dict[str, float]:
        try:
            raw = self._r.hgetall(self._key(day))  # type: ignore[attr-defined]
            snap = {str(k): float(v) for k, v in raw.items()}
            self._last[day] = snap
        except Exception as e:
            log.warning("бюджет LLM: Redis недоступен (%s), последний известный снимок", type(e).__name__)
            snap = dict(self._last.get(day, {}))
        for key, value in self._pending.snapshot(day).items():
            snap[key] = snap.get(key, 0.0) + value
        return snap


def shared_store(settings: LLMSettings) -> BudgetStore:
    """Redis — общий учёт всех процессов (backend, ingest, скрипты), переживает перезапуск; иначе память."""
    try:
        import redis

        r = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        r.ping()
        return RedisBudgetStore(settings.redis_url, client=r)
    except Exception:
        log.warning("бюджет LLM: Redis недоступен, учёт только в памяти процесса")
        return InMemoryBudgetStore()


def budget_day(now: datetime) -> str:
    """Бюджетные сутки идут с 03:00 МСК до 03:00 МСК."""
    local = now.astimezone(MSK) - timedelta(hours=RESET_HOUR_MSK)
    return local.date().isoformat()


class BudgetManager:
    def __init__(
        self,
        *,
        config: ModelsConfig | None = None,
        settings: LLMSettings | None = None,
        store: BudgetStore | None = None,
        clock: Callable[[], datetime] | None = None,
        priority_threshold: float = DEFAULT_PRIORITY_THRESHOLD,
    ) -> None:
        self._config = config or default_models_config()
        s = settings or LLMSettings()
        self.daily_limit_rub = s.llm_daily_budget_rub
        self.archive_cap_rub = s.llm_archive_daily_cap_rub
        self._store: BudgetStore = store or InMemoryBudgetStore()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.priority_threshold = priority_threshold
        # расход ключа всей командой по данным провайдера (заголовок LiteLLM), по суткам бюджета
        self._key_spend: dict[str, float] = {}

    # --- чтение ---
    def _day(self) -> str:
        return budget_day(self._clock())

    def spent_rub(self) -> float:
        """Потрачено за сутки: больше из своего учёта и расхода ключа всей командой у провайдера."""
        day = self._day()
        return max(self._store.snapshot(day).get("rub", 0.0), self._key_spend.get(day, 0.0))

    def own_spent_rub(self) -> float:
        return self._store.snapshot(self._day()).get("rub", 0.0)

    def observe_key_spend(self, rub: float) -> None:
        """Расход ключа от провайдера: общий для команды, режимы 70%/90% считаются по нему."""
        day = self._day()
        self._key_spend[day] = max(self._key_spend.get(day, 0.0), rub)

    def remaining_rub(self) -> float:
        return max(0.0, self.daily_limit_rub - self.spent_rub())

    def group_spent_rub(self, group: str) -> float:
        return self._store.snapshot(self._day()).get(f"group:{group}:rub", 0.0)

    def mode(self) -> Mode:
        if self.daily_limit_rub <= 0:
            return "save90"
        share = self.spent_rub() / self.daily_limit_rub
        if share >= SAVE90_FRACTION:
            return "save90"
        if share >= SAVE70_FRACTION:
            return "save70"
        return "normal"

    # --- проверка ---
    def check(self, step: str, priority: float | None = None) -> None:
        """Бросает BudgetExceeded, если шаг сейчас запрещён.

        priority=None считается низким приоритетом (0.0): глубокий разбор без указанного
        приоритета в режиме save70 не запускается.
        """
        group = self._config.group_of(step)  # KeyError для неизвестного шага
        mode = self.mode()
        if self.spent_rub() >= self.daily_limit_rub:
            raise BudgetExceeded(step, "save90")
        if group == ARCHIVE_GROUP and self.group_spent_rub(group) >= self.archive_cap_rub:
            raise BudgetExceeded(step, "archive_cap")
        if mode == "save90" and step not in SAVE90_ALLOWED:
            raise BudgetExceeded(step, mode)
        if mode == "save70":
            if step in ARCHIVE_STEPS:
                raise BudgetExceeded(step, mode)
            if step in DEEP_STEPS and (priority if priority is not None else 0.0) < self.priority_threshold:
                raise BudgetExceeded(step, mode)

    # --- запись ---
    def record(self, step: str, dev: str, tokens_in: int, tokens_out: int, cost_rub: float) -> None:
        day = self._day()
        group = self._config.group_of(step)
        for dim in (f"step:{step}", f"group:{group}", f"dev:{dev}", "total"):
            prefix = "" if dim == "total" else f"{dim}:"
            self._store.incr(day, f"{prefix}rub" if prefix else "rub", cost_rub)
            self._store.incr(day, f"{prefix}tin" if prefix else "tin", tokens_in)
            self._store.incr(day, f"{prefix}tout" if prefix else "tout", tokens_out)
            self._store.incr(day, f"{prefix}calls" if prefix else "calls", 1)

    def summary(self) -> dict:
        day = self._day()
        snap = self._store.snapshot(day)

        def bucket(dim: str) -> dict[str, dict[str, float]]:
            out: dict[str, dict[str, float]] = {}
            for key, val in snap.items():
                parts = key.split(":")
                if len(parts) == 3 and parts[0] == dim:
                    out.setdefault(parts[1], {})[parts[2]] = val
            return out

        spent = self.spent_rub()
        return {
            "day": day,
            "mode": self.mode(),
            "daily_limit_rub": self.daily_limit_rub,
            "spent_rub": round(spent, 6),
            "own_spent_rub": round(snap.get("rub", 0.0), 6),
            "key_spend_rub": round(self._key_spend[day], 6) if day in self._key_spend else None,
            "remaining_rub": round(max(0.0, self.daily_limit_rub - spent), 6),
            "tokens_in": int(snap.get("tin", 0)),
            "tokens_out": int(snap.get("tout", 0)),
            "calls": int(snap.get("calls", 0)),
            "archive_cap_rub": self.archive_cap_rub,
            "archive_spent_rub": round(snap.get("group:archive:rub", 0.0), 6),
            "by_step": bucket("step"),
            "by_group": bucket("group"),
            "by_dev": bucket("dev"),
        }
