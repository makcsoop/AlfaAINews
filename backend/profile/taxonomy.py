"""Таксономия Р2: факторы и матрица отраслей (channels_matrix.yaml), типы событий (event_types.yaml)."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import Signed

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    up: str


class ChannelsMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: dict[str, Channel]
    okved: dict[str, dict[str, Signed]] = Field(default_factory=dict)
    sector_links: dict[str, dict[str, float]] = Field(default_factory=dict)


class EventType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=list)
    prior: dict[str, Signed] = Field(default_factory=dict)
    patterns: list[str] = Field(default_factory=list)  # регулярки типирования Р1 (backend/extract/rules.py)


@lru_cache
def load_matrix(path: Path = CONFIG_DIR / "channels_matrix.yaml") -> ChannelsMatrix:
    return ChannelsMatrix.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@lru_cache
def load_event_types(path: Path = CONFIG_DIR / "event_types.yaml") -> dict[str, EventType]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["event_types"]
    return {k: EventType.model_validate(v or {}) for k, v in raw.items()}


def channel_name(channel: str) -> str:
    c = load_matrix().channels.get(channel)
    return c.name if c else channel
