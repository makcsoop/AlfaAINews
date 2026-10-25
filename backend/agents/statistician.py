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
from sqlalchemy import Connection, or_, select, text, true

from backend.db import models as m
from backend.ingest.private import PASTE_SOURCE_ID
from backend.ingest.registry import analyst_sources
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


def _key_words(title: str, n: int = 2) -> list[str]:
    """Самые характерные слова заголовка (длинные, не служебные) — для поиска заметок аналитиков."""
    words = [w.strip("«»\"'.,:;!?()") for w in title.split()]
    words = [w for w in words if len(w) >= 5 and not w[0].isdigit()]
    return sorted(words, key=len, reverse=True)[:n]


def similar_cases(conn: Connection, story: Story, event_type: str | None, k: int = 3) -> list[SimilarCase]:
    """Похожие случаи из архива материалов новостных источников, только раньше сюжета.

    Сюжеты склеиваются в окне 72 ч, а архив каналов глубже, поэтому ищем по материалам: символьный TF-IDF
    по заголовкам, сначала тот же тип события (правила), без почти одинаковых дублей.
    """
    from backend.extract.rules import event_type as rule_type

    analysts = analyst_sources()
    rows = conn.execute(
        select(
            m.raw_items.c.id,
            m.raw_items.c.title,
            m.raw_items.c.lead,
            m.raw_items.c.published_at,
            m.raw_items.c.story_id,
        ).where(
            m.raw_items.c.published_at < story.first_seen - timedelta(hours=6),
            m.raw_items.c.published_at >= story.first_seen - timedelta(days=ARCHIVE_DAYS),
            m.raw_items.c.dropped_reason.is_(None),
            m.raw_items.c.source_id.not_in(analysts),
            m.raw_items.c.source_id != PASTE_SOURCE_ID,  # вставки пользователей личные
        )
    ).all()
    if not rows:
        return []
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
    x = vec.fit_transform([story.title] + [r.title for r in rows])
    sim = (x[1:] @ x[0].T).toarray().ravel()
    top = np.argsort(-sim)[: 20 * k]
    same = {
        i: rule_type(rows[i].title, rows[i].lead)[0] == event_type and event_type not in (None, "other")
        for i in top
    }
    order = sorted(top, key=lambda i: (not same[i], -sim[i]))
    chosen: list[int] = []
    for i in order:
        if sim[i] <= 0:
            break
        # без дублей одного события: другой день или заметно другой заголовок
        if any(
            rows[i].published_at.date() == rows[j].published_at.date()
            and (x[i + 1] @ x[j + 1].T).toarray()[0, 0] > 0.5
            for j in chosen
        ):
            continue
        chosen.append(i)
        if len(chosen) >= k:
            break
    out = []
    for i in chosen:
        r = rows[i]
        words = _key_words(r.title)
        cond = or_(*[m.raw_items.c.text.ilike(f"%{w}%") for w in words]) if words else true()
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
                m.raw_items.c.published_at >= r.published_at,
                m.raw_items.c.published_at < r.published_at + timedelta(days=1),
                cond,
            )
            .order_by(m.raw_items.c.published_at)
            .limit(3)
        ).all()
        out.append(
            SimilarCase(
                story_id=r.story_id or r.id,
                title=r.title,
                event_type=rule_type(r.title, r.lead)[0],
                first_seen=r.published_at.isoformat(),
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
    if not channels and facts.get("event_type"):  # направления правила не нашли — факторы типа события
        from backend.extract.rules import _config

        channels = next((t.channels for t in _config()[0] if t.name == facts["event_type"]), [])
    return StatisticianReport(
        story_id=story_id,
        sector=sector,
        sensitivity=sector_sensitivity(conn, sector, channels),
        similar=similar_cases(conn, story, facts.get("event_type")),
    )
