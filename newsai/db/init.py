"""Инициализация БД: `python -m newsai.db.init [--load-case] [--load-fixtures]`."""

import argparse
import logging
from pathlib import Path

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import insert

from newsai.config import get_settings
from newsai.db import models as m
from newsai.db.case_loader import load_case_dir
from newsai.db.engine import get_engine
from newsai.fixtures import load_bundle

log = logging.getLogger(__name__)

SYNTHETIC_DIR = Path("data/case_synthetic")


def create_schema(conn: Connection) -> None:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    m.metadata.create_all(conn)


def _upsert(conn: Connection, table, rows: list[dict]) -> None:
    if not rows:
        return
    stmt = insert(table).values(rows)
    pk = [c.name for c in table.primary_key.columns]
    # обновляем все колонки, а не только data: иначе после правки фикстуры title/thesis остаются старыми
    update = {k: stmt.excluded[k] for k in rows[0] if k not in pk}
    conn.execute(stmt.on_conflict_do_update(index_elements=pk, set_=update))


def load_fixtures(conn: Connection, name: str = "rate_14") -> None:
    b = load_bundle(name)
    dump = lambda o: o.model_dump(mode="json")  # noqa: E731
    _upsert(
        conn,
        m.raw_items,
        [
            {
                **{
                    k: getattr(r, k)
                    for k in ("id", "source_id", "native_id", "url", "title", "lead", "text", "is_partial")
                },
                **{k: getattr(r, k) for k in ("published_at", "fetched_at", "content_hash")},
                "data": dump(r),
            }
            for r in b["raw_item"]
        ],
    )
    _upsert(
        conn,
        m.stories,
        [
            {
                "id": s.id,
                "title": s.title,
                "event_type": s.event_type,
                "status": s.status,
                "independent_sources": s.independent_sources,
                "first_seen": s.first_seen,
                "last_seen": s.last_seen,
                "version": s.version,
                "data": dump(s),
            }
            for s in b["story"]
        ],
    )
    _upsert(
        conn,
        m.event_facts,
        [
            {"story_id": e.story_id, "event_type": e.event_type, "status": e.status, "data": dump(e)}
            for e in b["event_facts"]
        ],
    )
    _upsert(
        conn,
        m.bank_positions,
        [
            {
                "id": p.id,
                "doc_id": p.doc_id,
                "channel": p.channel,
                "doc_date": p.doc_date,
                "thesis": p.thesis,
                "data": dump(p),
            }
            for p in b["bank_position"]
        ],
    )
    _upsert(
        conn,
        m.market_knowledge,
        [
            {
                "id": k.id,
                "kind": k.kind,
                "event_type": k.event_type,
                "channel": k.channel,
                "published_at": k.published_at,
                "data": dump(k),
            }
            for k in b["market_knowledge"]
        ],
    )
    _upsert(
        conn,
        m.company_profiles,
        [{"company_id": p.company_id, "sector": p.sector, "data": dump(p)} for p in b["company_profile"]],
    )
    _upsert(
        conn,
        m.counterparty_graphs,
        [
            {
                "company_id": g.company_id,
                "hhi_suppliers": g.hhi_suppliers,
                "hhi_buyers": g.hhi_buyers,
                "data": dump(g),
            }
            for g in b["counterparty_graph"]
        ],
    )
    _upsert(
        conn,
        m.insight_cards,
        [
            {
                "card_id": c.card_id,
                "company_id": c.company_id,
                "story_id": c.event.story_id,
                "user_role": c.user_role,
                "score": c.relevance.score,
                "data": dump(c),
            }
            for c in b["insight_card"]
        ],
    )
    _upsert(
        conn,
        m.run_traces,
        [
            {
                "run_id": t.run_id,
                "story_id": t.story_id,
                "company_id": t.company_id,
                "cost_rub": t.cost_rub,
                "started_at": t.started_at,
                "data": dump(t),
            }
            for t in b["run_trace"]
        ],
    )
    log.info("фикстура %s загружена", name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--load-case", action="store_true", help="загрузить CSV из CASE_DATA_DIR")
    ap.add_argument("--load-fixtures", action="store_true")
    ap.add_argument("--force-case", action="store_true", help="перезалить CSV, даже если файлы не менялись")
    args = ap.parse_args()

    with get_engine().begin() as conn:
        create_schema(conn)
        log.info("таблицы готовы")
        if args.load_fixtures:
            load_fixtures(conn)
        if args.load_case:
            case_dir = Path(get_settings().case_data_dir)
            if not any(case_dir.glob("*.csv")):
                log.warning("в %s нет CSV кейса — загружаю синтетику из %s", case_dir, SYNTHETIC_DIR)
                case_dir = SYNTHETIC_DIR
            log.info("CSV кейса: %s", load_case_dir(conn, case_dir, force=args.force_case))


if __name__ == "__main__":
    main()
