"""Профили v1 всех компаний → таблица company_profiles (её читают инструменты агентов Р3).

`python -m backend.profile.publish [папка с CSV]`. Графы контрагентов берутся из counterparty_graphs (Р1),
если они уже посчитаны; без них профиль строится по отрасли, продуктам и ВЭД.
"""

import logging
import sys
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.db.engine import get_engine
from backend.profile.exposures import with_exposures
from backend.profile.load import load_profiles
from backend.schemas import CompanyProfile, CounterpartyGraph

log = logging.getLogger(__name__)
BATCH = 2000


def read_graphs(conn: Connection) -> dict[str, CounterpartyGraph]:
    rows = conn.execute(select(m.counterparty_graphs.c.data)).scalars()
    return {g.company_id: g for g in (CounterpartyGraph.model_validate(r) for r in rows)}


def save_profiles(conn: Connection, profiles: Iterable[CompanyProfile]) -> int:
    rows = [
        {"company_id": p.company_id, "sector": p.sector, "data": p.model_dump(mode="json")} for p in profiles
    ]
    for i in range(0, len(rows), BATCH):
        stmt = insert(m.company_profiles).values(rows[i : i + BATCH])
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["company_id"],
                set_={"sector": stmt.excluded.sector, "data": stmt.excluded.data},
            )
        )
    return len(rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    base = load_profiles(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    with get_engine().begin() as conn:
        graphs = read_graphs(conn)
        n = save_profiles(conn, (with_exposures(p, graphs.get(cid)) for cid, p in base.items()))
    log.info("профилей записано: %d, из них с графом контрагентов: %d", n, len(set(base) & set(graphs)))


if __name__ == "__main__":
    main()
