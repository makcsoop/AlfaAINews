"""Распознавание сканов ЦМИ (Qwen3-VL) через общий LLM-клиент, шаг `vision`.

Картинка уходит составным сообщением (текст + image_url) через `LLMClient.call_text`: модель шага из белого
списка, бюджет до вызова, журнал и учёт ₽ после — как у всех вызовов (инвариант 1). Это разовая подготовка:
60 страниц уже распознаны и лежат в таблице `bank_pages` (выгрузка — `data/bank/`), стоимость в ₽ пишется
в `bank_pages.cost`.
"""

import base64
import logging
import threading
import time
import uuid
from dataclasses import dataclass

from backend.llm.budget import BudgetExceeded
from backend.llm.client import LLMClient, LLMUnavailable
from backend.llm.settings import LLMSettings

log = logging.getLogger(__name__)

STEP = "vision"
MAX_TOKENS = 4000  # длинная страница обзора — больше, чем у шага по умолчанию

_clients: dict[float, LLMClient] = {}
_clients_lock = threading.Lock()


def _client(timeout: float) -> LLMClient:
    """Один клиент на таймаут: потоки OCR делят бюджет и журнал."""
    with _clients_lock:
        if timeout not in _clients:
            _clients[timeout] = LLMClient(settings=LLMSettings(llm_timeout_s=timeout))
        return _clients[timeout]


@dataclass
class Usage:
    calls: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0  # ₽ — как записал общий клиент
    seconds: float = 0.0


class VisionOCR:
    def __init__(self, max_calls: int = 2, timeout: float = 240.0, client: LLMClient | None = None):
        self.llm = client or _client(timeout)
        s = self.llm.settings
        if s.llm_mode == "live" and not (s.llm_api_base and s.llm_api_key.get_secret_value()):
            raise RuntimeError("нет LLM_API_BASE / LLM_API_KEY в переменных окружения")
        self.max_calls = max_calls
        self.usage = Usage()

    def transcribe_image(
        self, *, model: str, image: bytes, prompt: str, mime: str = "image/jpeg"
    ) -> str | None:
        """Текст со скана страницы, температура 0. Модель — только модель шага vision из белого списка."""
        expected = self.llm.config.model(self.llm.config.step(STEP).model).provider_name
        if model != expected:
            raise ValueError(f"OCR — только модель шага {STEP} ({expected}), а не {model}")
        if self.usage.calls >= self.max_calls:
            return None
        b64 = base64.b64encode(image).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }
        ]
        self.usage.calls += 1
        t0 = time.monotonic()
        try:
            r = self.llm.call_text(
                step=STEP, messages=messages, trace_id=f"ocr-{uuid.uuid4().hex[:8]}", dev="r1",
                temperature=0.0, max_tokens=MAX_TOKENS,
            )
        except (LLMUnavailable, BudgetExceeded) as e:  # страница не распознана — не падаем, идём дальше
            self.usage.failures += 1
            log.warning("%s: %s", model, e)
            return None
        finally:
            self.usage.seconds += time.monotonic() - t0
        self.usage.tokens_in += r.tokens_in
        self.usage.tokens_out += r.tokens_out
        self.usage.cost += r.cost_rub
        return r.value.text
