"""Загрузка config/models.yaml и проверка правил: только разрешённые модели, запасная другого семейства."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

DEFAULT_MODELS_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"

ALLOWED_PROVIDER_NAMES = frozenset(
    {
        "google/gemma-4-26b-a4b-it",
        "z-ai/glm-5.3-flash",
        "qwen/qwen3.6-35b-a3b",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3-vl-30b-a3b-instruct",
        "moonshotai/kimi-k2.6",
    }
)


class ModelSpec(BaseModel):
    id: str
    provider_name: str
    family: str
    price_in_rub_per_m: float
    price_out_rub_per_m: float
    rpm_limit: int | None = None

    def cost_rub(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in_rub_per_m + tokens_out * self.price_out_rub_per_m) / 1_000_000


class StepSpec(BaseModel):
    name: str
    model: str
    fallback: str | None = None
    budget_group: str


class ModelsConfig(BaseModel):
    models: dict[str, ModelSpec]
    steps: dict[str, StepSpec]

    def step(self, name: str) -> StepSpec:
        try:
            return self.steps[name]
        except KeyError:
            raise KeyError(f"неизвестный шаг LLM: {name}") from None

    def model(self, model_id: str) -> ModelSpec:
        return self.models[model_id]

    def group_of(self, step: str) -> str:
        return self.step(step).budget_group


def parse_models_config(raw: dict[str, Any]) -> ModelsConfig:
    models: dict[str, ModelSpec] = {}
    for mid, body in (raw.get("models") or {}).items():
        spec = ModelSpec(id=mid, **body)
        if spec.provider_name not in ALLOWED_PROVIDER_NAMES:
            raise ValueError(f"модель {spec.provider_name} не входит в список разрешённых")
        models[mid] = spec
    steps: dict[str, StepSpec] = {}
    for name, body in (raw.get("steps") or {}).items():
        step = StepSpec(name=name, **body)
        if step.model not in models:
            raise ValueError(f"шаг {name}: неизвестная модель {step.model}")
        if step.fallback is not None:
            if step.fallback not in models:
                raise ValueError(f"шаг {name}: неизвестная запасная модель {step.fallback}")
            if models[step.fallback].family == models[step.model].family:
                raise ValueError(f"шаг {name}: запасная модель должна быть другого семейства")
        steps[name] = step
    return ModelsConfig(models=models, steps=steps)


def load_models_config(path: str | Path | None = None) -> ModelsConfig:
    p = Path(path) if path else DEFAULT_MODELS_PATH
    return parse_models_config(yaml.safe_load(p.read_text(encoding="utf-8")))


@lru_cache
def default_models_config() -> ModelsConfig:
    return load_models_config()
