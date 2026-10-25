"""Нормализация текста, ключи и отпечатки материалов."""

import hashlib
import html
import re
import unicodedata

# пробел, таб, неразрывный (U+00A0), тонкий (U+2009) и узкий неразрывный (U+202F) пробелы
_SPACES = " \t" + "".join(map(chr, (0x00A0, 0x2009, 0x202F)))
_WS = re.compile(f"[{_SPACES}]+")
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


# слух и неподтверждённые сведения: общий шаблон для склейки (статус сюжета) и экстрактора (is_rumor)
RUMOR = re.compile(
    r"по (данным|информации|словам) (источник|собеседник)|источники? (в|из|знаком|близк)|якобы|по слухам"
    r"|сообщают (сми|источники)|по неофициальным данным|неофициальн\w*|сообщил\w* источник"
    r"|не\s+подтвержд\w*|неподтвержд\w*|из (закрытого|анонимного) (чата|канала)",
    re.IGNORECASE,
)

# заголовок вставки пользователя: маркеры срочности, повторы пунктуации, разделители «;;;» и «|»
_TITLE_MARKERS = re.compile(r"\b(срочно|молния|breaking|эксклюзив|exclusive)\b[:!.\s-]*", re.IGNORECASE)
_TITLE_SPLIT = re.compile(r"\s*(?:[;|]{1,}|[!?.:]{2,}|\s[-–—]{2,}\s)\s*")


def clean_title(s: str | None) -> str | None:
    """«!!! СРОЧНО ;;; ЦБ поднимет ставку ;;; данные не подтверждены» → «ЦБ поднимет ставку»:
    первая содержательная часть без маркеров; капслок — в обычный регистр."""
    s = clean_text(s)
    if not s:
        return None
    parts = [_TITLE_MARKERS.sub("", p).strip(" -–—:;|") for p in _TITLE_SPLIT.split(s)]
    parts = [p for p in parts if p]
    if not parts:
        return None
    title = next((p for p in parts if len(p.split()) >= 3), parts[0])
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7 and len(title.split()) > 3:
        title = " ".join(w if _is_acronym(w) else w.lower() for w in title.split())
        title = title[0].upper() + title[1:]
    return title


def _is_acronym(word: str) -> bool:
    """ЦБ, ОФЗ, НДС — короткие слова капслоком оставляем как есть."""
    core = word.strip(".,:;!?%«»\"()")
    return 1 < len(core) <= 3 and core.isupper()


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
