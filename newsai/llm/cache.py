"""Дисковый кеш ответов по хешу (модель + сообщения + имя схемы + температура)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_json(obj: object) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_key(model: str, messages: list[dict[str, str]], schema_name: str, temperature: float) -> str:
    return sha256_json(
        {"model": model, "messages": messages, "schema": schema_name, "temperature": temperature}
    )


class ResponseCache:
    def __init__(self, directory: str | Path, enabled: bool = True) -> None:
        self._dir = Path(directory)
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, text: str, tokens_in: int, tokens_out: int) -> None:
        if not self.enabled:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {"text": text, "tokens_in": tokens_in, "tokens_out": tokens_out}
            self._path(key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
