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
