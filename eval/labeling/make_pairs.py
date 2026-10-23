"""Пары «сюжет × клиент» для разметки: `uv run python eval/labeling/make_pairs.py [--batch oct-60]`.

20 октябрьских экономических сюжетов (не больше двух на тип события, сначала самые подтверждённые) ×
6 крупных клиентов разного профиля, каждому сюжету — 3 клиента → 60 пар. В пару пишется снимок сюжета
и клиента: разметчики видят одно и то же, даже если сюжет потом обновится.
Разметка — ТЗ, день 3: «сюжет × клиент → важно / неважно, главный канал, знак», выгрузка в CSV.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import Connection, select, text
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.db import models as m
from backend.db.engine import get_engine

PRODUCTS = {
    "loans": "кредиты",
    "leasing": "лизинг",
    "deposits": "депозиты",
    "guarantees": "гарантии",
    "factoring": "факторинг",
    "payroll": "зарплатный проект",
    "rko": "РКО",
}
SECTOR_NAMES = {"outside": "вне базы"}

# Профили клиентов: (подпись, условие SQL). Берётся крупнейшая компания, подходящая под условие.
ARCHETYPES = [
    ("Оптовик-импортёр с кредитами", "c.sector = '46' AND c.loans AND c.fts_flag"),
    ("Компания с депозитами, без кредитов", "c.deposits AND NOT c.loans"),
    ("Строительство зданий", "c.sector = '41'"),
    ("Инфраструктурное строительство", "c.sector = '42'"),
    ("Автоторговля и сервис", "c.sector = '45'"),
    ("Логистика и транспорт", "c.sector IN ('49', '52')"),
]


def pick_clients(conn: Connection) -> list[tuple[str, str]]:
    out = []
    for label, cond in ARCHETYPES:
        cid = conn.execute(
            text(
                "SELECT c.company_id FROM companies c JOIN counterparty_graphs g USING (company_id) "
                f"WHERE c.revenue >= 15e9 AND NOT (g.data->>'sparse')::bool AND {cond} "
                "ORDER BY c.revenue DESC LIMIT 1"
            )
        ).scalar()
        if cid:
            out.append((label, cid))
    return out


def pick_stories(conn: Connection, n: int, since: str) -> list[str]:
    rows = conn.execute(
        select(m.stories.c.id, m.event_facts.c.event_type, m.event_facts.c.data["extractor_model"].astext)
        .join(m.event_facts, m.event_facts.c.story_id == m.stories.c.id)
        .where(
            m.event_facts.c.event_type != "other",
            m.stories.c.first_seen >= datetime.fromisoformat(since + "T00:00:00+00:00"),
        )
        .order_by(m.stories.c.corroboration.desc(), m.stories.c.last_seen.desc())
    ).all()
    # сначала сюжеты, где тип и факты уточнила модель (правила местами типизируют мимо)
    rows = sorted(rows, key=lambda r: (r[2] or "rules").startswith("rules"))
    chosen, per_type = [], {}
    for sid, et, _model in rows:
        if per_type.get(et, 0) >= 2:
            continue
        chosen.append(sid)
        per_type[et] = per_type.get(et, 0) + 1
        if len(chosen) >= n:
            break
    return chosen


def story_snapshot(conn: Connection, sid: str) -> dict:
    story = conn.execute(select(m.stories.c.data).where(m.stories.c.id == sid)).scalar_one()
    facts = conn.execute(select(m.event_facts.c.data).where(m.event_facts.c.story_id == sid)).scalar() or {}
    items = conn.execute(
        select(m.raw_items.c.source_id, m.raw_items.c.title, m.raw_items.c.url)
        .where(m.raw_items.c.id.in_(story["item_ids"]))
        .order_by(m.raw_items.c.published_at)
        .limit(5)
    ).mappings()
    return {
        "story_id": sid,
        "title": story["title"],
        "status": story["status"],
        "corroboration": story["corroboration"],
        "first_seen": story["first_seen"],
        "event_type": facts.get("event_type"),
        "summary": facts.get("summary"),
        "numbers": [f"{n['value']:g} {n.get('unit') or ''}".strip() for n in facts.get("numbers", [])[:6]],
        "channels": [f"{c['channel']} {c['direction']}" for c in facts.get("channels", [])],
        "items": [dict(i) for i in items],
    }


def client_snapshot(conn: Connection, label: str, cid: str) -> dict:
    c = conn.execute(text("SELECT * FROM companies WHERE company_id = :u"), {"u": cid}).mappings().one()
    g = (
        conn.execute(
            select(m.counterparty_graphs.c.data).where(m.counterparty_graphs.c.company_id == cid)
        ).scalar()
        or {}
    )
    products = [ru for name, ru in PRODUCTS.items() if c[name]]
    return {
        "company_id": cid,
        "archetype": label,
        "okved": f"{c['okved_code']} {c['okved_name']}",
        "revenue_bn": round(float(c["revenue"]) / 1e9, 1),
        "region": c["region"],
        "products": products,
        "fts_countries": c["fts_countries"] or [],
        "suppliers_by_sector": _top_sectors(g.get("sector_shares_suppliers")),
        "buyers_by_sector": _top_sectors(g.get("sector_shares_buyers")),
        "high_dependency": g.get("high_dependency"),
    }


def _top_sectors(shares: dict | None, n: int = 3) -> list[str]:
    """Крупнейшие отрасли контрагентов. Сортируем по доле: JSONB хранит ключи не в порядке вставки."""
    top = sorted((shares or {}).items(), key=lambda kv: -kv[1])[:n]
    return [f"{SECTOR_NAMES.get(k, 'ОКВЭД ' + k)}: {v:.0%}" for k, v in top]


def make_pairs(
    conn: Connection, batch: str, n_stories: int = 20, per_story: int = 3, since: str = "2026-10-01"
) -> int:
    clients = pick_clients(conn)
    stories = pick_stories(conn, n_stories, since)
    rows = []
    for i, sid in enumerate(stories):
        snap = story_snapshot(conn, sid)
        for k in range(per_story):
            label, cid = clients[(i + 2 * k) % len(clients)]  # у каждого сюжета разные профили клиентов
            rows.append(
                {
                    "pair_id": f"{batch}:{sid}:{cid}",
                    "batch": batch,
                    "story_id": sid,
                    "company_id": cid,
                    "data": {"story": snap, "client": client_snapshot(conn, label, cid)},
                }
            )
    if rows:
        conn.execute(insert(m.label_pairs).values(rows).on_conflict_do_nothing())
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="oct-60")
    ap.add_argument("--stories", type=int, default=20)
    args = ap.parse_args()
    with get_engine().begin() as conn:
        m.metadata.create_all(conn, tables=[m.label_pairs, m.labels])
        print(f"пар создано: {make_pairs(conn, args.batch, args.stories)} (партия {args.batch})")


if __name__ == "__main__":
    main()
