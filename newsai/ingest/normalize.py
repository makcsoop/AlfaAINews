"""Нормализация текста, ключи и отпечатки материалов."""

import hashlib
import html
import re
import unicodedata

_WS = re.compile(r"[ \t   ]+")
_NL = re.compile(r"\n{3,}")
_TAGS = re.compile(r"<[^>]+>")
_TOKEN = re.compile(r"\w+", re.UNICODE)

# Датлайны агентств: «МОСКВА, 19 окт - ПРАЙМ.», «Москва. 21 октября. INTERFAX.RU - »
_DATELINES = [
    re.compile(
        r"^[ \t]*[А-ЯЁ][А-ЯЁ\- ]{1,40},\s*\d{1,2}\s+[а-я]{3,9}\.?\s*[-–—]\s*[A-Za-zА-ЯЁа-яё]{2,15}\.\s*", re.M
    ),
    re.compile(r"^[ \t]*[А-ЯЁ][а-яё\- ]{1,40}\.\s*\d{1,2}\s+[а-я]{3,9}\.\s*INTERFAX\.RU\s*[-–—]\s*", re.M),
]
_DATELINE_WINDOW = 600  # trafilatura кладёт перед датлайном заголовок и подзаголовок


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = html.unescape(_TAGS.sub(" ", s))
    s = unicodedata.normalize("NFC", s).replace("\r\n", "\n")
    s = "\n".join(_WS.sub(" ", line).strip() for line in s.split("\n"))
    s = _NL.sub("\n\n", s).strip()
    return s or None


def strip_dateline(text: str) -> tuple[str, str | None]:
    """Убирает датлайн агентства и всё перед ним (заголовки); город возвращает отдельно как признак."""
    for rx in _DATELINES:
        m = rx.search(text, 0, _DATELINE_WINDOW)
        if m:
            city = re.split(r"[,.]", m.group(0).strip(), maxsplit=1)[0].strip().title()
            return text[m.end() :], city
    return text, None


def match_key(s: str) -> str:
    """Ключ сопоставления: нижний регистр, ё → е, только слова."""
    return " ".join(_TOKEN.findall(s.lower().replace("ё", "е")))


def item_id(source_id: str, native_id: str) -> str:
    return hashlib.sha1(f"{source_id}:{native_id}".encode()).hexdigest()


def content_hash(title: str, text: str | None) -> str:
    return hashlib.sha1(match_key(f"{title} {text or ''}").encode()).hexdigest()


def simhash(text: str, ngram: int = 3) -> int:
    """64-битный SimHash по словным шинглам. Знаковый int64, чтобы влезал в BIGINT."""
    words = match_key(text).split()
    shingles = [" ".join(words[i : i + ngram]) for i in range(max(1, len(words) - ngram + 1))]
    v = [0] * 64
    for sh in shingles:
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            v[bit] += 1 if h >> bit & 1 else -1
    out = sum(1 << bit for bit in range(64) if v[bit] > 0)
    return out - (1 << 64) if out >= 1 << 63 else out


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()
