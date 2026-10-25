"""Воркер сбора: `python -m backend.ingest.run [--once] [--source prime]`.

Каждая лента опрашивается раз в `poll_seconds`; сбой одной ленты не останавливает остальные.
"""

import argparse
import asyncio
import logging
import os
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
        try:
            page_text = None
            if src.full_text == "fetch" and not src.is_partial and entry.get("link"):
                page_text = await fetch_text(fetcher, entry["link"])
            item = entry_to_item(src, entry, fetched_at, page_text)
        except Exception as e:  # один битый элемент не роняет проход всей ленты
            log.warning("%s: элемент %s пропущен: %r", src.id, entry.get("link") or entry.get("id"), e)
            continue
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
TELEGRAM_EVERY = 180  # секунд между кругами по каналам
EXTRACT_LLM_CALLS = 60  # вызовов модели за проход склейки (раз в 5 минут): ≈1 ₽ в худшем случае


async def telegram_round(client, channels: list[Source]) -> int:
    from backend.ingest.telegram import ingest_channel

    total = 0
    for src in channels:  # по очереди: один клиент, без параллельных запросов к API
        try:
            total += await ingest_channel(client, src)
        except Exception:
            log.exception("%s: сбой Telegram", src.id)
    return total


async def telegram_web_forever(fetcher: Fetcher, channels: list[Source], once: bool) -> None:
    """Без аккаунта: публичные страницы t.me/s. Каналы по очереди, общий лимит 1 запрос/с на хост."""
    from backend.ingest.tme_web import ingest_channel_web

    while True:
        total = 0
        for src in channels:
            try:
                total += await ingest_channel_web(fetcher, src)
            except Exception:
                log.exception("%s: сбой t.me/s", src.id)
        log.info("Telegram (t.me/s, без входа): круг по %d каналам, записано %d", len(channels), total)
        if once:
            return
        await asyncio.sleep(TELEGRAM_EVERY)


async def telegram_forever(fetcher: Fetcher, channels: list[Source], once: bool = False) -> None:
    """Telethon, если есть сессия (TG_SESSION_STRING); иначе — публичные страницы t.me/s без входа."""
    from backend.ingest.telegram import make_client

    if not channels:
        return
    client = make_client()
    if client is None:
        await telegram_web_forever(fetcher, channels, once)
        return
    async with client:
        while True:
            log.info(
                "Telegram: круг по %d каналам, записано %d",
                len(channels),
                await telegram_round(client, channels),
            )
            if once:
                return
            await asyncio.sleep(TELEGRAM_EVERY)


def _rebuild_stories() -> dict:
    """Склейка, затем факты по новым и изменившимся сюжетам.

    EXTRACT_LLM=true — подтверждённые сюжеты типирует модель (шаг extract общего клиента, потолок вызовов
    за проход); иначе только правила.
    """
    from backend.extract.run import run as extract_facts
    from backend.stories.cluster import rebuild

    llm = None
    if os.environ.get("EXTRACT_LLM", "").strip().lower() in {"1", "true", "yes"}:
        from backend.llm.port import ClientJsonLLM

        llm = ClientJsonLLM(max_calls=EXTRACT_LLM_CALLS)
    with get_engine().begin() as conn:
        stats = rebuild(conn)
    # промежуточные коммиты: сбой посреди прохода не откатывает уже оплаченные разборы моделью
    with get_engine().connect() as conn:
        facts = extract_facts(conn, llm=llm, commit_every=10)
        conn.commit()
    stats["facts"] = facts["stories"]
    if facts.get("failed"):
        stats["facts_failed"] = facts["failed"]
    if llm:
        stats["llm_rub"] = round(llm.usage.cost, 3)
    return stats


async def stories_forever() -> None:
    """Склейка и факты идут в том же воркере, что и сбор: раз в 5 минут, в отдельном потоке."""
    while True:
        await asyncio.sleep(STORIES_EVERY)
        try:
            log.info("сюжеты: %s", await asyncio.to_thread(_rebuild_stories))
        except Exception:
            log.exception("сбой склейки")


async def main_async(once: bool, only: list[str] | None) -> None:
    registry = load_sources(enabled_only=not only)
    if only:
        registry = [s for s in registry if s.id in only]
    sources = [s for s in registry if s.kind == "rss"]
    channels = [s for s in registry if s.kind == "telegram"]
    log.info("ленты: %s", ", ".join(s.id for s in sources))
    fetcher = Fetcher()
    try:
        if once:
            await asyncio.gather(
                *(ingest_rss(fetcher, s) for s in sources), telegram_forever(fetcher, channels, once=True)
            )
        else:
            await asyncio.gather(
                *(run_source_forever(fetcher, s) for s in sources),
                telegram_forever(fetcher, channels),
                stories_forever(),
            )
    finally:
        await fetcher.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="один проход и выход")
    ap.add_argument("--source", action="append", help="только эти id (можно выключенные)")
    args = ap.parse_args()
    asyncio.run(main_async(args.once, args.source))


if __name__ == "__main__":
    main()
