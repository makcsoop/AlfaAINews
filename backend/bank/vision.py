"""Распознавание сканов ЦМИ (Qwen3-VL) — единственный вызов модели в обход общего клиента.

Клиент Р3 (`backend/llm/client.py`) передаёт только текстовые сообщения, а сканам нужна картинка.
Это разовая подготовка: 60 страниц уже распознаны и лежат в таблице `bank_pages` (выгрузка — `data/bank/`),
повторно OCR не нужен. Ключ и адрес — из настроек LLM-клиента (переменные окружения), стоимость в ₽
пишется в `bank_pages.cost`. Если OCR понадобится снова — попросить у Р3 шаг `vision` с изображением.
"""

import base64
import logging
import time
from dataclasses import dataclass

import httpx

from backend.llm.settings import LLMSettings

log = logging.getLogger(__name__)


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0  # ₽: поле usage.cost провайдера (на ключе организаторов — рубли)
    seconds: float = 0.0


class VisionOCR:
    def __init__(self, max_calls: int = 2, timeout: float = 240.0):
        s = LLMSettings()
        self.base = s.llm_api_base.rstrip("/")
        self._key = s.llm_api_key.get_secret_value()
        if not (self.base and self._key):
            raise RuntimeError("нет LLM_API_BASE / LLM_API_KEY в переменных окружения")
        self.max_calls = max_calls
        self.client = httpx.Client(timeout=timeout)
        self.usage = Usage()

    def transcribe_image(
        self, *, model: str, image: bytes, prompt: str, mime: str = "image/jpeg"
    ) -> str | None:
        """Текст со скана страницы. Без рассуждений, температура 0."""
        if self.usage.calls >= self.max_calls:
            return None
        b64 = base64.b64encode(image).decode()
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 4000,
            "reasoning_effort": "none",
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
        self.usage.seconds += time.monotonic() - t0
        if r.status_code != 200:
            self.usage.failures += 1
            log.warning("%s: HTTP %s %s", model, r.status_code, r.text[:200])
            return None
        data = r.json()
        u = data.get("usage") or {}
        self.usage.tokens_in += u.get("prompt_tokens", 0)
        self.usage.tokens_out += u.get("completion_tokens", 0)
        self.usage.cost += float(u.get("cost") or 0)
        return (data["choices"][0]["message"].get("content") or "").strip()
