"""Pydantic-схемы всех объектов системы. Импортируйте отсюда: `from backend.schemas import Story`."""

from backend.schemas.client import ChannelExposure, CompanyProfile, Counterparty, CounterpartyGraph
from backend.schemas.common import SCHEMA_VERSION, ChannelImpact, Evidence, NumberFact
from backend.schemas.insight import (
    Action,
    Analysis,
    AnalystReport,
    Claim,
    Contribution,
    Feedback,
    InsightCard,
    LlmCall,
    RelevanceScore,
    RunTrace,
    ToolCall,
)
from backend.schemas.knowledge import BankPosition, Forecast, MarketKnowledge
from backend.schemas.news import EventFacts, ItemEdit, Participant, RawItem, Source, Story

# Объекты «Контрактов данных» финальной архитектуры + служебные — для экспорта JSON Schema и проверки фикстур
ALL_SCHEMAS = {
    "source": Source,
    "raw_item": RawItem,
    "story": Story,
    "event_facts": EventFacts,
    "company_profile": CompanyProfile,
    "counterparty_graph": CounterpartyGraph,
    "bank_position": BankPosition,
    "relevance_score": RelevanceScore,
    "analyst_report": AnalystReport,
    "analysis": Analysis,
    "claim": Claim,
    "insight_card": InsightCard,
    "run_trace": RunTrace,
    "feedback": Feedback,
    "market_knowledge": MarketKnowledge,
}

__all__ = [
    "ALL_SCHEMAS",
    "SCHEMA_VERSION",
    "Action",
    "Analysis",
    "AnalystReport",
    "BankPosition",
    "ChannelExposure",
    "ChannelImpact",
    "Claim",
    "CompanyProfile",
    "Contribution",
    "Counterparty",
    "CounterpartyGraph",
    "EventFacts",
    "Evidence",
    "Feedback",
    "Forecast",
    "InsightCard",
    "ItemEdit",
    "LlmCall",
    "MarketKnowledge",
    "NumberFact",
    "Participant",
    "RawItem",
    "RelevanceScore",
    "RunTrace",
    "Source",
    "Story",
    "ToolCall",
]
