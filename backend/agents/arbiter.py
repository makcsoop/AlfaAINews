"""Арбитр кодом: спор бизнес-аналитика с финансовым и сверка согласованного направления с позицией ЦМИ.

Спор — противоположное направление фактора по общему каналу при силе не ниже средней у обоих
(«плоско» против сильного — не спор). Расхождение с ЦМИ не блокирует выдачу, а попадает в причины уверенности.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from backend.agents.dossier import DossierItem
from backend.cards.signals import MIN_STRENGTH, channel_mean, signed
from backend.schemas import AnalystReport

MEDIUM = 0.6  # вес силы «средняя» (analysts.STRENGTH_WEIGHT)
Direction = Literal["up", "down", "flat"]


class Dispute(BaseModel):
    channel: str
    business: float
    finance: float
    reason: str


class BankCheck(BaseModel):
    channel: str
    analysts_direction: Direction
    bank_direction: Direction
    agree: bool


class ArbiterResult(BaseModel):
    on_review: bool = False
    disputes: list[Dispute] = Field(default_factory=list)
    bank_check: list[BankCheck] = Field(default_factory=list)
    confidence_reasons: list[str] = Field(default_factory=list)


def _dir(x: float, threshold: float = 0.0) -> Direction:
    if x > threshold:
        return "up"
    if x < -threshold:
        return "down"
    return "flat"


def _bank_direction(channel: str, positions: Sequence[DossierItem]) -> Direction | None:
    """Направление ФАКТОРА по позициям ЦМИ: direction позиции — куда банк ждёт движения фактора."""
    total, seen = 0.0, False
    for p in positions:
        direction = p.data.get("direction")
        if (
            p.data.get("channel") != channel
            or not isinstance(direction, (int, float))
            or isinstance(direction, bool)
            or p.data.get("superseded_by")  # позицию заместил сюжет: она больше не ожидание
        ):
            continue
        seen = True
        total += float(direction)
    return _dir(total, 1e-9) if seen else None


def arbitrate(
    analyst_reports: Sequence[AnalystReport], bank_positions_in_dossier: Sequence[DossierItem]
) -> ArbiterResult:
    by = {r.analyst: r for r in analyst_reports}
    res = ArbiterResult()

    b, f = by.get("business"), by.get("finance")
    if b is not None and f is not None:
        bi = {c.channel: signed(c) for c in b.channels}
        fi = {c.channel: signed(c) for c in f.channels}
        for ch in bi:
            if ch not in fi:
                continue
            x, y = bi[ch], fi[ch]
            if x * y < 0 and min(abs(x), abs(y)) >= MEDIUM - 1e-9:
                res.disputes.append(
                    Dispute(
                        channel=ch,
                        business=x,
                        finance=y,
                        reason=f"по каналу {ch} направление фактора противоположно: "
                        f"бизнес {x:+.1f}, финансы {y:+.1f}",
                    )
                )
        res.on_review = bool(res.disputes)

    positions = [p for p in bank_positions_in_dossier if p.kind == "bank_position"]
    for ch in dict.fromkeys(str(p.data["channel"]) for p in positions if p.data.get("channel")):
        bank = _bank_direction(ch, positions)
        mean = channel_mean(analyst_reports, ch)
        if bank is None or mean is None:
            continue
        analysts = _dir(mean, MIN_STRENGTH)
        agree = not (analysts != "flat" and bank != "flat" and analysts != bank)
        res.bank_check.append(
            BankCheck(channel=ch, analysts_direction=analysts, bank_direction=bank, agree=agree)
        )
        if not agree:
            res.confidence_reasons.append(
                f"позиция ЦМИ по каналу {ch} ({bank}) расходится с аналитиками ({analysts})"
            )
    return res


__all__ = ["ArbiterResult", "BankCheck", "Dispute", "arbitrate"]
