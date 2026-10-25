"""Витрина демо-входа: список компаний из config/demo_companies.yaml.

Наружу (GET /demo/companies) уходят только витринные поля — без сумм платежей, контрагентов,
связей через физлиц и точной выручки: схема `ShowcaseCompany` запрещает лишние поля.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import UserRole

SHOWCASE_CONFIG = Path("config/demo_companies.yaml")


class ShowcaseProducts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loans: bool = False
    leasing: bool = False
    deposits: bool = False
    guarantees: bool = False
    factoring: bool = False
    fea: bool = Field(default=False, description="ВЭД: внешнеторговые операции")


class ShowcaseCompany(BaseModel):
    """Компания витрины: только то, что можно показать в переключателе клиента."""

    model_config = ConfigDict(extra="forbid")

    company_id: str
    kind: Literal["case", "demo"] = Field(description="case — компания кейса, demo — архетип фикстуры")
    title: str
    description: str = ""
    archetype: str | None = None
    default_role: UserRole
    okved_code: str
    okved_name: str
    region: str
    fea_countries: list[str] = Field(default_factory=list)
    products: ShowcaseProducts = Field(default_factory=ShowcaseProducts)


class ShowcaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[ShowcaseCompany]


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[ShowcaseCompany, ...]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = ShowcaseConfig.model_validate(raw)
    ids = [c.company_id for c in cfg.companies]
    if len(set(ids)) != len(ids):
        raise ValueError("config/demo_companies.yaml: company_id повторяется")
    return tuple(cfg.companies)


def showcase(path: Path = SHOWCASE_CONFIG) -> list[ShowcaseCompany]:
    """Компании витрины по порядку конфига; файла нет — пустой список."""
    if not path.exists():
        return []
    return list(_load(str(path)))
