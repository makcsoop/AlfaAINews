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
import threading
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


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
}


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "null":
        return value is None
    if kind in ("number", "integer") and isinstance(value, bool):
        return False  # True — не число
    return isinstance(value, _JSON_TYPES.get(kind, (object,)))


def _check(value: Any, schema: dict, path: str) -> str | None:
    """Подмножество JSON Schema, которым пользуются модули Р1: type, enum, properties, required, items.

    null в необязательном поле допустим (модели пишут null вместо пропуска) — проверяют правила Р1.
    """
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: значение не из списка"
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(_type_ok(value, k) for k in kinds):
            return f"{path}: ожидался тип {'/'.join(kinds)}"
    if isinstance(value, dict):
        missing = [k for k in schema.get("required", ()) if k not in value]
        if missing:
            return f"{path}: нет обязательных полей: {', '.join(missing)}"
        for key, sub in (schema.get("properties") or {}).items():
            if value.get(key) is not None and (err := _check(value[key], sub, f"{path}.{key}")):
                return err
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for n, el in enumerate(value):
            if err := _check(el, schema["items"], f"{path}[{n}]"):
                return err
    return None


def _schema_model(json_schema: dict) -> type[BaseModel]:
    """Pydantic-обёртка над JSON Schema порта: обязательные ключи и типы полей (инвариант 3).

    Невалидный ответ — ValueError внутри валидации, поэтому клиент делает повтор, потом запасную модель,
    а модуль Р1 при None остаётся на правилах.
    """
    schema = {"type": "object", **json_schema}

    class PortJson(BaseModel):
        model_config = ConfigDict(extra="allow")

        @model_validator(mode="before")
        @classmethod
        def _valid(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                raise ValueError("ответ не JSON-объект")
            if err := _check(data, schema, "$"):
                raise ValueError(err)
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
        self._lock = threading.Lock()  # экстрактор может звать модель из нескольких потоков (extract.run)

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
        with self._lock:
            if self.usage.calls >= self.max_calls:
                log.warning("потолок вызовов %d исчерпан — дальше только правила", self.max_calls)
                return None
            self.usage.calls += 1
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
            with self._lock:
                self.max_calls = 0  # лимит исчерпан — не долбим API
                self.usage.failures += 1
            return None
        except (LLMUnavailable, ValueError) as e:
            with self._lock:
                self.usage.failures += 1
            log.warning("%s: %s", step, e)
            return None
        with self._lock:
            self._record(res, step, purpose)
        return res.value.model_dump()

    def _record(self, res: Any, step: str, purpose: str) -> None:
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
