"""Уверенность «высокая / средняя / низкая» с причинами, без процентов: процент обещает калибровку,
которой пока нет. Баллы — по пяти признакам из плана: источники, согласие аналитиков,
доля проверенных утверждений, поддержка банка и рынка, прямая связь с клиентом.
Пороги — config/scoring.yaml (confidence).
"""

from collections.abc import Sequence

from backend.profile.taxonomy import channel_name
from backend.ranking.config import ScoringConfig, load_scoring
from backend.ranking.score import DIRECTION
from backend.schemas import (
    Analysis,
    BankPosition,
    Claim,
    CompanyProfile,
    MarketKnowledge,
    RelevanceScore,
    Source,
    Story,
)
from backend.schemas.common import Confidence


def _main_factor(relevance: RelevanceScore, channels: set[str]) -> str | None:
    best = max(
        (c for c in relevance.contributions if c.factor in channels), key=lambda c: c.value, default=None
    )
    return best.factor if best else None


def _sign(x: float | None) -> int:
    return 0 if not x else (1 if x > 0 else -1)


def confidence(
    *,
    story: Story,
    relevance: RelevanceScore,
    profile: CompanyProfile,
    sources: dict[str, Source] | None = None,
    analysis: Analysis | None = None,
    claims: Sequence[Claim] = (),
    bank_positions: Sequence[BankPosition] = (),
    market: Sequence[MarketKnowledge] = (),
    cfg: ScoringConfig | None = None,
) -> tuple[Confidence, list[str]]:
    cfg = cfg or load_scoring()
    c = cfg.confidence
    points = 0
    reasons: list[str] = []
    exposures = {e.channel: e for e in profile.exposures}
    factor = _main_factor(
        relevance, set(exposures) | {d.channel for d in (analysis.drivers if analysis else [])}
    )

    # 1. источники
    tiers = [sources[s].tier for s in story.source_ids if sources and s in sources]
    if 0 in tiers:
        points += 1
        reasons.append("первоисточник — регулятор или сама компания")
    elif story.corroboration >= c.corroboration_good:
        points += 1
        reasons.append(f"независимых источников: {story.corroboration}")
    else:
        reasons.append("пока один источник")

    # 2. согласие аналитиков по главному фактору
    direction = None
    if analysis is None:
        reasons.append("глубокого разбора ещё нет")
    else:
        driver = next((d for d in analysis.drivers if d.channel == factor), None)
        direction = DIRECTION[driver.direction] if driver else None
        agreement = analysis.agreement.get(factor or "")
        if agreement == "agree":
            points += 1
            reasons.append("аналитики согласны по направлению")
        elif agreement == "disagree":
            points -= 1
            reasons.append("аналитики расходятся по направлению — на проверке у эксперта")

    # 3. доля утверждений, прошедших проверку чисел и цитат
    checked = [cl for cl in claims if cl.verified]
    if checked:
        ok = sum(all(v is True or v == "yes" for v in cl.verified.values()) for cl in checked) / len(checked)
        if ok >= c.verified_good:
            points += 1
            reasons.append("числа и цитаты проверены")
        elif ok < c.verified_bad:
            points -= 1
            reasons.append(f"проверку прошли {round(ok * 100)}% утверждений")

    # 4. банк и рынок о том же факторе
    if factor and direction:
        bank = {_sign(p.direction) for p in bank_positions if p.channel == factor and p.direction}
        mk = {_sign(k.direction) for k in market if k.channel == factor and k.direction}
        if direction in bank or direction in mk:
            points += 1
            who = " и ".join(w for w, s in (("ЦМИ", bank), ("аналитики рынка", mk)) if direction in s)
            reasons.append(f"{who}: то же направление ({channel_name(factor).lower()})")
        if -direction in bank and direction not in bank:
            reasons.append("позиция ЦМИ расходится с разбором по направлению")

    # 5. связь с клиентом: продукт или отрасль, а не предположение
    e = exposures.get(factor or "")
    if e is not None and not e.is_assumption:
        points += 1
        reasons.append(f"прямая связь: {channel_name(e.channel).lower()}")
    else:
        reasons.append("связь с клиентом предположительная")

    level: Confidence = "high" if points >= c.high_from else "medium" if points >= c.medium_from else "low"
    if story.status in ("rumor", "contradictory"):
        level = "low"
        reasons.append("сюжет — слух или источники расходятся")
    if analysis is not None and analysis.status == "on_review" and level == "high":
        level = "medium"
    return level, reasons
