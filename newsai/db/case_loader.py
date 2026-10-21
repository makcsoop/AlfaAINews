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

# Представления с типами поверх сырых таблиц; числа в CSV могут быть с запятой
VIEWS = {
    "case_companies": """
        CREATE OR REPLACE VIEW companies AS
        SELECT *,
               NULLIF(replace(revenue_company, ',', '.'), '')::numeric AS revenue,
               left(regexp_replace(mainokved_code, '[^0-9]', '', 'g'), 2) AS sector
        FROM case_companies
    """,
    "case_payments": """
        CREATE OR REPLACE VIEW payments AS
        SELECT payer, beneficiary,
               NULLIF(replace(pay_amt, ',', '.'), '')::numeric AS pay_amt,
               NULLIF(replace(pay_cnt, ',', '.'), '')::numeric::bigint AS pay_cnt
        FROM case_payments
    """,
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
    if kind in VIEWS:
        conn.execute(text(VIEWS[kind]))
    log.info("%s → %s: %d строк", path.name, kind, n)
    return kind, n


def load_case_dir(conn: Connection, directory: Path) -> dict[str, int]:
    loaded: dict[str, int] = {}
    for path in sorted(directory.glob("*.csv")):
        res = load_csv(conn, path)
        if res:
            loaded[res[0]] = res[1]
    return loaded
