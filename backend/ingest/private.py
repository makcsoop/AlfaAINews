"""Вставки пользователя (`/analyze`) — личные для компании, которая их прислала.

Материал вставки хранится с меткой владельца в `source_tags` (`owner:<company_id>`), в общие сюжеты
не присоединяется и в склейку не идёт. Сюжет только из вставок — личный: его видит только владелец.
Модуль лёгкий (без сети и NLP), чтобы им могли пользоваться лента, инструменты и API.
"""

from collections.abc import Iterable, Mapping

from backend.schemas import RawItem, Story

PASTE_SOURCE_ID = "user_paste"
OWNER_PREFIX = "owner:"


def owner_tag(company_id: str) -> str:
    return f"{OWNER_PREFIX}{company_id}"


def item_owner(item: RawItem) -> str | None:
    """Владелец вставки; None — не вставка или вставка без метки (старые данные)."""
    if item.source_id != PASTE_SOURCE_ID:
        return None
    return next((t[len(OWNER_PREFIX):] for t in item.source_tags if t.startswith(OWNER_PREFIX)), None)


def is_paste(item: RawItem) -> bool:
    return item.source_id == PASTE_SOURCE_ID


def item_visible(item: RawItem, company_id: str | None) -> bool:
    """Обычный материал виден всем; вставка — только своей компании."""
    return not is_paste(item) or (company_id is not None and item_owner(item) == company_id)


def has_paste(story: Story) -> bool:
    return PASTE_SOURCE_ID in story.source_ids


def story_visible(story: Story, company_id: str | None, items: Mapping[str, RawItem]) -> bool:
    """Сюжет без вставок виден всем. Сюжет со вставками — только если все его вставки принадлежат
    company_id; не нашли материалы вставки — не показываем (безопаснее скрыть, чем показать чужое)."""
    if not has_paste(story):
        return True
    if company_id is None:
        return False
    found = [items[i] for i in story.item_ids if i in items]
    pastes = [it for it in found if is_paste(it)]
    return bool(pastes) and all(item_owner(it) == company_id for it in pastes)


def visible_stories(
    stories: Iterable[Story], company_id: str | None, items: Mapping[str, RawItem]
) -> list[Story]:
    return [s for s in stories if story_visible(s, company_id, items)]
