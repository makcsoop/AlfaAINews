"""Временный адаптер порта JsonLLM к OpenAI-совместимому API организаторов.

Заменяется LLM-клиентом Разработчика 3 (бюджет на команду, журнал, запасные модели). Здесь только то,
что нужно экстрактору и сравнению моделей: строгий JSON, потолок вызовов, учёт токенов и стоимости.
Ключ берётся из LLM_API_KEY (.env), в код и логи не попадает.
"""

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    path = Path(__file__).resolve().parents[2] / ".env"
    if path.exists():
        for k, v in re.findall(r"^([A-Z0-9_]+)=(.*)$", path.read_text(encoding="utf-8"), re.MULTILINE):
            env.setdefault(k, v.strip())
    return env


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0  # в единицах, которые возвращает API (поле usage.cost)
    seconds: float = 0.0
    log: list[dict] = field(default_factory=list)


class OpenAIJsonLLM:
    def __init__(self, max_calls: int = 50, timeout: float = 90.0, reasoning: bool = False):
        env = _env()
        self.base = env.get("LLM_API_BASE", "https://changellenge.up.railway.app/v1").rstrip("/")
        self._key = env.get("LLM_API_KEY", "")
        if not self._key:
            raise RuntimeError("LLM_API_KEY не задан в .env")
        self.max_calls = max_calls
        self.reasoning = reasoning
        self.client = httpx.Client(timeout=timeout)
        self.usage = Usage()

    def complete_json(
        self, *, model: str, system: str, user: str, json_schema: dict, purpose: str, max_tokens: int = 1500
    ) -> dict | None:
        if self.usage.calls >= self.max_calls:
            log.warning("потолок вызовов %d исчерпан — дальше только правила", self.max_calls)
            return None
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": system
                    + "\nСхема ответа (JSON Schema):\n"
                    + json.dumps(json_schema, ensure_ascii=False),
                },
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            # без этого Qwen3.6 тратит весь max_tokens на рассуждения и не доходит до JSON
            "reasoning": {"enabled": self.reasoning},
        }
        self.usage.calls += 1
        t0 = time.monotonic()
        for attempt in range(2):
            try:
                r = self.client.post(
                    f"{self.base}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
            except httpx.HTTPError as e:
                log.warning("%s: сеть: %s", model, e)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(2)
                continue
            break
        else:
            self.usage.failures += 1
            return None
        dt = time.monotonic() - t0
        self.usage.seconds += dt
        if r.status_code != 200:
            self.usage.failures += 1
            msg = r.text[:200]
            log.warning("%s: HTTP %s %s", model, r.status_code, msg)
            if "Budget has been exceeded" in msg:
                self.max_calls = 0  # лимит дня исчерпан — не долбим API
            return None
        data = r.json()
        u = data.get("usage") or {}
        self.usage.tokens_in += u.get("prompt_tokens", 0)
        self.usage.tokens_out += u.get("completion_tokens", 0)
        self.usage.cost += float(u.get("cost") or 0)
        content = (data["choices"][0]["message"].get("content") or "").strip()
        entry = {
            "model": model,
            "purpose": purpose,
            "seconds": round(dt, 2),
            "tokens_in": u.get("prompt_tokens"),
            "tokens_out": u.get("completion_tokens"),
            "cost": u.get("cost"),
            "json_ok": False,
        }
        self.usage.log.append(entry)
        try:
            parsed = json.loads(_FENCE.sub("", content))
        except json.JSONDecodeError:
            self.usage.failures += 1
            log.info("%s: ответ не JSON: %s", model, content[:120])
            return None
        if not isinstance(parsed, dict):
            self.usage.failures += 1
            return None
        entry["json_ok"] = True
        return parsed

    def transcribe_image(
        self, *, model: str, image: bytes, prompt: str, purpose: str = "ocr", mime: str = "image/jpeg"
    ) -> str | None:
        """Текст со скана страницы (Qwen3-VL). Без рассуждений, температура 0."""
        if self.usage.calls >= self.max_calls:
            return None
        b64 = base64.b64encode(image).decode()
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 4000,
            "reasoning": {"enabled": False},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
        }
        self.usage.calls += 1
        t0 = time.monotonic()
        try:
            r = self.client.post(
                f"{self.base}/chat/completions", json=body, headers={"Authorization": f"Bearer {self._key}"}
            )
        except httpx.HTTPError as e:  # таймаут длинной страницы и т.п. — страница не распознана, не падаем
            self.usage.failures += 1
            log.warning("%s: %s %s", model, type(e).__name__, e)
            return None
        dt = time.monotonic() - t0
        self.usage.seconds += dt
        if r.status_code != 200:
            self.usage.failures += 1
            log.warning("%s: HTTP %s %s", model, r.status_code, r.text[:200])
            return None
        data = r.json()
        u = data.get("usage") or {}
        self.usage.tokens_in += u.get("prompt_tokens", 0)
        self.usage.tokens_out += u.get("completion_tokens", 0)
        self.usage.cost += float(u.get("cost") or 0)
        self.usage.log.append(
            {"model": model, "purpose": purpose, "seconds": round(dt, 2), "cost": u.get("cost")}
        )
        return (data["choices"][0]["message"].get("content") or "").strip()
