"""Скоринг v0 пары «сюжет × клиент»: чистые функции без БД и LLM.

score = quality × freshness × (1 − exp(−raw / saturation)),
raw = Σ вкладов каналов + прямые совпадения + сюрприз (веса — config/scoring.yaml).

Вклад канала = сила фактора × |чувствительность клиента|, знак = направление фактора × знак
чувствительности (У17): аналитики и экстрактор дают только направление, «риск / возможность» ставится здесь.
Воздействие на факторы берётся по старшинству: глубокий разбор (Analysis) → факты экстрактора → приор
типа события.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from backend.profile.exposures import BASELINE_DRIVER
from backend.profile.taxonomy import ChannelsMatrix, load_event_types, load_matrix
from backend.ranking.config import ScoringConfig, load_scoring, role_weight
from backend.schemas import (
    Analysis,
    CompanyProfile,
    Contribution,
    CounterpartyGraph,
    EventFacts,
    RelevanceScore,
    Source,
    Story,
)
from backend.schemas.common import ChannelImpact, Polarity, UserRole
from backend.schemas.insight import Surprise

DIRECTION = {"up": 1, "down": -1, "flat": 0}
_RATE_QUOTE = re.compile(r"ставк|годовых", re.IGNORECASE)  # «ключевую ставку … 14%», «на уровне 14% годовых»


@dataclass(frozen=True)
class Driver:
    """Как сюжет двигает фактор: направление ±1, сила 0…1 и откуда это известно."""

    channel: str
    direction: int
    strength: float
    origin: str  # analysis | facts | prior | level


def story_drivers(
    story: Story, facts: EventFacts | None = None, analysis: Analysis | None = None
) -> list[Driver]:
    def from_impacts(impacts: Sequence[ChannelImpact], origin: str) -> list[Driver]:
        return [
            Driver(c.channel, DIRECTION[c.direction], c.strength, origin)
            for c in impacts
            if DIRECTION[c.direction] and c.strength > 0
        ]

    if analysis and analysis.drivers:
        return from_impacts(analysis.drivers, "analysis")
    if facts and facts.channels:
        return from_impacts(facts.channels, "facts")
    event_type = (facts.event_type if facts else None) or story.event_type
    et = load_event_types().get(event_type or "")
    if not et:
        return []
    return [Driver(ch, 1 if v > 0 else -1, abs(v), "prior") for ch, v in et.prior.items() if v]


def rate_level_driver(
    drivers: Sequence[Driver], facts: EventFacts | None, event_type: str | None, cfg: ScoringConfig
) -> Driver | None:
    """Пауза ЦБ: сдвига ставки нет (key_rate flat не даёт драйвера), но её уровень из фактов важен клиенту.
    Ставка выше нейтральной — фактор «ставка ↑» с силой по высоте; знак для клиента ставит экспозиция."""
    rl = cfg.rate_level
    if rl is None or facts is None or event_type not in rl.types:
        return None
    if any(d.channel == "key_rate" for d in drivers):
        return None
    rates = [
        n.value for n in facts.numbers if n.unit == "%" and 0 < n.value < 40 and _RATE_QUOTE.search(n.quote)
    ]
    rate = rates[0] if rates else None
    if rate is None:
        return None
    level = max(0.0, min(1.0, (rate - rl.neutral) / rl.full_at))
    strength = rl.strength_by_type.get(event_type, rl.strength)
    return Driver("key_rate", 1, round(level * strength, 3), "level") if level > 0 else None


def transmit(drivers: Sequence[Driver], cfg: ScoringConfig) -> list[Driver]:
    """Производные факторы (config transmission): Brent ↑ двигает и «топливо ↑» — у оптовика топливом и
    перевозчика в профиле топливо, а не нефть. Явный фактор сюжета производный не перебивает."""
    out = list(drivers)
    have = {d.channel for d in drivers}
    for d in drivers:
        for target, share in cfg.transmission.get(d.channel, {}).items():
            if target not in have:
                out.append(Driver(target, d.direction, round(d.strength * share, 3), d.origin))
                have.add(target)
    return out


def quality(
    story: Story, facts: EventFacts | None, sources: Mapping[str, Source], cfg: ScoringConfig
) -> float:
    """Качество сюжета: Р1 считает story.quality при склейке; если нет — по уровню источников и статусу."""
    q = cfg.quality
    if story.quality is not None:
        base = story.quality
    else:
        # уровень источника и его ручной вес достоверности из sources.yaml — поровну
        tiers = [(q.tier_weight[s.tier] + s.weight) / 2 for s in _known(story, sources)]
        base = max(tiers, default=q.default)
        base += min(q.corroboration_cap, q.corroboration_step * max(0, story.corroboration - 1))
        base *= q.status.get(story.status, 1.0)
    if facts is not None:
        base *= q.rumor if facts.is_rumor else 1.0
        base *= q.intent if facts.is_intent else 1.0
        base *= q.forecast if facts.status == "forecast" else 1.0
    return round(max(0.0, min(1.0, base)), 3)


def _known(story: Story, sources: Mapping[str, Source]) -> list[Source]:
    return [sources[s] for s in story.source_ids if s in sources]


def freshness(story: Story, now: datetime, cfg: ScoringConfig) -> float:
    age_h = max(0.0, (now - story.last_seen).total_seconds() / 3600)
    f = 0.5 ** (age_h / cfg.freshness.half_life_hours)
    return round(max(cfg.freshness.floor, f), 3)


def channel_contributions(
    drivers: Sequence[Driver],
    profile: CompanyProfile,
    cfg: ScoringConfig,
    scope: float = 1.0,
    role: UserRole | None = None,
) -> list[Contribution]:
    """scope < 1 — событие про другие отрасли (см. sector_scope); role — акценты роли из scoring.yaml."""
    exposures = {e.channel: e for e in profile.exposures}
    out = []
    for d in drivers:
        e = exposures.get(d.channel)
        if e is None or e.sensitivity == 0:
            continue
        sign = d.direction * (1 if e.sensitivity > 0 else -1)
        value = d.strength * abs(e.sensitivity) * cfg.weights.channels * scope
        value *= role_weight(cfg, role, d.channel)
        out.append(
            Contribution(factor=d.channel, value=round(value, 3), client_features=e.drivers, sign=sign)
        )
    return out


def sector_scope(
    profile: CompanyProfile,
    facts: EventFacts | None,
    graph: CounterpartyGraph | None,
    cfg: ScoringConfig,
    matrix: ChannelsMatrix,
) -> float:
    """1 — событие общее (ставка, курс) или про отрасль клиента, его соседей или контрагентов;
    иначе off_sector: пошлина на зерно двигает «ограничения ВЭД», но металлурга почти не касается."""
    sectors = set(facts.sectors_okved) if facts else set()
    if not sectors or profile.sector in sectors:
        return 1.0
    related = set(matrix.sector_links.get(profile.sector or "", {}))
    if graph is not None:
        min_share = cfg.profile.counterparty_min_share
        for shares in (graph.sector_shares_buyers, graph.sector_shares_suppliers):
            related |= {s for s, share in shares.items() if share >= min_share}
    return 1.0 if sectors & related else cfg.off_sector


def direct_contributions(
    profile: CompanyProfile,
    facts: EventFacts | None,
    story: Story,
    graph: CounterpartyGraph | None,
    cfg: ScoringConfig,
    matrix: ChannelsMatrix,
) -> list[Contribution]:
    """Прямые совпадения: событие называет компанию, её группу, контрагента, отрасль или регион.
    Знак неизвестен до разбора — sign = 0."""
    w, k = cfg.direct, cfg.weights.direct
    entity_ids = set(story.entities)
    sectors: set[str] = set()
    geography: set[str] = set()
    if facts is not None:
        entity_ids |= {p.entity_id for p in facts.participants if p.entity_id} | set(facts.entities)
        sectors = set(facts.sectors_okved)
        geography = {g.casefold() for g in facts.geography}
    out: list[Contribution] = []

    def add(factor: str, value: float, features: list[str]) -> None:
        out.append(Contribution(factor=factor, value=round(value * k, 3), client_features=features))

    if profile.company_id in entity_ids:
        add("direct:company", w.company, ["компания названа в событии"])
    elif profile.group_id and (
        profile.group_id in entity_ids or (graph and entity_ids & set(graph.group_members))
    ):
        add("direct:group", w.group, [f"GROUP_ID={profile.group_id}: компания группы названа в событии"])
    if graph is not None:
        named = [c for c in (*graph.buyers, *graph.suppliers) if c.company_id in entity_ids]
        if named:
            top = max(named, key=lambda c: c.share)
            side = "покупатель" if top.direction == "buyer" else "поставщик"
            add(
                "direct:counterparty",
                w.counterparty * max(top.share, 0.3),
                [f"ключевой {side} ({round(top.share * 100)}% платежей) назван в событии"],
            )
        for shares, phrase in (
            (graph.sector_shares_buyers, "входящих от ОКВЭД"),
            (graph.sector_shares_suppliers, "исходящих → ОКВЭД"),
        ):
            for sector, share in shares.items():
                if (
                    sector in sectors
                    and sector != profile.sector
                    and share >= cfg.profile.counterparty_min_share
                ):
                    add(
                        f"counterparty:{sector}",
                        w.counterparty_sector * share,
                        [f"payments: {round(share * 100)}% {phrase} {sector}"],
                    )
    if profile.sector and profile.sector in sectors:
        add("direct:sector", w.sector, [f"MAINOKVED_CODE={profile.okved_code}"])
    linked = matrix.sector_links.get(profile.sector or "", {})
    for sector, weight in linked.items():
        if sector in sectors and not any(c.factor == f"counterparty:{sector}" for c in out):
            add(
                f"linked_sector:{sector}",
                w.linked_sector * weight,
                [f"ОКВЭД {sector}: типичные заказчики или поставщики вашей отрасли"],
            )
    if profile.region and profile.region.casefold() in geography:
        add("direct:region", w.region, [f"регион: {profile.region}"])
    return out


def surprise_contribution(s: Surprise | None, cfg: ScoringConfig) -> Contribution | None:
    """Сюрприз — отклонение факта от ожиданий рынка или банка; берётся большее."""
    if s is None:
        return None
    full = cfg.surprise.full_at.get(s.unit or "")
    best, ref = 0.0, None
    for expected, r in ((s.market_expected, s.market_ref), (s.bank_expected, s.bank_ref)):
        if expected is None:
            continue
        scale = full if full else abs(expected) * cfg.surprise.default_full_at or 1.0
        level = min(1.0, abs(s.actual - expected) / scale)
        if level > best:
            best, ref = level, r
    if best == 0:
        return None
    return Contribution(
        factor="surprise",
        value=round(best * cfg.weights.surprise, 3),
        client_features=[f"сюрприз к ожиданиям: {s.metric}" + (f" ({ref})" if ref else "")],
    )


def is_baseline(c: Contribution) -> bool:
    """Вклад только «общего фактора для всех компаний» (налоги и т. п.) — не про клиента лично."""
    return bool(c.client_features) and all(f == BASELINE_DRIVER for f in c.client_features)


def _damp_weak_reasons(
    contribs: list[Contribution], cfg: ScoringConfig, role: UserRole | None = None
) -> list[Contribution]:
    """Регион без канала, сюрприза или другого прямого совпадения — direct.region_alone (0: не повод для
    карточки); общий фактор без конкретных причин — baseline_alone. Общий фактор в фокусе роли (налоги для
    главбуха) — уже личная причина: его не ослабляем."""

    def personal(c: Contribution) -> bool:
        return not is_baseline(c) or role_weight(cfg, role, c.factor) > 1.0

    meaningful = [c for c in contribs if c.value >= cfg.min_contribution]
    specific = [c for c in meaningful if c.factor != "direct:region" and personal(c)]
    if specific:
        return contribs
    out = []
    for c in contribs:
        k = 1.0
        if c.factor == "direct:region":
            k = cfg.direct.region_alone
        elif not personal(c):
            k = cfg.baseline_alone
        out.append(c if k == 1.0 else c.model_copy(update={"value": round(c.value * k, 3)}))
    return out


def polarity_of(contribs: Sequence[Contribution], cfg: ScoringConfig) -> Polarity:
    risk = sum(c.value for c in contribs if c.sign < 0)
    opp = sum(c.value for c in contribs if c.sign > 0)
    total = risk + opp
    if total < cfg.polarity.neutral_below:
        return "neutral"
    if risk >= cfg.polarity.dominance * total:
        return "risk"
    if opp >= cfg.polarity.dominance * total:
        return "opportunity"
    return "mixed"


def score_pair(
    story: Story,
    profile: CompanyProfile,
    *,
    now: datetime,
    facts: EventFacts | None = None,
    graph: CounterpartyGraph | None = None,
    sources: Mapping[str, Source] | None = None,
    analysis: Analysis | None = None,
    surprise: Surprise | None = None,
    role: UserRole | None = None,
    cfg: ScoringConfig | None = None,
) -> RelevanceScore:
    """Значимость сюжета для клиента со вкладами — основа «почему вы это видите».
    profile должен быть v1 (с exposures, см. backend.profile.exposures.with_exposures)."""
    cfg = cfg or load_scoring()
    matrix = load_matrix()
    drivers = story_drivers(story, facts, analysis)
    scope = sector_scope(profile, facts, graph, cfg, matrix)
    event_type = (facts.event_type if facts else None) or story.event_type
    if (level := rate_level_driver(drivers, facts, event_type, cfg)) is not None:
        drivers.append(level)
    drivers = transmit(drivers, cfg)
    only_prior = bool(drivers) and all(d.origin == "prior" for d in drivers)
    if only_prior and event_type in cfg.prior_unscoped_types and not (facts and facts.sectors_okved):
        scope *= cfg.prior_unscoped
    contribs = channel_contributions(drivers, profile, cfg, scope, role)
    contribs += direct_contributions(profile, facts, story, graph, cfg, matrix)
    if (s := surprise_contribution(surprise, cfg)) is not None:
        contribs.append(s)
    contribs = _damp_weak_reasons(contribs, cfg, role)
    contribs = sorted((c for c in contribs if c.value >= cfg.min_contribution), key=lambda c: -c.value)
    raw = sum(c.value for c in contribs)
    q = quality(story, facts, sources or {}, cfg)
    f = freshness(story, now, cfg)
    score = q * f * (1 - math.exp(-raw / cfg.saturation))
    return RelevanceScore(
        story_id=story.id,
        company_id=profile.company_id,
        role=role,
        score=round(min(1.0, score), 3),
        quality=q,
        freshness=f,
        contributions=contribs,
        polarity=polarity_of(contribs, cfg),
    )


def rank(scores: Sequence[RelevanceScore]) -> list[RelevanceScore]:
    """Лента клиента: по убыванию значимости, при равенстве — свежее выше."""
    return sorted(scores, key=lambda r: (-r.score, -(r.freshness or 0)))
