"""«Вставить ссылку или текст»: материал пользователя → сюжет (функция Р1 `ingest_paste`) → быстрая карточка.

Сюжет сохраняется в БД Р1 (источник `user_paste`, уровень 3), факты извлекает экстрактор через общий клиент
(`ClientJsonLLM`, шаг `extract`); текст уходит в модель только блоком данных. Карточка — для компании и роли
из сессии, отдаётся сразу; глубокий разбор догружается в ленте. До ближайшей склейки id сюжета может смениться
(Р1, HANDOFFS), поэтому клиент хранит саму карточку, а не story_id.
"""

import os
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

Paster = Callable[[str | None, str | None, str | None], str]  # (url, text, title) -> story_id

EXTRACT_MAX_CALLS = 2  # одна вставка — не больше двух вызовов экстрактора


class AnalyzeRequest(BaseModel):
    """company_id и роль в теле запрещены: они берутся из сессии."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, max_length=2000, description="Ссылка http(s) на новость")
    text: str | None = Field(default=None, max_length=50_000, description="Текст новости, если ссылки нет")
    title: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _url_or_text(self) -> "AnalyzeRequest":
        if not (self.url or "").strip() and not (self.text or "").strip():
            raise ValueError("нужна ссылка или текст")
        return self


def get_paster() -> Paster | None:
    """FastAPI-зависимость: запись вставки в БД Р1. None — стенд без БД (TOOL_SOURCE не db)."""
    if os.environ.get("TOOL_SOURCE", "").strip().lower() != "db":
        return None
    from backend.agents.steps import get_tool_source

    engine = getattr(get_tool_source(), "engine", None)
    if engine is None:
        return None

    def paste(url: str | None, text: str | None, title: str | None) -> str:
        from backend.ingest.paste import ingest_paste
        from backend.llm.port import ClientJsonLLM

        llm = ClientJsonLLM(max_calls=EXTRACT_MAX_CALLS, dev="r3")
        with engine.begin() as conn:
            return ingest_paste(conn, url=url, text=text, title=title, llm=llm)

    return paste
