"""Хранилище карточек за протоколом: фикстура rate_14 или конвейер (по env CARD_STORE)."""

import logging
import os
import threading
from datetime import UTC, datetime
from typing import Protocol

from newsai.agents.steps import AnalysisCache, make_handlers, run_for_client
from newsai.fixtures import load_bundle
from newsai.schemas.common import UserRole
from newsai.schemas.insight import InsightCard, RunTrace


class CardStore(Protocol):
    def list_cards(self, company_id: str, role: UserRole | None) -> list[InsightCard]:
        """Карточки только этой компании; при заданной роли — карточки этой роли."""
        ...

    def get_card(self, card_id: str, company_id: str) -> InsightCard | None:
        """Карточка, только если принадлежит company_id; иначе None."""
        ...

    def card_owner(self, card_id: str) -> str | None:
        """company_id владельца карточки — только для журнала безопасности, наружу не отдаётся."""
        ...

    def get_trace(self, trace_id: str) -> RunTrace | None:
        """Трассировка по id; проверку принадлежности делает вызывающий (по trace.company_id)."""
        ...


class FixtureCardStore:
    def __init__(self, bundle_name: str = "rate_14") -> None:
        bundle = load_bundle(bundle_name)
        self._cards: dict[str, InsightCard] = {c.card_id: c for c in bundle["insight_card"]}  # type: ignore[attr-defined]
        self._traces: dict[str, RunTrace] = {t.run_id: t for t in bundle["run_trace"]}  # type: ignore[attr-defined]

    def list_cards(self, company_id: str, role: UserRole | None) -> list[InsightCard]:
        own = [c for c in self._cards.values() if c.company_id == company_id]
        by_role = [c for c in own if role is None or c.user_role == role]
        # нет карточек под эту роль — показываем карточки компании, а не пустую ленту
        return by_role or own

    def get_card(self, card_id: str, company_id: str) -> InsightCard | None:
        card = self._cards.get(card_id)
        return card if card and card.company_id == company_id else None

    def card_owner(self, card_id: str) -> str | None:
        card = self._cards.get(card_id)
        return card.company_id if card else None

    def get_trace(self, trace_id: str) -> RunTrace | None:
        return self._traces.get(trace_id)


log = logging.getLogger(__name__)

# демо-пары «компания × роль», для которых есть фикстуры ответов модели
DEMO_PAIRS: tuple[tuple[str, str], ...] = (("U_DEMO_BORROWER", "cfo"), ("U_DEMO_DEPOSITOR", "treasurer"))


class PipelineCardStore:
    """Карточки из настоящего конвейера: считаются лениво и кешируются.

    Разбор отрасли (досье и отчёты аналитиков) считается один раз на «сюжет × отрасль» и
    переиспользуется для других ролей и компаний той же отрасли; карточка — на «клиент × роль».
    Ошибка конвейера не роняет запрос: отдаём фикстурную карточку со статусом «на проверке».
    """

    def __init__(self, bundle_name: str = "rate_14") -> None:
        self._bundle_name = bundle_name
        bundle = load_bundle(bundle_name)
        self._story_id: str = bundle["story"][0].id  # type: ignore[attr-defined]
        self._sectors: dict[str, str | None] = {
            p.company_id: p.sector for p in bundle["company_profile"]  # type: ignore[attr-defined]
        }
        self._fallback = FixtureCardStore(bundle_name)
        self._handlers = None
        self.analysis_cache = AnalysisCache()
        self._cards: dict[str, InsightCard] = {}
        self._traces: dict[str, RunTrace] = {}
        self._by_pair: dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()
        self._demo_done = False
        self.runs = 0  # сколько раз запускался конвейер (для тестов и мониторинга)

    # --- вычисление ---

    def _ensure(self, company_id: str, role: str) -> None:
        with self._lock:
            if (company_id, role) in self._by_pair or company_id not in self._sectors:
                return
            sector = self._sectors[company_id] or ""
            if self._handlers is None:
                self._handlers = make_handlers(self._bundle_name, cache=self.analysis_cache)
            self.runs += 1
            try:
                card, trace = run_for_client(
                    self._story_id, company_id, role, sector=sector,
                    handlers=self._handlers,
                )
            except Exception as e:
                log.warning("конвейер для %s/%s упал: %r", company_id, role, e)
                card, trace = self._degraded(company_id, role, e)
            if card is not None:
                self._cards[card.card_id] = card
                self._by_pair[(company_id, role)] = card.card_id
            if trace is not None:
                self._traces[trace.run_id] = trace

    def _degraded(
        self, company_id: str, role: str, exc: Exception
    ) -> tuple[InsightCard | None, RunTrace | None]:
        trace = getattr(exc, "trace", None)
        if trace is None:
            now = datetime.now(UTC)
            trace = RunTrace(
                run_id=f"run_failed_{company_id}_{role}", story_id=self._story_id, company_id=company_id,
                started_at=now, finished_at=now,
            )
        trace.steps.append(f"pipeline_failed:{type(exc).__name__}")
        cards = self._fallback.list_cards(company_id, role)  # type: ignore[arg-type]
        if not cards:
            return None, trace
        card = cards[0].model_copy(update={"review_status": "on_review", "trace_id": trace.run_id})
        return card, trace

    def _ensure_demo(self) -> None:
        with self._lock:
            if self._demo_done:
                return
            for company_id, role in DEMO_PAIRS:
                self._ensure(company_id, role)
            self._demo_done = True

    # --- протокол CardStore ---

    def list_cards(self, company_id: str, role: UserRole | None) -> list[InsightCard]:
        if role is not None:
            self._ensure(company_id, role)
        else:
            for c, r in DEMO_PAIRS:
                if c == company_id:
                    self._ensure(c, r)
        with self._lock:
            own = [c for c in self._cards.values() if c.company_id == company_id]
        by_role = [c for c in own if role is None or c.user_role == role]
        return by_role or own

    def get_card(self, card_id: str, company_id: str) -> InsightCard | None:
        self._ensure_demo()
        with self._lock:
            card = self._cards.get(card_id)
        return card if card and card.company_id == company_id else None

    def card_owner(self, card_id: str) -> str | None:
        self._ensure_demo()
        with self._lock:
            card = self._cards.get(card_id)
        return card.company_id if card else None

    def get_trace(self, trace_id: str) -> RunTrace | None:
        self._ensure_demo()
        with self._lock:
            return self._traces.get(trace_id)


_stores: dict[str, CardStore] = {}
_stores_lock = threading.Lock()


def store_mode() -> str:
    return "fixture" if os.environ.get("CARD_STORE", "pipeline").strip().lower() == "fixture" else "pipeline"


def reset_store() -> None:
    """Сбрасывает кеш хранилищ (тесты, смена фикстур)."""
    with _stores_lock:
        _stores.clear()


def get_store() -> CardStore:
    """FastAPI-зависимость: CARD_STORE=pipeline (по умолчанию) или fixture."""
    mode = store_mode()
    with _stores_lock:
        if mode not in _stores:
            _stores[mode] = PipelineCardStore() if mode == "pipeline" else FixtureCardStore()
        return _stores[mode]
