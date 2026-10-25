"""Сущности текста: Natasha (ORG, LOC, PER) + синонимы + словари валют и сырья.

Общий модуль для склейки (общая сущность — обязательное условие) и экстрактора фактов.
Natasha грузится лениво и один раз (около 2 с).
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DICTIONARIES = Path(__file__).resolve().parents[2] / "config" / "dictionaries.yaml"


@dataclass
class Entities:
    orgs: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    persons: set[str] = field(default_factory=set)
    currencies: set[str] = field(default_factory=set)
    commodities: set[str] = field(default_factory=set)

    def keys(self) -> set[str]:
        """Ключи для сравнения сущностей двух текстов. Валюты и страны не различают сюжеты —
        они есть почти в каждой экономической новости, поэтому в ключи не входят.

        Персона — по фамилии (последнее слово нормальной формы): «Дональд Трамп» в одном тексте
        и «Трамп» в другом дают один ключ `per:Трамп`.
        """
        return (
            {f"org:{x}" for x in self.orgs}
            | {f"per:{sn}" for x in self.persons if (sn := surname(x))}
            | {f"com:{x}" for x in self.commodities}
        )

    def all_names(self) -> list[str]:
        return sorted(self.orgs | self.locations | self.persons)


def surname(name: str) -> str | None:
    """Фамилия из имени персоны: последнее слово, не инициал («Шохин А. С.» → «Шохин»)."""
    words = [w for w in name.replace(".", ". ").split() if len(w) > 2 and not w.endswith(".")]
    return words[-1] if words else None


@lru_cache
def _dicts():
    cfg = yaml.safe_load(DICTIONARIES.read_text(encoding="utf-8"))
    alias = {}
    for canon, variants in cfg["aliases"].items():
        for v in [canon, *variants]:
            alias[_key(v)] = canon
    skip = {_key(x) for x in cfg.get("media", []) + cfg.get("generic", [])}
    cur = {k: re.compile(v, re.IGNORECASE) for k, v in cfg["currencies"].items()}
    com = {k: re.compile(v, re.IGNORECASE) for k, v in cfg["commodities"].items()}
    per = {k: re.compile(v, re.IGNORECASE) for k, v in (cfg.get("persons") or {}).items()}
    return alias, cur, com, set(cfg["regulators"]), skip, per


# хвост после «..» или эмодзи — склейка NER с текстом поста: «Reuters.. ⚡️Австрия» → «Reuters»
_NAME_TAIL = re.compile(r"\.\..*$|[^\w\s\-«»\"'.,&+()].*$", re.DOTALL)


def _clean_name(name: str) -> str:
    return _NAME_TAIL.sub("", name).strip()


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.lower().replace("ё", "е").strip(" «»\"'.,"))


@lru_cache
def _natasha():
    from natasha import Doc, MorphVocab, NewsEmbedding, NewsMorphTagger, NewsNERTagger, Segmenter

    emb = NewsEmbedding()
    return Doc, Segmenter(), MorphVocab(), NewsMorphTagger(emb), NewsNERTagger(emb)


def canonical(name: str) -> str:
    alias = _dicts()[0]
    return alias.get(_key(name), name.strip(" «»\"'"))


def regulators() -> set[str]:
    return _dicts()[3]


def _is_noise(name: str, kind: str) -> bool:
    if _key(name) in _dicts()[4]:
        return True
    # город из датлайна капсом («ПЕТЕРБУРГ», «С.-ПЕТЕРБУРГ») — не организация
    return kind == "ORG" and name.replace(".", "").replace("-", "").isupper() and len(name) >= 6


def _dedupe_persons(persons: set[str]) -> set[str]:
    """«Путин Путин» → «Путин»; фамилия отдельно от полного имени не нужна."""
    cleaned = set()
    for p in persons:
        tokens = p.split()
        cleaned.add(" ".join(dict.fromkeys(tokens)))
    return {p for p in cleaned if not any(p != q and p in q.split() for q in cleaned)}


def extract_entities(text: str, *, ner: bool = True) -> Entities:
    alias, cur, com, *_ = _dicts()
    ents = Entities(
        currencies={k for k, rx in cur.items() if rx.search(text)},
        commodities={k for k, rx in com.items() if rx.search(text)},
    )
    if not ner or not text.strip():
        return ents
    Doc, segmenter, morph_vocab, morph_tagger, ner_tagger = _natasha()
    doc = Doc(text[:5000])
    doc.segment(segmenter)
    doc.tag_morph(morph_tagger)
    doc.tag_ner(ner_tagger)
    for span in doc.spans:
        span.normalize(morph_vocab)
        # сначала исходный текст: Natasha иногда портит устойчивые имена («Банк России» → «Банк Россия»)
        raw = _clean_name(span.text)
        name = alias.get(_key(raw)) or canonical(_clean_name(span.normal or "") or raw)
        if len(name) < 2 or _is_noise(name, span.type):
            continue
        {"ORG": ents.orgs, "LOC": ents.locations, "PER": ents.persons}[span.type].add(name)
    # регулятор, записанный аббревиатурой, Natasha иногда пропускает — добираем по словарю синонимов
    low = _key(text)
    for variant, canon in alias.items():
        if (
            canon in regulators()
            and len(variant) > 2
            and re.search(rf"(?<!\w){re.escape(variant)}(?!\w)", low)
        ):
            ents.orgs.add(canon)
    # ключевых персон Natasha иногда пропускает («Трамп заявил о …» без сущностей) — добираем по словарю
    for canon, rx in _dicts()[5].items():
        if rx.search(text) and not any(surname(p) == canon for p in ents.persons):
            ents.persons.add(canon)
    ents.persons = _dedupe_persons(ents.persons)
    return ents
