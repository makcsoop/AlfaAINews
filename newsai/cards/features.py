"""Признаки компании для промпта и проверки client_features; акценты ролей.

В модель уходят признаки и доли, не суммы платежей и не данные физлиц (инвариант 8).
"""

import re

from newsai.schemas import CompanyProfile, CounterpartyGraph, RelevanceScore
from newsai.schemas.common import UserRole

CHANNEL_LABELS: dict[str, str] = {
    "rate_cost": "стоимость заёмных средств",
    "deposit_yield": "доходность размещения ликвидности",
    "fx_rub": "курс рубля",
    "oil_gas": "нефть и газ",
    "metals": "цены на металлы",
    "agro": "агросырьё",
    "gold": "золото",
    "consumer_demand": "потребительский спрос",
    "labor_market": "рынок труда и зарплаты",
    "construction": "стройка и ипотека",
    "tariffs_sanctions": "пошлины и санкции",
    "logistics": "логистика и платежи ВЭД",
    "taxes_regulation": "налоги и регулирование",
    "public_procurement": "госзаказ и господдержка",
    "inflation": "инфляция и издержки",
}

ROLE_FOCUS: dict[UserRole, str] = {
    "cfo": "долг и ставка: стоимость кредитов и лизинга, рефинансирование, процентные расходы, "
    "оборотный капитал",
    "treasurer": "курс и ликвидность: размещение свободных остатков, валютная выручка и "
    "платежи, сроки и доходность",
    "chief_accountant": "налоги и отчётность: налоговая нагрузка, сроки и формы отчётности, валютный учёт",
    "ceo": "спрос, контрагенты, стратегия: покупатели и поставщики, конкурентная позиция, "
    "инвестиционные решения",
    "owner": "спрос, контрагенты, стратегия: покупатели и поставщики, конкурентная позиция, "
    "инвестиционные решения",
    "other": "общее влияние на бизнес компании без привязки к функции",
}

_PRODUCT_NAMES = {
    "loans": "кредиты",
    "leasing": "лизинг",
    "factoring": "факторинг",
    "deposits": "депозиты",
    "guarantees": "гарантии",
    "rko": "РКО",
    "payroll": "зарплатный проект",
}

# признак, говорящий только об отрасли: в «≥ 2 признаков кроме отрасли» не считается
_SECTOR_ONLY = re.compile(r"^(отрасль\b.*|MAINOKVED_CODE=.*|ОКВЭД\s*[\d.]+)$", re.IGNORECASE)


def is_sector_feature(feature: str) -> bool:
    return bool(_SECTOR_ONLY.match(feature.strip()))


def channel_label(channel: str) -> str:
    return CHANNEL_LABELS.get(channel, channel)


def company_features(profile: CompanyProfile, graph: CounterpartyGraph | None = None) -> list[str]:
    """Читаемые признаки компании. Без сумм и числа платежей, без id контрагентов."""
    out: list[str] = []
    if profile.okved_code:
        name = f"{profile.okved_name} " if profile.okved_name else ""
        out.append(f"отрасль: {name}(ОКВЭД {profile.okved_code})")
    used = [_PRODUCT_NAMES[k] for k, v in profile.products.items() if v and k in _PRODUCT_NAMES]
    if used:
        out.append("продукты в банке: " + ", ".join(used))
    if profile.fts_flag:
        out.append(f"ВЭД: {profile.fts_country}" if profile.fts_country else "ВЭД: есть")
    for e in profile.exposures:
        out.extend(e.drivers)
    if profile.revenue_rank_in_okved and profile.revenue_peers_in_okved:
        out.append(
            f"место по выручке в отрасли: {profile.revenue_rank_in_okved} из {profile.revenue_peers_in_okved}"
        )
    if graph is not None:
        buyers = sorted((c for c in graph.buyers if not c.via_person and c.sector), key=lambda c: -c.share)
        suppliers = sorted(
            (c for c in graph.suppliers if not c.via_person and c.sector), key=lambda c: -c.share
        )
        for c in buyers[:2]:
            out.append(f"payments: {round(c.share * 100)}% входящих от ОКВЭД {c.sector}")
        for c in suppliers[:2]:
            out.append(f"payments: {round(c.share * 100)}% исходящих → ОКВЭД {c.sector}")
    return _unique(out)


def allowed_features(
    profile: CompanyProfile, graph: CounterpartyGraph | None, relevance: RelevanceScore
) -> list[str]:
    feats = company_features(profile, graph)
    for c in relevance.contributions:
        feats.extend(c.client_features)
    return _unique(feats)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    res = []
    for s in items:
        key = s.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            res.append(s.strip())
    return res


def canonical(feature: str, allowed: list[str]) -> str | None:
    key = feature.strip().casefold()
    return next((a for a in allowed if a.casefold() == key), None)
