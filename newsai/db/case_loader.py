"""Загрузка CSV кейса в Postgres как есть: все колонки текстом, затем типизированные представления.

Тип таблицы определяется по заголовку, а не по имени файла: в брифе у физлиц и платежей
одно имя файла (df_Fid_Uid_link_hack.csv), поэтому полагаться на имя нельзя.
"""

import csv
import logging
import re
from pathlib import Path

from sqlalchemy import Connection, text

log = logging.getLogger(__name__)

KINDS = {
    "case_payments": {"PAYER", "BENEFICIARY"},
    "case_persons": {"SYNTHETIC_F", "SYNTHETIC_U"},
    "case_companies": {"SYNTHETIC_U", "MAINOKVED_CODE"},
}


def _num(col: str) -> str:
    return f"NULLIF(replace({col}, ',', '.'), '')::numeric"


def _flag(col: str) -> str:
    # в CSV флаги записаны как '0.0' / '1.0'
    return f"({_num(col)} = 1)"


# Представления с типами поверх сырых таблиц
VIEWS = {
    "case_companies": f"""
        CREATE OR REPLACE VIEW companies AS
        SELECT synthetic_u AS company_id, group_id, okfs_name, company_age_years_gradation AS company_age,
               NULLIF(eco_mark_label, '') AS eco_stage,
               mainokved_code AS okved_code, mainokved_name AS okved_name,
               left(regexp_replace(mainokved_code, '[^0-9]', '', 'g'), 2) AS sector,
               amount_workers_fns_gradation AS workers_fns,
               NULLIF(amount_workers_spark_gradation, '') AS workers_spark,
               cl_type AS client_type, {_flag("negativemark")} AS negative_mark,
               {_flag("cl_product_rko_mark")} AS rko, {_flag("cl_product_gar_mark")} AS guarantees,
               {_flag("cl_product_loa_mark")} AS loans, {_flag("cl_product_lea_mark")} AS leasing,
               {_flag("cl_product_dep_mark")} AS deposits, {_flag("cl_product_zpp_mark")} AS payroll,
               {_flag("factoring_mark")} AS factoring, {_flag("fts_flag")} AS fts_flag,
               string_to_array(NULLIF(fts_country_name, ''), ', ') AS fts_countries,
               {_num("revenue_company")} AS revenue, city, region
        FROM case_companies
        -- id, повторяющийся у разных компаний, неоднозначен: в профиль и граф не берём
        WHERE synthetic_u NOT IN (SELECT synthetic_u FROM case_companies GROUP BY 1 HAVING count(*) > 1)
    """,
    "case_payments": f"""
        CREATE OR REPLACE VIEW payments AS
        SELECT payer, beneficiary, {_num("pay_amt")} AS pay_amt, {_num("pay_cnt")}::bigint AS pay_cnt,
               payer = beneficiary AS is_self
        FROM case_payments
    """,
}

INDEXES = {
    "case_companies": [
        "CREATE INDEX ON case_companies (synthetic_u)",
        "CREATE INDEX ON case_companies (group_id)",
    ],
    "case_payments": ["CREATE INDEX ON case_payments (payer)", "CREATE INDEX ON case_payments (beneficiary)"],
    "case_persons": [
        "CREATE INDEX ON case_persons (synthetic_f)",
        "CREATE INDEX ON case_persons (synthetic_u)",
    ],
}


def _read_head(path: Path) -> tuple[str, str]:
    raw = path.open("rb").read(64 * 1024)
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}: неизвестная кодировка")


def detect(path: Path) -> tuple[str | None, str, str, list[str]]:
    head, enc = _read_head(path)
    dialect = csv.Sniffer().sniff(head.splitlines()[0], delimiters=",;\t|")
    header = next(csv.reader([head.splitlines()[0]], dialect))
    cols = {c.strip().upper() for c in header}
    kind = next((k for k, need in KINDS.items() if need <= cols), None)
    return kind, enc, dialect.delimiter, header


def _col(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.strip().lower()) or "col"


def load_csv(conn: Connection, path: Path) -> tuple[str, int] | None:
    kind, enc, delim, header = detect(path)
    if kind is None:
        log.warning("%s: не распознан тип по заголовку %s", path.name, header[:6])
        return None
    cols = [_col(c) for c in header]
    conn.execute(text(f"DROP TABLE IF EXISTS {kind} CASCADE"))
    conn.execute(text(f"CREATE TABLE {kind} ({', '.join(f'{c} text' for c in cols)})"))

    raw = conn.connection.driver_connection  # psycopg3: COPY напрямую, без pandas
    with path.open(encoding=enc, newline="") as f, raw.cursor() as cur:
        reader = csv.reader(f, delimiter=delim)
        next(reader)
        with cur.copy(f"COPY {kind} ({', '.join(cols)}) FROM STDIN") as copy:
            for row in reader:
                copy.write_row([v if v != "" else None for v in row])
    n = conn.execute(text(f"SELECT count(*) FROM {kind}")).scalar_one()
    for ddl in INDEXES.get(kind, []):
        conn.execute(text(ddl))
    if kind in VIEWS:
        conn.execute(text(VIEWS[kind]))
    if kind == "case_companies":
        dups = conn.execute(
            text("SELECT synthetic_u, count(*) FROM case_companies GROUP BY 1 HAVING count(*) > 1")
        ).all()
        for uid, cnt in dups:
            log.warning(
                "%s встречается %d раз у разных компаний — исключён из представления companies", uid, cnt
            )
    log.info("%s → %s: %d строк", path.name, kind, n)
    return kind, n


def load_case_dir(conn: Connection, directory: Path) -> dict[str, int]:
    loaded: dict[str, int] = {}
    for path in sorted(directory.glob("*.csv")):
        res = load_csv(conn, path)
        if res:
            loaded[res[0]] = res[1]
    return loaded
