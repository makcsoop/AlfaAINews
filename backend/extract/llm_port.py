"""Интерфейс, через который экстрактор зовёт LLM. Реализацию даёт LLM-клиент Разработчика 3
(бюджет, журнал, запасная модель); пока его нет — заглушка, и работает только слой правил.

ТЗ: «если LLM-клиент опаздывает, экстрактор пишешь на его интерфейсе с заглушкой».
"""

from typing import Protocol


class JsonLLM(Protocol):
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
        """Ответ модели как JSON по схеме или None (нет бюджета, сбой, невалидный ответ)."""


class NoLLM:
    """Заглушка: модели нет — экстрактор работает только на правилах."""

    def complete_json(self, **_) -> dict | None:
        return None


class FixtureLLM:
    """Заглушка для тестов: отдаёт заранее заготовленный ответ."""

    def __init__(self, answer: dict | None):
        self.answer = answer
        self.calls: list[dict] = []

    def complete_json(self, **kwargs) -> dict | None:
        self.calls.append(kwargs)
        return self.answer
