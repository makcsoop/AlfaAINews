"""Обратная связь по карточкам: «полезно», «не про нас», «неверно», клик по источнику.

Хранилище за протоколом: таблица `feedback` Р1 (FEEDBACK_STORE=db; по умолчанию — при TOOL_SOURCE=db)
или память.
В `data` — объект `Feedback` контракта плюс company_id, user_id и story_id из сессии и карточки; в обучение
скоринга идут только явные отметки (решает Р2).
"""

import logging
import os
import threading
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas import Feedback

log = logging.getLogger(__name__)

UserFeedbackKind = Literal["useful", "not_relevant", "wrong", "click_source"]  # expert_edit — только эксперт


class FeedbackRequest(BaseModel):
    """company_id и роль в теле запрещены: они берутся из сессии."""

    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(min_length=1, max_length=300)
    kind: UserFeedbackKind
    comment: str | None = Field(default=None, max_length=1000)
    claim_id: str | None = Field(default=None, max_length=50, description="Утверждение (для «неверно»)")


class FeedbackResponse(BaseModel):
    ok: bool = True
    id: int


class FeedbackStore(Protocol):
    def add(self, feedback: Feedback, extra: dict[str, Any]) -> int: ...

    def list(self, company_id: str) -> list[dict[str, Any]]: ...


class MemoryFeedbackStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, feedback: Feedback, extra: dict[str, Any]) -> int:
        with self._lock:
            self.rows.append({**feedback.model_dump(mode="json"), **extra})
            return len(self.rows)

    def list(self, company_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self.rows if r.get("company_id") == company_id]


class DbFeedbackStore:
    """Запись в таблицу `feedback` Р1: только INSERT пользовательских отметок и SELECT своей компании."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def add(self, feedback: Feedback, extra: dict[str, Any]) -> int:
        from backend.db import models as m

        data = {**feedback.model_dump(mode="json"), **extra}
        with self.engine.begin() as conn:
            res = conn.execute(
                m.feedback.insert()
                .values(card_id=feedback.card_id, kind=feedback.kind, at=feedback.at, data=data)
                .returning(m.feedback.c.id)
            )
            return int(res.scalar_one())

    def list(self, company_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from backend.db import models as m

        with self.engine.connect() as conn:
            rows = conn.execute(select(m.feedback.c.data).order_by(m.feedback.c.id)).scalars().all()
        return [r for r in rows if r.get("company_id") == company_id]


_store: FeedbackStore | None = None
_store_lock = threading.Lock()


def _make_store() -> FeedbackStore:
    default = "db" if os.environ.get("TOOL_SOURCE", "").strip().lower() == "db" else "memory"
    if os.environ.get("FEEDBACK_STORE", default).strip().lower() == "db":
        try:
            from backend.db.engine import get_engine

            return DbFeedbackStore(get_engine())
        except Exception as e:  # БД недоступна — отметки не теряем, пишем в память
            log.warning("FEEDBACK_STORE=db, БД недоступна (%s): память", e)
    return MemoryFeedbackStore()


def get_feedback_store() -> FeedbackStore:
    """FastAPI-зависимость (в тестах подменяется)."""
    global _store
    with _store_lock:
        if _store is None:
            _store = _make_store()
        return _store


def reset_feedback_store() -> None:
    global _store
    with _store_lock:
        _store = None
