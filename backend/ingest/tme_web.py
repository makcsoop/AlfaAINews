"""Telegram без входа: публичные страницы https://t.me/s/<канал> → raw_items.

Не нужен аккаунт: страница открывается как в браузере и отдаёт около 20 последних постов, более старые —
через ?before=<id>. Ограничения: только публичные каналы с включённым предпросмотром; страница может
отставать от канала (в архитектуре — опыт Honey Money), нет точной даты правки. Запросы — через общий
Fetcher: не больше 1 в секунду на хост.
"""

import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import lxml.html

from backend.db.engine import get_engine
from backend.ingest import store
from backend.ingest.http import Fetcher
from backend.ingest.telegram import message_to_item, username
from backend.schemas import RawItem, Source

log = logging.getLogger(__name__)

BACKFILL = 100  # постов на первый проход канала
MAX_PAGES = 6


def _count(s: str | None) -> int | None:
    """«29.6K» → 29600, «1.2M» → 1200000."""
    if not s:
        return None
    m = re.match(r"([\d.,]+)\s*([KM]?)", s.strip(), re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return int(value * {"": 1, "K": 1_000, "M": 1_000_000}[m.group(2).upper()])


def parse_page(html: bytes | str, channel: str) -> list[SimpleNamespace]:
    """Посты страницы как объекты с полями сообщения Telethon (id, message, date, …)."""
    doc = lxml.html.fromstring(html)
    posts = []
    for node in doc.xpath('//div[contains(@class, "tgme_widget_message ") and @data-post]'):
        post = node.get("data-post", "")
        if not post.lower().startswith(channel.lower() + "/"):
            continue
        mid = int(post.rsplit("/", 1)[1])
        text_nodes = node.xpath('.//div[contains(@class, "tgme_widget_message_text")]')
        text = None
        if text_nodes:
            for br in text_nodes[0].xpath(".//br"):
                br.tail = "\n" + (br.tail or "")
            text = text_nodes[0].text_content()
        times = node.xpath('.//a[contains(@class, "tgme_widget_message_date")]//time/@datetime')
        if not times:
            continue
        fwd_name = node.xpath(
            'string(.//*[contains(@class, "tgme_widget_message_forwarded_from_name")])'
        ).strip()
        views = node.xpath('string(.//span[contains(@class, "tgme_widget_message_views")])')
        posts.append(
            SimpleNamespace(
                id=mid,
                message=text,
                date=datetime.fromisoformat(times[0]).astimezone(UTC),
                edit_date=None,
                fwd_from=SimpleNamespace(from_name=fwd_name, from_id=None) if fwd_name else None,
                views=_count(views),
                forwards=None,
            )
        )
    return posts


async def ingest_channel_web(fetcher: Fetcher, src: Source) -> int:
    channel = username(src)
    engine = get_engine()
    with engine.begin() as conn:
        state = store.get_state(conn, src.id)
    last = state.get("last_native_id") or 0
    fetched_at = datetime.now(UTC)
    url = f"https://t.me/s/{channel}"
    collected: dict[int, SimpleNamespace] = {}
    before = None
    for _page in range(MAX_PAGES):
        resp = await fetcher.get(url + (f"?before={before}" if before else ""))
        if resp.status_code != 200:
            with engine.begin() as conn:
                store.save_state(
                    conn, src.id, last_status=resp.status_code, last_error=f"t.me/s: HTTP {resp.status_code}"
                )
            log.warning("%s: t.me/s HTTP %s", src.id, resp.status_code)
            return 0
        page = parse_page(resp.content, channel)
        if not page:
            break
        for p in page:
            if p.id > last:
                collected[p.id] = p
        oldest = min(p.id for p in page)
        # дочитали до уже сохранённого или набрали бэкфилл первого прохода
        if oldest <= last or (not last and len(collected) >= BACKFILL) or before == oldest:
            break
        before = oldest
    items: list[RawItem] = [
        i
        for p in sorted(collected.values(), key=lambda p: p.id)
        if (i := message_to_item(src, channel, p, fetched_at))
    ]
    newest = max([last, *collected])
    with engine.begin() as conn:
        n = store.insert_items(conn, items)
        store.save_state(
            conn,
            src.id,
            last_status=200,
            last_error=None,
            last_native_id=newest,
            items_total=(state.get("items_total") or 0) + n,
        )
    log.info(
        "%s (t.me/s/%s): постов %d, записано %d, последний id %d", src.id, channel, len(items), n, newest
    )
    return n


async def backfill_channel(fetcher: Fetcher, src: Source, since: datetime, max_pages: int = 800) -> int:
    """Архив канала назад до даты since: листаем ?before= от самого старого сохранённого поста.

    last_native_id (точку дочитывания вперёд) не трогаем. Повторный запуск продолжает с места остановки.
    """
    from sqlalchemy import Integer, cast, func, select

    from backend.db import models as m

    channel = username(src)
    engine = get_engine()
    with engine.connect() as conn:
        oldest = conn.execute(
            select(func.min(cast(m.raw_items.c.native_id, Integer))).where(m.raw_items.c.source_id == src.id)
        ).scalar()
    before, total, fetched_at = oldest, 0, datetime.now(UTC)
    for _page in range(max_pages):
        resp = await fetcher.get(f"https://t.me/s/{channel}" + (f"?before={before}" if before else ""))
        if resp.status_code != 200:
            log.warning("%s: архив t.me/s HTTP %s", src.id, resp.status_code)
            break
        page = parse_page(resp.content, channel)
        if not page:
            break
        items = [i for p in page if (i := message_to_item(src, channel, p, fetched_at))]
        with engine.begin() as conn:
            total += store.insert_items(conn, items)
        oldest_post = min(page, key=lambda p: p.id)
        if oldest_post.date < since or oldest_post.id == before:
            break
        before = oldest_post.id
    log.info("%s: архив до %s — записано %d", src.id, since.date(), total)
    return total
