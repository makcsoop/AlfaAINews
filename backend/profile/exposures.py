"""Профиль v1: чувствительность компании к факторам со знаком и причиной.

Складывается из четырёх частей, по каждому фактору сумма обрезается до −1…1:
отрасль (строка ОКВЭД в channels_matrix.yaml), продукты банка, ВЭД, отрасли контрагентов из графа платежей.
Знак — что получает клиент, когда фактор растёт: кредит при росте ставки −, депозит +.
"""

from collections import defaultdict

from backend.profile.build import PRODUCT_COLUMNS
from backend.profile.taxonomy import ChannelsMatrix, load_matrix
from backend.ranking.config import ProfileCfg, load_scoring
from backend.schemas import ChannelExposure, CompanyProfile, CounterpartyGraph

# продукт → флаг CSV, которым он подтверждается (driver для «почему вы это видите»)
PRODUCT_FLAG = {key: f"{col}=1" for col, key in PRODUCT_COLUMNS.items()}


class _Acc:
    """Сумма вкладов по фактору с причинами и пометкой «предположение»."""

    def __init__(self) -> None:
        self.value: dict[str, float] = defaultdict(float)
        self.drivers: dict[str, list[str]] = defaultdict(list)
        # фактор — предположение, только если у него нет ни одной твёрдой причины (поле CSV или продукт):
        # кредит делает ставку прямым каналом, даже если к нему добавились отрасли контрагентов
        self.firm: dict[str, bool] = defaultdict(bool)

    def add(self, row: dict[str, float], k: float, driver: str, *, assumption: bool = False) -> None:
        for channel, v in row.items():
            if v * k == 0:
                continue
            self.value[channel] += v * k
            if driver not in self.drivers[channel]:
                self.drivers[channel].append(driver)
            self.firm[channel] |= not assumption

    def exposures(self) -> list[ChannelExposure]:
        out = [
            ChannelExposure(
                channel=ch,
                sensitivity=round(max(-1.0, min(1.0, v)), 3),
                drivers=self.drivers[ch],
                is_assumption=not self.firm[ch],
            )
            for ch, v in self.value.items()
            if abs(v) >= 0.01
        ]
        return sorted(out, key=lambda e: -abs(e.sensitivity))


def fea_role(sector: str | None, cfg: ProfileCfg) -> str | None:
    """Экспортёр или импортёр — предположение по отрасли: направление ВЭД в CSV не указано."""
    if sector in cfg.exporter_sectors:
        return "exporter"
    if sector in cfg.importer_sectors:
        return "importer"
    return None


def subsector(okved_code: str | None, matrix: ChannelsMatrix) -> str | None:
    """Самая точная строка матрицы длиннее двух цифр ОКВЭД (46.71 для 46.71.2), если она есть."""
    code = okved_code or ""
    return next((code[:n] for n in (5, 4) if len(code) >= n and code[:n] in matrix.okved), None)


def compute_exposures(
    profile: CompanyProfile,
    graph: CounterpartyGraph | None = None,
    *,
    matrix: ChannelsMatrix | None = None,
    cfg: ProfileCfg | None = None,
) -> list[ChannelExposure]:
    matrix = matrix or load_matrix()
    cfg = cfg or load_scoring().profile
    acc = _Acc()
    acc.add(cfg.baseline, 1.0, "общий фактор для всех компаний", assumption=True)

    if profile.sector and profile.sector in matrix.okved:
        acc.add(matrix.okved[profile.sector], cfg.sector_weight, f"MAINOKVED_CODE={profile.okved_code}")
        # подотрасль поверх отрасли: «46» — опт вообще, «46.71» — опт топливом, у него ещё и цены на топливо
        if sub := subsector(profile.okved_code, matrix):
            acc.add(matrix.okved[sub], cfg.sector_weight, f"MAINOKVED_CODE={profile.okved_code}")

    for product, on in profile.products.items():
        if on and product in cfg.products:
            acc.add(cfg.products[product], 1.0, PRODUCT_FLAG[product])

    if profile.fts_flag:
        acc.add(cfg.fea.any, 1.0, "FTS_FLAG=1")
        role = fea_role(profile.sector, cfg)
        if role:
            word = "экспортёр" if role == "exporter" else "импортёр"
            row = cfg.fea.exporter if role == "exporter" else cfg.fea.importer
            acc.add(row, 1.0, f"ОКВЭД {profile.sector}: скорее {word}", assumption=True)
        if profile.fts_country:
            # страна ВЭД — причина для курса и ограничений, сама чувствительность не меняется
            for ch in ("trade_restrictions", "fx_rub", "logistics_costs"):
                if ch in acc.value:
                    acc.drivers[ch].append(f"FTS_COUNTRY_NAME={profile.fts_country}")

    if graph is not None:
        _add_counterparties(acc, graph, matrix, cfg)
    return acc.exposures()


def _add_counterparties(acc: _Acc, graph: CounterpartyGraph, matrix: ChannelsMatrix, cfg: ProfileCfg) -> None:
    # формат причины — как в backend/cards/features.py, чтобы карточка принимала признак
    sides = (
        (graph.sector_shares_buyers, "входящих от ОКВЭД"),
        (graph.sector_shares_suppliers, "исходящих → ОКВЭД"),
    )
    for shares, phrase in sides:
        for sector, share in shares.items():
            row = matrix.okved.get(sector)
            if row and share >= cfg.counterparty_min_share:
                driver = f"payments: {round(share * 100)}% {phrase} {sector}"
                acc.add(row, share * cfg.counterparty_weight, driver, assumption=True)
    hhi = max(graph.hhi_buyers or 0, graph.hhi_suppliers or 0)
    if graph.high_dependency or hhi >= cfg.hhi_high:
        acc.add({"counterparty_defaults": cfg.hhi_defaults}, 1.0, f"концентрация контрагентов: HHI {hhi:.2f}")


def with_exposures(profile: CompanyProfile, graph: CounterpartyGraph | None = None) -> CompanyProfile:
    """Профиль v1: копия профиля v0 с чувствительностями."""
    return profile.model_copy(update={"exposures": compute_exposures(profile, graph)})
