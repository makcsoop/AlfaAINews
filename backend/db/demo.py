"""Демо-база с нуля одной командой (А4) и снимок знаний Р1 для стенда (А6).

    python -m backend.db.demo --export        # на машине Р1: снимок в data/snapshot/ (не в git)
    python -m backend.db.demo                 # на любой машине: база для ленты по шагам ниже
    python -m backend.db.demo --llm           # то же, но факты подтверждённых сюжетов типирует модель

Шаги: таблицы → демо-компании фикстуры (сам сюжет фикстуры — только `--with-fixture`) → CSV кейса
(или синтетика) → граф платежей → профили (Р2) → снимок знаний
(страницы ЦМИ, реестр позиций, консенсус, архив материалов) → один проход сбора → склейка за 72 ч → факты.
Реестр ЦМИ и консенсус берутся из снимка — без повторного OCR и без вызовов Kimi; если снимка нет,
консенсус строится заново по архиву каналов (`--llm`), а реестр — `python -m backend.bank.positions`.
Снимок содержит тексты ЦМИ и чужие материалы: передаётся вручную, в git не попадает (`data/snapshot/`).
"""

import argparse
import asyncio
import gzip
import json
import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import Connection, DateTime, func, select

from backend.db import models as m
from backend.db.engine import get_engine
from backend.db.init import _upsert, create_schema, load_fixtures, refresh_graph_if_stale

log = logging.getLogger(__name__)

SNAPSHOT = Path(__file__).resolve().parents[2] / "data" / "snapshot"
# что переносим: страницы ЦМИ, реестр, консенсус и знания, архив материалов (похожие случаи, консенсус)
TABLES = [m.bank_pages, m.bank_positions, m.market_knowledge, m.raw_items]
ARCHIVE_DAYS = 120  # архив материалов в снимке: каналы аналитиков с июля, «Статистик» смотрит 120 дней
BATCH = 500


def _json(v):
    if isinstance(v, datetime | date):
        return v.isoformat()
    raise TypeError(type(v))


def export(conn: Connection, days: int = ARCHIVE_DAYS) -> dict[str, int]:
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    out = {}
    for t in TABLES:
        q = select(t)
        if t is m.raw_items:
            q = q.where(t.c.published_at >= datetime.now(UTC) - timedelta(days=days))
        rows = [dict(r._mapping) for r in conn.execute(q)]
        if t is m.raw_items:
            for r in rows:
                r["story_id"] = None  # сюжеты стенд склеивает сам
        with gzip.open(SNAPSHOT / f"{t.name}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, default=_json)
        out[t.name] = len(rows)
    return out


def load(conn: Connection) -> dict[str, int]:
    out = {}
    for t in TABLES:
        path = SNAPSHOT / f"{t.name}.json.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as f:
            rows = json.load(f)
        dt_cols = [c.name for c in t.columns if isinstance(c.type, DateTime)]
        cols = {c.name for c in t.columns}
        for r in rows:
            for c in list(r):
                if c not in cols:  # колонка из более новой схемы — пропускаем
                    del r[c]
            for c in dt_cols:
                if r.get(c):
                    r[c] = datetime.fromisoformat(r[c])
        for i in range(0, len(rows), BATCH):
            _upsert(conn, t, rows[i : i + BATCH])
        out[t.name] = len(rows)
    return out


def _step(name: str, fn):
    t0 = time.monotonic()
    log.info("▶ %s", name)
    res = fn()
    log.info("✔ %s: %s (%.0f с)", name, res, time.monotonic() - t0)
    return res


def _case(with_fixture: bool = False) -> dict:
    """Таблицы, демо-компании из фикстуры и CSV кейса. Сюжет фикстуры (11.09) — только с `with_fixture`:
    в живой ленте синтетическому сюжету не место."""
    from backend.config import get_settings
    from backend.db.case_loader import load_case_dir
    from backend.db.init import SYNTHETIC_DIR

    case_dir = Path(get_settings().case_data_dir)
    if not any(case_dir.glob("*.csv")):
        log.warning("в %s нет CSV кейса — синтетика %s (выдуманные компании)", case_dir, SYNTHETIC_DIR)
        case_dir = SYNTHETIC_DIR
    with get_engine().begin() as conn:
        create_schema(conn)
        load_fixtures(conn, profiles_only=not with_fixture)
        return load_case_dir(conn, case_dir)


def _profiles() -> int:
    from backend.profile.exposures import with_exposures
    from backend.profile.load import load_profiles
    from backend.profile.publish import read_graphs, save_profiles

    base = load_profiles(None)
    with get_engine().begin() as conn:
        graphs = read_graphs(conn)
        return save_profiles(conn, (with_exposures(p, graphs.get(cid)) for cid, p in base.items()))


def _ingest() -> str:
    from backend.ingest.run import main_async

    asyncio.run(main_async(once=True, only=None))
    return "один проход RSS и t.me/s"


def _facts(use_llm: bool) -> dict:
    from backend.extract.run import run

    llm = None
    if use_llm:
        from backend.llm.port import ClientJsonLLM

        llm = ClientJsonLLM(max_calls=300)  # шаг extract: ≈0,02 ₽ за вызов
    # вызовы модели параллельно: по одному заливка шла 40 минут (≈ 8 с на вызов)
    workers = int(os.environ.get("DEMO_EXTRACT_WORKERS", "6"))
    with get_engine().begin() as conn:
        stats = run(conn, hours=72, llm=llm, force=use_llm, workers=workers)
    if llm:
        stats["llm_rub"] = round(llm.usage.cost, 2)
    return stats


def _consensus(use_llm: bool) -> str:
    with get_engine().connect() as conn:
        n = conn.execute(
            select(func.count()).where(m.market_knowledge.c.id.like("mk_consensus_%"))
        ).scalar()
    if n:
        return f"есть в базе: {n} заседаний"
    if not use_llm:
        return "нет снимка и нет --llm — консенсус пропущен"
    from backend.knowledge.consensus import build, meetings
    from backend.llm.port import ClientJsonLLM

    llm = ClientJsonLLM(max_calls=400)
    with get_engine().begin() as conn:
        for mt in meetings():
            build(conn, mt, llm)
    return f"построен заново, {llm.usage.cost:.2f} ₽"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "trafilatura", "telethon"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="снять снимок знаний в data/snapshot/ и выйти")
    ap.add_argument("--days", type=int, default=ARCHIVE_DAYS, help="глубина архива материалов в снимке")
    ap.add_argument("--llm", action="store_true", help="факты и консенсус моделью (LLM_MODE=live)")
    ap.add_argument("--skip-ingest", action="store_true", help="без свежего сбора — только снимок")
    ap.add_argument(
        "--with-fixture", action="store_true",
        help="залить и сюжет фикстуры «ставка 14%%» (11.09); по умолчанию — только демо-компании",
    )
    args = ap.parse_args()

    if args.export:
        with get_engine().connect() as conn:
            log.info("снимок в %s: %s", SNAPSHOT, export(conn, args.days))
        return

    _step("таблицы, демо-компании, CSV кейса", lambda: _case(args.with_fixture))
    _step("граф платежей", refresh_graph_if_stale)
    _step("профили клиентов (Р2)", _profiles)

    def _snapshot() -> dict:
        with get_engine().begin() as conn:
            return load(conn) or {"снимок": f"нет файлов в {SNAPSHOT}"}

    _step("снимок знаний", _snapshot)
    if not args.skip_ingest:
        _step("сбор", _ingest)

    def _stories() -> dict:
        from backend.stories.cluster import rebuild

        with get_engine().begin() as conn:
            return rebuild(conn, hours=72)

    _step("склейка за 72 ч", _stories)
    _step("факты", lambda: _facts(args.llm))
    _step("консенсус к заседаниям ЦБ", lambda: _consensus(args.llm))
    tables = [m.raw_items, m.stories, m.event_facts, m.bank_positions, m.market_knowledge, m.company_profiles]
    with get_engine().connect() as conn:
        counts = {t.name: conn.execute(select(func.count()).select_from(t)).scalar() for t in tables}
    log.info("готово: %s — лента на http://localhost:3000", counts)


if __name__ == "__main__":
    main()
