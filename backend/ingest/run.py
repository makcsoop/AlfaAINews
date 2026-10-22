"""Воркер сбора: `python -m backend.ingest.run [--once] [--source prime]`.

Каждая лента опрашивается раз в `poll_seconds`; сбой одной ленты не останавливает остальные.
"""

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from backend.db.engine import get_engine
from backend.ingest import store
from backend.ingest.http import Fetcher
from backend.ingest.registry import load_sources
from backend.ingest.rss import entry_to_item, fetch_text, native_id, poll_feed
from backend.schemas import Source

log = logging.getLogger("backend.ingest")


async def ingest_rss(fetcher: Fetcher, src: Source) -> int:
    engine = get_engine()
    with engine.begin() as conn:
        state = store.get_state(conn, src.id)
    try:
        feed = await poll_feed(fetcher, src, state.get("etag"), state.get("last_modified"))
    except Exception as e:
        with engine.begin() as conn:
            store.save_state(conn, src.id, last_status=0, last_error=str(e)[:500])
        log.warning("%s: лента недоступна: %s", src.id, e)
        return 0

    with engine.begin() as conn:
        known = store.known_native_ids(conn, src.id, [native_id(e) for e in feed.entries])
    fresh = [e for e in feed.entries if native_id(e) not in known]

    fetched_at = datetime.now(UTC)
    items = []
    for entry in fresh:
        page_text = None
        if src.full_text == "fetch" and not src.is_partial and entry.get("link"):
            page_text = await fetch_text(fetcher, entry["link"])
        item = entry_to_item(src, entry, fetched_at, page_text)
        if item:
            items.append(item)

    with engine.begin() as conn:
        n = store.insert_items(conn, items)
        store.save_state(
            conn,
            src.id,
            etag=feed.etag,
            last_modified=feed.last_modified,
            last_status=feed.status,
            last_error=feed.bozo,
            items_total=(state.get("items_total") or 0) + n,
        )
    log.info(
        "%s: статус %s, в ленте %d, новых %d, записано %d",
        src.id,
        feed.status,
        len(feed.entries),
        len(fresh),
        n,
    )
    return n


async def run_source_forever(fetcher: Fetcher, src: Source) -> None:
    while True:
        try:
            await ingest_rss(fetcher, src)
        except Exception:
            log.exception("%s: сбой прохода", src.id)
        await asyncio.sleep(src.poll_seconds)


STORIES_EVERY = 300  # секунд между пересборками сюжетов


def _rebuild_stories() -> dict:
    from backend.stories.cluster import rebuild

    with get_engine().begin() as conn:
        return rebuild(conn)


async def stories_forever() -> None:
    """Склейка идёт в том же воркере, что и сбор: раз в 5 минут, в отдельном потоке."""
    while True:
        await asyncio.sleep(STORIES_EVERY)
        try:
            log.info("сюжеты: %s", await asyncio.to_thread(_rebuild_stories))
        except Exception:
            log.exception("сбой склейки")


async def main_async(once: bool, only: list[str] | None) -> None:
    sources = [s for s in load_sources(enabled_only=not only) if s.kind == "rss"]
    if only:
        sources = [s for s in sources if s.id in only]
    log.info("ленты: %s", ", ".join(s.id for s in sources))
    fetcher = Fetcher()
    try:
        if once:
            await asyncio.gather(*(ingest_rss(fetcher, s) for s in sources))
        else:
            await asyncio.gather(*(run_source_forever(fetcher, s) for s in sources), stories_forever())
    finally:
        await fetcher.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="один проход и выход")
    ap.add_argument("--source", action="append", help="только эти id (можно выключенные)")
    args = ap.parse_args()
    asyncio.run(main_async(args.once, args.source))


if __name__ == "__main__":
    main()
