"""Профиль компании v0: поля CSV кейса → CompanyProfile, ранг выручки в классе ОКВЭД, полнота.

Чистые функции без БД: на вход строки CSV (колонки как в df_Uid_data_hack.csv), на выход профили.
Знаки чувствительности (exposures) появятся в профиле v1.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from newsai.schemas import CompanyProfile

# Колонка CSV → ключ products в CompanyProfile
PRODUCT_COLUMNS = {
    "CL_PRODUCT_RKO_MARK": "rko",
    "CL_PRODUCT_GAR_MARK": "guarantees",
    "CL_PRODUCT_LOA_MARK": "loans",
    "CL_PRODUCT_LEA_MARK": "leasing",
    "CL_PRODUCT_DEP_MARK": "deposits",
    "CL_PRODUCT_ZPP_MARK": "payroll",
    "FACTORING_MARK": "factoring",
}

# Поля, по которым считается полнота профиля
KEY_FIELDS = (
    "okved_code",
    "region",
    "revenue",
    "group_id",
    "company_age",
    "workers_fns",
    "eco_stage",
    "fts_flag",
)


def _str(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _num(v: str | None) -> float | None:
    # в настоящем CSV десятичная запятая: "152842581652,27"
    v = _str(v)
    if v is None:
        return None
    try:
        return float(v.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _flag(v: str | None) -> bool | None:
    # флаги записаны как 0/1 в синтетике и как 0.0/1.0 в настоящем CSV
    n = _num(v)
    return None if n is None else n == 1


def sector_of(okved_code: str | None) -> str | None:
    """Класс ОКВЭД — первые две цифры: «41.2» и «41.20» попадают в один сектор."""
    digits = re.sub(r"[^0-9]", "", okved_code or "")
    return digits[:2] if len(digits) >= 2 else None


def profile_from_row(row: Mapping[str, str | None]) -> CompanyProfile:
    r = {k.strip().upper(): v for k, v in row.items() if k}
    okved = _str(r.get("MAINOKVED_CODE"))
    products = {key: f for col, key in PRODUCT_COLUMNS.items() if (f := _flag(r.get(col))) is not None}
    profile = CompanyProfile(
        company_id=_str(r.get("SYNTHETIC_U")) or "",
        group_id=_str(r.get("GROUP_ID")),
        org_type=_str(r.get("ORG_TYPE")),
        okfs_name=_str(r.get("OKFS_NAME")),
        okved_code=okved,
        okved_name=_str(r.get("MAINOKVED_NAME")),
        sector=sector_of(okved),
        company_age=_str(r.get("COMPANY_AGE_YEARS_GRADATION")),
        eco_stage=_str(r.get("ECO_MARK_LABEL")),
        workers_fns=_str(r.get("AMOUNT_WORKERS_FNS_GRADATION")),
        workers_spark=_str(r.get("AMOUNT_WORKERS_SPARK_GRADATION")),
        client_type=_str(r.get("CL_TYPE")),
        negative_mark=_flag(r.get("NEGATIVEMARK")),
        revenue=_num(r.get("REVENUE_COMPANY")),
        city=_str(r.get("CITY")),
        region=_str(r.get("REGION")),
        fts_flag=_flag(r.get("FTS_FLAG")),
        fts_country=_str(r.get("FTS_COUNTRY_NAME")),
        products=products,
    )
    profile.completeness = completeness(profile)
    return profile


def completeness(p: CompanyProfile) -> float:
    filled = sum(getattr(p, f) is not None for f in KEY_FIELDS)
    filled += len(p.products) / len(PRODUCT_COLUMNS)  # все продуктовые флаги считаются за одно поле
    return round(filled / (len(KEY_FIELDS) + 1), 2)


def rank_revenue(profiles: list[CompanyProfile]) -> None:
    """Место по выручке среди компаний того же класса ОКВЭД (1 — крупнейшая). Это оценка по датасету,
    а не по всей отрасли: в датасете не все компании отрасли."""
    by_sector: dict[str, list[CompanyProfile]] = defaultdict(list)
    for p in profiles:
        if p.sector and p.revenue is not None:
            by_sector[p.sector].append(p)
    for peers in by_sector.values():
        peers.sort(key=lambda p: p.revenue or 0, reverse=True)
        for i, p in enumerate(peers, 1):
            p.revenue_rank_in_okved = i
            p.revenue_peers_in_okved = len(peers)


def build_profiles(rows: Iterable[Mapping[str, str | None]]) -> dict[str, CompanyProfile]:
    """Профили всех компаний. id, который встречается у разных компаний (U_428697), неоднозначен —
    такие компании исключаются, как в представлении companies."""
    profiles = [profile_from_row(r) for r in rows]
    counts = Counter(p.company_id for p in profiles)
    unique = [p for p in profiles if p.company_id and counts[p.company_id] == 1]
    rank_revenue(unique)
    return {p.company_id: p for p in unique}
