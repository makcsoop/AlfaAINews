"""Досье разбора: все объекты, на которые могут ссылаться аналитики, критик и карточка.

Досье собирает планировщик на пару «сюжет × отрасль». У каждого элемента свой id —
утверждения аналитиков и карточки ссылаются только на эти id (refs), критик проверяет,
что ссылка существует. Тексты новостей и постов попадают в промпт только через data_block.
"""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from newsai.llm import data_block

DossierKind = Literal[
    "story",  # сам сюжет
    "fact",  # проверенный факт / число из event_facts
    "news",  # материал (raw_item)
    "bank_position",  # позиция ЦМИ, id начинается с cmi_
    "market_knowledge",  # знание аналитиков рынка (mk_…), только до даты сюжета
    "case",  # похожий прошлый случай
    "calc",  # результат калькулятора
    "sector",  # признаки отрасли
    "profile",  # признаки компании (только для карточки и вопрос-ответа, не для разбора отрасли)
    "counterparties",  # доли и отрасли контрагентов, без сумм платежей
]

# Виды, чей текст пришёл извне и может содержать внедрённые команды
# (факты и заголовок сюжета тоже: их цитаты извлечены из текста новостей)
UNTRUSTED_KINDS: frozenset[str] = frozenset({"story", "fact", "news", "market_knowledge", "case"})


class DossierItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="стабильный id: item id, cmi_…, mk_…, fact:…, calc:…")
    kind: DossierKind
    title: str
    text: str = Field(default="", description="короткий фрагмент/цитата, ≤ 600 символов")
    data: dict[str, Any] = Field(default_factory=dict, description="структурные поля (числа, даты, канал)")
    source_tool: str | None = Field(default=None, description="инструмент, которым получен элемент")
    published_at: datetime | None = None


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = ""
    origin: Literal["template", "llm", "critic"] = "template"


class Dossier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_id: str
    story_version: int = 1
    sector: str
    event_type: str | None = None
    story_published_at: datetime
    trace_id: str
    plan: list[PlanStep] = Field(default_factory=list)
    items: list[DossierItem] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list, description="чего не хватило (для достройки плана и критика)"
    )

    def ids(self) -> set[str]:
        return {i.id for i in self.items}

    def get(self, item_id: str) -> DossierItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def by_kind(self, kind: str) -> list[DossierItem]:
        return [i for i in self.items if i.kind == kind]

    def add(self, item: DossierItem) -> None:
        """Добавляет элемент; повтор id заменяет старый (повторный вызов инструмента)."""
        self.items = [i for i in self.items if i.id != item.id] + [item]

    def render_for_prompt(self, max_chars: int = 12000, kinds: set[str] | None = None) -> str:
        """Досье для промпта: доверенные элементы — JSON, внешние тексты — внутри <data>."""
        trusted: list[dict[str, Any]] = []
        untrusted: list[str] = []
        for i in self.items:
            if kinds is not None and i.kind not in kinds:
                continue
            if i.kind in UNTRUSTED_KINDS:
                data = f"\n{json.dumps(i.data, ensure_ascii=False, default=str)}" if i.data else ""
                untrusted.append(f"[{i.id}] ({i.kind}) {i.title}\n{i.text}{data}")
            else:
                trusted.append(i.model_dump(mode="json", exclude_none=True, exclude={"source_tool"}))
        # Бюджет символов режет содержимое, а не готовую строку: обёртка <data>…</data> всегда цела.
        head = "Досье (ссылайся только на эти id):\n"
        while trusted and len(head + json.dumps(trusted, ensure_ascii=False)) > max_chars:
            trusted.pop()  # доверенная часть режется целыми элементами с хвоста
        out = head + json.dumps(trusted, ensure_ascii=False)
        if not untrusted:
            return out
        prefix = "\n\nВнешние материалы:\n"
        room = max_chars - len(out) - len(prefix) - len(data_block(""))
        if room <= 0:
            return out
        parts: list[str] = []
        used = 0
        for u in untrusted:
            sep = 2 if parts else 0
            if used + sep + len(u) <= room:
                parts.append(u)
                used += sep + len(u)
            else:
                left = room - used - sep
                if left > 0:
                    parts.append(u[:left])  # последний элемент обрезается, остальные отбрасываются
                break
        body = "\n\n".join(parts)
        block = data_block(body)
        while len(block) > max_chars - len(out) - len(prefix) and body:  # экранирование удлиняет текст
            body = body[: -max(1, len(block) - (max_chars - len(out) - len(prefix)))]
            block = data_block(body)
        return out + prefix + block if body else out
