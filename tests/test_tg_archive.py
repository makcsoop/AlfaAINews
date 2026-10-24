from datetime import UTC, datetime

from backend.ingest.tg_archive import channel_items, dedup_albums, to_message
from backend.schemas import Source

SRC = Source(
    id="tg_rosstat_official",
    name="Росстат",
    kind="telegram",
    url="https://t.me/rosstat_official",
    tier=0,
    weight=1.0,
    official=True,
)
NOW = datetime(2026, 10, 24, tzinfo=UTC)


def _post(mid, text, date="2026-10-22T07:00:00+00:00", **kw):
    return {
        "source_id": "tg_rosstat",
        "username": "rosstat_official",
        "msg_id": mid,
        "date_utc": date,
        "edit_date_utc": None,
        "text": text,
        "views": 100,
        "forwards": 2,
        "grouped_id": None,
        "fwd_from": None,
        **kw,
    }


def test_message_fields():
    m = to_message(_post(5, "Инфляция за неделю 0,1%", edit_date_utc="2026-10-22T08:00:00+00:00"))
    assert m.id == 5 and m.date.tzinfo and m.edit_date > m.date and m.fwd_from is None


def test_album_is_one_item_and_media_only_skipped():
    posts = [
        _post(1, "", grouped_id=7),
        _post(2, "Подпись альбома: выпуск зерна вырос на 5%", grouped_id=7),
        _post(3, "", grouped_id=7),
        _post(4, None),
    ]
    assert [p["msg_id"] for p in dedup_albums(posts)] == [2, 4]
    items = channel_items(SRC, posts, None, NOW)
    assert len(items) == 1 and items[0].url == "https://t.me/rosstat_official/2"


def test_ids_match_live_collector_and_since_filter():
    posts = [
        _post(10, "Старый пост про ВВП за квартал", date="2026-03-01T00:00:00+00:00"),
        _post(11, "Росстат: промпроизводство в сентябре выросло на 2%"),
    ]
    items = channel_items(SRC, posts, datetime(2026, 4, 1, tzinfo=UTC), NOW)
    assert [i.native_id for i in items] == ["11"]
    assert items[0].source_id == "tg_rosstat_official" and items[0].metrics == {"views": 100, "forwards": 2}


def test_forward_marked():
    items = channel_items(
        SRC, [_post(12, "Переслано: ЦБ сохранил ставку 14%", fwd_from="Банк России")], None, NOW
    )
    assert items[0].fwd_from == "Банк России"
