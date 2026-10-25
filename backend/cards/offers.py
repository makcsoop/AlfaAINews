"""«Предложение банка»: только по продуктовому триггеру, кодом, с пометкой рекламы.

Модель предложений не пишет. Триггер = продукт клиента + направление фактора + вклад канала в значимость.
Не больше одного предложения на карточку: берётся триггер с наибольшим вкладом.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.cards.signals import rate_direction
from backend.schemas import AnalystReport, CompanyProfile, RelevanceScore


@dataclass(frozen=True)
class BankOffer:
    trigger_id: str
    text: str  # текст предложения (реклама): продукт, почему вам, следующий шаг
    factor: str  # канал, по которому сработал триггер
    client_features: tuple[str, ...]
    weight: float
    product: str = ""
    why: str = ""  # почему вам — из признаков клиента, без чисел и сумм
    next_step: str = ""


@dataclass(frozen=True)
class _Trigger:
    id: str
    product: str
    factor: str
    # (профиль, направление ставки, вклад канала) -> сработал ли
    when: Callable[[CompanyProfile, int, float], bool]
    why: Callable[[CompanyProfile], str]
    next_step: str


def _has(profile: CompanyProfile, *products: str) -> bool:
    return any(profile.products.get(p) for p in products)


_PRODUCT_NAMES = {
    "loans": "кредиты", "leasing": "лизинг", "deposits": "депозиты", "factoring": "факторинг",
    "guarantees": "гарантии",
}


def _products(profile: CompanyProfile, *keys: str) -> str:
    return ", ".join(_PRODUCT_NAMES[k] for k in keys if profile.products.get(k))


def _fea(profile: CompanyProfile) -> str:
    return f"ВЭД с контрагентами из страны «{profile.fts_country}»" if profile.fts_country else "есть ВЭД"


# Таблица триггеров. Порядок — приоритет при равных вкладах.
TRIGGERS: tuple[_Trigger, ...] = (
    _Trigger(
        id="fix_rate",
        product="Фиксация ставки по новым траншам",
        factor="key_rate",
        when=lambda p, rate, w: _has(p, "loans", "leasing") and rate > 0,
        why=lambda p: f"у компании в банке {_products(p, 'loans', 'leasing')}, "
        "а ставка остаётся высокой дольше",
        next_step="обсудить с менеджером условия фиксации до следующего заседания ЦБ",
    ),
    _Trigger(
        id="long_deposit",
        product="Депозит на более длинный срок по текущей ставке",
        factor="key_rate",
        when=lambda p, rate, w: _has(p, "deposits") and rate > 0,
        why=lambda p: "у компании в банке депозиты: текущую доходность можно закрепить до снижения ставки",
        next_step="запросить у менеджера ставки по длинным срокам и условия досрочного отзыва",
    ),
    _Trigger(
        id="fx_hedge",
        product="Валютный контроль и хеджирование курсового риска по ВЭД",
        factor="fx_rub",
        when=lambda p, rate, w: bool(p.fts_flag) and w > 0,
        why=lambda p: f"у компании {_fea(p)}, курс рубля меняет выручку или затраты в рублях",
        next_step="согласовать с менеджером инструмент хеджирования по ближайшим контрактам",
    ),
    _Trigger(
        id="receivables_protection",
        product="Факторинг и банковские гарантии по сделкам с покупателями",
        factor="counterparty_defaults",
        when=lambda p, rate, w: _has(p, "factoring", "guarantees") and w > 0,
        why=lambda p: f"у компании в банке {_products(p, 'factoring', 'guarantees')}, "
        "а неплатежи контрагентов бьют по выручке",
        next_step="обсудить с менеджером лимит по крупнейшим покупателям",
    ),
    _Trigger(
        id="fx_control_support",
        product="Сопровождение валютного контроля по внешнеторговым контрактам",
        factor="capital_controls",
        when=lambda p, rate, w: bool(p.fts_flag) and w > 0,
        why=lambda p: f"у компании {_fea(p)}, а требования валютного контроля меняются",
        next_step="сверить с менеджером документы по действующим контрактам",
    ),
)


def offer_text(product: str, why: str, next_step: str) -> str:
    return f"{product}. Почему вам: {why}. Следующий шаг: {next_step}."


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
    rate = rate_direction(analyses, relevance, profile)
    best: BankOffer | None = None
    for t in TRIGGERS:
        contrib = [c for c in relevance.contributions if c.factor == t.factor]
        weight = max((c.value for c in contrib), default=0.0)
        if not t.when(profile, rate, weight):
            continue
        feats = tuple(dict.fromkeys(f for c in contrib for f in c.client_features))
        if not feats:
            feats = tuple(_product_features(profile, t))
        why = t.why(profile)
        offer = BankOffer(
            t.id, offer_text(t.product, why, t.next_step), t.factor, feats, weight,
            product=t.product, why=why, next_step=t.next_step,
        )
        if best is None or offer.weight > best.weight:
            best = offer
    return best


def _product_features(profile: CompanyProfile, trigger: _Trigger) -> list[str]:
    if trigger.id in ("fx_hedge", "fx_control_support"):
        return [f"ВЭД: {profile.fts_country}" if profile.fts_country else "ВЭД: есть"]
    keys = {"fix_rate": ("loans", "leasing"), "long_deposit": ("deposits",)}.get(
        trigger.id, ("factoring", "guarantees")
    )
    used = [_PRODUCT_NAMES[k] for k in keys if profile.products.get(k)]
    return ["продукты в банке: " + ", ".join(used)] if used else []
