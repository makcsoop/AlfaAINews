"""«Вставить ссылку или текст»: проверка ссылок и сборка материала — без сети и без БД."""

from datetime import UTC, datetime

import pytest

from backend.ingest.paste import PasteError, check_url, make_item

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://1.1.1.1/x",
        "file:///etc/passwd",
        "http://127.0.0.1:8000/labeling/export.csv",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data/",  # метаданные облака
        "http://[::1]/",
        "javascript:alert(1)",
    ],
)
def test_internal_and_non_http_urls_rejected(url):
    with pytest.raises(PasteError):
        check_url(url)


def test_public_ip_url_allowed():
    assert check_url("https://1.1.1.1/news?id=1") == "https://1.1.1.1/news?id=1"


def test_item_is_unverified_user_source():
    it = make_item(url=None, text="Минфин разместил ОФЗ на 50 млрд рублей.\nСпрос превысил предложение.", title=None, now=NOW, owner="U_A")
    assert it.source_id == "user_paste" and it.tier == 3 and it.dropped_reason is None
    assert it.title == "Минфин разместил ОФЗ на 50 млрд рублей."
    same = make_item(url=None, text="Минфин разместил ОФЗ на 50 млрд рублей.\nСпрос превысил предложение.", title="x", now=NOW, owner="U_A")
    assert same.id == it.id  # повторная вставка того же текста — тот же материал


def test_paste_is_private_to_owner():
    # B3: вставка личная — у каждой компании свой материал с меткой владельца
    from backend.ingest.private import item_owner

    text = "Минфин разместил ОФЗ на 50 млрд рублей.\nСпрос превысил предложение."
    a = make_item(url=None, text=text, title=None, now=NOW, owner="U_A")
    b = make_item(url=None, text=text, title=None, now=NOW, owner="U_B")
    assert a.id != b.id
    assert item_owner(a) == "U_A" and item_owner(b) == "U_B"


def test_too_short_text_rejected():
    with pytest.raises(PasteError):
        make_item(url=None, text="коротко", title=None, now=NOW, owner="U_A")


@pytest.fixture
def dns(monkeypatch):
    """Подменный DNS: имя → адрес; второй запрос того же имени может вернуть другой адрес (rebinding)."""
    import socket

    from backend.ingest import paste

    table: dict[str, list[str]] = {}

    def getaddrinfo(host, port, *a, **kw):
        ips = table[host]
        ip = ips.pop(0) if len(ips) > 1 else ips[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(paste.socket, "getaddrinfo", getaddrinfo)
    return table


def test_fetch_connects_to_checked_ip_with_host_and_sni(dns):
    import httpx

    from backend.ingest.paste import fetch_page

    dns["news.example"] = ["93.184.216.34", "10.0.0.5"]  # повторный резолв вёл бы во внутреннюю сеть
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        html = "<html><head><title>Новость</title></head><body><article><p>" + "Текст статьи. " * 50
        return httpx.Response(200, html=html + "</p></article></body></html>")

    title, text = fetch_page("https://news.example/a", transport=httpx.MockTransport(handler))
    req = seen[0]
    assert req.url.host == "93.184.216.34" and req.headers["host"] == "news.example"
    assert req.extensions["sni_hostname"] == "news.example"
    assert title == "Новость" and text and "Текст статьи" in text


def test_fetch_rejects_redirect_inside_and_caps_body(dns, monkeypatch):
    import httpx

    from backend.ingest import paste

    dns["news.example"] = ["93.184.216.34"]
    dns["intranet.example"] = ["10.0.0.5"]
    redirect = httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "http://intranet.example/"}))
    with pytest.raises(PasteError, match="внутреннюю сеть"):
        paste.fetch_page("https://news.example/a", transport=redirect)

    monkeypatch.setattr(paste, "MAX_BYTES", 1000)
    chunks: list[int] = []

    def stream():
        for _ in range(100):  # 100 КБ, а читаем не больше 1000 байт
            chunks.append(1)
            yield b"x" * 1024

    big = httpx.MockTransport(lambda r: httpx.Response(200, content=stream()))
    paste.fetch_page("https://news.example/big", transport=big)
    assert len(chunks) <= 2


SHOUT = "!!! СРОЧНО ;;; ЦБ экстренно поднимет ставку до 20% в понедельник ;;; данные не подтверждены"


def test_paste_title_cleaned_of_markers_and_punctuation():
    # CRITERIA_CHECK 7г: заголовок вставки — первая строка как есть, с «!!!», «;;;» и «СРОЧНО»
    it = make_item(url=None, text=SHOUT + "\nПодробностей нет.", title=None, now=NOW, owner="U_A")
    assert it.title == "ЦБ экстренно поднимет ставку до 20% в понедельник"
    assert "данные не подтверждены" in it.text  # текст не трогаем: по нему статус «слух»


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("МОЛНИЯ: ЦБ СОХРАНИЛ СТАВКУ 14%", "ЦБ сохранил ставку 14%"),
        ("BREAKING | Минфин разместил ОФЗ", "Минфин разместил ОФЗ"),
        ("Обычный заголовок без шума", "Обычный заголовок без шума"),
    ],
)
def test_clean_title(raw, clean):
    from backend.ingest.normalize import clean_title

    assert clean_title(raw) == clean


def test_unconfirmed_paste_becomes_rumor():
    from backend.ingest.registry import load_sources
    from backend.stories.cluster import _Draft, to_story

    it = make_item(url=None, text=SHOUT + "\nПодробностей нет.", title=None, now=NOW, owner="U_A")
    story = to_story(_Draft(items=[it], idx=[0]), {s.id: s for s in load_sources()})
    assert story.status == "rumor"


@pytest.mark.parametrize(
    "text",
    [
        "Данные не подтверждены: ЦБ поднимет ставку",
        "По неподтверждённым данным, банк закроют",
        "Как сообщил источник, сделка сорвалась",
        "Информация из закрытого чата трейдеров",
    ],
)
def test_rumor_markers(text):
    from backend.extract import rules
    from backend.stories import cluster

    assert cluster.RUMOR.search(text) and rules.RUMOR.search(text)


TG_EMBED = """<html><body>
<div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="centralbank_russia/3900">
 <div class="tgme_widget_message_text js-message_text">Банк России сохранил ключевую ставку<br/>на уровне 14% годовых.</div>
 <a class="tgme_widget_message_date" href="#"><time datetime="2026-10-23T13:30:00+03:00">13:30</time></a>
</div></body></html>"""


def test_telegram_post_text_from_embed(dns):
    """Обычная страница поста t.me текст подгружает скриптом — берём встраиваемую версию ?embed=1."""
    import httpx

    from backend.ingest.paste import fetch_page, telegram_post

    assert telegram_post("https://t.me/centralbank_russia/3900") == ("centralbank_russia", 3900)
    assert telegram_post("https://t.me/s/centralbank_russia/3900") == ("centralbank_russia", 3900)
    assert telegram_post("https://www.rbc.ru/finances/1/2") is None
    dns["t.me"] = ["149.154.167.99"]
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, html=TG_EMBED)

    title, text = fetch_page("https://t.me/centralbank_russia/3900", transport=httpx.MockTransport(handler))
    assert seen[0].url.params.get("embed") == "1" and seen[0].url.path == "/centralbank_russia/3900"
    assert text == "Банк России сохранил ключевую ставку\nна уровне 14% годовых." and title is None


def test_unreachable_site_gives_clear_error_after_retry(dns):
    """Сайт закрыт для зарубежного сервера (РБК): повтор, потом понятная ошибка, а не «попробуйте позже»."""
    import httpx

    from backend.ingest.paste import UNREACHABLE, fetch_page

    dns["www.rbc.ru"] = ["178.248.233.6"]
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        raise httpx.ConnectTimeout("_ssl.c:993: The handshake operation timed out")

    with pytest.raises(PasteError) as e:
        fetch_page("https://www.rbc.ru/technology_and_media/26/10/2026/x", transport=httpx.MockTransport(handler))
    assert str(e.value) == UNREACHABLE and len(calls) == 2
