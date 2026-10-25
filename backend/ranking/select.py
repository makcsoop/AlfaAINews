"""Что отправить на глубокий разбор и как объяснить место в ленте.

Разбор дорогой и кешируется на пару «сюжет × отрасль», поэтому отбираются пары, а не клиенты:
приоритет пары — лучшая значимость среди её клиентов, при равенстве — у кого больше клиентов.
K считается от остатка дневного бюджета LLM (менеджер бюджета Р3: backend.llm budget.summary()).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.profile.taxonomy import channel_name
from backend.ranking.config import ScoringConfig, load_scoring
from backend.schemas import Contribution, RelevanceScore

ARROW = {1: "возможность", -1: "риск", 0: "касается вас"}


@dataclass(frozen=True)
class DeepPair:
    story_id: str
    sector: str
    priority: float
    clients: int


def deep_k(spent_rub: float, limit_rub: float, cfg: ScoringConfig | None = None) -> tuple[int, float]:
    """Сколько пар разбирать и с какого порога значимости: при 70% лимита — режим экономии, при 90% — стоп."""
    d = (cfg or load_scoring()).deep
    used = spent_rub / limit_rub if limit_rub > 0 else 1.0
    if used >= d.stop_at:
        return 0, 1.0
    k = int((limit_rub - spent_rub) * d.share_of_budget / d.cost_per_pair_rub)
    k = min(d.k_max, max(0, k))
    if used >= d.economy_at:
        return k // 2, d.economy_min_score
    return k, d.min_score


def select_deep(
    scores: Sequence[RelevanceScore],
    sector_of: Mapping[str, str | None],
    *,
    spent_rub: float,
    limit_rub: float,
    cfg: ScoringConfig | None = None,
) -> list[DeepPair]:
    """Топ-K пар «сюжет × отрасль». sector_of — company_id → сектор (2 знака ОКВЭД)."""
    k, threshold = deep_k(spent_rub, limit_rub, cfg)
    best: dict[tuple[str, str], float] = defaultdict(float)
    count: dict[tuple[str, str], int] = defaultdict(int)
    for r in scores:
        sector = sector_of.get(r.company_id)
        if not sector or r.score < threshold:
            continue
        key = (r.story_id, sector)
        best[key] = max(best[key], r.score)
        count[key] += 1
    pairs = [DeepPair(s, sec, round(best[(s, sec)], 3), count[(s, sec)]) for s, sec in best]
    pairs.sort(key=lambda p: (-p.priority, -p.clients, p.story_id, p.sector))
    return pairs[:k]


def mark_deep(
    scores: Sequence[RelevanceScore], pairs: Sequence[DeepPair], sector_of: Mapping[str, str | None]
):
    """Копии скоров с deep_analysis=True у клиентов отобранных пар."""
    chosen = {(p.story_id, p.sector) for p in pairs}
    return [
        r.model_copy(update={"deep_analysis": (r.story_id, sector_of.get(r.company_id)) in chosen})
        for r in scores
    ]


def factor_label(factor: str) -> str:
    kind, _, arg = factor.partition(":")
    if kind == "counterparty":
        return f"контрагенты из ОКВЭД {arg}"
    if kind == "linked_sector":
        return f"связанная отрасль, ОКВЭД {arg}"
    if kind == "direct":
        return {
            "company": "о вашей компании",
            "group": "о вашей группе",
            "counterparty": "о вашем контрагенте",
            "sector": "о вашей отрасли",
            "region": "о вашем регионе",
        }.get(arg, arg)
    if factor == "surprise":
        return "неожиданный исход"
    return channel_name(factor)


def top_contributions(r: RelevanceScore, n: int = 3) -> list[Contribution]:
    """Главные вклады; «общий фактор для всех компаний» — только если конкретных причин нет."""
    from backend.ranking.score import is_baseline

    ranked = sorted(r.contributions, key=lambda c: -c.value)
    specific = [c for c in ranked if not is_baseline(c)]
    return (specific or ranked)[:n]


def why_you_see(r: RelevanceScore, n: int = 3) -> list[str]:
    """Три главных вклада словами: «Ключевая ставка — риск: CL_PRODUCT_LOA_MARK=1, …»."""
    return [
        f"{factor_label(c.factor)} — {ARROW[c.sign]}: " + ", ".join(c.client_features)
        for c in top_contributions(r, n)
    ]
