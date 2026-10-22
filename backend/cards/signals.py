"""Направления факторов из отчётов аналитиков и вкладов скоринга. Только арифметика, без текста."""

from collections.abc import Sequence

from backend.schemas import AnalystReport, CompanyProfile, RelevanceScore
from backend.schemas.common import ChannelImpact

MIN_STRENGTH = 0.2  # слабее — направление не считаем определённым


DIRECTION_SIGN = {"up": 1.0, "down": -1.0, "flat": 0.0}


def signed(c: ChannelImpact) -> float:
    """Направление фактора со знаком × сила: ставка ↑ сильно = +0.9. Знак для клиента здесь не ставится."""
    return DIRECTION_SIGN[c.direction] * c.strength


def channel_impacts(analyses: Sequence[AnalystReport], channel: str) -> list[ChannelImpact]:
    return [c for a in analyses for c in a.channels if c.channel == channel]


def channel_mean(analyses: Sequence[AnalystReport], channel: str) -> float | None:
    imp = channel_impacts(analyses, channel)
    return sum(signed(c) for c in imp) / len(imp) if imp else None


def channel_horizon(analyses: Sequence[AnalystReport], channel: str) -> str | None:
    """Горизонт самого сильного воздействия по каналу."""
    imp = [c for c in channel_impacts(analyses, channel) if c.horizon]
    if not imp:
        return None
    return max(imp, key=lambda c: c.strength).horizon


def analysts_disagree(analyses: Sequence[AnalystReport], channel: str) -> bool:
    signs = {
        1 if c.direction == "up" else -1
        for c in channel_impacts(analyses, channel)
        if c.direction != "flat" and c.strength >= 0.3
    }
    return len(signs) > 1


def rate_direction(
    analyses: Sequence[AnalystReport], relevance: RelevanceScore, profile: CompanyProfile | None = None
) -> int:
    """+1 — ставка выше/дольше, −1 — ставка ниже, 0 — неясно.

    Источник — аналитики: направление фактора key_rate. Нет отчёта по каналу — направление выводится из знака
    вклада скоринга и чувствительности клиента к ставке (знак вклада = направление × чувствительность).
    """
    m = channel_mean(analyses, "key_rate")
    if m is not None and abs(m) >= MIN_STRENGTH:
        return 1 if m > 0 else -1
    votes: list[int] = []
    if profile is not None:
        sens = next((e.sensitivity for e in profile.exposures if e.channel == "key_rate"), 0.0)
        if sens:
            for c in relevance.contributions:
                if c.factor == "key_rate" and c.sign:
                    votes.append(1 if c.sign * sens > 0 else -1)
    s = sum(votes)
    return (s > 0) - (s < 0)
