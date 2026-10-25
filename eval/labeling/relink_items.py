"""Дописать в CSV меток материалы сюжета (`item_ids`) для партий, размеченных до этой колонки.

`uv run python eval/labeling/relink_items.py data/labeling/labels-oct-60.csv [--origin razmetka.xlsx]`

Пересклейка меняет id сюжета, материалы остаются — по ним метрики находят пару (backend.eval.metrics).
У старой партии материалов в файле нет, но есть то, что видел разметчик: ссылки источников и заголовок.
Материал ищется в raw_items по ссылке, а без ссылки — по точному заголовку. CSV перезаписывается с колонкой
item_ids; пары, для которых ничего не нашлось, остаются без неё (в отчёте метрик они — «потеряно»).
"""

import argparse
import csv
import re
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openpyxl import load_workbook
from sqlalchemy import Connection

from eval.labeling.sheet import SHEET

ROOT = Path(__file__).resolve().parents[2]
_URL = re.compile(r"https?://\S+")


def origin_keys(path: Path) -> dict[str, tuple[list[str], str]]:
    """pair_id → (ссылки источников, заголовок) из выданного файла разметчика."""
    wb = load_workbook(path, data_only=True)
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb.worksheets[0]
    header = [str(c.value or "") for c in ws[1]]
    col = {key: next(i for i, h in enumerate(header) if f"({key})" in h or h == key) for key in ("pair_id",)}
    story_col = header.index("Новость")
    sources_col = header.index("Источники")
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        pid = row[col["pair_id"]]
        if not pid:
            continue
        title = str(row[story_col] or "").split("\n", 1)[0].strip()
        out[str(pid)] = (_URL.findall(str(row[sources_col] or "")), title)
    return out


def find_items(conn: Connection, urls: Iterable[str], title: str) -> list[str]:
    from sqlalchemy import or_, select

    from backend.db import models as m

    urls = list(urls)
    cond = [m.raw_items.c.url.in_(urls)] if urls else []
    if title:
        cond.append(m.raw_items.c.title == title)
    if not cond:
        return []
    return sorted(conn.execute(select(m.raw_items.c.id).where(or_(*cond))).scalars())


def relink(labels: Path, origin: Path) -> tuple[int, int]:
    from backend.db.engine import get_engine

    keys = origin_keys(origin)
    with labels.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "item_ids" not in fields:
        fields.append("item_ids")
    found = 0
    with get_engine().connect() as conn:
        for r in rows:
            if r.get("item_ids"):
                found += 1
                continue
            urls, title = keys.get(r["pair_id"], ([], ""))
            ids = find_items(conn, urls, title)
            r["item_ids"] = " ".join(ids)
            found += bool(ids)
    with labels.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return found, len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("labels", type=Path)
    ap.add_argument("--origin", type=Path, default=ROOT / "data" / "labeling" / "razmetka.xlsx")
    args = ap.parse_args()
    found, total = relink(args.labels, args.origin)
    print(f"материалы найдены для {found} из {total} пар; CSV: {args.labels}")


if __name__ == "__main__":
    main()
