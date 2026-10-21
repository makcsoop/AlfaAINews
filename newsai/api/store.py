"""Хранилище карточек за протоколом: сейчас фикстура rate_14, позже — конвейер."""

from functools import lru_cache
from typing import Protocol

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


@lru_cache(maxsize=1)
def _fixture_store() -> FixtureCardStore:
    return FixtureCardStore()


def get_store() -> CardStore:
    """FastAPI-зависимость: позже подменяется на хранилище конвейера."""
    return _fixture_store()
