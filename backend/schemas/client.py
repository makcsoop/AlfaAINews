"""Профиль компании из CSV кейса и граф контрагентов."""

from typing import Literal

from pydantic import Field

from backend.schemas.common import Schema, Score01, Signed


class ChannelExposure(Schema):
    """Чувствительность компании к каналу со знаком и причиной (поле CSV или контрагенты)."""

    channel: str
    sensitivity: Signed = Field(
        description="что получает клиент, когда фактор растёт: + выигрывает, − проигрывает; модуль — сила"
    )
    drivers: list[str] = Field(description="'CL_PRODUCT_LOA_MARK=1', 'payments: 35% исходящих → ОКВЭД 24'")
    is_assumption: bool = Field(
        default=False, description="предположение AI (например, импорт/экспорт по ОКВЭД)"
    )


class CompanyProfile(Schema):
    """5. Профиль компании. Поля 1:1 с df_Uid_data_hack.csv плюс производные признаки."""

    company_id: str = Field(description="SYNTHETIC_U")
    group_id: str | None = None
    org_type: str | None = None
    okfs_name: str | None = None
    okved_code: str | None = None
    okved_name: str | None = None
    sector: str | None = Field(default=None, description="сектор по первым 2 цифрам ОКВЭД")
    company_age: str | None = None
    eco_stage: str | None = None
    workers_fns: str | None = None
    workers_spark: str | None = None
    client_type: str | None = None
    negative_mark: bool | None = None
    revenue: float | None = None
    revenue_rank_in_okved: int | None = Field(
        default=None, description="положение в отрасли — оценка по датасету"
    )
    revenue_peers_in_okved: int | None = None
    city: str | None = None
    region: str | None = None
    fts_flag: bool | None = None
    fts_country: str | None = None
    products: dict[str, bool] = Field(
        default_factory=dict, description="rko, guarantees, loans, leasing, deposits, payroll, factoring"
    )
    exposures: list[ChannelExposure] = Field(default_factory=list)
    completeness: Score01 | None = Field(default=None, description="доля заполненных ключевых полей")


# Корзина отраслей для контрагентов, которых нет в файле компаний (у них нет ОКВЭД)
OUTSIDE_SECTOR = "outside"


class Counterparty(Schema):
    company_id: str
    direction: Literal["supplier", "buyer"]
    share: Score01 = Field(description="доля во внешних платежах клиента по этому направлению")
    pay_amt: float
    pay_cnt: int | None = None
    in_dataset: bool = Field(default=True, description="контрагент есть в файле компаний (известен ОКВЭД)")
    okved_code: str | None = None
    sector: str | None = None
    same_group: bool = False
    via_person: bool = False


class CounterpartyGraph(Schema):
    """6. Топ-контрагенты, их отрасли, доли и концентрация (HHI) по направлениям.

    Поставщики — получатели платежей клиента (PAYER = клиент), покупатели — плательщики клиенту.
    Доли и HHI считаются по внешним платежам: самоплатежи и платежи внутри GROUP_ID исключены
    и показаны отдельной суммой. Контрагенты вне файла компаний попадают в сектор «outside»."""

    company_id: str
    group_id: str | None = None
    suppliers: list[Counterparty] = Field(default_factory=list, description="топ-10 по сумме")
    buyers: list[Counterparty] = Field(default_factory=list, description="топ-10 по сумме")
    total_out: float = Field(default=0, description="внешние исходящие платежи")
    total_in: float = Field(default=0, description="внешние входящие платежи")
    n_suppliers: int = 0
    n_buyers: int = 0
    intragroup_out: float = 0
    intragroup_in: float = 0
    hhi_suppliers: float | None = Field(default=None, ge=0, le=1)
    hhi_buyers: float | None = Field(default=None, ge=0, le=1)
    top_supplier_share: Score01 | None = None
    top_buyer_share: Score01 | None = None
    sector_shares_suppliers: dict[str, float] = Field(
        default_factory=dict, description="2 знака ОКВЭД → доля; 'outside' — контрагенты вне базы"
    )
    sector_shares_buyers: dict[str, float] = Field(default_factory=dict)
    group_size: int = Field(default=1, description="компаний с тем же GROUP_ID, включая клиента")
    group_members: list[str] = Field(default_factory=list, description="до 50 компаний группы")
    linked_via_persons: list[str] = Field(default_factory=list, description="гипотеза «скрытой группы»")
    high_dependency: bool = Field(
        default=False,
        description="при 5+ контрагентах крупнейший получает ≥ 50% внешних платежей по направлению",
    )
    sparse: bool = Field(
        default=False, description="меньше 5 контрагентов в обе стороны: концентрацию клиенту не показываем"
    )
