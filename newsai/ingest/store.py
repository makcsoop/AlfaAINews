"""Запись в raw_items и состояние лент (ETag, Last-Modified, счётчики)."""

from datetime import UTC, datetime

from sqlalchemy import Connection, func, select, text
from sqlalchemy.dialects.postgresql import insert

from newsai.db import models as m
from newsai.schemas import RawItem


def get_state(conn: Connection, source_id: str) -> dict:
    row = conn.execute(select(m.feed_state).where(m.feed_state.c.source_id == source_id)).mappings().first()
    return dict(row) if row else {}


def save_state(conn: Connection, source_id: str, **fields) -> None:
    fields["last_polled_at"] = datetime.now(UTC)
    stmt = insert(m.feed_state).values(source_id=source_id, **fields)
    conn.execute(stmt.on_conflict_do_update(index_elements=["source_id"], set_=fields))


def known_native_ids(conn: Connection, source_id: str, native_ids: list[str]) -> set[str]:
    if not native_ids:
        return set()
    q = select(m.raw_items.c.native_id).where(
        m.raw_items.c.source_id == source_id, m.raw_items.c.native_id.in_(native_ids)
    )
    return set(conn.execute(q).scalars())


def insert_items(conn: Connection, items: list[RawItem]) -> int:
    if not items:
        return 0
    rows = [
        {
            "id": it.id,
            "source_id": it.source_id,
            "native_id": it.native_id,
            "url": it.url,
            "title": it.title,
            "lead": it.lead,
            "text": it.text,
            "is_partial": it.is_partial,
            "published_at": it.published_at,
            "fetched_at": it.fetched_at,
            "content_hash": it.content_hash,
            "simhash": it.simhash,
            "dropped_reason": it.dropped_reason,
            "data": it.model_dump(mode="json"),
        }
        for it in items
    ]
    res = conn.execute(insert(m.raw_items).values(rows).on_conflict_do_nothing().returning(m.raw_items.c.id))
    return len(res.all())


def ingest_stats(conn: Connection) -> list[dict]:
    q = (
        select(
            m.raw_items.c.source_id,
            func.count().label("total"),
            func.count()
            .filter(m.raw_items.c.fetched_at > text("now() - interval '1 hour'"))
            .label("last_hour"),
            func.count().filter(m.raw_items.c.text.isnot(None)).label("with_text"),
            func.max(m.raw_items.c.published_at).label("latest"),
        )
        .group_by(m.raw_items.c.source_id)
        .order_by(m.raw_items.c.source_id)
    )
    return [dict(r) for r in conn.execute(q).mappings()]
