"""Экстрактор: правила, проверка цитат и чисел, LLM-слой на заглушке."""

from datetime import UTC, datetime

import pytest

from backend.extract import rules
from backend.extract.extractor import extract, extract_rules
from backend.extract.llm_port import FixtureLLM
from backend.extract.verify import number_in_quote, quote_in_text
from backend.schemas import RawItem, Story

T0 = datetime(2026, 9, 11, 10, 30, tzinfo=UTC)
TEXT = (
    "11 сентября 2026 года совет директоров Банка России принял решение сохранить ключевую ставку "
    "на уровне 14% годовых. Это первая пауза после десяти снижений подряд. "
    "Рубль после решения укрепился на 0,5%."
)


def _item(
    source="cbr_press", title="Банк России сохранил ключевую ставку на уровне 14,00% годовых", text=TEXT
):
    return RawItem(
        id="it1",
        source_id=source,
        native_id="1",
        title=title,
        lead=None,
        text=text,
        published_at=T0,
        fetched_at=T0,
        content_hash="h",
    )


def _story():
    return Story(id="st1", title="t", item_ids=["it1"], corroboration=1, first_seen=T0, last_seen=T0)


@pytest.mark.parametrize(
    ("title", "etype"),
    [
        ("Банк России сохранил ключевую ставку на уровне 14%", "rate_decision"),
        ("Минфин разместил ОФЗ на 50 млрд рублей", "ofz_auction"),
        ("ЕС ввел новые санкции против российских банков", "sanctions"),
        ("Brent превысила $111 за баррель впервые за пять месяцев", "commodity_price"),
        ("Госдума приняла в первом чтении законопроект о платформенной экономике", "regulation_proposed"),
        ("Лазертаг стал официальным видом спорта в России", "other"),
    ],
)
def test_event_type(title, etype):
    assert rules.event_type(title, None)[0] == etype


# живой прогон 23.10: политика со словом «санкции» без меры занимала топ ленты как sanctions
@pytest.mark.parametrize(
    ("title", "lead"),
    [
        (
            "Уиткофф передал Москве предложения США по урегулированию на Украине",
            "План предусматривает поэтапное снятие санкций и гарантии безопасности.",
        ),
        (
            "Россия и Черногория обсудили отношения",
            "Подгорица присоединилась к антироссийским санкциям ЕС в 2022 году.",
        ),
        ("ЕС не стал вводить санкции на мех соболя", "Мех соболя исключили из проекта ограничений."),
        (
            "Водолацкий: США и Европа переходят к диалогу",
            "Депутат считает, что санкции больше не работают и Запад ищет компромисс.",
        ),
    ],
)
def test_political_statements_are_not_sanctions(title, lead):
    assert not rules.event_type(title, lead)[0].endswith("sanctions")


@pytest.mark.parametrize(
    ("title", "etype"),
    [
        ("США ввели санкции против «Роснефти» и «Лукойла»", "sanctions"),
        ("Евросоюз утвердил 21-й пакет санкций против России", "sanctions"),
        ("Минфин США внес «Газпром нефть» в список SDN", "sanctions"),
        ("Санкции против российских банков продлены до 2027 года", "sanctions"),
        ("ЕС не ввел новые санкции против банков", "other"),
        ("США сняли санкции с «Белавиа»", "sanctions_relief"),
        ("Минфин США ввел вторичные санкции против китайских банков", "secondary_sanctions"),
    ],
)
def test_sanctions_need_a_measure(title, etype):
    assert rules.event_type(title, None)[0] == etype


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("ЦБ сохранил ставку.", "fact"),
        ("Минфин планирует повысить НДС с 2027 года.", "planned"),
        ("По данным источников, правительство обсуждает новый налог.", "rumor"),
        ("Аналитики ожидают снижения ставки до 13%.", "forecast"),
    ],
)
def test_status(text, status):
    assert rules.status(text)[0] == status


def test_numbers_units_and_prefix_currency():
    nums = {
        (n.value, n.unit)
        for n in rules.numbers("Ставка 14% годовых. Выручка 1,5 млрд рублей. Brent — $111,05.")
    }
    assert {(14.0, "%"), (1.5, "млрд руб"), (111.05, "USD")} <= nums


def test_verify_quote_and_number():
    assert quote_in_text("сохранить ключевую ставку на уровне 14% годовых", TEXT)
    assert quote_in_text("«сохранить ключевую ставку» на уровне 14%", TEXT)  # кавычки не мешают
    assert not quote_in_text("повысить ключевую ставку до 16%", TEXT)
    assert not quote_in_text("14%", "ставка 15%")
    assert number_in_quote(14, "на уровне 14% годовых") and number_in_quote(1.5, "1,5 млрд рублей")
    assert not number_in_quote(16, "на уровне 14% годовых")


def test_rules_facts_are_grounded():
    facts = extract_rules(_story(), [_item()])
    assert facts.event_type == "rate_decision" and facts.status == "fact"
    assert any(p.name == "Банк России" and p.role == "regulator" for p in facts.participants)
    assert any(n.value == 14 and n.unit == "%" for n in facts.numbers)
    source_text = _item().title + "\n" + TEXT  # цитаты сверяются с полным текстом источника
    assert all(quote_in_text(n.quote, source_text) and "\n" not in n.quote for n in facts.numbers)
    assert {(c.channel, c.direction) for c in facts.channels} >= {("key_rate", "flat"), ("fx_rub", "up")}
    assert facts.extractor_model.startswith("rules") and facts.source_item_ids == ["it1"]


def test_weak_lead_sentence_falls_back_to_title():
    item = _item(text=None, title="Цена газа в Европе превысила $1050")
    item.lead = "Это произошло впервые с декабря 2022 года. Подробнее в материале."
    assert extract_rules(_story(), [item]).summary == "Цена газа в Европе превысила $1050"


GOOD = {
    "event_type": "rate_decision",
    "status": "fact",
    "summary": "ЦБ сохранил ставку 14%",
    "summary_quote": "принял решение сохранить ключевую ставку на уровне 14% годовых",
    "numbers": [{"value": 14, "unit": "%", "quote": "на уровне 14% годовых"}],
    "channels": [
        {"channel": "key_rate", "direction": "flat", "strength": 0.7, "quote": "сохранить ключевую ставку"}
    ],
}


def test_llm_verified_fields_applied():
    llm = FixtureLLM(GOOD)
    facts = extract(_story(), [_item()], llm)
    assert facts.summary == "ЦБ сохранил ставку 14%" and facts.unverified_fields == []
    assert facts.channels[0].strength == 0.7 and facts.channels[0].evidence[0].verified
    assert len(llm.calls) == 1
    assert "<data>\n[материал it1]" in llm.calls[0]["user"]  # новость — только как данные (data_block)


def test_llm_hallucinations_dropped_after_one_retry():
    bad = {
        **GOOD,
        "summary_quote": "ЦБ повысил ставку до 16%",  # цитаты нет в тексте
        "numbers": [{"value": 16, "unit": "%", "quote": "на уровне 14% годовых"}],  # числа нет в цитате
        "channels": [{"channel": "weather", "direction": "up", "quote": "сохранить ключевую ставку"}],
    }
    llm = FixtureLLM(bad)
    facts = extract(_story(), [_item()], llm)
    assert len(llm.calls) == 2  # ответ + ровно один повтор
    assert "не прошли проверку" in llm.calls[1]["user"]
    assert len(facts.unverified_fields) == 3
    assert not any(n.value == 16 for n in facts.numbers)
    assert all(c.channel != "weather" for c in facts.channels)
    assert facts.summary != "ЦБ сохранил ставку 14%"  # сводка осталась от правил


SANCTIONS_TEXT = (
    "США ввели санкции против «Роснефти» и «Лукойла». Ограничения касаются экспорта нефти "
    "и операций в долларах."
)


def test_rules_put_okved_on_sector_events_only():
    oil = extract_rules(_story(), [_item(title="США ввели санкции против «Роснефти» и «Лукойла»", text=SANCTIONS_TEXT)])
    assert oil.event_type == "sanctions" and oil.sectors_okved == ["06"]
    rate = extract_rules(_story(), [_item()])
    assert rate.event_type == "rate_decision" and rate.sectors_okved == []  # макрособытие — без отрасли


def test_llm_other_overrides_rule_type_and_its_channels():
    # правила видят меру, модель — нет (условная проверка приоритета): «other» модели побеждает
    item = _item(title="США ввели санкции против «Роснефти» и «Лукойла»", text=SANCTIONS_TEXT)
    llm = FixtureLLM({**GOOD, "event_type": "other", "channels": [], "summary_quote": "США ввели санкции"})
    facts = extract(_story(), [item], llm)
    assert facts.event_type == "other" and facts.sectors_okved == [] and facts.channels == []


def test_llm_sectors_need_a_quote():
    item = _item(title="США ввели санкции против «Роснефти» и «Лукойла»", text=SANCTIONS_TEXT)
    ans = {
        **GOOD,
        "event_type": "sanctions",
        "summary_quote": "США ввели санкции",
        "channels": [],
        "numbers": [],
        "sectors": [
            {"okved": "06", "quote": "Ограничения касаются экспорта нефти"},
            {"okved": "64", "quote": "санкции против банков"},  # цитаты нет в тексте
            {"okved": "99", "quote": "США ввели санкции"},  # такого ОКВЭД нет в матрице
        ],
    }
    facts = extract(_story(), [item], FixtureLLM(ans))
    assert facts.sectors_okved == ["06"]


@pytest.mark.parametrize(
    ("title", "lead", "etype"),
    [
        ("Еврокомиссия сохранит исключение на соболиный мех из России даже в самом жестком пакете санкций", None, "other"),
        ("Модники из Евросоюза захотели российских шуб", "ЕС вывел из-под санкций российский соболиный мех.", "sanctions_relief"),
    ],
)
def test_sanctions_exemptions_are_not_new_measures(title, lead, etype):
    assert rules.event_type(title, lead)[0] == etype


def test_other_type_gets_no_factors_even_from_llm():
    # политика и происшествия вне таксономии: факторы с цитатами от модели не должны поднимать их в ленте
    item = _item(title="Воздушная тревога объявлена в аэропорту Шереметьево", text="Аэропорт закрыт на два часа, рейсы задерживаются.")
    ans = {
        **GOOD,
        "event_type": "other",
        "summary_quote": "Аэропорт закрыт на два часа",
        "numbers": [],
        "channels": [{"channel": "logistics_costs", "direction": "up", "strength": 0.5, "quote": "рейсы задерживаются"}],
    }
    facts = extract(_story(), [item], FixtureLLM(ans))
    assert facts.event_type == "other" and facts.channels == []


@pytest.mark.parametrize(
    ("title", "etype"),
    [
        ("Задействованную в ликвидации последствий стихии в Ялте группировку увеличили", "other"),
        ("Кадыров заявил о ликвидации подозреваемого в диверсиях в районе Гудермеса", "other"),
        ("Суд начал ликвидацию компании «Ромашка» по иску ФНС", "default_bankruptcy"),
        ("ЦБ отозвал лицензию у московского банка", "default_bankruptcy"),
    ],
)
def test_liquidation_means_a_company(title, etype):
    assert rules.event_type(title, None)[0] == etype


def test_llm_factors_keep_event_type_own_factor():
    # живой прогон 23.10: у сюжета о решении ЦБ модель дала только consumer_demand из соседнего абзаца,
    # и ключевая ставка пропала из факторов
    text = TEXT + " Продажи автомобилей вырастут по итогам года на 66%."
    ans = {
        **GOOD,
        "channels": [
            {"channel": "consumer_demand", "direction": "up", "strength": 0.7, "quote": "Продажи автомобилей вырастут"}
        ],
    }
    facts = extract(_story(), [_item(text=text)], FixtureLLM(ans))
    chans = {(c.channel, c.direction) for c in facts.channels}
    assert ("consumer_demand", "up") in chans and ("key_rate", "flat") in chans
