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
    baseline: dict[str, Signed]
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
    forecast: float = 1.0  # факты — ожидание или прогноз, а не свершившееся событие


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


class SurpriseMetric(_Cfg):
    metric: str
    unit: str
    number_hint: str
    forecast_prefix: str
    # прогноз «без изменений до конца года» (key_rate_eoy_2026, направление 0) — ожидание и для каждого
    # заседания этого года после публикации: «ожидаем сохранения на текущем значении 14,0% до конца года»
    hold_prefix: str | None = None


class SurpriseCfg(_Cfg):
    full_at: dict[str, float]
    default_full_at: float
    metrics: dict[str, SurpriseMetric]


class RoleCfg(_Cfg):
    focus: dict[str, float]
    other: float


class ConfidenceCfg(_Cfg):
    high_from: int
    medium_from: int
    verified_good: float
    verified_bad: float
    corroboration_good: int


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


class RateLevelCfg(_Cfg):
    types: list[str]
    neutral: float
    full_at: float
    strength: float
    strength_by_type: dict[str, float] = {}


class FeedCfg(_Cfg):
    min_score: float
    deep_per_request: int
    feedback_weight: float
    feedback_cap: float
    max_age_hours: float | None = None  # окно свежести живой ленты (источник — БД); None — 72 ч в store


class ScoringConfig(_Cfg):
    version: str
    profile: ProfileCfg
    quality: QualityCfg
    freshness: FreshnessCfg
    weights: Weights
    direct: DirectCfg
    surprise: SurpriseCfg
    roles: dict[str, RoleCfg]
    confidence: ConfidenceCfg
    off_sector: float
    prior_unscoped: float
    prior_unscoped_types: list[str]
    saturation: float
    min_contribution: float
    polarity: PolarityCfg
    deep: DeepCfg
    feed: FeedCfg
    rate_level: RateLevelCfg | None = None
    # фактор → производные факторы с долей силы: нефть дорожает → топливо дорожает
    transmission: dict[str, dict[str, float]] = {}


def role_weight(cfg: "ScoringConfig", role: str | None, channel: str) -> float:
    """Множитель канала для роли; роль без акцентов (ceo, owner) или без роли — 1."""
    r = cfg.roles.get(role or "")
    return 1.0 if r is None else r.focus.get(channel, r.other)


@lru_cache
def load_scoring(path: Path = CONFIG_DIR / "scoring.yaml") -> ScoringConfig:
    return ScoringConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
