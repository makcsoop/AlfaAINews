"""Примеры разборов аналитиков рынка для подсказки двум аналитикам.

Берутся только разборы после ПРОШЛЫХ событий того же типа (post_event_review), опубликованные строго раньше
даты сюжета (инвариант 10). Это образец рассуждения, а не источник фактов: в refs аналитика они не попадают.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from pydantic import BaseModel

from newsai.agents.tools.source import ToolDataSource
from newsai.schemas import MarketKnowledge

CHARS_PER_TOKEN = 4  # оценка токенов: len // 4
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class Example(BaseModel):
    id: str
    channel_source: str
    published_at: datetime
    text: str


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _sector_match(sectors: list[str], sector: str | None) -> bool:
    if not sector:
        return False
    return any(s.startswith(sector) or sector.startswith(s) for s in sectors if s)


def cut_by_sentence(text: str, max_chars: int) -> str:
    """Самый длинный префикс из целых предложений не длиннее max_chars; пусто, если не влезло первое."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        cand = f"{out} {sentence}".strip()
        if len(cand) > max_chars:
            break
        out = cand
    return out


def select_examples(
    source: ToolDataSource,
    *,
    event_type: str | None,
    sector: str | None,
    story_published_at: datetime,
    max_tokens: int = 600,
    k: int = 3,
    allow_telegram_origin: bool = True,
) -> list[Example]:
    """До k примеров в пределах max_tokens на все вместе.

    Порядок: тот же event_type (если задан, другие типы не берутся), затем совпадение сектора, затем свежее.
    """
    cutoff = _utc(story_published_at)
    candidates: list[MarketKnowledge] = []
    for mk in source.market_knowledge():
        if mk.kind != "post_event_review" or not mk.statement.strip():
            continue
        if _utc(mk.published_at) >= cutoff:  # инвариант 10; заодно отсекает разбор самого сюжета
            continue
        target: date | None = mk.target_event_date
        if target is not None and target >= cutoff.date():  # разбор относится к этому же или будущему событию
            continue
        if mk.origin == "telegram" and not allow_telegram_origin:
            continue
        if event_type is not None and mk.event_type != event_type:
            continue
        candidates.append(mk)
    candidates.sort(
        key=lambda m: (_sector_match(m.sectors_okved, sector), _utc(m.published_at), m.id), reverse=True
    )
    left = max_tokens * CHARS_PER_TOKEN  # суммарный бюджет в символах: sum(len)//4 <= max_tokens
    out: list[Example] = []
    for mk in candidates:
        if len(out) >= k or left <= 0:
            break
        text = cut_by_sentence(mk.statement, left)
        if not text:
            continue
        left -= len(text)
        out.append(Example(id=mk.id, channel_source=mk.source_id, published_at=mk.published_at, text=text))
    return out
