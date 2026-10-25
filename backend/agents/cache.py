"""Хранилища кеша разбора и карточек: в памяти или Redis (CACHE_BACKEND=memory|redis).

Redis недоступен — предупреждение и память. Значения — JSON; модели Pydantic
сериализуются через `model_dump(mode="json")` и читаются через `model_validate`.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from backend.schemas.insight import AnalystReport, InsightCard, RunTrace

log = logging.getLogger(__name__)


class KV(Protocol):
    """Минимум клиента Redis, который нам нужен."""

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: str, ex: int | None = None) -> Any: ...


def cache_ttl_s() -> int:
    from backend.llm.settings import LLMSettings

    return LLMSettings().cache_ttl_s


def make_kv(redis_url: str | None = None) -> KV | None:
    """Клиент Redis при CACHE_BACKEND=redis и доступном сервере, иначе None (кеш в памяти)."""
    from backend.llm.settings import LLMSettings

    settings = LLMSettings()
    if settings.cache_backend != "redis":
        return None
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url or settings.redis_url, socket_connect_timeout=2, decode_responses=True
        )
        client.ping()
        return client
    except Exception as exc:
        log.warning("Redis недоступен (%s): кеш разбора и карточек в памяти", exc)
        return None


def _text(raw: Any) -> str:
    return raw.decode() if isinstance(raw, bytes) else str(raw)


class RedisJsonStore:
    """Кеш разбора, карточек и трасс в Redis с TTL."""

    def __init__(self, client: KV, ttl_s: int | None = None) -> None:
        self._r = client
        self._ttl = ttl_s if ttl_s is not None else cache_ttl_s()

    def _put(self, key: str, payload: Any) -> None:
        try:
            self._r.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl)
        except Exception as exc:
            log.warning("не удалось записать %s в Redis: %r", key, exc)

    def _read(self, key: str) -> Any | None:
        try:
            raw = self._r.get(key)
            return None if raw is None else json.loads(_text(raw))
        except Exception as exc:
            log.warning("не удалось прочитать %s из Redis: %r", key, exc)
            return None

    # --- разбор ---

    def get_analysis(self, key: str) -> dict[str, Any] | None:
        data = self._read(f"analysis:{key}")
        if data is None:
            return None
        try:
            return {
                "dossier": data["dossier"],
                "reports": [AnalystReport.model_validate(r) for r in data["reports"]],
                "artifacts": data["artifacts"],
            }
        except Exception as exc:
            log.warning("битая запись analysis:%s в Redis: %r", key, exc)
            return None

    def put_analysis(self, key: str, value: dict[str, Any]) -> None:
        self._put(
            f"analysis:{key}",
            {
                "dossier": value["dossier"],
                "reports": [r.model_dump(mode="json") for r in value["reports"]],
                "artifacts": value["artifacts"],
            },
        )

    # --- карточки и трассы ---

    # версия сюжета в ключе: после существенного изменения сюжета старая глубокая карточка не отдаётся

    def get_card(
        self, story_id: str, company_id: str, role: str, version: int = 1
    ) -> tuple[InsightCard, str | None] | None:
        data = self._read(f"card:{story_id}:v{version}:{company_id}:{role}")
        if data is None:
            return None
        try:
            return InsightCard.model_validate(data["card"]), data.get("run_id")
        except Exception as exc:
            log.warning("битая карточка %s/%s/%s в Redis: %r", story_id, company_id, role, exc)
            return None

    def put_card(
        self,
        story_id: str,
        company_id: str,
        role: str,
        card: InsightCard,
        run_id: str | None,
        version: int = 1,
    ) -> None:
        self._put(
            f"card:{story_id}:v{version}:{company_id}:{role}",
            {"card": card.model_dump(mode="json"), "run_id": run_id},
        )

    def get_trace(self, run_id: str) -> RunTrace | None:
        data = self._read(f"trace:{run_id}")
        if data is None:
            return None
        try:
            return RunTrace.model_validate(data)
        except Exception as exc:
            log.warning("битая трасса %s в Redis: %r", run_id, exc)
            return None

    def put_trace(self, trace: RunTrace) -> None:
        self._put(f"trace:{trace.run_id}", trace.model_dump(mode="json"))
