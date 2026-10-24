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


def test_version_ignores_incidental_numbers_but_not_the_rate():
    from backend.extract.run import material_change
    from backend.schemas import NumberFact

    old = extract_rules(_story(), [_item()])
    extra = old.model_copy(update={"numbers": [*old.numbers, NumberFact(value=2.0, unit="%", quote="на 2%", verified=True)]})
    assert material_change(old, extra) == []  # новый материал принёс своё число — не новая версия
    other_rate = old.model_copy(
        update={"numbers": [NumberFact(value=16.0, unit="%", quote="ключевую ставку до 16%", verified=True)]}
    )
    assert material_change(old, other_rate) == ["значение: ключевая ставка"]


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("Иноагентом объявлен журналист Максим Курников", "Минюст признал иностранными агентами журналиста."),
        ("Президент Кубы назвал ордер на арест Рауля Кастро «цирком в Майами»", "Президент Кубы высмеял ордер."),
        (
            "Захарова сравнила планы стран ЕС по санкциям с «мышами, грызущими кактус»",
            "Захарова ответила на намерение Еврокомиссии ввести новые санкции. Каллас хочет принять 20-й пакет санкций.",
        ),
    ],
)
def test_llm_sanctions_type_needs_measure_in_text(title, text):
    # живой прогон воркера 23.10: модель ставила sanctions политике — тип-мера от модели сверяется с текстом
    item = _item(title=title, text=text)
    ans = {**GOOD, "event_type": "sanctions", "summary_quote": title[:30], "numbers": [], "channels": []}
    facts = extract(_story(), [item], FixtureLLM(ans))
    assert facts.event_type == "other"


def test_llm_sanctions_type_kept_with_measure():
    item = _item(title="США ввели санкции против «Роснефти» и «Лукойла»", text=SANCTIONS_TEXT)
    ans = {**GOOD, "event_type": "sanctions", "summary_quote": "США ввели санкции", "numbers": [], "channels": []}
    assert extract(_story(), [item], FixtureLLM(ans)).event_type == "sanctions"


def test_conditional_relief_and_background_measure():
    lead = "Нормализация возможна, но для этого нужно, чтобы сняли все абсолютно санкции, заявил Грушко."
    assert rules.event_type("В Москве допускают нормализацию отношений с США", lead)[0] == "other"
    # мера в фоне статьи («ранее ввели») — не тип события от модели
    text = "Президент Кубы высмеял ордер на арест. Он выступил по телевидению. США ранее ввели санкции против Кубы."
    item = _item(title="Президент Кубы назвал ордер на арест Рауля Кастро «цирком в Майами»", text=text)
    ans = {**GOOD, "event_type": "sanctions", "summary_quote": "Президент Кубы", "numbers": [], "channels": []}
    assert extract(_story(), [item], FixtureLLM(ans)).event_type == "other"


@pytest.mark.parametrize(
    "title",
    [
        "Центробанк Турции снизил ключевую ставку до 36%",
        "Нацбанк Казахстана снизил базовую ставку",
        "Центральный банк Бразилии повысил ключевую ставку",
        "ЦБ Турции повысил ключевую ставку",
    ],
)
def test_foreign_central_bank_is_not_rate_decision(title):
    from backend.extract.rules import event_type, has_measure

    assert event_type(title, None)[0] == "other"
    assert not has_measure("rate_decision", title)


def test_bank_of_russia_rate_decision_kept():
    from backend.extract.rules import event_type

    for title in ("Банк России сохранил ключевую ставку на уровне 14,00% годовых",
                  "Центробанк России оставил ключевую ставку без изменений", "ЦБ сохранил ключевую ставку на уровне 14%"):
        assert event_type(title, None)[0] == "rate_decision"


def test_foreign_central_bank_in_title_excludes_monetary_lead():
    from backend.extract.rules import event_type

    lead = "Регулятор продолжает смягчение денежно-кредитной политики на фоне замедления инфляции."
    assert event_type("Нацбанк Казахстана снизил базовую ставку", lead)[0] != "monetary_signal"


def test_windfall_tax_not_bankruptcy():
    from backend.extract.rules import event_type

    title = "Минфин заявил о готовности обсудить налог на сверхдоходы"
    assert event_type(title, "Отдельно обсуждали риск дефолтов эмитентов.")[0] == "windfall_tax"


@pytest.mark.parametrize(
    "title",
    [
        "Заседание Банка России по ключевой ставке впереди, а ожидания по нему уже в торгах",
        "Аналитики ждут, что ЦБ сохранит ключевую ставку: решение по ключевой ставке в пятницу",
    ],
)
def test_meeting_announcement_is_not_rate_decision(title):
    assert rules.event_type(title, None)[0] != "rate_decision"
    assert rules.event_type("Рынок готовится", title)[0] != "rate_decision"


def test_decision_with_expectations_word_kept():
    assert rules.event_type("ЦБ сохранил ключевую ставку 14%, как и ожидали аналитики", None)[0] == "rate_decision"


@pytest.mark.parametrize(
    ("etype", "text"),
    [
        ("supply_disruption", "ВСУ совершили ракетную атаку по гражданскому объекту в Орле, есть пострадавшие."),
        ("supply_disruption", "Депутат заявил, что Запад проиграет информационную войну."),
        ("sanctions", "Делегации продолжат переговоры в Стамбуле на следующей неделе."),
    ],
)
def test_llm_measure_type_needs_measure_in_text(etype, text):
    assert etype in rules.MEASURE_TYPES
    assert not rules.has_measure(etype, text)


def test_keep_llm_typing_drops_measure_type_rules_did_not_find():
    from backend.extract.run import keep_llm_typing
    from backend.schemas import EventFacts

    old = EventFacts(story_id="st_x", event_type="supply_disruption", status="fact", summary="s", extractor_model="qwen")
    new = EventFacts(story_id="st_x", event_type="other", status="fact", summary="s", extractor_model="rules")
    assert keep_llm_typing(old, new).event_type == "other"
    old2 = EventFacts(story_id="st_x", event_type="state_support", status="fact", summary="s", extractor_model="qwen")
    assert keep_llm_typing(old2, new).event_type == "state_support"


def test_hour_before_decision_is_not_rate_decision():
    assert rules.event_type("Остался час до решения по ключевой ставке", None)[0] != "rate_decision"


def test_mention_of_decision_is_not_rate_decision():
    t = "В ЦБ не видят рисков для финансовой стабильности, это не могло быть фактором для принятия решения по ключевой ставке"
    assert rules.event_type(t, None)[0] != "rate_decision"
    assert rules.event_type("Банк России принял решение по ключевой ставке: сохранил 14%", None)[0] == "rate_decision"


def test_repatriation_of_people_is_not_fx_control():
    assert rules.event_type("Суд в Индии постановил организовать репатриацию 23 моряков с танкера", None)[0] != "fx_control"
    assert rules.event_type("Правительство продлило требование о репатриации валютной выручки", None)[0] == "fx_control"


@pytest.mark.parametrize("model_type", ["rate_decision", "other", "monetary_signal"])
def test_model_cannot_downgrade_decision_found_in_title(model_type):
    """Независимая проверка 24.10: представительный материал — длинное заявление без глагола решения,
    по нему модель понижала решение ЦБ до other (тип и факторы пропадали)."""
    release = _item(title="Банк России принял решение сохранить ключевую ставку на уровне 14,00% годовых",
                    text="Совет директоров Банка России 23 октября принял решение сохранить ключевую ставку.")
    statement = RawItem(
        **{**release.model_dump(), "id": "it2", "native_id": "2", "source_id": "cbr_press",
           "title": "Заявление Председателя Банка России по итогам заседания Совета директоров",
           "text": "Инфляционное давление снижается, но остаётся высоким. " * 30}
    )
    story = Story(id="st1", title="t", item_ids=["it1", "it2"], corroboration=1, first_seen=T0, last_seen=T0)
    ans = {**GOOD, "event_type": model_type, "summary_quote": "Инфляционное давление снижается"}
    if model_type == "other":
        ans["channels"] = []
    facts = extract(story, [release, statement], FixtureLLM(ans))
    assert facts.event_type == "rate_decision"


def test_keep_llm_typing_does_not_override_rule_decision():
    from backend.extract.run import keep_llm_typing
    from backend.schemas import EventFacts

    old = EventFacts(story_id="st_x", event_type="other", status="fact", summary="s", extractor_model="qwen")
    new = EventFacts(story_id="st_x", event_type="rate_decision", status="fact", summary="s", extractor_model="rules")
    assert keep_llm_typing(old, new).event_type == "rate_decision"
