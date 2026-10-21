"""API (заготовка). Эндпоинты карточек, ленты и Q&A добавляют Разработчики 2 и 3."""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from backend.db import models as m
from backend.db.engine import get_engine
from backend.ingest.store import ingest_stats

app = FastAPI(title="AlfaAINews")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


@app.get("/health")
def health() -> dict:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/raw_items")
def raw_items(limit: int = Query(50, ge=1, le=500), source_id: str | None = None) -> list[dict]:
    q = select(m.raw_items.c.data).order_by(m.raw_items.c.published_at.desc()).limit(limit)
    if source_id:
        q = q.where(m.raw_items.c.source_id == source_id)
    with get_engine().connect() as conn:
        return list(conn.execute(q).scalars())


@app.get("/stats/ingest")
def stats() -> list[dict]:
    with get_engine().connect() as conn:
        return ingest_stats(conn)
