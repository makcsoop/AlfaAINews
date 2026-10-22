"""Разбор постов Telegram в RawItem — на поддельных сообщениях, без сети и аккаунта."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend.ingest.registry import load_sources
from backend.ingest.telegram import message_to_item, post_title, username

SRC = {s.id: s for s in load_sources("config/sources.yaml")}["tg_centralbank_russia"]
NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def msg(mid, text, *, fwd=None, edit=None, views=1000):
    return SimpleNamespace(
        id=mid,
        message=text,
        date=datetime(2026, 10, 22, 10, tzinfo=UTC),
        edit_date=edit,
        fwd_from=fwd,
        views=views,
        forwards=12,
    )


def test_username_from_url():
    assert username(SRC) == "centralbank_russia"


def test_post_becomes_item():
    text = "Банк России сохранил ключевую ставку на уровне 14% годовых\n\nПодробнее в пресс-релизе."
    item = message_to_item(SRC, "centralbank_russia", msg(501, text, edit=NOW), NOW)
    assert item.native_id == "501" and item.url == "https://t.me/centralbank_russia/501"
    assert item.title == "Банк России сохранил ключевую ставку на уровне 14% годовых"
    assert item.text.endswith("Подробнее в пресс-релизе.") and item.tier == 0
    assert item.updated_at == NOW and item.metrics == {"views": 1000, "forwards": 12}
    assert item.dropped_reason is None and item.fwd_from is None


def test_forward_and_ads_and_media_only():
    fwd = SimpleNamespace(from_name=None, from_id=SimpleNamespace(channel_id=1101170442))
    item = message_to_item(SRC, "c", msg(1, "Пересланная новость про ОФЗ", fwd=fwd), NOW)
    assert item.fwd_from == "channel:1101170442"
    ad = message_to_item(
        SRC, "c", msg(2, "Откройте вклад под 20%!\nРеклама. ООО «Банк». erid: 2VtzqwXYZ"), NOW
    )
    assert ad.dropped_reason == "ad"
    assert message_to_item(SRC, "c", msg(3, None), NOW) is None  # только медиа


def test_long_first_line_is_trimmed_on_word():
    item = message_to_item(SRC, "c", msg(4, "слово " * 80), NOW)
    assert len(item.title) <= 200 and item.title.endswith("…")


@pytest.mark.parametrize(
    ("text", "title"),
    [
        (
            "❗️Путин назначил Шестакова врио губернатора Приморья.",
            "Путин назначил Шестакова врио губернатора Приморья.",
        ),
        (
            "🇷🇺#SBER\nНабсовет Сбербанка рассмотрит кандидатуру Григоренко",
            "Набсовет Сбербанка рассмотрит кандидатуру Григоренко",
        ),
        ("▪️ «Газпром» и CNPC договорились о цене", "«Газпром» и CNPC договорились о цене"),
        ("#нефть #brent Brent превысила $111", "Brent превысила $111"),
    ],
)
def test_post_title_strips_emoji_and_tags(text, title):
    assert post_title(text) == title
