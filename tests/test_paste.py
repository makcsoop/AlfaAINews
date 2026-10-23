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
    it = make_item(url=None, text="Минфин разместил ОФЗ на 50 млрд рублей.\nСпрос превысил предложение.", title=None, now=NOW)
    assert it.source_id == "user_paste" and it.tier == 3 and it.dropped_reason is None
    assert it.title == "Минфин разместил ОФЗ на 50 млрд рублей."
    same = make_item(url=None, text="Минфин разместил ОФЗ на 50 млрд рублей.\nСпрос превысил предложение.", title="x", now=NOW)
    assert same.id == it.id  # повторная вставка того же текста — тот же материал


def test_too_short_text_rejected():
    with pytest.raises(PasteError):
        make_item(url=None, text="коротко", title=None, now=NOW)
