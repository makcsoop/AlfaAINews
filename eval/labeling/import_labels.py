"""Заполненные Excel-файлы разметчиков → CSV меток → согласие и метрики.

`uv run python eval/labeling/import_labels.py data/labeling/razmetka_*.xlsx [--db] [--no-metrics]`

1. Читает файлы (eval/labeling/sheet.py), печатает ошибки и нарушения ТЗ. Строки с ошибкой не принимаются,
   строки без значимости считаются неразмеченными.
2. Пишет `data/labeling/labels-<партия>.csv` в формате /labeling/export.csv: «важно» = значимость ≥ 2.
3. `--db` — кладёт метки в таблицу labels (повторный импорт того же разметчика заменяет его метки).
4. Считает согласие на общих парах (backend.eval.agreement) и nDCG@5 / precision@5 против бейзлайна
   (backend.eval.metrics) → reports/agreement.md и reports/metrics.md.
"""

import argparse
import csv
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from eval.labeling.sheet import LabelRow, ReadResult, load_channels, read_sheet

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = [
    "pair_id",
    "story_id",
    "company_id",
    "labeler",
    "relevance",
    "important",
    "main_channel",
    "sign",
    "comment",
    "labeled_at",
]


def write_csv(path: Path, labels: Sequence[LabelRow]) -> None:
    at = datetime.now(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM — Excel откроет кириллицу
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for x in labels:
            w.writerow(
                [
                    x.pair_id,
                    x.story_id,
                    x.company_id,
                    x.labeler,
                    x.relevance,
                    int(x.relevance >= 2),
                    x.main_channel or "",
                    "" if x.sign is None else x.sign,
                    x.comment or "",
                    at,
                ]
            )


def save_db(labels: Sequence[LabelRow]) -> int:
    from sqlalchemy import delete, insert

    from backend.db import models as m
    from backend.db.engine import get_engine

    with get_engine().begin() as conn:
        for name in {x.labeler for x in labels}:
            pids = [x.pair_id for x in labels if x.labeler == name]
            conn.execute(delete(m.labels).where(m.labels.c.labeler == name, m.labels.c.pair_id.in_(pids)))
        conn.execute(
            insert(m.labels),
            [
                {
                    "pair_id": x.pair_id,
                    "labeler": x.labeler,
                    "relevance": x.relevance,
                    "important": x.relevance >= 2,
                    "main_channel": x.main_channel,
                    "sign": x.sign,
                    "comment": x.comment,
                }
                for x in labels
            ],
        )
    return len(labels)


def report(results: Sequence[ReadResult]) -> bool:
    """Печатает итог по файлам; False — есть строки с ошибками."""
    ok = True
    for r in results:
        print(f"{r.labeler}: принято {len(r.labels)}, не размечено {len(r.unfilled)}, ошибок {len(r.errors)}")
        for e in r.errors:
            print(f"  ошибка: {e}")
            ok = False
        for w in r.warnings:
            print(f"  замечание: {w}")
    return ok


def run_metrics(path: Path) -> None:
    from backend.eval import agreement, metrics

    labelers = {x.labeler for x in agreement.read_labels(path)}
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    if len(labelers) > 1:
        text = agreement.format_report(agreement.agreement(agreement.read_labels(path)))
        (out / "agreement.md").write_text(f"# Согласие разметчиков\n\n{text}\n", encoding="utf-8")
        print(text)
    else:
        print("согласие не считается: разметчик один")
    result = metrics.evaluate(metrics.load_pairs(path))
    metrics.REPORT.write_text(
        metrics.format_report(result, note=f"Источник разметки: `{path.name}`."), encoding="utf-8"
    )
    print(metrics.REPORT.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--batch", default="oct-60")
    ap.add_argument("--out", type=Path, help="CSV меток, по умолчанию data/labeling/labels-<партия>.csv")
    ap.add_argument("--db", action="store_true", help="записать метки в таблицу labels")
    ap.add_argument("--no-metrics", action="store_true")
    ap.add_argument(
        "--origin",
        type=Path,
        default=ROOT / "data" / "labeling" / "razmetka.xlsx",
        help="выданный файл: из него берутся связки пар, если в заполненном нет листа _meta",
    )
    ap.add_argument("--labeler", help="имя разметчика вместо записанного в файле")
    args = ap.parse_args()
    names = load_channels()
    origin = args.origin if args.origin.exists() else None
    results = [read_sheet(f, names, origin, args.labeler) for f in args.files]
    ok = report(results)
    labels = [x for r in results for x in r.labels]
    if not labels:
        raise SystemExit("нет ни одной размеченной пары")
    out = args.out or ROOT / "data" / "labeling" / f"labels-{args.batch}.csv"
    write_csv(out, labels)
    print(f"метки: {out} ({len(labels)} строк)")
    if args.db:
        print(f"в таблицу labels записано: {save_db(labels)}")
    if not ok:
        print("есть строки с ошибками — исправьте файлы и запустите импорт снова; метрики пока по принятым")
    if not args.no_metrics:
        run_metrics(out)


if __name__ == "__main__":
    main()
