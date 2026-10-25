"""Отсев словарём и склейка в сюжеты на синтетических материалах (без базы и без Natasha)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from backend.ingest.normalize import content_hash, simhash
from backend.ingest.registry import load_sources
from backend.schemas import RawItem
from backend.stories.cluster import _Draft, cluster_items, story_id, to_story
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


def test_one_cbr_decision_is_one_story():
    """3 материала о решении ЦБ из разных источников, которые TF-IDF разводит, → один сюжет; анонс отдельно."""
    items = [
        item(1, "cbr_press", "Банк России принял решение сохранить ключевую ставку на уровне 14,00% годовых"),
        item(2, "interfax", "ЦБ сохранил ключевую ставку на уровне 14% годовых", minutes=1),
        item(3, "tg_bbbreaking", "Ключевая ставка осталась на уровне 14% годовых", minutes=2),
        item(4, "rbc", "Заседание Банка России по ключевой ставке впереди, ожидания 14% уже в торгах",
             minutes=-600),
    ]
    drafts = cluster_items(items, entity_keys=[set(), set(), set(), set()])
    decision = [d for d in drafts if {i.id for i in d.items} >= {"id1", "id2", "id3"}]
    assert len(decision) == 1
    assert all("id4" not in {i.id for i in d.items} for d in decision)


def test_decision_key_rate_day_and_foreign_bank():
    from backend.stories.cluster import decision_key

    assert decision_key(item(1, "interfax", "ЦБ сохранил ключевую ставку на уровне 14%")) == "2026-10-21:14"
    assert decision_key(item(2, "prime", "Банк России принял решение сохранить ключевую ставку на уровне 14,00%")) \
        == "2026-10-21:14"
    assert decision_key(item(3, "prime", "ЦБ Турции сохранил ключевую ставку на уровне 40%")) is None
    assert decision_key(item(4, "tass", "ЦБ снизил ключевую ставку до 13%", minutes=60 * 24 * 40)) == "2026-11-30:13"
    assert decision_key(item(5, "rbc", "Аналитики ждут, что ключевая ставка останется 14%")) is None


@pytest.mark.parametrize(
    ("title", "key"),
    [
        ("ЦБ снизил ключевую ставку на 0,5% до 16%", "2026-10-21:16"),
        ("При инфляции 6% ЦБ сохранил ключевую ставку 14%", "2026-10-21:14"),
        ("Банк России сохранил ключевую ставку на уровне 14,00% годовых", "2026-10-21:14"),
        ("Сбербанк снизил ставки по вкладам после того, как ЦБ сохранил ключевую ставку 14%", None),
        ("Набиуллина: ключевая ставка 14% сохранится долго", None),
    ],
)
def test_decision_key_rate_value_and_subordinate(title, key):
    from backend.stories.cluster import decision_key

    assert decision_key(item(1, "interfax", title)) == key


def test_decision_without_number_and_paste_join_the_decision():
    from backend.stories.cluster import decision_key

    assert decision_key(item(1, "cbr_press", "Банк России принял решение по ключевой ставке")) == "2026-10-21:?"
    assert decision_key(item(2, "user_paste", "ЦБ сохранил ключевую ставку на уровне 14%", minutes=1440)) is None
    items = [
        item(1, "cbr_press", "Банк России принял решение по ключевой ставке"),
        item(2, "interfax", "ЦБ сохранил ключевую ставку на уровне 14% годовых", minutes=3),
    ]
    assert len(cluster_items(items, entity_keys=[set(), set()])) == 1


def test_user_paste_not_independent_source():
    # B9: вставка пользователя не подтверждает сюжет
    a = item(1, "interfax", "Северсталь запустила прокатный стан", "в Череповце за 50 млрд рублей")
    b = item(2, "user_paste", "Северсталь запустила прокатный стан", "в Череповце за 50 млрд рублей", minutes=5)
    drafts = cluster_items([a, b], entity_keys=[{"org:Северсталь"}] * 2)
    assert len(drafts) == 1
    story = to_story(drafts[0], SOURCES)
    assert story.corroboration == 1 and story.status == "single"
    only = to_story(cluster_items([b], entity_keys=[{"org:Северсталь"}])[0], SOURCES)
    assert only.corroboration == 0 and only.status == "single"


def test_independent_sources_counted_by_publisher():
    """CRITERIA_CHECK 7а: ленты одного издателя (пресс-релизы и события ЦБ, сайт и Telegram «Коммерсанта»)
    — один независимый источник, как и в списке источников карточки."""
    titles = [
        ("cbr_press", "ЦБ сохранил ключевую ставку 14% годовых", "Совет директоров Банка России"),
        ("cbr_events", "Набиуллина объяснила решение сохранить ставку 14%", "пресс-конференция Банка России"),
        ("tg_kommersant", "Банк России оставил ставку 14%", "решение совета директоров"),
        ("kommersant", "ЦБ оставил ключевую ставку на уровне 14%", "регулятор сохранил паузу"),
    ]
    items = [item(n, src, t, lead, minutes=n * 3) for n, (src, t, lead) in enumerate(titles, 1)]
    # склейка — отдельный тест; здесь только подсчёт независимых по готовому черновику
    story = to_story(_Draft(items=items, idx=list(range(len(items)))), SOURCES)
    assert story.corroboration == 2
    assert len(story.source_ids) == 4  # сами ленты в сюжете остаются


# --- персоны по фамилии и второй проход склейки (живая проверка 25.10) --------------------------

# тексты материалов из живой базы 23–24.10 (title и lead или первые 400 знаков текста, как в _text)

TRUMP_CANADA = (
    'prime',
    'Трамп может отменить пошлины на товары из Канады, пишет WSJ',
    'Администрация президента США Дональда Трампа, как ожидается, отменит 50-процентные пошлины на некоторые канадские товары, чтобы помочь сенатору-республиканке Сьюзан Коллинз и другим представителям Республиканской партии на предстоящих промежуточных выборах, сообщает Wall Street Journal со ссылкой на источники. "Ожидается, что США отменят 50-процентные пошлины на некоторые канадские товары, чтобы п',
)

TRUMP_PRIME = (
    'prime',
    'Трамп заявил, что новые пошлины не помогли бы разрешить конфликт на Украине',
    'Введение дополнительных пошлин США не помогло бы разрешить украинский конфликт из-за невысокого объема торговли со странами, заявил американский президент Дональд Трамп. "Пошлины помогли нам завершить восемь войн. Пять из восьми (конфликтов были разрешены - ред.), я бы сказал, из-за торговли и пошлин. "Если вы продолжите сражаться, мы введем пошлины в размере 200% для вас обоих". И они прекращали ',
)

TRUMP_BANKSTA = (
    'tg_banksta',
    'Президент США Дональд Трамп допустил введение 200% пошлин против Украины и России из-за отсутствия мирного соглашения. Заявление прозвучало на предвыборном митинге республиканцев.',
    'Президент США Дональд Трамп допустил введение 200% пошлин против Украины и России из-за отсутствия мирного соглашения. Заявление прозвучало на предвыборном митинге республиканцев.  «Пошлины помогли нам завершить восемь войн. Возможно, нам стоит немедленно применить этот подход к Украине и России. Я раньше об этом не думал, но это все равно не сработает, поскольку мы мало что у них покупаем», — зая',
)

TRUMP_MARKETTWITS = (
    'tg_markettwits',
    'Вчера Трамп заявил, что мог бы применить пошлины против Украины и России для принуждения к миру, но смысла в этом не видит т.к. объём торговли США с этими странами практически нулевой и введение…',
    '🇺🇸🇷🇺🇺🇦#украина #трамп #геополитика #пошлины #трамп Вчера Трамп заявил, что мог бы применить пошлины против Украины и России для принуждения к миру, но смысла в этом не видит т.к. объём торговли США с этими странами практически нулевой и введение пошлин будет неэффективным.  К тому же, введение пошлин против Росси стало бы очень тупым решением на фоне смягчения санкций против РФ на фоне дизельной с',
)

TRUMP_CANADA_2 = (
    'kommersant',
    'США отменили пошлины на некоторые канадские товары',
    'Президент США Дональд Трамп отменил пошлины в размере 50% на некоторые товары из Канады. Это решение затрагивает, в частности, листы фанеры, некоторые бумажные изделия, холодильное оборудование, а также упаковочные ящики и коробки, сообщила пресс-служба Белого дома.',
)

TRUMP_VED = (
    'vedomosti',
    'Трамп заявил о возможном введении 200%-ных пошлин против России и Украины',
    'Глава Белого дома сомневается в эффективности меры',
)

# фон для TF-IDF: в живом окне тысячи материалов, на трёх текстах idf считается иначе
BACKGROUND = [
    ("interfax", "Минфин разместил ОФЗ на 50 млрд рублей на аукционе", "Спрос превысил предложение"),
    ("rbc", "Сбербанк повысил ставки по вкладам", "Новые условия действуют с понедельника"),
    ("kommersant", "Нефть марки Brent подорожала на фоне сокращения запасов в США", ""),
    ("prime", "Курс юаня на Мосбирже снизился", "Торги проходят спокойно"),
    ("vedomosti", "Правительство продлило льготную ипотеку для семей с детьми", ""),
    ("tass", "Росстат зафиксировал замедление недельной инфляции", "Цены выросли на 0,1%"),
    ("interfax", "ЕС обсуждает новый пакет санкций против России", "Решение ожидается в ноябре"),
    ("rbc", "Китай увеличил импорт российского угля", "Поставки выросли за месяц"),
    ("kommersant", "АвтоВАЗ снизил продажи автомобилей Lada в сентябре", ""),
    ("prime", "Индекс Мосбиржи завершил торги ростом", "Лидерами стали акции нефтяников"),
    ("vedomosti", "Газпром нефть увеличила добычу на арктических месторождениях", ""),
    ("tass", "Путин провёл совещание по экономическим вопросам", "Обсуждали бюджет и налоги"),
]


def _items(*specs, step=60):
    news = [item(n, src, title, lead, minutes=n * step) for n, (src, title, lead) in enumerate(specs, 1)]
    bg = [item(f"bg{n}", src, t, lead, minutes=n * 7) for n, (src, t, lead) in enumerate(BACKGROUND)]
    return news + bg


def _groups(drafts):
    """Группы материалов по сюжетам без фоновых материалов."""
    groups = [sorted(i.id for i in d.items if not i.id.startswith("idbg")) for d in drafts]
    return sorted(g for g in groups if g)


def test_person_key_by_surname():
    from backend.extract.entities import extract_entities

    a = extract_entities("Президент США Дональд Трамп допустил введение 200% пошлин").keys()
    b = extract_entities("Трамп заявил о возможном введении 200%-ных пошлин против России и Украины").keys()
    assert "per:Трамп" in a and "per:Трамп" in b


LIVE_TRUMP = [  # (материал, минуты от первого) — порядок и время как в живой базе
    (TRUMP_CANADA, 0), (TRUMP_PRIME, 68), (TRUMP_BANKSTA, 242), (TRUMP_MARKETTWITS, 834),
    (TRUMP_CANADA_2, 995), (TRUMP_VED, 1027),
]


def _live(*names):
    chosen = [(n, spec, t) for n, (spec, t) in enumerate(LIVE_TRUMP, 1) if spec in names]
    news = [item(n, src, title, lead, minutes=t) for n, (src, title, lead), t in chosen]
    bg = [item(f"bg{n}", src, t, lead, minutes=n * 7) for n, (src, t, lead) in enumerate(BACKGROUND)]
    return news + bg


def test_trump_tariffs_rewrites_one_story():
    """Пересказы одного заявления разными словами (TF-IDF 0,21–0,25 < 0,30): общий per:Трамп и «200%»."""
    drafts = cluster_items(_live(TRUMP_PRIME, TRUMP_BANKSTA, TRUMP_VED))
    assert _groups(drafts) == [["id2", "id3", "id6"]]


def test_trump_canada_vs_russia_two_stories():
    """Один человек и тема «пошлины», но разные события: Канада и Россия с Украиной не склеиваются."""
    groups = _groups(cluster_items(_live(*(spec for spec, _ in LIVE_TRUMP))))
    canada, russia = {"id1", "id5"}, {"id2", "id3", "id6"}
    assert not any(set(g) & canada and set(g) & russia for g in groups)
    assert any(russia <= set(g) for g in groups)
    # MarketTwits пересказывает без числа «200%»: второй проход его не берёт (ограничение правила)
    assert ["id4"] in groups or any({"id4"} | russia <= set(g) for g in groups)


def test_shared_number_without_shared_entity_no_merge():
    """Общее число и похожесть без общей отличительной сущности — разные события."""
    a = item(1, "interfax", "Сбербанк повысил ставки по вкладам до 15%", minutes=0)
    b = item(2, "rbc", "ВТБ повысил ставки по вкладам до 15%", minutes=30)
    drafts = cluster_items([a, b], entity_keys=[{"org:Сбербанк"}, {"org:ВТБ"}])
    assert len(drafts) == 2


def test_cbr_forecast_same_day_merges_into_decision():
    items = [
        item(1, "cbr_press", "Банк России принял решение сохранить ключевую ставку на уровне 14,00% годовых"),
        item(2, "tg_banksta", "ЦБ сохранил ключевую ставку на уровне 14%", minutes=1),
        item(3, "prime", "ЦБ скорректировал прогнозы по инфляции, ставке и кредитованию", minutes=23),
        item(4, "frank_media", "Центробанк ухудшил прогнозы по инфляции и по ключевой ставке на 2026-2029 годы",
             "При этом прогноз по росту ВВП на 2026 год был улучшен", minutes=36),
        item(5, "interfax", "Главное из заявления Эльвиры Набиуллиной по итогам заседания Совета директоров "
             "Банка России по денежно-кредитной политике", minutes=105),
        # чужой прогноз и прогноз ЦБ на другой день — не часть решения
        item(6, "rbc", "Сбербанк ухудшил прогноз по ВВП на 2027 год", minutes=40),
        item(7, "prime", "ЦБ повысил прогноз цены на нефть Urals до $72 за баррель", minutes=60 * 30),
    ]
    # ключи — в порядке публикации (cluster_items сортирует материалы по времени)
    keys = [{"org:Банк России"}] * 4 + [{"org:Сбербанк"}] + [{"org:Банк России"}] * 2
    drafts = cluster_items(items, entity_keys=keys)
    decision = next(d for d in drafts if "id1" in {i.id for i in d.items})
    assert {i.id for i in decision.items} == {"id1", "id2", "id3", "id4", "id5"}


ALCOHOL = [  # живое окно 25.10: «Ъ» на сайте и в Telegram и перепечатка — три сюжета вместо одного
    ("kommersant", "Пошлину на иностранный крепкий алкоголь могут повысить уже в ноябре",
     "Федеральные власти намерены повысить пошлины на импорт крепкого алкоголя из недружественных стран "
     "с текущих €3 до €5 за литр, рассказали два источника “Ъ” на алкогольном рынке."),
    ("tg_kommersant", "Федеральные власти намерены повысить пошлины на импорт крепкого алкоголя из "
     "недружественных стран с текущих €3 до €5 за литр, стало известно «Ъ». Рост средней стоимости одной "
     "бутылки в таком случае…",
     "Российские производители ожидают, что такая мера позволит им вернуть часть потребителей."),
    ("tg_agroinvestor", "Федеральные власти намерены повысить пошлины на импорт крепкого алкоголя из "
     "недружественных стран с текущих 3 евро до 5 евро за литр, рассказали два источника «Коммерсанта» на "
     "алкогольном рынке.…",
     "Соответствующее постановление должно быть издано в ноябре 2026 года."),
]


def test_rewrites_without_number_or_entity_merge_by_title_words():
    """Без общего числа («3 евро» — малое целое) и без общей сущности: склеивают общие редкие слова заголовков."""
    items = _items(*ALCOHOL, step=0)
    items[1].published_at += timedelta(minutes=8)
    items[2].published_at += timedelta(hours=13)
    assert _groups(cluster_items(items)) == [["id1", "id2", "id3"]]


@pytest.mark.parametrize(
    ("numbers", "similarity", "merged"),
    [
        (({"64.3"}, {"64.3"}), 0.5, True),
        (({"64.3"}, set()), 0.5, True),  # у одной стороны чисел нет — не противоречие
        (({"64.3"}, {"66.8"}), 0.5, False),  # сводки одного рынка за разные дни
        (({"64.3"}, {"64.3"}), 0.25, False),  # похожесть ниже SIM_WORDS и SIM_RARE
    ],
)
def test_title_words_merge_conditions(numbers, similarity, merged):
    """Второй проход по словам заголовков: числа не противоречат и похожесть ≥ SIM_WORDS."""
    from backend.stories.cluster import _Draft, merge_related

    a = item(1, "prime", "Нефть марки Urals подорожала на торгах в Лондоне", minutes=0)
    b = item(2, "rbc", "Urals на торгах в Лондоне: нефть подорожала", minutes=60)
    drafts = [_Draft(items=[a], idx=[0]), _Draft(items=[b], idx=[1])]
    sim = np.array([[1.0, similarity], [similarity, 1.0]])
    words = [{"нефть", "марка", "urals", "подорожать", "торги", "лондон"}] * 2
    out = merge_related(drafts, sim, [set(), set()], list(numbers), words)
    assert (len(out) == 1) is merged


def _pair(sim_ab, keys, words):
    from backend.stories.cluster import _Draft, merge_related

    a = item(1, "prime", "a", minutes=0)
    b = item(2, "rbc", "b", minutes=60)
    drafts = [_Draft(items=[a], idx=[0]), _Draft(items=[b], idx=[1])]
    sim = np.array([[1.0, sim_ab], [sim_ab, 1.0]])
    return len(merge_related(drafts, sim, keys, [set(), set()], words))


def test_note_next_to_big_story_merges_by_head_titles():
    """«Россия завоевала право провести чемпионат мира по тяжёлой атлетике»: заголовки похожи (0,40), три общих
    нечастых слова; по объединению слов большого сюжета и средней похожести (0,34) второй проход её не брал."""
    big = {"москва", "принять", "чемпионат", "тяжёлый", "атлетика", "iwf", "пресс-служба"}
    small = {"завоевать", "право", "провести", "чемпионат", "тяжёлый", "атлетика"}
    assert _pair(0.40, [{"org:IWF"}, set()], [big, small]) == 1
    assert _pair(0.36, [{"org:IWF"}, set()], [big, small]) == 2  # заголовки не так похожи
    assert _pair(0.40, [{"org:IWF"}, {"org:FIFA"}], [big, small]) == 2  # разные организации
    assert _pair(0.40, [{"org:IWF"}, set()], [big, {"завоевать", "право", "чемпионат", "атлетика"}]) == 2


def test_trump_retelling_without_number_stays_apart():
    """Ограничение: пересказ без «200%» делит с сюжетом одно нечастое слово («пошлина»). Правило, которое его
    ловит (общая персона, три общие страны, похожесть ≥ 0,25), на живом окне склеивало и разные события."""
    keys = [{"per:Трамп"}, {"per:Трамп"}]
    told = {"трамп", "введение", "пошлина", "россия", "украина", "возможный", "президент"}
    retold = {"трамп", "введение", "пошлина", "россия", "украина", "применить", "принуждение", "торговля"}
    assert _pair(0.27, keys, [told, retold]) == 2


def test_entity_noise_cleaned():
    """Хвост поста и площадки — не сущности; МИД под разными именами — одна."""
    from backend.extract.entities import extract_entities

    assert not extract_entities("Австрия объявила дипломатов персонами нон-грата, сообщает Reuters.. ⚡️Австрия").keys()
    assert extract_entities("Решение приняло Министерство иностранных дел Австрии").keys() == {"org:МИД"}
    assert extract_entities("Пишут Profil и Heute со ссылкой на МИД. Подписывайтесь на канал в MAX").keys() == {"org:МИД"}
