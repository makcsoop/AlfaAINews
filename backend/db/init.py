"""Инициализация БД: `python -m backend.db.init [--load-case] [--load-fixtures | --load-demo-profiles]
[--drop-fixtures]`."""

import argparse
import gzip
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import insert

from backend.config import get_settings
from backend.db import models as m
from backend.db.case_loader import load_case_dir
from backend.db.engine import get_engine
from backend.fixtures import load_bundle

log = logging.getLogger(__name__)

SYNTHETIC_DIR = Path("data/case_synthetic")


# Переименования колонок в уже созданных базах (create_all существующие таблицы не меняет)
_RENAMES = [("stories", "independent_sources", "corroboration")]
# колонки, добавленные после первого запуска стенда: create_all существующие таблицы не меняет
_ADD_COLUMNS = [("labels", "relevance", "INTEGER")]


def create_schema(conn: Connection) -> None:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    for table, old, new in _RENAMES:
        exists = conn.execute(
            text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
            {"t": table, "c": old},
        ).first()
        if exists:
            conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"))
    m.metadata.create_all(conn)
    for table, col, typ in _ADD_COLUMNS:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}"))


def _upsert(conn: Connection, table, rows: list[dict]) -> None:
    if not rows:
        return
    stmt = insert(table).values(rows)
    pk = [c.name for c in table.primary_key.columns]
    # обновляем все колонки, а не только data: иначе после правки фикстуры title/thesis остаются старыми
    update = {k: stmt.excluded[k] for k in rows[0] if k not in pk}
    conn.execute(stmt.on_conflict_do_update(index_elements=pk, set_=update))


def load_fixtures(conn: Connection, name: str = "rate_14", *, profiles_only: bool = False) -> None:
    """Фикстура в БД. `profiles_only` — только демо-компании (профили и графы для демо-входа), без сюжета,
    материалов, фактов, позиций ЦМИ и карточек: так живая лента не получает синтетический сюжет 11.09."""
    b: dict[str, list[Any]] = load_bundle(name)  # type: ignore[assignment]  # объекты разных схем
    dump = lambda o: o.model_dump(mode="json")  # noqa: E731
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
    if profiles_only:
        log.info("фикстура %s: только демо-компании", name)
        return
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
                "corroboration": s.corroboration,
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
        m.analyses,
        [
            {
                "story_id": a.story_id,
                "sector": a.sector,
                "version": a.version,
                "status": a.status,
                "trace_id": a.trace_id,
                "data": dump(a),
            }
            for a in b["analysis"]
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


SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "snapshot"


def _snapshot_ids(snapshot_dir: Path, table: str) -> set[str]:
    path = snapshot_dir / f"{table}.json.gz"
    if not path.exists():
        return set()
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return {r["id"] for r in json.load(f) if "id" in r}


def drop_fixtures(
    conn: Connection, name: str = "rate_14", *, snapshot_dir: Path | None = None
) -> dict[str, int]:
    """Убирает из живой базы сюжет фикстуры: сюжет, его материалы, факты, разборы, карточки и трассы.

    Демо-компании (профили, графы) остаются — на них демо-вход. Позиции ЦМИ и знания рынка фикстуры
    удаляются, только если их нет в снимке знаний Р1 (`data/snapshot/`): совпавший id — настоящая запись.
    """
    b: dict[str, list[Any]] = load_bundle(name)  # type: ignore[assignment]
    snap = snapshot_dir or SNAPSHOT_DIR
    stories = [s.id for s in b["story"]]
    items = [r.id for r in b["raw_item"]]
    positions = [p.id for p in b["bank_position"] if p.id not in _snapshot_ids(snap, "bank_positions")]
    knowledge = [k.id for k in b["market_knowledge"] if k.id not in _snapshot_ids(snap, "market_knowledge")]
    out: dict[str, int] = {}

    def rm(table, col, ids: list[str]) -> None:
        out[table.name] = conn.execute(table.delete().where(col.in_(ids))).rowcount if ids else 0

    rm(m.insight_cards, m.insight_cards.c.story_id, stories)
    rm(m.run_traces, m.run_traces.c.story_id, stories)
    rm(m.analyses, m.analyses.c.story_id, stories)
    rm(m.event_facts, m.event_facts.c.story_id, stories)
    rm(m.raw_items, m.raw_items.c.id, items)
    rm(m.stories, m.stories.c.id, stories)
    rm(m.bank_positions, m.bank_positions.c.id, positions)
    rm(m.market_knowledge, m.market_knowledge.c.id, knowledge)
    log.info("фикстура %s удалена из базы: %s", name, out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--load-case", action="store_true", help="загрузить CSV из CASE_DATA_DIR")
    ap.add_argument("--load-fixtures", action="store_true", help="вся фикстура rate_14 (стенд на фикстуре)")
    ap.add_argument(
        "--load-demo-profiles", action="store_true",
        help="из фикстуры только демо-компании (живой стенд: без синтетического сюжета 11.09)",
    )
    ap.add_argument(
        "--drop-fixtures", action="store_true",
        help="удалить сюжет фикстуры, его материалы, факты и карточки из базы (демо-компании остаются)",
    )
    ap.add_argument("--force-case", action="store_true", help="перезалить CSV, даже если файлы не менялись")
    args = ap.parse_args()

    with get_engine().begin() as conn:
        create_schema(conn)
        log.info("таблицы готовы")
        if args.drop_fixtures:
            drop_fixtures(conn)
        if args.load_fixtures:
            load_fixtures(conn)
        elif args.load_demo_profiles:
            load_fixtures(conn, profiles_only=True)
        if args.load_case:
            case_dir = Path(get_settings().case_data_dir)
            if not any(case_dir.glob("*.csv")):
                log.warning("в %s нет CSV кейса — загружаю синтетику из %s", case_dir, SYNTHETIC_DIR)
                case_dir = SYNTHETIC_DIR
            log.info("CSV кейса: %s", load_case_dir(conn, case_dir, force=args.force_case))

    if args.load_case:
        refresh_graph_if_stale()


def refresh_graph_if_stale() -> None:
    """Граф платежей пересчитывается, если его нет или платежи загружены позже последнего расчёта."""
    from backend.profile.graph import build_graphs, save_graphs

    engine = get_engine()
    with engine.begin() as conn:
        stale = conn.execute(
            text(
                "SELECT (SELECT max(loaded_at) FROM case_load_state"
                " WHERE kind IN ('case_payments', 'case_companies'))"
                " > coalesce((SELECT loaded_at FROM case_load_state WHERE kind = 'graph'), 'epoch')"
            )
        ).scalar()
    if not stale:
        log.info("граф платежей актуален — пропускаю")
        return
    with engine.connect() as read, engine.begin() as write:
        n = save_graphs(write, build_graphs(read))
        write.execute(
            text(
                "INSERT INTO case_load_state (kind, file, sha1, rows) VALUES ('graph', 'payments', '-', :n) "
                "ON CONFLICT (kind) DO UPDATE SET rows = :n, loaded_at = now()"
            ),
            {"n": n},
        )
    log.info("граф платежей пересчитан: %d компаний", n)


if __name__ == "__main__":
    main()
