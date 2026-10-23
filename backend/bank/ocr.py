"""Текст страниц обзоров ЦМИ: PDF — сканы без текстового слоя, страницы читает Qwen3-VL.

`python -m backend.bank.ocr [--workers 4] [--doc cmi_keyrate_2026_09]` — докачивает только нераспознанные
страницы, поэтому обрыв не страшен. Документы с enabled: false в config/bank_docs.yaml пропускаются.
"""

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf
import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.bank.vision import VisionOCR
from backend.db import models as m
from backend.db.engine import get_engine

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "config" / "bank_docs.yaml"
PDF_DIR = ROOT / "data" / "bank" / "pdf"
MODEL = "qwen/qwen3-vl-30b-a3b-instruct"
DPI = 150
PROMPT = (
    "Перепиши весь текст со страницы дословно, сохраняя абзацы и числа. Таблицы — строками через ' | '. "
    "Подписи к графикам — одной строкой «[График: …]», значения с графика не выдумывай. "
    "Повторяющийся юридический дисклеймер внизу страницы пропусти. Только текст страницы, без комментариев."
)


def bank_docs(enabled_only: bool = True) -> list[dict]:
    docs = yaml.safe_load(DOCS.read_text(encoding="utf-8"))["docs"]
    return [d for d in docs if d.get("enabled", True) or not enabled_only]


def _ocr_page(doc: dict, page_no: int) -> tuple[str, int, str | None, float]:
    pdf = pymupdf.open(PDF_DIR / doc["file"])
    # JPEG: цветная страница в PNG весит до 5 МБ, в JPEG — в несколько раз меньше, текст читается так же
    jpg = pdf[page_no - 1].get_pixmap(dpi=DPI).tobytes("jpg", jpg_quality=80)
    llm = VisionOCR(max_calls=2, timeout=240)
    text = llm.transcribe_image(model=MODEL, image=jpg, prompt=PROMPT)
    return doc["id"], page_no, text, llm.usage.cost


def run(workers: int = 4, only: str | None = None) -> dict:
    engine = get_engine()
    with engine.begin() as conn:
        m.metadata.create_all(conn, tables=[m.bank_pages])
        done = set(conn.execute(select(m.bank_pages.c.doc_id, m.bank_pages.c.page)).tuples())
    todo = [
        (d, p)
        for d in bank_docs()
        if not only or d["id"] == only
        for p in range(1, pymupdf.open(PDF_DIR / d["file"]).page_count + 1)
        if (d["id"], p) not in done
    ]
    log.info("страниц к распознаванию: %d", len(todo))
    stats = {"pages": 0, "failed": 0, "cost": 0.0}
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_ocr_page, d, p) for d, p in todo]
        for f in as_completed(futures):
            try:
                doc_id, page, text, cost = f.result()
            except Exception:
                log.exception("сбой распознавания страницы")
                stats["failed"] += 1
                continue
            stats["cost"] += cost
            if not text:
                stats["failed"] += 1
                log.warning("%s стр. %d: не распознана", doc_id, page)
                continue
            with engine.begin() as conn:
                conn.execute(
                    insert(m.bank_pages)
                    .values(doc_id=doc_id, page=page, text=text, model=MODEL, cost=cost)
                    .on_conflict_do_nothing()
                )
            stats["pages"] += 1
            log.info(
                "%s стр. %d: %d знаков (%d/%d, %.0f с)",
                doc_id,
                page,
                len(text),
                stats["pages"],
                len(todo),
                time.monotonic() - t0,
            )
    stats["cost"] = round(stats["cost"], 3)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--doc")
    args = ap.parse_args()
    log.info("итог распознавания: %s", run(args.workers, args.doc))


if __name__ == "__main__":
    main()
