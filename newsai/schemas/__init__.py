"""Pydantic-схемы всех объектов системы. Импортируйте отсюда: `from newsai.schemas import Story`."""

from newsai.schemas.client import ChannelExposure, CompanyProfile, Counterparty, CounterpartyGraph
from newsai.schemas.common import SCHEMA_VERSION, ChannelImpact, Evidence, NumberFact
from newsai.schemas.insight import (
    Action,
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
from newsai.schemas.knowledge import BankPosition, MarketKnowledge
from newsai.schemas.news import EventFacts, Participant, RawItem, Source, Story

# 13 основных объектов + market_knowledge — для экспорта JSON Schema и проверки фикстур
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
    "InsightCard",
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
