"""Веса и пороги скоринга и профиля из config/scoring.yaml."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from backend.profile.taxonomy import CONFIG_DIR
from backend.schemas.common import Signed


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fea(_Cfg):
    any: dict[str, Signed]
    exporter: dict[str, Signed]
    importer: dict[str, Signed]


class ProfileCfg(_Cfg):
    sector_weight: float
    products: dict[str, dict[str, Signed]]
    fea: Fea
    exporter_sectors: list[str]
    importer_sectors: list[str]
    counterparty_weight: float
    counterparty_min_share: float
    hhi_high: float
    hhi_defaults: Signed


class QualityCfg(_Cfg):
    tier_weight: dict[int, float]
    corroboration_step: float
    corroboration_cap: float
    status: dict[str, float]
    rumor: float
    intent: float
    default: float


class FreshnessCfg(_Cfg):
    half_life_hours: float
    floor: float


class Weights(_Cfg):
    channels: float
    direct: float
    surprise: float


class DirectCfg(_Cfg):
    company: float
    group: float
    counterparty: float
    sector: float
    counterparty_sector: float
    linked_sector: float
    region: float


class SurpriseCfg(_Cfg):
    full_at: dict[str, float]
    default_full_at: float


class PolarityCfg(_Cfg):
    dominance: float
    neutral_below: float


class DeepCfg(_Cfg):
    share_of_budget: float
    cost_per_pair_rub: float
    k_max: int
    min_score: float
    economy_at: float
    economy_min_score: float
    stop_at: float


class ScoringConfig(_Cfg):
    version: str
    profile: ProfileCfg
    quality: QualityCfg
    freshness: FreshnessCfg
    weights: Weights
    direct: DirectCfg
    surprise: SurpriseCfg
    off_sector: float
    saturation: float
    min_contribution: float
    polarity: PolarityCfg
    deep: DeepCfg


@lru_cache
def load_scoring(path: Path = CONFIG_DIR / "scoring.yaml") -> ScoringConfig:
    return ScoringConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
