"""Веса источников: старт × надёжность по закрытым сюжетам — без БД."""

from datetime import UTC, datetime, timedelta

from backend.ingest.source_weights import Counts, count, learned_weight
from backend.schemas import Story

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _s(sid, sources, corr, *, age_h=100, status="single"):
    t = NOW - timedelta(hours=age_h)
    return Story(
        id=sid, title="t", item_ids=["i"], source_ids=sources, corroboration=corr, status=status,
        first_seen=t, last_seen=t,
    )


def test_counts_only_closed_stories():
    stories = [
        _s("a", ["rbc", "tass"], 2),  # подтвердили двое
        _s("b", ["tg_x"], 1, status="rumor"),  # слух, никто не подтвердил
        _s("e", ["kommersant"], 1),  # эксклюзив без маркеров слуха — не штраф
        _s("c", ["tg_x", "interfax"], 2),  # опровергнут Интерфаксом
        _s("d", ["tg_x"], 1, age_h=10),  # ещё открыт — не считаем
    ]
    c = count(stories, {"c": {"interfax"}}, NOW)
    assert (c["rbc"].confirmed, c["tass"].confirmed) == (1, 1)
    assert (c["tg_x"].unconfirmed, c["tg_x"].refuted, c["tg_x"].confirmed) == (1, 1, 0)
    assert "interfax" not in c  # опровергающий источник сам себе не засчитывается
    assert c["kommersant"] == Counts()


def test_weight_moves_from_start_and_stays_in_bounds():
    assert learned_weight(0.8, Counts()) == 0.8  # без данных — стартовый вес
    assert learned_weight(0.8, Counts(confirmed=20)) == 1.0
    assert learned_weight(0.6, Counts(unconfirmed=5, refuted=3)) < 0.45
    assert learned_weight(0.1, Counts(refuted=50)) == 0.1
