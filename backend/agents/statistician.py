"""Модуль «Статистик» (Р1, для разбора Р3): чувствительность отрасли и похожие случаи. Без генерации.

Что отдаёт по паре «сюжет × отрасль»:
- чувствительность отрасли к факторам сюжета — из матрицы config/channels_matrix.yaml; пока матрица
  не заполнена (Р2), — данные кейса: доля компаний отрасли с кредитами, депозитами и ВЭД;
- 3 похожих сюжета из архива — только раньше текущего, сначала того же типа события;
- что писали аналитические каналы в течение суток после каждого похожего сюжета (цитаты со ссылками).
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import yaml
from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import Connection, select, text

from backend.db import models as m
from backend.ingest.registry import load_sources
from backend.schemas import Story

MATRIX = Path(__file__).resolve().parents[2] / "config" / "channels_matrix.yaml"
ARCHIVE_DAYS = 120


class Sensitivity(BaseModel):
    channel: str
    value: float | None
    source: str  # "matrix" | "case_data" | "none"
    basis: str


class AnalystNote(BaseModel):
    source_id: str
    item_id: str
    url: str | None
    published_at: str
    excerpt: str


class SimilarCase(BaseModel):
    story_id: str
    title: str
    event_type: str | None
    first_seen: str
    similarity: float
    analyst_notes: list[AnalystNote]


class StatisticianReport(BaseModel):
    story_id: str
    sector: str
    sensitivity: list[Sensitivity]
    similar: list[SimilarCase]


def sector_sensitivity(conn: Connection, sector: str, channels: list[str]) -> list[Sensitivity]:
    matrix = (yaml.safe_load(MATRIX.read_text(encoding="utf-8")).get("okved") or {}).get(sector) or {}
    shares = (
        conn.execute(
            text(
                "SELECT count(*) n, avg(loans::int) loans, avg(leasing::int) leasing, "
                "avg(deposits::int) deposits, "
                "avg(fts_flag::int) fts FROM companies WHERE sector = :s"
            ),
            {"s": sector},
        )
        .mappings()
        .one()
    )
    out = []
    for ch in channels:
        if ch in matrix:
            out.append(
                Sensitivity(channel=ch, value=matrix[ch], source="matrix", basis=f"матрица ОКВЭД {sector}")
            )
        elif ch == "key_rate" and shares["n"]:
            # ставка ↑: заёмщикам хуже, держателям депозитов лучше — оценка по долям компаний отрасли
            v = float(shares["deposits"]) - float(shares["loans"]) - 0.5 * float(shares["leasing"])
            out.append(
                Sensitivity(
                    channel=ch,
                    value=round(max(-1.0, min(1.0, v)), 3),
                    source="case_data",
                    basis=f"в ОКВЭД {sector}: кредиты {shares['loans']:.0%}, лизинг {shares['leasing']:.0%}, "
                    f"депозиты {shares['deposits']:.0%} из {shares['n']} компаний",
                )
            )
        elif ch in {"fx_rub", "trade_restrictions", "logistics_costs"} and shares["n"]:
            out.append(
                Sensitivity(
                    channel=ch,
                    value=None,
                    source="case_data",
                    basis=f"ВЭД у {shares['fts']:.0%} компаний ОКВЭД {sector}; знак — импорт или экспорт",
                )
            )
        else:
            out.append(Sensitivity(channel=ch, value=None, source="none", basis="нет данных в матрице"))
    return out


def similar_cases(conn: Connection, story: Story, event_type: str | None, k: int = 3) -> list[SimilarCase]:
    since = story.first_seen - timedelta(days=ARCHIVE_DAYS)
    rows = conn.execute(
        select(m.stories.c.id, m.stories.c.title, m.stories.c.first_seen, m.event_facts.c.event_type)
        .select_from(m.stories.outerjoin(m.event_facts, m.event_facts.c.story_id == m.stories.c.id))
        .where(m.stories.c.first_seen < story.first_seen, m.stories.c.first_seen >= since)
    ).all()
    if not rows:
        return []
    texts = [story.title] + [r.title for r in rows]
    x = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True).fit_transform(texts)
    sim = (x[1:] @ x[0].T).toarray().ravel()
    same_type = np.array([r.event_type == event_type and event_type not in (None, "other") for r in rows])
    order = np.lexsort((-sim, ~same_type))  # сначала тот же тип события, внутри — по похожести
    analysts = [s.id for s in load_sources() if s.kind == "telegram" and not s.official]
    out = []
    for i in order[:k]:
        r = rows[i]
        notes = conn.execute(
            select(
                m.raw_items.c.source_id,
                m.raw_items.c.id,
                m.raw_items.c.url,
                m.raw_items.c.published_at,
                m.raw_items.c.text,
            )
            .where(
                m.raw_items.c.source_id.in_(analysts),
                m.raw_items.c.published_at >= r.first_seen,
                m.raw_items.c.published_at < r.first_seen + timedelta(days=1),
                m.raw_items.c.text.ilike(f"%{r.title.split()[0]}%") if r.title else True,
            )
            .order_by(m.raw_items.c.published_at)
            .limit(3)
        ).all()
        out.append(
            SimilarCase(
                story_id=r.id,
                title=r.title,
                event_type=r.event_type,
                first_seen=r.first_seen.isoformat(),
                similarity=round(float(sim[i]), 3),
                analyst_notes=[
                    AnalystNote(
                        source_id=n.source_id,
                        item_id=n.id,
                        url=n.url,
                        published_at=n.published_at.isoformat(),
                        excerpt=(n.text or "")[:300],
                    )
                    for n in notes
                ],
            )
        )
    return out


def report(conn: Connection, story_id: str, sector: str) -> StatisticianReport:
    story = Story.model_validate(
        conn.execute(select(m.stories.c.data).where(m.stories.c.id == story_id)).scalar_one()
    )
    facts = (
        conn.execute(select(m.event_facts.c.data).where(m.event_facts.c.story_id == story_id)).scalar() or {}
    )
    channels = [c["channel"] for c in facts.get("channels", [])]
    return StatisticianReport(
        story_id=story_id,
        sector=sector,
        sensitivity=sector_sensitivity(conn, sector, channels),
        similar=similar_cases(conn, story, facts.get("event_type")),
    )
