"""Направления факторов из отчётов аналитиков и вкладов скоринга. Только арифметика, без текста."""

from collections.abc import Sequence

from newsai.schemas import AnalystReport, RelevanceScore
from newsai.schemas.common import ChannelImpact

MIN_STRENGTH = 0.2  # слабее — направление не считаем определённым


def channel_impacts(analyses: Sequence[AnalystReport], channel: str) -> list[ChannelImpact]:
    return [c for a in analyses for c in a.channels if c.channel == channel]


def channel_mean(analyses: Sequence[AnalystReport], channel: str) -> float | None:
    imp = channel_impacts(analyses, channel)
    return sum(c.impact for c in imp) / len(imp) if imp else None


def channel_horizon(analyses: Sequence[AnalystReport], channel: str) -> str | None:
    """Горизонт самого сильного воздействия по каналу."""
    imp = [c for c in channel_impacts(analyses, channel) if c.horizon]
    if not imp:
        return None
    return max(imp, key=lambda c: abs(c.impact)).horizon


def analysts_disagree(analyses: Sequence[AnalystReport], channel: str) -> bool:
    signs = {1 if c.impact > 0 else -1 for c in channel_impacts(analyses, channel) if abs(c.impact) >= 0.3}
    return len(signs) > 1


def rate_direction(analyses: Sequence[AnalystReport], relevance: RelevanceScore) -> int:
    """+1 — ставка выше/дольше (заёмные дороже, депозиты доходнее), −1 — ставка ниже, 0 — неясно.

    Источник — аналитики: rate_cost < 0 и deposit_yield > 0 означают «ставка выше». Нет отчётов по этим
    каналам — берём знак вкладов скоринга (rate_cost со знаком −1 или deposit_yield со знаком +1).
    """
    votes: list[int] = []
    m = channel_mean(analyses, "rate_cost")
    if m is not None and abs(m) >= MIN_STRENGTH:
        votes.append(1 if m < 0 else -1)
    m = channel_mean(analyses, "deposit_yield")
    if m is not None and abs(m) >= MIN_STRENGTH:
        votes.append(1 if m > 0 else -1)
    if not votes:
        for c in relevance.contributions:
            if c.factor == "rate_cost" and c.sign:
                votes.append(1 if c.sign < 0 else -1)
            elif c.factor == "deposit_yield" and c.sign:
                votes.append(1 if c.sign > 0 else -1)
    s = sum(votes)
    return (s > 0) - (s < 0)
