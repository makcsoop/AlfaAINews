"""Источник данных инструментов из БД Р1: только SELECT, объекты — `model_validate` схем из `data` (JSONB).

Таблицы Р1 хранят полный объект схемы в колонке `data`; запросы идут через text(), чтобы не зависеть
от типов pgvector/JSONB диалекта. Битые строки пропускаются с warning.
"""

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, text

from backend.schemas import (
    BankPosition,
    CompanyProfile,
    CounterpartyGraph,
    EventFacts,
    MarketKnowledge,
    RawItem,
    Source,
    Story,
)

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

RAW_ITEMS_LIMIT = 3000  # свежие не отброшенные новости; поиск по архиву целиком инструментам не нужен


class DbDataSource:
    def __init__(self, engine: Engine, *, raw_items_limit: int = RAW_ITEMS_LIMIT) -> None:
        self._engine = engine
        self.engine = engine  # для модулей Р1 «Банк и рынок» / «Статистик» (подключение к БД, только чтение)
        self._raw_limit = raw_items_limit

    def _rows(self, sql: str, **params: Any) -> list[Any]:
        with self._engine.connect() as conn:
            return [r[0] for r in conn.execute(text(sql), params)]

    def _load(self, schema: type[T], sql: str, **params: Any) -> list[T]:
        out: list[T] = []
        for raw in self._rows(sql, **params):
            try:
                data = json.loads(raw) if isinstance(raw, str | bytes) else raw
                out.append(schema.model_validate(data))
            except (ValueError, ValidationError, TypeError) as e:
                log.warning("пропущена строка %s: %s", schema.__name__, str(e)[:200])
        return out

    def profile(self, company_id: str) -> CompanyProfile | None:
        rows = self._load(
            CompanyProfile, "SELECT data FROM company_profiles WHERE company_id = :cid", cid=company_id
        )
        return next((p for p in rows if p.company_id == company_id), None)

    def counterparties(self, company_id: str) -> CounterpartyGraph | None:
        rows = self._load(
            CounterpartyGraph, "SELECT data FROM counterparty_graphs WHERE company_id = :cid", cid=company_id
        )
        return next((g for g in rows if g.company_id == company_id), None)

    def raw_items(self) -> list[RawItem]:
        return self._load(
            RawItem,
            "SELECT data FROM raw_items WHERE dropped_reason IS NULL ORDER BY published_at DESC LIMIT :n",
            n=self._raw_limit,
        )

    def bank_positions(self) -> list[BankPosition]:
        return self._load(BankPosition, "SELECT data FROM bank_positions")

    def market_knowledge(self) -> list[MarketKnowledge]:
        return self._load(MarketKnowledge, "SELECT data FROM market_knowledge")

    def stories(self) -> list[Story]:
        return self._load(Story, "SELECT data FROM stories")

    def event_facts(self) -> list[EventFacts]:
        return self._load(EventFacts, "SELECT data FROM event_facts")

    def sources(self) -> list[Source]:
        # реестр config/sources.yaml (Р1) с выученными весами из таблицы source_weights
        # (без пересчёта — стартовые)
        try:
            from backend.ingest.source_weights import learned_sources

            with self._engine.connect() as conn:
                return learned_sources(conn)
        except Exception as e:
            log.warning("выученные веса источников недоступны (%s): стартовые веса", e)
        try:
            from backend.ingest.registry import load_sources

            return load_sources()
        except Exception as e:
            log.warning("реестр источников недоступен: %s", e)
            return []
