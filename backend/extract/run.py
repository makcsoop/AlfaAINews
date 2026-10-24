"""Извлечение фактов по сюжетам: `python -m backend.extract.run [--hours 72] [--all]`.

Обрабатывает сюжеты без фактов и сюжеты, у которых изменился состав материалов.
LLM-клиент подставляется, когда он готов (Разработчик 3); до тех пор работают правила.
"""

import argparse
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.extract import rules
from backend.extract.extractor import extract
from backend.extract.llm_port import JsonLLM
from backend.schemas import EventFacts, RawItem, Story

log = logging.getLogger(__name__)

LLM_MIN_CORROBORATION = 2  # модель — для confirmed и для сюжетов из 2+ независимых источников


def stories_to_extract(conn: Connection, since: datetime, force: bool = False) -> list[Story]:
    rows = conn.execute(
        select(m.stories.c.data, m.event_facts.c.data)
        .select_from(m.stories.outerjoin(m.event_facts, m.event_facts.c.story_id == m.stories.c.id))
        .where(m.stories.c.last_seen >= since)
    ).all()
    out = []
    for story_data, facts_data in rows:
        story = Story.model_validate(story_data)
        if force or facts_data is None or set(facts_data.get("source_item_ids") or []) != set(story.item_ids):
            out.append(story)
    return out


def material_change(old: EventFacts | None, new: EventFacts) -> list[str]:
    """Что существенно изменилось в фактах сюжета — повод для новой версии (разбор Р3 кешируется по версии).

    Существенно: тип события, слух / намерение, главное число события (ставка, инфляция, курс — метрика
    сюрприза из config/scoring.yaml), направления факторов, отрасли. Новые материалы с теми же фактами,
    прочие числа (новый материал приносит свои), формулировка сводки или участники — нет.
    """
    if old is None:
        return []
    from backend.ranking.config import load_scoring
    from backend.ranking.surprise import actual_value

    reasons = []
    if old.event_type != new.event_type:
        reasons.append(f"тип {old.event_type} → {new.event_type}")
    if (old.is_rumor, old.is_intent) != (new.is_rumor, new.is_intent):
        reasons.append("статус: слух / намерение")
    metric = load_scoring().surprise.metrics.get(new.event_type)
    if metric and actual_value(old, metric) != actual_value(new, metric):
        reasons.append(f"значение: {metric.metric}")
    dirs = {(c.channel, c.direction) for c in old.channels}, {(c.channel, c.direction) for c in new.channels}
    if dirs[0] != dirs[1]:
        reasons.append("направления факторов")
    if set(old.sectors_okved) != set(new.sectors_okved):
        reasons.append("отрасли")
    return reasons


def save_facts(conn: Connection, facts: EventFacts) -> None:
    old_raw = conn.execute(
        select(m.event_facts.c.data).where(m.event_facts.c.story_id == facts.story_id)
    ).scalar()
    story_data = conn.execute(select(m.stories.c.data).where(m.stories.c.id == facts.story_id)).scalar_one()
    version = story_data.get("version") or 1
    reasons = material_change(EventFacts.model_validate(old_raw) if old_raw else None, facts)
    if reasons:
        version += 1
        log.info("%s: версия %d (%s)", facts.story_id, version, ", ".join(reasons))
    facts.version = version
    row = {
        "story_id": facts.story_id,
        "event_type": facts.event_type,
        "status": facts.status,
        "data": facts.model_dump(mode="json"),
    }
    stmt = insert(m.event_facts).values(row)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["story_id"], set_={k: v for k, v in row.items() if k != "story_id"}
        )
    )
    story_data["event_type"] = facts.event_type
    story_data["version"] = version
    conn.execute(
        update(m.stories)
        .where(m.stories.c.id == facts.story_id)
        .values(event_type=facts.event_type, version=version, data=story_data)
    )


def keep_llm_typing(old: EventFacts, new: EventFacts) -> EventFacts:
    """Модель в этот раз не ответила (потолок, бюджет, сюжет не дошёл до модели), а раньше типировала сюжет —
    тип, факторы, отрасли и сводку берём от модели: правила по одному слову ошибаются чаще.
    Числа — объединение (новый материал мог добавить проверенные числа)."""
    if not (new.extractor_model or "rules").startswith("rules"):
        return new
    if not old.extractor_model or old.extractor_model.startswith("rules"):
        return new
    if new.event_type == "rate_decision" and old.event_type != "rate_decision":
        return new  # решение ЦБ правила нашли по заголовку материала — прошлый тип модели его не перебивает
    if old.event_type in rules.MEASURE_TYPES and new.event_type != old.event_type:
        return new  # тип-мера от модели, которую правила в тексте не нашли, не переносим
    seen = {(n.value, n.unit) for n in old.numbers}
    return new.model_copy(
        update={
            "event_type": old.event_type,
            "status": old.status,
            "is_rumor": old.is_rumor,
            "is_intent": old.is_intent,
            "channels": old.channels,
            "sectors_okved": old.sectors_okved,
            "summary": old.summary,
            "summary_evidence": old.summary_evidence,
            "numbers": old.numbers + [n for n in new.numbers if (n.value, n.unit) not in seen],
            # не с «rules»: при следующем пересчёте разбор модели снова сохранится
            "extractor_model": old.extractor_model.split(" (+правила)")[0] + " (+правила)",
        }
    )


def run(
    conn: Connection,
    hours: float = 72,
    llm: JsonLLM | None = None,
    force: bool = False,
    limit: int | None = None,
    min_corroboration: int = 0,
    commit_every: int | None = None,
) -> dict:
    since = datetime.now(UTC) - timedelta(hours=hours)
    stories = [s for s in stories_to_extract(conn, since, force) if s.corroboration >= min_corroboration]
    # модель типирует подтверждённые сюжеты (их видит лента), остальные — правила;
    # сначала самые подтверждённые
    for_llm = sorted(
        (s for s in stories if s.status == "confirmed" or s.corroboration >= LLM_MIN_CORROBORATION),
        key=lambda s: (-s.corroboration, s.first_seen),
    )
    llm_ids = {s.id for s in for_llm[:limit]} if llm else set()
    # сначала сюжеты для модели (по важности): потолок вызовов за проход достаётся главным
    stories = [s for s in for_llm if s.id in llm_ids] + [s for s in stories if s.id not in llm_ids]
    stats = {"stories": len(stories), "llm": 0, "unverified": 0, "kept_llm": 0}
    for story in stories:
        rows = conn.execute(select(m.raw_items.c.data).where(m.raw_items.c.id.in_(story.item_ids))).scalars()
        items = [RawItem.model_validate(r) for r in rows]
        if not items:
            continue
        facts = extract(story, items, llm if story.id in llm_ids else None)
        old_raw = conn.execute(
            select(m.event_facts.c.data).where(m.event_facts.c.story_id == story.id)
        ).scalar()
        if old_raw and (kept := keep_llm_typing(EventFacts.model_validate(old_raw), facts)) is not facts:
            facts = kept
            stats["kept_llm"] += 1
        save_facts(conn, facts)
        done = stats.get("done", 0) + 1
        stats["done"] = done
        if commit_every and done % commit_every == 0:  # долгий прогон с моделью: обрыв не теряет сделанное
            conn.commit()
            log.info("сохранено %d из %d", done, len(stories))
        stats["llm"] += facts.extractor_model is not None and not facts.extractor_model.startswith("rules")
        stats["unverified"] += bool(facts.unverified_fields)
        stats[facts.event_type] = stats.get(facts.event_type, 0) + 1
    return stats


def main() -> None:
    from backend.db.engine import get_engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--all", action="store_true", help="все сюжеты, а не только за окно")
    ap.add_argument(
        "--force", action="store_true", help="пересчитать и уже извлечённые (после правки правил)"
    )
    ap.add_argument("--llm", action="store_true", help="уточнять факты моделью (шаг extract общего клиента)")
    ap.add_argument("--limit", type=int, help="моделью не больше N сюжетов (самые подтверждённые)")
    ap.add_argument("--min-corroboration", type=int, default=0)
    args = ap.parse_args()
    llm = None
    if args.llm:
        from backend.llm.port import ClientJsonLLM

        llm = ClientJsonLLM(max_calls=2 * (args.limit or 150))  # шаг бюджета extract
    t0 = time.monotonic()
    with get_engine().connect() as conn:
        stats = run(
            conn,
            24 * 365 * 10 if args.all else args.hours,
            llm=llm,
            force=args.force,
            limit=args.limit,
            min_corroboration=args.min_corroboration,
            commit_every=20,
        )
        conn.commit()
    if llm:
        u = llm.usage
        stats["llm_usage"] = {"calls": u.calls, "failures": u.failures, "cost": round(u.cost, 4)}
    log.info("факты: %s (%.1f с)", stats, time.monotonic() - t0)


if __name__ == "__main__":
    main()
