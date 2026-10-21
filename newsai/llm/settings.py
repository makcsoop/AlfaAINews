"""Настройки LLM-платформы (отдельно от newsai/config.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_base: str = ""
    # запасной адрес для российских хостингов; пустая строка — отключить
    llm_api_base_fallback: str = "https://api.alfahack-gateway26.ru/v1"
    # курс для стоимости от провайдера, пришедшей в долларах
    llm_usd_rub: float = 83.5
    # единицы поля cost/total_cost и заголовков LiteLLM без пометки валюты: на ключе организаторов —
    # рубли (проверено 21.10.2026: GLM 138+71 токенов → 0,0067, оценка по models.yaml 0,0063 ₽)
    llm_cost_unit: Literal["usd", "rub", "unknown"] = "rub"
    llm_api_key: SecretStr = SecretStr("")
    llm_mode: Literal["live", "fixture"] = "fixture"
    llm_daily_budget_rub: float = 300.0
    llm_archive_daily_cap_rub: float = 80.0
    llm_journal_dir: str = "data/llm_journal"
    llm_cache_dir: str = "data/llm_cache"
    llm_cache_enabled: bool = True
    llm_timeout_s: float = 60.0
    llm_fixtures_dir: str = "newsai/llm/fixtures"
    redis_url: str = "redis://localhost:6379/0"
