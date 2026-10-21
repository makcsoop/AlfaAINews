"""Архетипы клиентов из config/archetypes.yaml и их привязка к компаниям CSV.

`python -m newsai.profile.archetypes [папка с CSV]` — печатает, какие компании выбраны.
"""

import sys
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from newsai.profile.build import PRODUCT_COLUMNS
from newsai.schemas import CompanyProfile
from newsai.schemas.common import UserRole

ARCHETYPES_CONFIG = Path("config/archetypes.yaml")
PRODUCT_KEYS = set(PRODUCT_COLUMNS.values())


class Match(BaseModel):
    model_config = ConfigDict(extra="forbid")

    okved_prefixes: list[str]
    fallback_okved_prefixes: list[str] = Field(default_factory=list)
    require: dict[str, bool] = Field(default_factory=dict)
    prefer: dict[str, bool] = Field(default_factory=dict)


class Archetype(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    default_role: UserRole
    demo: str
    match: Match
    fixture_company: str | None = None
    company_id: str | None = None


class ArchetypesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_revenue: float
    archetypes: list[Archetype]


def load_archetypes(path: Path = ARCHETYPES_CONFIG) -> ArchetypesConfig:
    return ArchetypesConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _value(p: CompanyProfile, key: str) -> bool | None:
    return p.products.get(key) if key in PRODUCT_KEYS else getattr(p, key)


def _okved_ok(p: CompanyProfile, prefixes: list[str]) -> bool:
    return any((p.okved_code or "").startswith(x) for x in prefixes)


def candidates(p_all: list[CompanyProfile], m: Match) -> list[CompanyProfile]:
    for prefixes in (m.okved_prefixes, m.fallback_okved_prefixes):
        found = [
            p
            for p in p_all
            if _okved_ok(p, prefixes) and all(_value(p, k) is v for k, v in m.require.items())
        ]
        if found:
            return found
    return []


def resolve(
    profiles: dict[str, CompanyProfile], cfg: ArchetypesConfig | None = None
) -> dict[str, CompanyProfile | None]:
    """archetype_id → компания. Закреплённый company_id важнее правила; одна компания — один архетип."""
    cfg = cfg or load_archetypes()
    used = {a.company_id for a in cfg.archetypes if a.company_id}
    out: dict[str, CompanyProfile | None] = {}
    for a in cfg.archetypes:
        if a.company_id:
            out[a.id] = profiles.get(a.company_id)
            continue
        pool = [p for p in profiles.values() if p.company_id not in used]

        def key(p: CompanyProfile, a: Archetype = a) -> tuple:
            prefer_hits = sum(_value(p, k) is v for k, v in a.match.prefer.items())
            return (
                (p.revenue or 0) >= cfg.min_revenue,
                prefer_hits,
                p.negative_mark is not True,
                p.revenue or 0,
                p.company_id,  # детерминированный выбор при равенстве
            )

        found = max(candidates(pool, a.match), key=key, default=None)
        out[a.id] = found
        if found:
            used.add(found.company_id)
    return out


def main() -> None:
    from newsai.profile.load import load_profiles

    profiles = load_profiles(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    cfg = load_archetypes()
    picked = resolve(profiles, cfg)
    for a in cfg.archetypes:
        p = picked[a.id]
        if p is None:
            print(f"{a.id:20} — нет подходящей компании")
            continue
        rev = f"{(p.revenue or 0) / 1e9:.1f} млрд"
        prods = ",".join(k for k, v in p.products.items() if v)
        print(f"{a.id:20} {p.company_id:12} ОКВЭД {p.okved_code:6} {rev:>12}  ВЭД={p.fts_flag} [{prods}]")


if __name__ == "__main__":
    main()
