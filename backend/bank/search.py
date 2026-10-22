"""Поиск по реестру позиций ЦМИ: BM25 с лемматизацией pymorphy + плотный поиск, слияние RRF (k = 60).

Плотная часть — символьный TF-IDF: эмбеддинги (USER-bge-m3 + pgvector) подключаются, если организаторы
разрешат энкодеры; интерфейс поиска от этого не меняется. Ищем по позициям (тезис + цитата + название обзора).
Фильтр `as_of` оставляет только позиции, опубликованные до даты события (сюрприз считается к ожиданиям «до»),
и помечает устаревшие (superseded_by / valid_until).
"""

import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import Connection, select

from backend.db import models as m
from backend.schemas import BankPosition

RRF_K = 60
_WORD = re.compile(r"[a-zа-яё0-9]+(?:[.,]\d+)?", re.IGNORECASE)


@lru_cache
def _morph():
    # pymorphy2 напрямую не стартует на Python 3.12 (inspect.getargspec); обёртка Natasha работает
    from natasha import MorphVocab

    return MorphVocab()


@lru_cache(maxsize=50_000)
def _lemma(word: str) -> str:
    if word[0].isdigit():
        return word.replace(",", ".")
    return _morph().parse(word)[0].normal_form


def tokens(text: str) -> list[str]:
    return [_lemma(w.lower().replace("ё", "е")) for w in _WORD.findall(text)]


@dataclass
class Hit:
    position: BankPosition
    score: float
    bm25_rank: int | None
    dense_rank: int | None
    stale: bool  # устарела: перекрыта новой позицией или истёк срок актуальности


class BankIndex:
    def __init__(self, positions: list[BankPosition]):
        self.positions = positions
        self._by_id = {p.id: p for p in positions}
        docs = [f"{p.doc_title}. {p.thesis} {p.quote}" for p in positions]
        self.bm25 = BM25Okapi([tokens(d) for d in docs]) if docs else None
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)
        self.matrix = self.vec.fit_transform(docs) if docs else None

    def _stale(self, p: BankPosition, as_of: date | None) -> bool:
        """Устарела на дату события: перекрыта позицией, вышедшей не позже события, или истёк срок."""
        if as_of and p.valid_until and p.valid_until < as_of:
            return True
        if not p.superseded_by:
            return False
        newer = self._by_id.get(p.superseded_by)
        return as_of is None or newer is None or newer.doc_date <= as_of

    @classmethod
    def load(cls, conn: Connection) -> "BankIndex":
        rows = conn.execute(select(m.bank_positions.c.data)).scalars()
        return cls([BankPosition.model_validate(r) for r in rows])

    def search(
        self,
        query: str,
        k: int = 5,
        channel: str | None = None,
        as_of: date | None = None,
        registry_only: bool = True,
    ) -> list[Hit]:
        if not self.positions:
            return []
        n = len(self.positions)
        bm = self.bm25.get_scores(tokens(query))
        dense = (self.matrix @ self.vec.transform([query]).T).toarray().ravel()
        bm_rank = {i: r for r, i in enumerate(np.argsort(-bm)) if bm[i] > 0}
        dn_rank = {i: r for r, i in enumerate(np.argsort(-dense)) if dense[i] > 0}
        hits = []
        for i in range(n):
            p = self.positions[i]
            if registry_only and "#r" not in p.id:
                continue
            if channel and p.channel != channel:
                continue
            if as_of and p.doc_date > as_of:  # только опубликованное до события
                continue
            if i not in bm_rank and i not in dn_rank:
                continue
            score = sum(1 / (RRF_K + r + 1) for r in (bm_rank.get(i), dn_rank.get(i)) if r is not None)
            stale = self._stale(p, as_of)
            hits.append(Hit(p, score, bm_rank.get(i), dn_rank.get(i), stale))
        hits.sort(key=lambda h: (h.stale, -h.score))  # действующие позиции выше устаревших
        return hits[:k]
