"""Хранение `Analysis` (Б4): разбор пары «сюжет × отрасль × версия» — отдельная строка таблицы `analyses`.

Ключ (story_id, sector, version) совпадает с `InsightCard.analysis_ref` = "story_id:sector:version",
поэтому карточку всегда можно связать с разбором, на котором она построена. Пишет конвейер Р3 после арбитра.
"""

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.schemas import Analysis


def analysis_ref(a: Analysis) -> str:
    return f"{a.story_id}:{a.sector}:{a.version}"


def save_analysis(conn: Connection, a: Analysis) -> str:
    row = {
        "story_id": a.story_id,
        "sector": a.sector,
        "version": a.version,
        "status": a.status,
        "trace_id": a.trace_id,
        "data": a.model_dump(mode="json"),
    }
    stmt = insert(m.analyses).values(row)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["story_id", "sector", "version"],
            set_={k: stmt.excluded[k] for k in ("status", "trace_id", "data")},
        )
    )
    return analysis_ref(a)


def get_analysis(conn: Connection, ref: str) -> Analysis | None:
    """По `analysis_ref` карточки: "story_id:sector:version" (story_id может содержать «:»)."""
    story_id, sector, version = ref.rsplit(":", 2)
    data = conn.execute(
        select(m.analyses.c.data).where(
            m.analyses.c.story_id == story_id,
            m.analyses.c.sector == sector,
            m.analyses.c.version == int(version),
        )
    ).scalar()
    return Analysis.model_validate(data) if data else None
