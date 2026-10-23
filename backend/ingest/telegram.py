"""Сбор Telegram-каналов через Telethon → raw_items.

Каналы — источники kind: telegram в config/sources.yaml (username берётся из url). Для каждого канала
дочитываем посты от последнего сохранённого id (feed_state.last_native_id): обрыв связи не теряет посты.
Первый проход берёт последние BACKFILL постов. FloodWait до 2 минут Telethon пережидает сам.
Сохраняем пересылки (fwd_from — видно копирование и первоисточник), дату правки и просмотры/пересылки.
Реклама (erid, «реклама») уходит в отсев по config/filters.yaml.

Сессия — только из TG_SESSION_STRING (.env, Docker-секрет); создаётся scripts/telegram_login.py.
Условия Telegram API п. 1.5: данные не используются для обучения моделей — пометка в THIRD_PARTY_NOTICES.
"""

import logging
import os
import re
from datetime import UTC, datetime

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.sessions import StringSession

from backend.db.engine import get_engine
from backend.ingest import store
from backend.ingest.normalize import clean_text, content_hash, item_id, simhash
from backend.schemas import RawItem, Source
from backend.stories.filters import drop_reason

log = logging.getLogger(__name__)

BACKFILL = 200
TITLE_MAX = 200


def _env(key: str) -> str:
    """Только переменные окружения (инвариант 9): в Docker их передаёт compose, с хоста — export."""
    return os.environ.get(key, "").strip()


def credentials() -> tuple[int, str, str] | None:
    api_id, api_hash, session = (_env(k) for k in ("TG_API_ID", "TG_API_HASH", "TG_SESSION_STRING"))
    if not (api_id and api_hash and session):
        return None
    return int(api_id), api_hash, session


def username(src: Source) -> str:
    return (src.url or "").rstrip("/").rsplit("/", 1)[-1].lstrip("@")


# значки и хештеги в начале строки: «❗️», «⚡️», «▪️», «🇷🇺#SBER»
_LEADING_NOISE = re.compile(r"^(?:#\w+\s*|[^\w«\"(\[])+")
_ONLY_TAGS = re.compile(r"^[\W_]*(?:[#$]\w+[\W_]*)+$")


def post_title(text: str) -> str:
    """Заголовок поста: первая содержательная строка без эмодзи и хештегов в начале."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    line = next((ln for ln in lines if not _ONLY_TAGS.match(ln) and len(ln.split()) >= 2), lines[0])
    line = _LEADING_NOISE.sub("", line).strip() or line
    return line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"


def message_to_item(src: Source, channel: str, msg, fetched_at: datetime) -> RawItem | None:
    text = clean_text(getattr(msg, "message", None))
    if not text:
        return None  # только медиа без подписи
    title = post_title(text)
    fwd = None
    if getattr(msg, "fwd_from", None):
        f = msg.fwd_from
        peer = getattr(f, "from_id", None)
        fwd = (
            f.from_name
            or (f"channel:{getattr(peer, 'channel_id', '')}" if getattr(peer, "channel_id", None) else None)
            or "unknown"
        )
    metrics = {k: v for k, v in (("views", msg.views), ("forwards", msg.forwards)) if v is not None}
    item = RawItem(
        id=item_id(src.id, str(msg.id)),
        source_id=src.id,
        native_id=str(msg.id),
        tier=src.tier,
        url=f"https://t.me/{channel}/{msg.id}",
        title=title,
        text=text,
        is_partial=False,
        published_at=msg.date.astimezone(UTC),
        updated_at=msg.edit_date.astimezone(UTC) if msg.edit_date else None,
        fetched_at=fetched_at,
        fwd_from=fwd,
        metrics=metrics,
        content_hash=content_hash(title, text),
        simhash=simhash(text),
    )
    item.dropped_reason = drop_reason(item)
    return item


async def ingest_channel(client: TelegramClient, src: Source) -> int:
    channel = username(src)
    engine = get_engine()
    with engine.begin() as conn:
        state = store.get_state(conn, src.id)
    last = state.get("last_native_id") or 0
    fetched_at = datetime.now(UTC)
    items: list[RawItem] = []
    newest = last
    try:
        kwargs = {"min_id": last, "reverse": True} if last else {"limit": BACKFILL}
        async for msg in client.iter_messages(channel, **kwargs):
            newest = max(newest, msg.id)
            item = message_to_item(src, channel, msg, fetched_at)
            if item:
                items.append(item)
    except (UsernameInvalidError, UsernameNotOccupiedError, ChannelPrivateError, ValueError) as e:
        with engine.begin() as conn:
            store.save_state(conn, src.id, last_status=0, last_error=f"канал недоступен: {e}"[:500])
        log.warning("%s (@%s): канал недоступен: %s", src.id, channel, e)
        return 0
    except FloodWaitError as e:  # дольше порога Telethon — отложим канал до следующего круга
        log.warning("%s: FloodWait %s с — пропускаю круг", src.id, e.seconds)
        return 0
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
    log.info("%s (@%s): постов %d, записано %d, последний id %d", src.id, channel, len(items), n, newest)
    return n


def make_client() -> TelegramClient | None:
    creds = credentials()
    if creds is None:
        return None
    api_id, api_hash, session = creds
    # один процесс на сессию: два процесса на одной сессии дают «database is locked» и риск бана
    return TelegramClient(StringSession(session), api_id, api_hash, flood_sleep_threshold=120)
