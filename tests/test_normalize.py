from newsai.ingest.normalize import clean_text, content_hash, item_id, simhash, strip_dateline


def test_clean_text_spaces_entities_tags():
    raw = "ЦБ сохранил&nbsp;ставку <b>14%</b> годовых .\r\n\r\n\r\n\r\nВторой абзац"
    assert clean_text(raw) == "ЦБ сохранил ставку 14% годовых .\n\nВторой абзац"


def test_clean_text_empty():
    assert clean_text(None) is None
    assert clean_text("  <br> ") is None


def test_dateline_variants():
    assert strip_dateline("МОСКВА, 21 окт - ПРАЙМ. Текст") == ("Текст", "Москва")
    assert strip_dateline("САНКТ-ПЕТЕРБУРГ, 3 сен — ПРАЙМ. Текст") == ("Текст", "Санкт-Петербург")
    assert strip_dateline("Москва. 21 октября. INTERFAX.RU - Текст") == ("Текст", "Москва")


def test_dateline_absent_or_far():
    text = "Обычный текст без датлайна."
    assert strip_dateline(text) == (text, None)
    far = "x" * 700 + "\nМОСКВА, 21 окт - ПРАЙМ. Текст"
    assert strip_dateline(far) == (far, None)


def test_ids_and_hashes_stable():
    assert item_id("prime", "1") == item_id("prime", "1") != item_id("rbc", "1")
    assert content_hash("Ёлка", "ёж") == content_hash("елка", "еж")
    assert isinstance(simhash(""), int)
    assert -(1 << 63) <= simhash("любой текст " * 50) < (1 << 63)
