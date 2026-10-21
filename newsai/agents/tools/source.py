"""Источник данных инструментов. БД Р1/Р2 подставляются только через этот протокол."""

from typing import Any, Protocol

from newsai.schemas import (
    BankPosition,
    CompanyProfile,
    CounterpartyGraph,
    EventFacts,
    MarketKnowledge,
    RawItem,
    Source,
    Story,
)


class ToolDataSource(Protocol):
    def profile(self, company_id: str) -> CompanyProfile | None: ...
    def counterparties(self, company_id: str) -> CounterpartyGraph | None: ...
    def raw_items(self) -> list[RawItem]: ...
    def bank_positions(self) -> list[BankPosition]: ...
    def market_knowledge(self) -> list[MarketKnowledge]: ...
    def stories(self) -> list[Story]: ...
    def event_facts(self) -> list[EventFacts]: ...
    def sources(self) -> list[Source]: ...


class FixtureDataSource:
    """Данные из fixtures/<name>/bundle.json."""

    def __init__(self, name: str = "rate_14") -> None:
        from newsai.fixtures import load_bundle

        self._b = load_bundle(name)

    def _all(self, key: str) -> list[Any]:
        return list(self._b.get(key, []))

    def profile(self, company_id: str) -> CompanyProfile | None:
        return next((p for p in self._all("company_profile") if p.company_id == company_id), None)

    def counterparties(self, company_id: str) -> CounterpartyGraph | None:
        return next((g for g in self._all("counterparty_graph") if g.company_id == company_id), None)

    def raw_items(self) -> list[RawItem]:
        return self._all("raw_item")

    def bank_positions(self) -> list[BankPosition]:
        return self._all("bank_position")

    def market_knowledge(self) -> list[MarketKnowledge]:
        return self._all("market_knowledge")

    def stories(self) -> list[Story]:
        return self._all("story")

    def event_facts(self) -> list[EventFacts]:
        return self._all("event_facts")

    def sources(self) -> list[Source]:
        return self._all("source")
