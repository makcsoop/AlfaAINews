"""Своя компания по анкете: профиль и граф контрагентов из ответов пользователя вместо CSV кейса.

Дальше всё как у компаний кейса: чувствительности считает `with_exposures`, ленту — скоринг, карточки —
конвейер.
Отличие одно: доли контрагентов по отраслям заявлены пользователем, а не посчитаны по платежам.

Профиль хранится в Redis (CACHE_BACKEND=redis) или в памяти процесса. id выдаёт сервер (`U_OWN_…`), войти в
компанию можно только с ключом владельца из ответа на создание: чужую анкету не открыть, подобрав id.
"""

import hashlib
import json
import logging
import re
import secrets
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.profile.build import completeness, sector_of
from backend.schemas import CompanyProfile, CounterpartyGraph

log = logging.getLogger(__name__)

OWN_PREFIX = "U_OWN_"
OWN_TTL_S = 30 * 24 * 3600  # анкета живёт месяц; потом её заполняют заново
SECTORS_CONFIG = Path("config/okved_sectors.yaml")
_OKVED = re.compile(r"^\d{2}(\.\d{1,2}){0,2}$")
_KEY = "own:company:"


@lru_cache(maxsize=1)
def okved_sectors() -> dict[str, str]:
    """Отрасли анкеты: код → название (строки матрицы каналов)."""
    raw = yaml.safe_load(SECTORS_CONFIG.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in raw.items()}


class OwnProducts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loans: bool = False
    leasing: bool = False
    deposits: bool = False
    guarantees: bool = False
    factoring: bool = False
    payroll: bool = False


class OwnCompanyForm(BaseModel):
    """Анкета: только то, что влияет на ленту. Сумм, ИНН и данных людей нет."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=2, max_length=80, description="Как назвать компанию в интерфейсе")
    okved_code: str = Field(description="Основной ОКВЭД: 46.71 или 46.71.2; отрасль — первые 2 цифры")
    region: str | None = Field(default=None, max_length=60)
    products: OwnProducts = Field(default_factory=OwnProducts)
    fea: bool = Field(default=False, description="Есть внешнеторговые операции")
    fea_countries: list[str] = Field(default_factory=list, max_length=5)
    buyers: dict[str, int] = Field(
        default_factory=dict, description="отрасль покупателей (код анкеты) → доля входящих платежей, %"
    )
    suppliers: dict[str, int] = Field(
        default_factory=dict, description="отрасль поставщиков (код анкеты) → доля исходящих платежей, %"
    )

    @field_validator("okved_code")
    @classmethod
    def _okved(cls, v: str) -> str:
        if not _OKVED.match(v):
            raise ValueError("ОКВЭД в виде 46 или 46.71 или 46.71.2")
        if sector_of(v) not in okved_sectors():
            raise ValueError("отрасли нет в справочнике анкеты")
        return v

    @field_validator("fea_countries")
    @classmethod
    def _countries(cls, v: list[str]) -> list[str]:
        out = [c.strip() for c in v if c.strip()]
        if any(len(c) > 40 for c in out):
            raise ValueError("название страны длиннее 40 символов")
        return out

    @field_validator("buyers", "suppliers")
    @classmethod
    def _shares(cls, v: dict[str, int]) -> dict[str, int]:
        if len(v) > 5:
            raise ValueError("не больше 5 отраслей")
        known = okved_sectors()
        for code, pct in v.items():
            if code not in known:
                raise ValueError(f"отрасли {code} нет в справочнике анкеты")
            if not 1 <= pct <= 100:
                raise ValueError("доля — от 1 до 100%")
        if sum(v.values()) > 100:
            raise ValueError("сумма долей больше 100%")
        return v

    @model_validator(mode="after")
    def _fea(self) -> "OwnCompanyForm":
        if self.fea_countries and not self.fea:
            self.fea = True  # указал страны — значит, ВЭД есть
        return self


def build_own(form: OwnCompanyForm, company_id: str) -> tuple[CompanyProfile, CounterpartyGraph | None]:
    """Профиль v0 и граф по анкете. Чувствительности (v1) добавит `with_exposures`, как у компаний кейса."""
    code = form.okved_code
    names = okved_sectors()
    sub = next((code[:n] for n in (5, 4) if len(code) >= n and code[:n] in names), None)
    profile = CompanyProfile(
        company_id=company_id,
        okved_code=code,
        okved_name=names.get(sub or "") or names.get(sector_of(code) or ""),
        sector=sector_of(code),
        region=form.region or None,
        fts_flag=form.fea,
        fts_country=", ".join(form.fea_countries) or None,
        products={"rko": True, **form.products.model_dump()},
    )
    profile.completeness = completeness(profile)
    graph = None
    if form.buyers or form.suppliers:
        # доли заявлены пользователем: на них строятся чувствительности по отраслям контрагентов (как по
        # платежам), а концентрацию по одной анкете не оцениваем — sparse
        graph = CounterpartyGraph(
            company_id=company_id,
            sector_shares_buyers={sector_of(k) or k: v / 100 for k, v in form.buyers.items()},
            sector_shares_suppliers={sector_of(k) or k: v / 100 for k, v in form.suppliers.items()},
            sparse=True,
        )
    return profile, graph


def _hash(owner_key: str) -> str:
    return hashlib.sha256(owner_key.encode()).hexdigest()


class OwnCompanies:
    """Анкеты своих компаний: Redis (переживает перезапуск, общий для процессов) или память процесса."""

    def __init__(self, kv: Any | None = None) -> None:
        self._kv = kv
        self._mem: dict[str, str] = {}
        self._lock = threading.Lock()

    def _get(self, company_id: str) -> dict[str, Any] | None:
        raw = None
        if self._kv is not None:
            try:
                raw = self._kv.get(_KEY + company_id)
            except Exception as e:  # Redis пропал — работаем с тем, что в памяти
                log.warning("анкета %s: Redis недоступен (%s)", company_id, type(e).__name__)
        if raw is None:
            with self._lock:
                raw = self._mem.get(company_id)
        if raw is None:
            return None
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)  # type: ignore[no-any-return]

    def create(self, form: OwnCompanyForm) -> tuple[str, str]:
        """Новая компания: (company_id, ключ владельца). Ключ отдаётся один раз, хранится только его хеш."""
        company_id = OWN_PREFIX + secrets.token_hex(8).upper()
        owner_key = secrets.token_urlsafe(24)
        profile, graph = build_own(form, company_id)
        record = {
            "owner": _hash(owner_key),
            "form": form.model_dump(),
            "profile": profile.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json") if graph else None,
        }
        raw = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self._mem[company_id] = raw
        if self._kv is not None:
            try:
                self._kv.set(_KEY + company_id, raw, ex=OWN_TTL_S)
            except Exception as e:
                log.warning("анкета %s сохранена только в памяти: %s", company_id, type(e).__name__)
        return company_id, owner_key

    def verify(self, company_id: str, owner_key: str | None) -> bool:
        if not is_own(company_id) or not owner_key:
            return False
        rec = self._get(company_id)
        return rec is not None and secrets.compare_digest(rec["owner"], _hash(owner_key))

    def title(self, company_id: str) -> str | None:
        rec = self._get(company_id) if is_own(company_id) else None
        return rec["form"]["title"] if rec else None

    def profile(self, company_id: str) -> CompanyProfile | None:
        rec = self._get(company_id) if is_own(company_id) else None
        return CompanyProfile.model_validate(rec["profile"]) if rec else None

    def counterparties(self, company_id: str) -> CounterpartyGraph | None:
        rec = self._get(company_id) if is_own(company_id) else None
        return CounterpartyGraph.model_validate(rec["graph"]) if rec and rec.get("graph") else None


def is_own(company_id: str) -> bool:
    return company_id.startswith(OWN_PREFIX)


_registry: OwnCompanies | None = None
_registry_lock = threading.Lock()


def own_companies() -> OwnCompanies:
    global _registry
    with _registry_lock:
        if _registry is None:
            from backend.agents.cache import make_kv

            _registry = OwnCompanies(make_kv())
        return _registry


def reset_own_companies() -> None:
    """Тесты: новое пустое хранилище."""
    global _registry
    with _registry_lock:
        _registry = None


class WithOwnCompanies:
    """Источник данных инструментов + свои компании: профиль и граф `U_OWN_…` — из анкеты, остальное —
    как было. Только чтение, как у всех инструментов агентов."""

    def __init__(self, src: Any) -> None:
        self._src = src

    @property
    def inner(self) -> Any:
        """Исходный источник: БД кейса или фикстура."""
        return self._src

    def profile(self, company_id: str) -> CompanyProfile | None:
        if is_own(company_id):
            return own_companies().profile(company_id)
        return self._src.profile(company_id)  # type: ignore[no-any-return]

    def counterparties(self, company_id: str) -> CounterpartyGraph | None:
        if is_own(company_id):
            return own_companies().counterparties(company_id)
        return self._src.counterparties(company_id)  # type: ignore[no-any-return]

    def __getattr__(self, name: str) -> Any:
        # stories, raw_items, engine, live_feed_window… — у исходного источника
        return getattr(self._src, name)
