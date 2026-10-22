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
        они есть почти в каждой экономической новости, поэтому в ключи не входят."""
        return (
            {f"org:{x}" for x in self.orgs}
            | {f"per:{x}" for x in self.persons}
            | {f"com:{x}" for x in self.commodities}
        )

    def all_names(self) -> list[str]:
        return sorted(self.orgs | self.locations | self.persons)


@lru_cache
def _dicts():
    cfg = yaml.safe_load(DICTIONARIES.read_text(encoding="utf-8"))
    alias = {}
    for canon, variants in cfg["aliases"].items():
        for v in [canon, *variants]:
            alias[_key(v)] = canon
    cur = {k: re.compile(v, re.IGNORECASE) for k, v in cfg["currencies"].items()}
    com = {k: re.compile(v, re.IGNORECASE) for k, v in cfg["commodities"].items()}
    return alias, cur, com, set(cfg["regulators"])


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


def extract_entities(text: str, *, ner: bool = True) -> Entities:
    alias, cur, com, _ = _dicts()
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
        name = alias.get(_key(span.text)) or canonical(span.normal or span.text)
        if len(name) < 2:
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
    return ents
