"""Архив аналитических Telegram-каналов через t.me/s, без входа.

`uv run python scripts/backfill_telegram.py [--since 2026-07-10]`

Нужен для знаний аналитиков рынка и консенсуса ожиданий перед заседаниями ЦБ (ТЗ, день 3).
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ingest.http import Fetcher
from backend.ingest.registry import load_sources
from backend.ingest.tme_web import backfill_channel

# аналитические и рыночные каналы + ЦБ (фактические решения); новостные ленты агентств архив не нужен
ANALYTIC = [
    "tg_centralbank_russia",
    "tg_dohod",
    "tg_smartlabnews",
    "tg_alfa_investments",
    "tg_markettwits",
    "tg_banksta",
    "tg_swaneconomy",
    "tg_cb_economics",
    "tg_prime1",
]


async def main(since: datetime) -> None:
    sources = {s.id: s for s in load_sources()}
    fetcher = Fetcher()
    try:
        for sid in ANALYTIC:
            await backfill_channel(fetcher, sources[sid], since)
    finally:
        await fetcher.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-10")
    args = ap.parse_args()
    asyncio.run(main(datetime.fromisoformat(args.since).replace(tzinfo=UTC)))
