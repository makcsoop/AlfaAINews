"""«Предложение банка»: только по продуктовому триггеру, кодом, с пометкой рекламы.

Модель предложений не пишет. Триггер = продукт клиента + направление фактора + вклад канала в значимость.
Не больше одного предложения на карточку: берётся триггер с наибольшим вкладом.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from newsai.cards.signals import rate_direction
from newsai.schemas import AnalystReport, CompanyProfile, RelevanceScore


@dataclass(frozen=True)
class BankOffer:
    trigger_id: str
    text: str  # текст предложения (реклама)
    factor: str  # канал, по которому сработал триггер
    client_features: tuple[str, ...]
    weight: float


@dataclass(frozen=True)
class _Trigger:
    id: str
    text: str
    factor: str
    # (профиль, направление ставки, вклад канала) -> сработал ли
    when: Callable[[CompanyProfile, int, float], bool]


def _has(profile: CompanyProfile, *products: str) -> bool:
    return any(profile.products.get(p) for p in products)


# Таблица триггеров. Порядок — приоритет при равных вкладах.
TRIGGERS: tuple[_Trigger, ...] = (
    _Trigger(
        id="fix_rate",
        text="Фиксация ставки по новым траншам",
        factor="rate_cost",
        when=lambda p, rate, w: _has(p, "loans", "leasing") and rate > 0,
    ),
    _Trigger(
        id="long_deposit",
        text="Депозит на более длинный срок по текущей ставке",
        factor="deposit_yield",
        when=lambda p, rate, w: _has(p, "deposits") and rate > 0,
    ),
    _Trigger(
        id="fx_hedge",
        text="Валютный контроль и хеджирование курсового риска по ВЭД",
        factor="fx_rub",
        when=lambda p, rate, w: bool(p.fts_flag) and w > 0,
    ),
)

NO_OFFER_STATUSES = {"rumor", "contradictory"}


def find_offer(
    *,
    profile: CompanyProfile,
    relevance: RelevanceScore,
    analyses: Sequence[AnalystReport],
    story_status: str = "confirmed",
) -> BankOffer | None:
    if story_status in NO_OFFER_STATUSES:
        return None
    rate = rate_direction(analyses, relevance)
    best: BankOffer | None = None
    for t in TRIGGERS:
        contrib = [c for c in relevance.contributions if c.factor == t.factor]
        weight = max((c.value for c in contrib), default=0.0)
        if not t.when(profile, rate, weight):
            continue
        feats = tuple(dict.fromkeys(f for c in contrib for f in c.client_features))
        if not feats:
            feats = tuple(_product_features(profile, t))
        offer = BankOffer(t.id, t.text, t.factor, feats, weight)
        if best is None or offer.weight > best.weight:
            best = offer
    return best


def _product_features(profile: CompanyProfile, trigger: _Trigger) -> list[str]:
    if trigger.id == "fx_hedge":
        return [f"ВЭД: {profile.fts_country}" if profile.fts_country else "ВЭД: есть"]
    names = {"loans": "кредиты", "leasing": "лизинг", "deposits": "депозиты"}
    used = [n for k, n in names.items() if profile.products.get(k)]
    return ["продукты в банке: " + ", ".join(used)] if used else []
