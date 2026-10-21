"""Источники, сырые материалы, сюжеты и факты событий."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from newsai.schemas.common import (
    ChannelImpact,
    EventStatus,
    Evidence,
    NumberFact,
    Schema,
    Score01,
    SourceTier,
    StoryStatus,
)


class Source(Schema):
    """1. Запись реестра источников (config/sources.yaml)."""

    id: str
    name: str
    kind: Literal["rss", "telegram", "api", "html", "bank_pdf", "manual"]
    url: str | None = None
    tier: SourceTier
    weight: Score01 = Field(description="стартовый вес достоверности, задаётся вручную")
    value: int | None = Field(default=None, ge=1, le=5, description="ценность для бизнеса из sources.md")
    poll_seconds: int = 180
    full_text: Literal["feed", "fetch", "none"] = "fetch"
    is_partial: bool = Field(default=False, description="пейволл: храним только заголовок и лид")
    official: bool = Field(
        default=False, description="официальный источник: одного достаточно для «подтверждено»"
    )
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    audience: str | None = None


class ItemEdit(Schema):
    """Правка материала после публикации (Telegram edit_date, РБК newsModifDate)."""

    at: datetime
    title: str | None = None
    content_hash: str
    deleted: bool = Field(default=False, description="пост удалён — слабый сигнал опровержения")


class RawItem(Schema):
    """2. Нормализованный материал из любой ленты. id = sha1(source_id:native_id)."""

    id: str
    source_id: str
    native_id: str
    tier: SourceTier | None = Field(default=None, description="уровень источника на момент сбора")
    url: str | None = None
    title: str
    lead: str | None = None
    text: str | None = None
    is_partial: bool = False
    published_at: datetime
    updated_at: datetime | None = None
    fetched_at: datetime
    lang: str = "ru"
    source_tags: list[str] = Field(default_factory=list)
    priority: int | None = None
    fwd_from: str | None = None
    attributed_to: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)
    content_hash: str
    simhash: int | None = None
    dropped_reason: str | None = Field(
        default=None, description="почему отсеяно: реклама, дайджест, служебное"
    )
    edits: list[ItemEdit] = Field(default_factory=list)


class Story(Schema):
    """3. Сюжет: склейка материалов об одном событии (окно 72 ч)."""

    id: str
    title: str
    event_type: str | None = None
    status: StoryStatus = "single"
    item_ids: list[str]
    corroboration: int = Field(ge=0, description="число независимых источников, без перепечаток и пересылок")
    source_ids: list[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime
    first_source_id: str | None = None
    entities: list[str] = Field(default_factory=list)
    quality: Score01 | None = Field(
        default=None, description="пре-ранжирование: вес источника, подтверждения, новизна"
    )
    version: int = 1


class Participant(Schema):
    name: str
    role: Literal["actor", "target", "regulator", "counterparty", "other"] = "other"
    entity_type: Literal["ORG", "PER", "LOC", "GOV"] | None = None
    entity_id: str | None = None
    quote: str | None = None


class EventFacts(Schema):
    """4. Структурированное событие сюжета. Каждое поле подкреплено цитатой."""

    story_id: str
    version: int = Field(default=1, description="версия сюжета, по которой извлечены факты")
    event_type: str = Field(description="тип из config/taxonomy.yaml")
    status: EventStatus
    summary: str
    summary_evidence: list[Evidence] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    entities: list[str] = Field(
        default_factory=list, description="нормализованные сущности (Natasha + словари)"
    )
    numbers: list[NumberFact] = Field(default_factory=list)
    event_date: datetime | None = None
    effective_date: datetime | None = None
    geography: list[str] = Field(default_factory=list)
    sectors_okved: list[str] = Field(default_factory=list, description="2-значные коды ОКВЭД")
    commodities: list[str] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=list)
    channels: list[ChannelImpact] = Field(default_factory=list)
    is_rumor: bool = False
    is_intent: bool = False
    contradictions: list[str] = Field(default_factory=list)
    extractor_model: str | None = None
    unverified_fields: list[str] = Field(default_factory=list)
