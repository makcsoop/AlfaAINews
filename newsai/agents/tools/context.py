"""Контекст вызова инструментов: компания и роль берутся только из сессии (инвариант 6)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from newsai.agents.tools.source import ToolDataSource


class ToolError(Exception):
    """Ошибка инструмента. Наружу — только код и короткое сообщение, без внутренностей."""

    def __init__(self, code: str, message: str, call: Any = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.call = call  # newsai.schemas.ToolCall для трассы


@dataclass(frozen=True)
class ToolContext:
    company_id: str  # из сессии; в аргументах инструментов его нет
    role: str
    story_id: str
    story_published_at: datetime  # граница «только раньше сюжета» (инвариант 10)
    trace_id: str
    source: ToolDataSource
    allow_telegram_origin: bool = True

    def __post_init__(self) -> None:
        if self.story_published_at.tzinfo is None:
            object.__setattr__(self, "story_published_at", self.story_published_at.replace(tzinfo=UTC))

    @classmethod
    def for_story(
        cls,
        source: ToolDataSource,
        *,
        story_id: str,
        company_id: str,
        role: str,
        trace_id: str,
        allow_telegram_origin: bool = True,
    ) -> "ToolContext":
        """Граница — момент последнего материала сюжета: всё, что вошло в сюжет, уже «известно»."""
        story = next((s for s in source.stories() if s.id == story_id), None)
        if story is None:
            raise ToolError("not_found", "сюжет не найден")
        return cls(
            company_id=company_id,
            role=role,
            story_id=story_id,
            story_published_at=story.last_seen,
            trace_id=trace_id,
            source=source,
            allow_telegram_origin=allow_telegram_origin,
        )
