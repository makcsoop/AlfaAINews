"""Проверка кодом: цитата есть в тексте источника, число есть в цитате.

Используется экстрактором (поле без подтверждения уходит в unverified_fields) и годится для критика.
"""

import re

from rapidfuzz import fuzz

QUOTE_MIN_SCORE = 90  # ТЗ: rapidfuzz ≥ 90

_NUM = re.compile(r"[-−]?\d+(?:[  ]\d{3})*(?:[.,]\d+)?")


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е").replace(" ", " ").replace("−", "-")
    s = re.sub(r"[«»“”„\"']", "", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_in_text(quote: str | None, text: str) -> bool:
    """Дословная (с поправкой на кавычки, ё и пробелы) или почти дословная цитата."""
    if not quote or not quote.strip():
        return False
    q, t = _norm(quote), _norm(text)
    if q in t:
        return True
    if len(q) < 12:  # короткие цитаты только дословно, иначе fuzzy находит их где угодно
        return False
    return fuzz.partial_ratio(q, t) >= QUOTE_MIN_SCORE


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0).replace(" ", "").replace(" ", "").replace(",", ".").replace("−", "-")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def number_in_quote(value: float, quote: str) -> bool:
    """Число есть в цитате: «14%» = «14 процентов», «1,5 млрд» = 1.5. Сравнение с допуском на округление."""
    return any(abs(v - value) <= max(1e-9, abs(value) * 1e-6) for v in numbers_in(quote))
