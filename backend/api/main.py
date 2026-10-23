"""API: лента, карточка, журнал, вопрос-ответ (заглушки), сессия и защита company_id."""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from backend.api.errors import install_error_handling
from backend.api.labeling import router as labeling_router
from backend.api.routes import router
from backend.db import models as m
from backend.db.engine import get_engine
from backend.ingest.store import ingest_stats
from backend.profile.graph import get_graph
from backend.schemas import CounterpartyGraph

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
app.include_router(labeling_router)


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


@app.get("/companies/{company_id}/counterparties", response_model=CounterpartyGraph)
def counterparties(company_id: str) -> CounterpartyGraph:
    """Топ-10 поставщиков и покупателей, их отрасли, доли, HHI и связи компании (граф платежей)."""
    with get_engine().connect() as conn:
        graph = get_graph(conn, company_id)
    if graph is None:
        raise HTTPException(404, f"нет платежей по компании {company_id}")
    return graph


@app.get("/stories")
def stories(
    limit: int = Query(50, ge=1, le=500),
    event_type: str | None = None,
    status: str | None = None,
    business_only: bool = Query(False, description="только сюжеты с типом события, отличным от other"),
) -> list[dict]:
    """Свежие сюжеты: заголовок, тип события, статус подтверждения, число независимых источников."""
    q = (
        select(m.stories.c.data, m.event_facts.c.event_type)
        .select_from(m.stories.outerjoin(m.event_facts, m.event_facts.c.story_id == m.stories.c.id))
        .order_by(m.stories.c.last_seen.desc())
        .limit(limit)
    )
    if event_type:
        q = q.where(m.event_facts.c.event_type == event_type)
    if status:
        q = q.where(m.stories.c.status == status)
    if business_only:
        q = q.where(m.event_facts.c.event_type.notin_(["other"]))
    with get_engine().connect() as conn:
        return [{**data, "event_type": et} for data, et in conn.execute(q)]


@app.get("/stories/{story_id}")
def story(story_id: str) -> dict:
    """Сюжет целиком: сюжет, факты с цитатами и материалы (заголовок, лид, ссылка — без полного текста)."""
    with get_engine().connect() as conn:
        data = conn.execute(select(m.stories.c.data).where(m.stories.c.id == story_id)).scalar()
        if data is None:
            raise HTTPException(404, f"нет сюжета {story_id}")
        facts = conn.execute(
            select(m.event_facts.c.data).where(m.event_facts.c.story_id == story_id)
        ).scalar()
        items = conn.execute(
            select(
                m.raw_items.c.id,
                m.raw_items.c.source_id,
                m.raw_items.c.title,
                m.raw_items.c.lead,
                m.raw_items.c.url,
                m.raw_items.c.published_at,
            )
            .where(m.raw_items.c.id.in_(data["item_ids"]))
            .order_by(m.raw_items.c.published_at)
        ).mappings()
        return {"story": data, "facts": facts, "items": [dict(i) for i in items]}


@app.get("/stories/{story_id}/bank_market")
def bank_market(story_id: str) -> dict:
    """«Банк и рынок»: позиции ЦМИ до события, консенсус рынка, сюрприз. Без генерации."""
    from backend.agents.bank_market import report

    with get_engine().connect() as conn:
        if not conn.execute(select(m.stories.c.id).where(m.stories.c.id == story_id)).first():
            raise HTTPException(404, f"нет сюжета {story_id}")
        return report(conn, story_id).model_dump(mode="json")


@app.get("/stories/{story_id}/statistician")
def statistician(story_id: str, sector: str = Query(min_length=2, max_length=2)) -> dict:
    """«Статистик»: чувствительность отрасли (2 знака ОКВЭД) и похожие случаи из архива."""
    from backend.agents.statistician import report

    with get_engine().connect() as conn:
        if not conn.execute(select(m.stories.c.id).where(m.stories.c.id == story_id)).first():
            raise HTTPException(404, f"нет сюжета {story_id}")
        return report(conn, story_id, sector).model_dump(mode="json")
