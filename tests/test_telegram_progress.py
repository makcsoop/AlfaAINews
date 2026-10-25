"""Точка дочитывания Telegram: обрыв и отставание не теряют посты (Telethon и t.me/s), без БД и сети."""

import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError

from backend.ingest import store, telegram, tme_web
from backend.schemas import Source

SRC = Source(id="tg_test", name="Тест", kind="telegram", url="https://t.me/test_chan", tier=2, weight=0.5)
T0 = datetime(2026, 10, 20, tzinfo=UTC)


def post(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=i, message=f"Пост номер {i} о ключевой ставке", date=T0 + timedelta(minutes=i),
        edit_date=None, fwd_from=None, views=None, forwards=None,
    )


@pytest.fixture
def db(monkeypatch):
    """Состояние канала и сохранённые посты в памяти вместо Postgres."""
    state: dict = {}
    saved: dict[str, object] = {}

    def insert_items(conn, items):
        new = [i for i in items if i.id not in saved]
        saved.update({i.id: i for i in new})
        return len(new)

    monkeypatch.setattr(store, "get_state", lambda conn, sid: dict(state))
    monkeypatch.setattr(store, "save_state", lambda conn, sid, **f: state.update(f))
    monkeypatch.setattr(store, "insert_items", insert_items)
    engine = SimpleNamespace(begin=lambda: nullcontext(None))
    monkeypatch.setattr(telegram, "get_engine", lambda: engine)
    monkeypatch.setattr(tme_web, "get_engine", lambda: engine)

    def saved_above(conn, sid, last):
        ids = [int(i.native_id) for i in saved.values() if int(i.native_id) > last]
        return (min(ids), max(ids)) if ids else (None, None)

    monkeypatch.setattr(tme_web, "saved_above", saved_above)
    return SimpleNamespace(state=state, saved=saved)


def native_ids(db) -> set[int]:
    return {int(i.native_id) for i in db.saved.values()}


class FakeClient:
    """iter_messages(min_id, reverse=True) отдаёт посты по возрастанию и обрывается после `fail_after`."""

    def __init__(self, newest: int, fail_after: int | None, error: Exception):
        self.newest, self.fail_after, self.error = newest, fail_after, error

    async def iter_messages(self, channel, min_id=0, reverse=False, limit=None):
        assert reverse and min_id, "дочитывание — от старых к новым"
        for n, i in enumerate(range(min_id + 1, self.newest + 1)):
            if self.fail_after is not None and n == self.fail_after:
                raise self.error
            yield post(i)


@pytest.mark.parametrize("error", [FloodWaitError(request=None, capture=900), ConnectionError("обрыв")])
def test_telethon_keeps_progress_on_interruption(db, error):
    db.state["last_native_id"] = 100
    client = FakeClient(newest=400, fail_after=120, error=error)
    asyncio.run(telegram.ingest_channel(client, SRC))
    # прочитанное до обрыва сохранено, точка — на последнем прочитанном
    assert db.state["last_native_id"] == 220 and native_ids(db) == set(range(101, 221))
    assert "прервано" in db.state["last_error"]
    client.fail_after = None
    asyncio.run(telegram.ingest_channel(client, SRC))
    assert db.state["last_native_id"] == 400 and native_ids(db) == set(range(101, 401))
    assert db.state["last_error"] is None


def test_telethon_interrupted_before_any_post_keeps_state(db):
    db.state["last_native_id"] = 100
    asyncio.run(telegram.ingest_channel(FakeClient(400, 0, ConnectionError("обрыв")), SRC))
    assert db.state["last_native_id"] == 100 and not db.saved


class FakeFetcher:
    """t.me/s: страница — 20 постов не новее `before`; канал растёт от 1 до `newest`."""

    def __init__(self, newest: int):
        self.newest = newest

    async def get(self, url: str):
        before = int(url.split("before=")[1]) if "before=" in url else self.newest + 1
        return SimpleNamespace(status_code=200, content=str(before).encode())


@pytest.fixture
def pages(monkeypatch):
    def parse_page(content, channel):
        before = int(content.decode())
        return [post(i) for i in range(max(1, before - 20), before)]

    monkeypatch.setattr(tme_web, "parse_page", parse_page)


def test_tme_web_does_not_skip_gap(db, pages):
    db.state["last_native_id"] = 100
    fetcher = FakeFetcher(newest=400)  # 300 новых постов — больше, чем MAX_PAGES страниц за проход
    asyncio.run(tme_web.ingest_channel_web(fetcher, SRC))
    assert db.state["last_native_id"] == 100  # дыра не закрыта — точку не сдвигаем
    assert min(native_ids(db)) > 101
    for _ in range(3):
        asyncio.run(tme_web.ingest_channel_web(fetcher, SRC))
    assert native_ids(db) == set(range(101, 401)) and db.state["last_native_id"] == 400
    fetcher.newest = 430  # дыра закрыта — обычное дочитывание сверху
    asyncio.run(tme_web.ingest_channel_web(fetcher, SRC))
    assert db.state["last_native_id"] == 430 and native_ids(db) == set(range(101, 431))


def test_tme_web_small_update_moves_pointer(db, pages):
    db.state["last_native_id"] = 100
    asyncio.run(tme_web.ingest_channel_web(FakeFetcher(newest=130), SRC))
    assert db.state["last_native_id"] == 130 and native_ids(db) == set(range(101, 131))
