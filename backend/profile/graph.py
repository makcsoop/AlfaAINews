"""Граф платежей: топ-контрагенты каждой компании, их отрасли, доли и концентрация (HHI).

`python -m backend.profile.graph [--company U_1]` — пересчитать и записать в counterparty_graphs.

Поставщики — получатели платежей клиента, покупатели — плательщики клиенту. Доли и HHI считаются
по внешним платежам: самоплатежи и платежи внутри GROUP_ID исключены и показаны отдельной суммой.
Связь через общих физлиц — только гипотеза «скрытой группы», в доли она не влияет.
"""

import argparse
import logging
import time
from collections import defaultdict

from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.db.engine import get_engine
from backend.schemas import Counterparty, CounterpartyGraph
from backend.schemas.client import OUTSIDE_SECTOR

log = logging.getLogger(__name__)

TOP_N = 10
# Архитектура предлагала 20–30%, но в кейсе платежи частичные (судя по всему, только через Альфа-Банк):
# у медианной компании с 5+ поставщиками крупнейший забирает 50%, и порог 25% срабатывал у 95% компаний.
# Поэтому зависимость = больше половины внешних платежей одному контрагенту при 5+ контрагентах.
HIGH_DEPENDENCY_SHARE = 0.5
MIN_COUNTERPARTIES = 5
MAX_GROUP_MEMBERS = 50
MAX_LINKED = 20

# Рёбра «клиент → контрагент» в обе стороны; дубли пар (17 в кейсе) суммируются
_EDGES = """
WITH c AS (SELECT company_id, group_id, okved_code, sector FROM companies),
p AS (
    SELECT payer, beneficiary, sum(pay_amt) AS amt, sum(pay_cnt) AS cnt
    FROM payments WHERE NOT is_self AND pay_amt > 0 {pay_filter}
    GROUP BY 1, 2
),
e AS (
    SELECT payer AS company_id, beneficiary AS cp, 'supplier' AS dir, amt, cnt FROM p
    UNION ALL
    SELECT beneficiary, payer, 'buyer', amt, cnt FROM p
)
SELECT e.company_id, e.dir, e.cp, e.amt, e.cnt,
       k.company_id IS NOT NULL AS in_dataset, k.okved_code, k.sector,
       cc.group_id IS NOT NULL AND cc.group_id = k.group_id AS same_group
FROM e
JOIN c cc ON cc.company_id = e.company_id
LEFT JOIN c k ON k.company_id = e.cp
{company_filter}
ORDER BY e.company_id
"""


def _rows(conn: Connection, company: str | None):
    """Рёбра, отсортированные по компании. Для всех компаний — потоком (серверный курсор),
    поэтому conn должен быть отдельным соединением только для чтения."""
    if company:
        sql = _EDGES.format(
            pay_filter="AND (payer = :u OR beneficiary = :u)", company_filter="WHERE e.company_id = :u"
        )
        return conn.execute(text(sql), {"u": company})
    sql = _EDGES.format(pay_filter="", company_filter="")
    return conn.execution_options(stream_results=True, yield_per=20000).execute(text(sql))


def _by_company(rows):
    """Группирует отсортированный поток рёбер по компании, не держа весь граф в памяти."""
    current, sides = None, {"supplier": [], "buyer": []}
    for row in rows:
        if row.company_id != current:
            if current is not None:
                yield current, sides
            current, sides = row.company_id, {"supplier": [], "buyer": []}
        sides[row.dir].append(row)
    if current is not None:
        yield current, sides


def _person_links(conn: Connection) -> dict[str, set[str]]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT a.synthetic_u, b.synthetic_u FROM case_persons a "
            "JOIN case_persons b ON a.synthetic_f = b.synthetic_f AND a.synthetic_u <> b.synthetic_u"
        )
    )
    links: dict[str, set[str]] = defaultdict(set)
    for u, v in rows:
        links[u].add(v)
    return links


def _groups(conn: Connection) -> tuple[dict[str, str], dict[str, list[str]]]:
    group_of: dict[str, str] = {}
    members: dict[str, list[str]] = defaultdict(list)
    for cid, gid in conn.execute(
        text("SELECT company_id, group_id FROM companies WHERE group_id IS NOT NULL")
    ):
        group_of[cid] = gid
        members[gid].append(cid)
    return group_of, members


def _side(edges: list, direction: str, links: set[str]) -> dict:
    external = [e for e in edges if not e.same_group]
    total = sum(float(e.amt) for e in external)
    intragroup = sum(float(e.amt) for e in edges if e.same_group)
    if total <= 0:
        return {
            "top": [],
            "total": 0.0,
            "n": 0,
            "intragroup": intragroup,
            "hhi": None,
            "top_share": None,
            "sectors": {},
        }
    external.sort(key=lambda e: float(e.amt), reverse=True)
    shares = [float(e.amt) / total for e in external]
    sectors: dict[str, float] = defaultdict(float)
    for e, s in zip(external, shares, strict=True):
        sectors[e.sector if e.in_dataset and e.sector else OUTSIDE_SECTOR] += s
    top = [
        Counterparty(
            company_id=e.cp,
            direction=direction,
            share=round(s, 6),
            pay_amt=float(e.amt),
            pay_cnt=int(e.cnt) if e.cnt is not None else None,
            in_dataset=e.in_dataset,
            okved_code=e.okved_code,
            sector=e.sector if e.in_dataset else OUTSIDE_SECTOR,
            same_group=False,
            via_person=e.cp in links,
        )
        for e, s in zip(external[:TOP_N], shares[:TOP_N], strict=True)
    ]
    return {
        "top": top,
        "total": total,
        "n": len(external),
        "intragroup": intragroup,
        "hhi": round(min(1.0, sum(s * s for s in shares)), 6),
        "top_share": round(shares[0], 6),
        "sectors": {k: round(v, 6) for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])},
    }


def build_graphs(conn: Connection, company: str | None = None):
    """Собирает CounterpartyGraph для одной компании или для всех компаний с платежами."""
    links = _person_links(conn)
    group_of, members = _groups(conn)
    for cid, sides in _by_company(_rows(conn, company)):
        linked = links.get(cid, set())
        sup = _side(sides["supplier"], "supplier", linked)
        buy = _side(sides["buyer"], "buyer", linked)
        gid = group_of.get(cid)
        group = members.get(gid, [cid]) if gid else [cid]
        dependent = [
            side["top_share"] >= HIGH_DEPENDENCY_SHARE
            for side in (sup, buy)
            if side["n"] >= MIN_COUNTERPARTIES and side["top_share"] is not None
        ]
        yield CounterpartyGraph(
            company_id=cid,
            group_id=gid,
            suppliers=sup["top"],
            buyers=buy["top"],
            total_out=sup["total"],
            total_in=buy["total"],
            n_suppliers=sup["n"],
            n_buyers=buy["n"],
            intragroup_out=sup["intragroup"],
            intragroup_in=buy["intragroup"],
            hhi_suppliers=sup["hhi"],
            hhi_buyers=buy["hhi"],
            top_supplier_share=sup["top_share"],
            top_buyer_share=buy["top_share"],
            sector_shares_suppliers=sup["sectors"],
            sector_shares_buyers=buy["sectors"],
            group_size=len(group),
            group_members=[x for x in group if x != cid][:MAX_GROUP_MEMBERS],
            linked_via_persons=sorted(linked)[:MAX_LINKED],
            high_dependency=any(dependent),
            sparse=sup["n"] < MIN_COUNTERPARTIES and buy["n"] < MIN_COUNTERPARTIES,
        )


def save_graphs(conn: Connection, graphs, batch: int = 2000) -> int:
    n = 0
    buf: list[dict] = []

    def flush():
        if not buf:
            return
        stmt = insert(m.counterparty_graphs).values(buf)
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["company_id"],
                set_={
                    "hhi_suppliers": stmt.excluded.hhi_suppliers,
                    "hhi_buyers": stmt.excluded.hhi_buyers,
                    "data": stmt.excluded.data,
                },
            )
        )
        buf.clear()

    for g in graphs:
        buf.append(
            {
                "company_id": g.company_id,
                "hhi_suppliers": g.hhi_suppliers,
                "hhi_buyers": g.hhi_buyers,
                "data": g.model_dump(mode="json"),
            }
        )
        n += 1
        if len(buf) >= batch:
            flush()
    flush()
    return n


def get_graph(conn: Connection, company_id: str) -> CounterpartyGraph | None:
    data = conn.execute(
        text("SELECT data FROM counterparty_graphs WHERE company_id = :u"), {"u": company_id}
    ).scalar()
    return CounterpartyGraph.model_validate(data) if data else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="пересчитать одну компанию")
    args = ap.parse_args()
    t0 = time.monotonic()
    engine = get_engine()
    # чтение потоком и запись — на разных соединениях: серверный курсор не терпит INSERT рядом
    with engine.connect() as read, engine.begin() as write:
        n = save_graphs(write, build_graphs(read, args.company))
    log.info("граф платежей: %d компаний за %.1f с", n, time.monotonic() - t0)


if __name__ == "__main__":
    main()
