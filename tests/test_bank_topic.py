"""Позиция ЦМИ по теме сюжета: общий канал — ещё не тема (CRITERIA, сверка 25.10)."""

from datetime import date

import pytest

from backend.bank.search import topic_overlap
from backend.ranking.fast_card import _bank_view
from backend.schemas import BankPosition

POLYUS = "Альфа-Банк не ожидает ухудшения кредитного качества Полюса в краткосрочном и среднесрочном горизонте."
EXPORT = "Альфа-Банк сохраняет прогноз экспорта без изменений из-за логистических сложностей и санкционных рисков."
RATE = "Для снижения ключевой ставки ЦБ нужна уверенность в снижении устойчивой инфляции при сдержанном спросе."
FED = "Банк полагает, что вероятность повышения ставки ФРС на 25 б.п. в сентябре выросла до 70%."


@pytest.mark.parametrize(
    ("position", "story"),
    [
        (POLYUS, "Сколько дефолтов случилось на российском долговом рынке с 19 по 23 октября"),
        (POLYUS, "Немецкий производитель аккумуляторов Sonnen подал заявление о банкротстве"),
        (EXPORT, "Федеральные власти намерены повысить пошлины на импорт крепкого алкоголя"),
        (EXPORT, "Трамп может отменить пошлины на товары из Канады, пишет WSJ"),
        (FED, "Банк России сохранил ключевую ставку на уровне 14%"),  # чужой регулятор
    ],
)
def test_off_topic_position_rejected(position: str, story: str) -> None:
    assert topic_overlap(position, story) == 0


@pytest.mark.parametrize(
    ("position", "story"),
    [
        (RATE, "Минфин связал повышенную ключевую ставку с политикой ЦБ по инфляции"),
        (RATE, "ЦБ сохранил ключевую ставку на уровне 14%"),
        (EXPORT, "Экспорт угля упал из-за логистических ограничений и санкций"),
        (POLYUS, "Полюс разместил облигации: кредитное качество эмитента остаётся высоким"),
    ],
)
def test_on_topic_position_kept(position: str, story: str) -> None:
    assert topic_overlap(position, story) > 0


def _pos(pid: str, thesis: str, day: date) -> BankPosition:
    return BankPosition(
        id=pid, doc_id=pid.split("#")[0], doc_title="Бонд Бриф: Итоги недели", doc_date=day, page=1,
        thesis=thesis, channel="counterparty_defaults", quote=thesis,
    )


def test_fast_card_bank_view_needs_topic() -> None:
    polyus = _pos("cmi_bb#p2#r1", POLYUS, date(2026, 9, 14))
    defaults = "Сколько дефолтов случилось на российском долговом рынке. Список нарушителей пополнили три новичка"
    assert _bank_view([polyus], "counterparty_defaults", date(2026, 10, 23), defaults) is None
    general = _pos("cmi_bb#p3#r1", "Банк ожидает роста числа дефолтов на долговом рынке.", date(2026, 9, 1))
    claim = _bank_view([polyus, general], "counterparty_defaults", date(2026, 10, 23), defaults)
    assert claim is not None and claim.evidence[0].source_id == "cmi_bb#p3#r1"  # свежее, но не по теме — мимо
