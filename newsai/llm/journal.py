"""Журнал вызовов LLM: в памяти и JSONL по дням. Ключей и текстов ответов здесь нет."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from newsai.schemas import LlmCall


class JournalEntry(BaseModel):
    trace_id: str
    step: str
    dev: str = "r3"
    model: str
    fallback_used: bool = False
    attempt: int = 1
    tokens_in: int = 0
    tokens_out: int = 0
    cost_rub: float = 0.0
    base: Literal["primary", "fallback"] | None = None  # какой адрес API отработал (сам URL не пишем)
    provider_cost: float | None = None  # стоимость от провайдера, ₽
    provider_cost_raw: float | None = None  # сырое число, если единицы неизвестны
    cached: bool = False
    prompt_version: str | None = None
    prompt_hash: str = ""
    args_hash: str = ""
    validation: Literal["ok", "invalid", "error"] = "ok"
    error: str | None = None
    # live — настоящий вызов; fixture — ответ из фикстуры (тесты), в итог бюджета не идёт.
    # У старых записей поля нет: live, если отработал адрес API (base), иначе fixture.
    mode: Literal["live", "fixture"] | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Journal:
    def __init__(self, directory: str | Path | None = None) -> None:
        self._dir = Path(directory) if directory else None
        self._entries: list[JournalEntry] = []
        self._lock = threading.Lock()

    def record(self, entry: JournalEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            if self._dir is not None:
                try:
                    self._dir.mkdir(parents=True, exist_ok=True)
                    path = self._dir / f"{entry.at.date().isoformat()}.jsonl"
                    with path.open("a", encoding="utf-8") as f:
                        f.write(entry.model_dump_json() + "\n")
                except OSError:
                    pass  # журнал не должен ронять вызов

    def entries(self) -> list[JournalEntry]:
        with self._lock:
            return list(self._entries)

    def by_trace(self, trace_id: str) -> list[dict]:
        return [e.model_dump(mode="json") for e in self.entries() if e.trace_id == trace_id]

    def to_llm_calls(self, trace_id: str) -> list[LlmCall]:
        return [
            LlmCall(
                model=e.model,
                purpose=e.step,
                prompt_hash=e.prompt_hash,
                tokens_in=e.tokens_in,
                tokens_out=e.tokens_out,
                cost_rub=e.cost_rub,
                at=e.at,
                cached=e.cached,
            )
            for e in self.entries()
            if e.trace_id == trace_id
        ]

    def cost_by_step(self, trace_id: str | None = None) -> dict[str, dict[str, float]]:
        """Звонки, токены и ₽ по шагам; с trace_id — только по одному прогону."""
        out: dict[str, dict[str, float]] = {}
        for e in self.entries():
            if trace_id is not None and e.trace_id != trace_id:
                continue
            b = out.setdefault(e.step, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0})
            b["calls"] += 1
            b["tokens_in"] += e.tokens_in
            b["tokens_out"] += e.tokens_out
            b["cost_rub"] += e.cost_rub
        return out
