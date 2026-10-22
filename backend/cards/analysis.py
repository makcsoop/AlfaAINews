"""Analysis из отчётов аналитиков: направление фактора и согласие аналитиков. Без знака для клиента."""

from collections.abc import Sequence

from backend.cards.signals import analysts_disagree, channel_impacts, signed
from backend.schemas import Analysis, AnalystReport
from backend.schemas.common import ChannelImpact

_FLAT = 0.1  # средняя сила слабее — направление «без изменений»


def build_analysis(
    story_id: str, sector: str, version: int, reports: Sequence[AnalystReport], *, on_review: bool = False,
    trace_id: str | None = None,
) -> Analysis:
    channels = list(dict.fromkeys(c.channel for r in reports for c in r.channels))
    drivers: list[ChannelImpact] = []
    agreement: dict[str, str] = {}
    for ch in channels:
        imp = channel_impacts(reports, ch)
        mean = sum(signed(c) for c in imp) / len(imp)
        direction = "flat" if abs(mean) < _FLAT else "up" if mean > 0 else "down"
        top = max(imp, key=lambda c: c.strength)
        drivers.append(
            ChannelImpact(
                channel=ch,
                direction=direction,
                strength=min(1.0, abs(mean)) if direction != "flat" else 0.0,
                horizon=top.horizon,
                rationale=top.rationale,
            )
        )
        agreement[ch] = (
            "disagree" if analysts_disagree(reports, ch) else "agree" if len(imp) > 1 else "single"
        )
    return Analysis(
        story_id=story_id,
        sector=sector,
        version=version,
        drivers=drivers,
        reports=list(reports),
        agreement=agreement,  # type: ignore[arg-type]
        status="on_review" if on_review else "published",
        trace_id=trace_id,
    )
