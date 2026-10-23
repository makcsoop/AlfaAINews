"""Отсев словарём и склейка в сюжеты на синтетических материалах (без базы и без Natasha)."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.ingest.normalize import content_hash, simhash
from backend.ingest.registry import load_sources
from backend.schemas import RawItem
from backend.stories.cluster import cluster_items, story_id, to_story
from backend.stories.filters import drop_reason

T0 = datetime(2026, 10, 21, 10, tzinfo=UTC)
SOURCES = {s.id: s for s in load_sources("config/sources.yaml")}


def item(i, source, title, lead="", minutes=0, tags=()):
    text = f"{title}. {lead}"
    return RawItem(
        id=f"id{i}",
        source_id=source,
        native_id=str(i),
        title=title,
        lead=lead or None,
        published_at=T0 + timedelta(minutes=minutes),
        fetched_at=T0,
        content_hash=content_hash(title, lead),
        simhash=simhash(text),
        source_tags=list(tags),
    )


@pytest.mark.parametrize(
    ("it", "reason"),
    [
        (item(1, "rbc", "Новый вклад", "Реклама. 18+ erid: 2VtzqvXYZ"), "ad"),
        (item(2, "interfax", "Что произошло за день: среда, 21 октября"), "digest"),
        (item(3, "kommersant", "Главные новости к утру 22 октября // ..."), "digest"),
        (item(4, "cbr_press", "Монета, посвященная судебной системе России"), "cbr_service"),
        (item(5, "rbc", "Эксперты оценили корпоративный спорт", tags=["Радио"]), "not_news"),
        (item(6, "cbr_press", "Банк России сохранил ключевую ставку на уровне 14%"), None),
        # служебное правило ЦБ не задевает другие источники
        (item(7, "prime", "Минфин приглашает инвесторов на аукцион ОФЗ"), None),
    ],
)
def test_drop_reason(it, reason):
    assert drop_reason(it) == reason


RATE = "ЦБ сохранил ключевую ставку на уровне 14% годовых"
OTHER = "Минфин разместил ОФЗ на 50 млрд рублей на аукционе"


def test_reprints_and_independent_sources_confirm():
    items = [
        item(1, "cbr_press", "Банк России принял решение сохранить ключевую ставку на уровне 14% годовых"),
        item(2, "interfax", RATE, "Совет директоров Банка России сохранил ставку", minutes=2),
        item(3, "prime", RATE, "Совет директоров Банка России сохранил ставку", minutes=3),  # копия interfax
        item(4, "prime", OTHER, "Спрос превысил предложение", minutes=5),
    ]
    keys = [{"org:Банк России"}, {"org:Банк России"}, {"org:Банк России"}, {"org:Минфин России"}]
    drafts = cluster_items(items, entity_keys=keys)
    assert len(drafts) == 2
    rate = next(d for d in drafts if len(d.items) == 3)
    assert "id3" in rate.copies  # почти дословная перепечатка
    story = to_story(rate, SOURCES)
    assert story.id == story_id("id1") and story.status == "confirmed"
    assert story.corroboration == 2  # cbr_press и interfax; копия prime не считается
    assert story.title.startswith("Банк России принял")  # заголовок от источника уровня 0


def test_no_shared_entity_no_merge():
    a = item(1, "interfax", "Компания объявила о выплате дивидендов за полугодие", minutes=0)
    b = item(2, "rbc", "Компания объявила о выплате дивидендов за квартал", minutes=10)
    drafts = cluster_items([a, b], entity_keys=[{"org:Сбербанк"}, {"org:Лукойл"}])
    assert len(drafts) == 2


def test_window_72h():
    a = item(1, "interfax", RATE)
    b = item(2, "rbc", RATE + " снова", minutes=73 * 60)
    drafts = cluster_items([a, b], entity_keys=[{"org:Банк России"}] * 2)
    assert len(drafts) == 2


def test_rumor_and_single_status():
    r = item(1, "kommersant", "Минфин может повысить НДС", "по данным источников, решение обсуждается")
    s = item(2, "vedomosti", "Сбербанк открыл офис в Казани")
    d1, d2 = cluster_items([r, s], entity_keys=[{"org:Минфин России"}, {"org:Сбербанк"}])
    assert to_story(d1, SOURCES).status == "rumor"
    assert to_story(d2, SOURCES).status == "single"
