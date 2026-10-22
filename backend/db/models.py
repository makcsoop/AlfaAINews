"""Таблицы. Основные поля — колонками для фильтров и индексов, полный объект схемы — в `data` (JSONB)."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

EMBED_DIM = 1024  # deepvk/USER-bge-m3

metadata = MetaData()

feed_state = Table(
    "feed_state",
    metadata,
    Column("source_id", String, primary_key=True),
    Column("etag", String),
    Column("last_modified", String),
    Column("last_polled_at", DateTime(timezone=True)),
    Column("last_status", Integer),
    Column("last_error", Text),
    Column("items_total", Integer, nullable=False, server_default="0"),
    # Telegram: дочитывание от последнего id
    Column("last_native_id", BigInteger),
)

raw_items = Table(
    "raw_items",
    metadata,
    Column("id", String(40), primary_key=True),
    Column("source_id", String, nullable=False, index=True),
    Column("native_id", String, nullable=False),
    Column("url", Text),
    Column("title", Text, nullable=False),
    Column("lead", Text),
    Column("text", Text),
    Column("is_partial", Boolean, nullable=False, server_default="false"),
    Column("published_at", DateTime(timezone=True), nullable=False, index=True),
    Column("fetched_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("content_hash", String(40), nullable=False, index=True),
    Column("simhash", BigInteger),
    Column("dropped_reason", String),
    Column("story_id", String, index=True),
    Column("data", JSONB, nullable=False),
)

stories = Table(
    "stories",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", Text, nullable=False),
    Column("event_type", String, index=True),
    Column("status", String, nullable=False),
    Column("corroboration", Integer, nullable=False, server_default="0"),
    Column("first_seen", DateTime(timezone=True), nullable=False, index=True),
    Column("last_seen", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("embedding", Vector(EMBED_DIM)),
    Column("data", JSONB, nullable=False),
)

event_facts = Table(
    "event_facts",
    metadata,
    Column("story_id", String, ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True),
    Column("event_type", String, index=True),
    Column("status", String),
    Column("data", JSONB, nullable=False),
)

bank_positions = Table(
    "bank_positions",
    metadata,
    Column("id", String, primary_key=True),
    Column("doc_id", String, nullable=False, index=True),
    Column("channel", String, index=True),
    Column("doc_date", DateTime(timezone=True)),
    Column("thesis", Text, nullable=False),
    Column("embedding", Vector(EMBED_DIM)),
    Column("data", JSONB, nullable=False),
)

market_knowledge = Table(
    "market_knowledge",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False, index=True),
    Column("event_type", String, index=True),
    Column("channel", String, index=True),
    Column("published_at", DateTime(timezone=True), nullable=False),
    Column("embedding", Vector(EMBED_DIM)),
    Column("data", JSONB, nullable=False),
)

analyses = Table(
    "analyses",
    metadata,
    Column("story_id", String, primary_key=True),
    Column("sector", String, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("status", String, nullable=False),
    Column("trace_id", String),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("data", JSONB, nullable=False),
)

company_profiles = Table(
    "company_profiles",
    metadata,
    Column("company_id", String, primary_key=True),
    Column("sector", String, index=True),
    Column("data", JSONB, nullable=False),
)

counterparty_graphs = Table(
    "counterparty_graphs",
    metadata,
    Column("company_id", String, primary_key=True),
    Column("hhi_suppliers", Float),
    Column("hhi_buyers", Float),
    Column("data", JSONB, nullable=False),
)

insight_cards = Table(
    "insight_cards",
    metadata,
    Column("card_id", String, primary_key=True),
    Column("company_id", String, nullable=False, index=True),
    Column("story_id", String, nullable=False, index=True),
    Column("user_role", String),
    Column("score", Float),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("data", JSONB, nullable=False),
)

run_traces = Table(
    "run_traces",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("story_id", String, index=True),
    Column("company_id", String),
    Column("cost_rub", Float),
    Column("started_at", DateTime(timezone=True)),
    Column("data", JSONB, nullable=False),
)

feedback = Table(
    "feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("card_id", String, nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("at", DateTime(timezone=True), server_default=func.now()),
    Column("data", JSONB, nullable=False),
)

# Разметка пар «сюжет × клиент» (инструмент разметки, eval/labeling). В data — снимок сюжета и клиента
# на момент создания пары, чтобы все разметчики видели одно и то же.
label_pairs = Table(
    "label_pairs",
    metadata,
    Column("pair_id", String, primary_key=True),
    Column("batch", String, nullable=False, index=True),
    Column("story_id", String, nullable=False),
    Column("company_id", String, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("data", JSONB, nullable=False),
)

labels = Table(
    "labels",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "pair_id", String, ForeignKey("label_pairs.pair_id", ondelete="CASCADE"), nullable=False, index=True
    ),
    Column("labeler", String, nullable=False),
    Column("important", Boolean, nullable=False),
    Column("main_channel", String),
    Column("sign", Integer),  # −1 риск, 0 нейтрально, +1 возможность
    Column("comment", Text),
    Column("at", DateTime(timezone=True), server_default=func.now()),
)

Index("ix_raw_items_source_native", raw_items.c.source_id, raw_items.c.native_id, unique=True)
