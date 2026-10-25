"""Архив Telegram из выгрузки Telethon (`tg_export.py` пакета «источники») → raw_items.

`python -m backend.ingest.tg_archive <папка с tg_*.jsonl> [--since 2026-04-01]`

Одна строка jsonl — пост: source_id, username, msg_id, date_utc, edit_date_utc, text, views, forwards,
grouped_id, fwd_from. Канал сопоставляется с реестром по username (config/sources.yaml, kind: telegram);
канала нет в реестре — файл пропускается. Альбом (один grouped_id) — один материал: текст берём из поста
альбома с подписью. Посты пишутся тем же `message_to_item`, что и живой сбор, поэтому id совпадают,
а повторный импорт ничего не дублирует. После импорта last_native_id канала сдвигается на последний пост
архива: живой сбор с t.me/s продолжает с него, а не листает назад.
Данные Telegram не используются для обучения моделей (условия API п. 1.5, THIRD_PARTY_NOTICES).
"""

import argparse
import json
import logging
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from backend.db.engine import get_engine
from backend.ingest import store
from backend.ingest.registry import load_sources
from backend.ingest.telegram import message_to_item, username
from backend.schemas import RawItem, Source

log = logging.getLogger(__name__)

BATCH = 500


def _dt(v: str | None) -> datetime | None:
    """Поля *_utc выгрузки: без зоны — это UTC, а не местное время машины, где идёт импорт."""
    if not v:
        return None
    dt = datetime.fromisoformat(v)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def to_message(post: dict) -> SimpleNamespace:
    """Строка выгрузки → объект с полями сообщения Telethon, которые читает message_to_item."""
    fwd = post.get("fwd_from")
    if isinstance(fwd, dict):
        fwd = fwd.get("from_name") or fwd.get("channel_id") or "unknown"
    return SimpleNamespace(
        id=int(post["msg_id"]),
        message=post.get("text"),
        date=_dt(post["date_utc"]),
        edit_date=_dt(post.get("edit_date_utc")),
        fwd_from=SimpleNamespace(from_name=str(fwd), from_id=None) if fwd else None,
        views=post.get("views"),
        forwards=post.get("forwards"),
    )


def dedup_albums(posts: Iterable[dict]) -> Iterator[dict]:
    """Из альбома остаётся первый пост с подписью (подпись обычно у одного поста альбома)."""
    seen: set[int] = set()
    for p in sorted(posts, key=lambda p: int(p["msg_id"])):
        g = p.get("grouped_id")
        if g is not None:
            if g in seen or not (p.get("text") or "").strip():
                continue
            seen.add(g)
        yield p


def channel_items(src: Source, posts: Iterable[dict], since: datetime | None, now: datetime) -> list[RawItem]:
    channel = username(src)
    out = []
    for p in dedup_albums(posts):
        msg = to_message(p)
        if since and msg.date < since:
            continue
        if item := message_to_item(src, channel, msg, now):
            out.append(item)
    return out


def import_dir(directory: Path, since: datetime | None = None) -> dict[str, int]:
    by_user = {username(s).lower(): s for s in load_sources() if s.kind == "telegram"}
    engine = get_engine()
    now = datetime.now(UTC)
    stats: dict[str, int] = {}
    for f in sorted(directory.glob("tg_*.jsonl")):
        posts = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not posts:
            continue
        src = by_user.get(str(posts[0].get("username", "")).lower())
        if src is None:
            log.warning("%s: канала @%s нет в реестре — пропуск", f.name, posts[0].get("username"))
            continue
        items = channel_items(src, posts, since, now)
        written = 0
        with engine.begin() as conn:
            for i in range(0, len(items), BATCH):
                written += store.insert_items(conn, items[i : i + BATCH])
            state = store.get_state(conn, src.id)
            newest = max(int(p["msg_id"]) for p in posts)
            if newest > (state.get("last_native_id") or 0):
                store.save_state(
                    conn,
                    src.id,
                    last_native_id=newest,
                    items_total=(state.get("items_total") or 0) + written,
                )
        stats[src.id] = written
        log.info(
            "%s → %s: постов %d, материалов %d, записано %d", f.name, src.id, len(posts), len(items), written
        )
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", type=Path)
    ap.add_argument("--since", type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC))
    args = ap.parse_args()
    stats = import_dir(args.directory, args.since)
    log.info("итог: каналов %d, записано %d", len(stats), sum(stats.values()))


if __name__ == "__main__":
    main()
