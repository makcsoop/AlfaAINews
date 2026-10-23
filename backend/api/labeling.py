"""Инструмент разметки пар «сюжет × клиент» (ТЗ, день 3): важно / неважно, главный канал, знак → CSV.

Страница — GET /labeling, только при LABELING_ENABLED=true: страница показывает профили компаний без сессии,
поэтому на демо-стенде выключена (404). Пары создаёт eval/labeling/make_pairs.py. Каждый разметчик видит пары,
которые он ещё не размечал; одну пару могут разметить несколько человек (для согласия разметчиков).
"""

import csv
import io
import os
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.db.engine import get_engine


def _enabled() -> None:
    """Инвариант 6: данные клиентов без сессии наружу не отдаём — инструмент только для разметки."""
    if os.environ.get("LABELING_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
        raise HTTPException(404, "Not Found")


router = APIRouter(prefix="/labeling", tags=["labeling"], dependencies=[Depends(_enabled)])
PAGE = Path(__file__).with_name("labeling.html")
CHANNELS = Path(__file__).resolve().parents[2] / "config" / "channels_matrix.yaml"


class LabelIn(BaseModel):
    pair_id: str
    labeler: str = Field(min_length=1, max_length=40)
    relevance: Literal[0, 1, 2, 3]  # 0 не про нас, 1 фон, 2 важно, 3 важно сегодня
    important: bool | None = None  # по умолчанию relevance ≥ 2
    main_channel: str | None = None
    sign: Literal[-1, 0, 1] | None = None
    comment: str | None = Field(default=None, max_length=1000)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@router.get("/channels")
def channels() -> list[dict]:
    cfg = yaml.safe_load(CHANNELS.read_text(encoding="utf-8"))["channels"]
    return [{"id": k, "name": v["name"], "up": v.get("up")} for k, v in cfg.items()]


@router.get("/next")
def next_pair(labeler: str = Query(min_length=1, max_length=40), batch: str = "oct-60") -> dict:
    with get_engine().connect() as conn:
        done = select(m.labels.c.pair_id).where(m.labels.c.labeler == labeler)
        row = conn.execute(
            select(m.label_pairs.c.pair_id, m.label_pairs.c.data)
            .where(m.label_pairs.c.batch == batch, m.label_pairs.c.pair_id.not_in(done))
            .order_by(m.label_pairs.c.pair_id)
            .limit(1)
        ).first()
        total = conn.execute(select(func.count()).where(m.label_pairs.c.batch == batch)).scalar()
        mine = conn.execute(
            select(func.count())
            .select_from(m.labels.join(m.label_pairs))
            .where(m.labels.c.labeler == labeler, m.label_pairs.c.batch == batch)
        ).scalar()
    return {"pair": {"pair_id": row[0], **row[1]} if row else None, "done": mine, "total": total}


@router.post("/labels")
def save_label(label: LabelIn) -> dict:
    with get_engine().begin() as conn:
        if not conn.execute(
            select(m.label_pairs.c.pair_id).where(m.label_pairs.c.pair_id == label.pair_id)
        ).first():
            raise HTTPException(404, "нет такой пары")
        row = label.model_dump()
        if row["important"] is None:
            row["important"] = label.relevance >= 2
        conn.execute(insert(m.labels).values(**row))
    return {"ok": True}


@router.get("/export.csv")
def export(batch: str = "oct-60") -> StreamingResponse:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(
                m.label_pairs.c.pair_id,
                m.label_pairs.c.story_id,
                m.label_pairs.c.company_id,
                m.label_pairs.c.data,
                m.labels.c.labeler,
                m.labels.c.relevance,
                m.labels.c.important,
                m.labels.c.main_channel,
                m.labels.c.sign,
                m.labels.c.comment,
                m.labels.c.at,
            )
            .select_from(m.labels.join(m.label_pairs))
            .where(m.label_pairs.c.batch == batch)
            .order_by(m.labels.c.at)
        ).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "pair_id",
            "story_id",
            "company_id",
            "archetype",
            "event_type",
            "story_title",
            "labeler",
            "relevance",
            "important",
            "main_channel",
            "sign",
            "comment",
            "labeled_at",
        ]
    )
    for r in rows:
        w.writerow(
            [
                r.pair_id,
                r.story_id,
                r.company_id,
                r.data["client"]["archetype"],
                r.data["story"]["event_type"],
                r.data["story"]["title"],
                r.labeler,
                "" if r.relevance is None else r.relevance,
                int(r.important),
                r.main_channel or "",
                "" if r.sign is None else r.sign,
                r.comment or "",
                r.at.isoformat(),
            ]
        )
    # BOM — чтобы Excel открыл кириллицу без танцев с кодировкой
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="labels-{batch}.csv"'},
    )
