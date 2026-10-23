"""Приоры каналов по архиву аналитиков: разметка поста и агрегат — без сети и БД."""

from datetime import UTC, datetime

from backend.extract.llm_port import FixtureLLM
from backend.knowledge.priors import MIN_CHANNELS, aggregate, label
from backend.schemas import Story

T = datetime(2026, 9, 11, 12, tzinfo=UTC)
STORY = Story(id="s", title="ЦБ сохранил ставку 14%", item_ids=["i"], corroboration=1, first_seen=T, last_seen=T)
POST = {
    "id": "p1",
    "source_id": "tg_a",
    "url": None,
    "published_at": T,
    "text": "Пауза ЦБ означает, что ставки по кредитам останутся высокими, а рубль будет поддержан.",
}
CHANNELS = {"key_rate": "Ключевая ставка", "fx_rub": "Курс рубля"}


def test_label_keeps_only_quoted_known_factors():
    llm = FixtureLLM(
        {
            "about_event": True,
            "factors": [
                {"channel": "key_rate", "direction": "up", "quote": "ставки по кредитам останутся высокими"},
                {"channel": "fx_rub", "direction": "up", "quote": "рубль резко упадёт"},  # цитаты нет в посте
                {"channel": "weather", "direction": "up", "quote": "Пауза ЦБ"},  # такого фактора нет
            ],
        }
    )
    lab = label(llm, POST, STORY, "rate_decision", CHANNELS)
    assert [f["channel"] for f in lab["factors"]] == ["key_rate"]
    assert "<data>" in llm.calls[0]["user"]  # пост — только блоком данных


def test_post_about_other_event_skipped():
    assert label(FixtureLLM({"about_event": False, "factors": []}), POST, STORY, "rate_decision", CHANNELS) is None


def test_prior_needs_five_channels():
    def lab(src, d):
        return ({**POST, "id": f"p_{src}", "source_id": src}, {"factors": [{"channel": "key_rate", "direction": d, "quote": "q"}]})

    few = aggregate("rate_decision", [lab("a", "up"), lab("b", "up"), lab("c", "down")])
    assert few[0]["prior"] == round(1 / 3, 2) and few[0]["channels"] == 3 and not few[0]["coverage_ok"]
    many = aggregate("rate_decision", [lab(s, "up") for s in "abcde"])
    assert many[0]["prior"] == 1.0 and many[0]["channels"] == MIN_CHANNELS and many[0]["coverage_ok"]
