from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://newsai:newsai@localhost:55432/newsai"
    redis_url: str = "redis://localhost:6379/0"

    ingest_user_agent: str = "AlfaAINews-hackathon-bot/0.1 (+https://github.com/makcsoop/AlfaAINews)"
    ingest_host_rps: float = 1.0
    sources_config: str = "config/sources.yaml"

    case_data_dir: str = "data/case"


@lru_cache
def get_settings() -> Settings:
    return Settings()
