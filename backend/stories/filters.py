"""Отсев словарём до склейки: реклама, дайджесты, служебное ЦБ, не-новости.

Материал не удаляется: в raw_items.dropped_reason пишется причина, и склейка такие материалы пропускает.
`python -m backend.stories.filters` — пересчитать причины для уже собранных материалов.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import Connection, select, update

from backend.db import models as m
from backend.schemas import RawItem

log = logging.getLogger(__name__)

FILTERS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "filters.yaml"


@dataclass(frozen=True)
class Rule:
    reason: str
    where: str
    pattern: re.Pattern
    sources: frozenset[str] | None = None


@lru_cache
def load_rules(path: Path = FILTERS_CONFIG) -> tuple[Rule, ...]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(
        Rule(
            reason=r["reason"],
            where=r["where"],
            pattern=re.compile(r["pattern"], re.IGNORECASE),
            sources=frozenset(r["sources"]) if r.get("sources") else None,
        )
        for r in cfg["rules"]
    )


def drop_reason(item: RawItem, rules: tuple[Rule, ...] | None = None) -> str | None:
    """Первая сработавшая причина отсева или None."""
    for rule in rules or load_rules():
        if rule.sources and item.source_id not in rule.sources:
            continue
        hit: bool
        if rule.where == "title":
            hit = bool(rule.pattern.search(item.title))
        elif rule.where == "body":
            hit = bool(rule.pattern.search(" ".join(filter(None, (item.title, item.lead, item.text)))))
        elif rule.where == "tags":
            hit = any(rule.pattern.search(t) for t in item.source_tags)
        else:
            raise ValueError(f"неизвестное where={rule.where!r} в {FILTERS_CONFIG.name}")
        if hit:
            return rule.reason
    return None


def refilter(conn: Connection) -> Counter:
    """Пересчитывает dropped_reason для всех материалов (после правки словаря)."""
    stats: Counter = Counter()
    rows = conn.execute(select(m.raw_items.c.id, m.raw_items.c.data, m.raw_items.c.dropped_reason)).all()
    for item_id, data, old in rows:
        item = RawItem.model_validate(data)
        reason = drop_reason(item)
        stats[reason or "kept"] += 1
        if reason != old:
            data["dropped_reason"] = reason
            conn.execute(
                update(m.raw_items)
                .where(m.raw_items.c.id == item_id)
                .values(dropped_reason=reason, data=data)
            )
    return stats


def main() -> None:
    from backend.db.engine import get_engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with get_engine().begin() as conn:
        log.info("отсев: %s", dict(refilter(conn)))


if __name__ == "__main__":
    main()
