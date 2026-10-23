"""Порт `JsonLLM` модулей Р1 (экстрактор, реестр ЦМИ, консенсус) поверх общего LLM-клиента.

Заменяет временный `backend.extract.llm_adapter.OpenAIJsonLLM`: тот же метод `complete_json` и счётчик
`usage`, но вызов идёт через `LLMClient` — бюджет команды, журнал, кеш, запасная модель другого семейства
(инварианты 1 и 9: без прямых HTTP к провайдеру и без чтения `.env`).

Шаг бюджета выбирается по `purpose`; модель — из `config/models.yaml` для шага. Если Р1 просит другую
разрешённую модель (сравнение моделей), вызов идёт шагом `probe` с этой моделью и без запасной.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from backend.llm.budget import BudgetExceeded
from backend.llm.client import LLMClient, LLMUnavailable, get_client

log = logging.getLogger(__name__)

STEP_BY_PURPOSE = {
    "extract": "extract",
    "consensus": "knowledge_archive",
    "bank_positions": "bank_registry",
}


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0  # ₽ по журналу клиента
    log: list[dict] = field(default_factory=list)


def _schema_model(json_schema: dict) -> type[BaseModel]:
    """Pydantic-обёртка над JSON Schema порта: объект с обязательными ключами верхнего уровня."""
    required = tuple(json_schema.get("required", ()))

    class PortJson(BaseModel):
        model_config = ConfigDict(extra="allow")

        @model_validator(mode="before")
        @classmethod
        def _required(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                raise ValueError("ответ не JSON-объект")
            missing = [k for k in required if k not in data]
            if missing:
                raise ValueError(f"нет обязательных полей: {', '.join(missing)}")
            return data

    return PortJson


class ClientJsonLLM:
    """Реализация `backend.extract.llm_port.JsonLLM` через `LLMClient`."""

    def __init__(
        self,
        max_calls: int = 50,
        *,
        client: LLMClient | None = None,
        trace_id: str | None = None,
        dev: str = "r1",
    ) -> None:
        self.max_calls = max_calls
        self.client = client or get_client()
        self.trace_id = trace_id or f"r1_{uuid.uuid4().hex[:12]}"
        self.dev = dev
        self.usage = Usage()

    def _route(self, purpose: str, model: str | None) -> tuple[str, str | None]:
        step = STEP_BY_PURPOSE.get(purpose, "extract")
        if not model:
            return step, None
        spec = self.client.config.step(step)
        allowed = {m for m in (spec.model, spec.fallback) if m}
        names = {m: self.client.config.model(m).provider_name for m in allowed}
        if model in allowed or model in names.values():
            return step, None
        return "probe", model  # другая разрешённая модель — только как явное сравнение

    def complete_json(
        self,
        *,
        model: str | None = None,
        system: str,
        user: str,
        json_schema: dict,
        purpose: str,
        max_tokens: int | None = None,
    ) -> dict | None:
        if self.usage.calls >= self.max_calls:
            log.warning("потолок вызовов %d исчерпан — дальше только правила", self.max_calls)
            return None
        step, override = self._route(purpose, model)
        messages = [
            {
                "role": "system",
                "content": system
                + "\nСхема ответа (JSON Schema):\n"
                + json.dumps(json_schema, ensure_ascii=False),
            },
            {"role": "user", "content": user},
        ]
        self.usage.calls += 1
        try:
            res = self.client.call(
                step=step,
                messages=messages,
                schema=_schema_model(json_schema),
                trace_id=self.trace_id,
                dev=self.dev,
                temperature=0.0,
                max_tokens=max_tokens,
                model_override=override,
                fixture_key=purpose,
            )
        except BudgetExceeded:
            self.max_calls = 0  # лимит исчерпан — не долбим API
            self.usage.failures += 1
            return None
        except (LLMUnavailable, ValueError) as e:
            self.usage.failures += 1
            log.warning("%s: %s", step, e)
            return None
        self.usage.tokens_in += res.tokens_in
        self.usage.tokens_out += res.tokens_out
        self.usage.cost += res.cost_rub
        self.usage.log.append(
            {
                "step": step,
                "model": res.model,
                "purpose": purpose,
                "cost_rub": res.cost_rub,
                "fallback": res.fallback_used,
            }
        )
        return res.value.model_dump()
