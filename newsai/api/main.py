"""API: лента, карточка, журнал, вопрос-ответ (заглушки), сессия и защита company_id."""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from newsai.api.errors import install_error_handling
from newsai.api.routes import router
from newsai.db import models as m
from newsai.db.engine import get_engine
from newsai.ingest.store import ingest_stats

app = FastAPI(title="AlfaAINews")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id"],
)
install_error_handling(app)
app.include_router(router)


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
