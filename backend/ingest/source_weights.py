"""Веса источников (день 4): стартовый вес из config/sources.yaml + счётчик подтверждений и опровержений.

`python -m backend.ingest.source_weights` — пересчитать по сюжетам и записать в таблицу `source_weights`.

По каждому источнику считаем закрытые сюжеты (последний материал старше 72 ч — склейка их уже не меняет):
- подтверждено — источник сообщил, и сюжет подтвердили другие независимые источники (corroboration ≥ 2);
- не подтвердилось — источник подал слух («по данным источников»), за 72 ч его никто не подтвердил;
  эксклюзив без маркеров слуха штрафом не считается: не перепечатали ≠ ошиблись;
- опровергнуто — в сюжете есть материал другого источника с опровержением («опроверг», «фейк»…).
Надёжность — среднее бета-распределения с априорным 2 : 2 (без данных = 0,5), опровержение весит втрое.
Вес = стартовый × (0,5 + надёжность), в пределах 0,1…1: без данных вес не меняется.
Официальные источники (ЦБ, Минфин, Росстат) не пересчитываются: их слово и есть подтверждение.
"""

import argparse
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.ingest.registry import load_sources
from backend.schemas import Source, Story

log = logging.getLogger(__name__)

CLOSED_AFTER = timedelta(hours=72)
PRIOR_HIT, PRIOR_MISS = 2.0, 2.0
REFUTE_WEIGHT = 3.0
REFUTE = re.compile(
    r"опроверг\w*|\bфейк\w*|не соответству\w+ действительност\w*|недостоверн\w+", re.IGNORECASE
)


@dataclass
class Counts:
    confirmed: int = 0
    unconfirmed: int = 0
    refuted: int = 0


def reliability(c: Counts) -> float:
    hits = c.confirmed + PRIOR_HIT
    return hits / (hits + c.unconfirmed + REFUTE_WEIGHT * c.refuted + PRIOR_MISS)


def learned_weight(start: float, c: Counts) -> float:
    return round(min(1.0, max(0.1, start * (0.5 + reliability(c)))), 3)


def count(stories: list[Story], refuting: dict[str, set[str]], now: datetime) -> dict[str, Counts]:
    """refuting: story_id → источники, опубликовавшие опровержение в этом сюжете."""
    out: dict[str, Counts] = defaultdict(Counts)
    for s in stories:
        if s.last_seen > now - CLOSED_AFTER:
            continue
        against = refuting.get(s.id, set())
        for src in set(s.source_ids) - against:
            c = out[src]
            if against:
                c.refuted += 1
            elif s.corroboration >= 2:
                c.confirmed += 1
            elif s.status == "rumor":
                c.unconfirmed += 1
    return out


def compute(conn: Connection, now: datetime | None = None) -> dict[str, tuple[Counts, float]]:
    now = now or datetime.now(UTC)
    stories = [Story.model_validate(d) for d in conn.execute(select(m.stories.c.data)).scalars()]
    refuting: dict[str, set[str]] = defaultdict(set)
    for story_id, source_id, title, lead in conn.execute(
        select(
            m.raw_items.c.story_id, m.raw_items.c.source_id, m.raw_items.c.title, m.raw_items.c.lead
        ).where(m.raw_items.c.story_id.is_not(None))
    ):
        if REFUTE.search(f"{title} {lead or ''}"):
            refuting[story_id].add(source_id)
    counts = count(stories, refuting, now)
    return {
        s.id: (c, s.weight if s.official else learned_weight(s.weight, c))
        for s in load_sources(enabled_only=False)
        if (c := counts.get(s.id, Counts())) is not None
    }


def save(conn: Connection, result: dict[str, tuple[Counts, float]]) -> int:
    for sid, (c, w) in result.items():
        row = {
            "source_id": sid,
            "confirmed": c.confirmed,
            "unconfirmed": c.unconfirmed,
            "refuted": c.refuted,
            "weight": w,
            "updated_at": datetime.now(UTC),
        }
        conn.execute(
            insert(m.source_weights).values(row).on_conflict_do_update(index_elements=["source_id"], set_=row)
        )
    return len(result)


def learned_sources(conn: Connection) -> list[Source]:
    """Реестр источников с выученными весами (для скоринга); нет пересчёта — стартовые веса."""
    weights = dict(conn.execute(select(m.source_weights.c.source_id, m.source_weights.c.weight)).all())
    return [s.model_copy(update={"weight": weights.get(s.id, s.weight)}) for s in load_sources()]


def main() -> None:
    from backend.db.engine import get_engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    argparse.ArgumentParser().parse_args()
    with get_engine().begin() as conn:
        m.metadata.create_all(conn, tables=[m.source_weights])
        result = compute(conn)
        save(conn, result)
    start = {s.id: s.weight for s in load_sources(enabled_only=False)}
    moved = sorted(result.items(), key=lambda kv: abs(kv[1][1] - start[kv[0]]), reverse=True)
    for sid, (c, w) in moved[:15]:
        log.info(
            "%-24s старт %.2f → %.3f (подтв. %d, не подтв. %d, опроверг. %d)",
            sid,
            start[sid],
            w,
            c.confirmed,
            c.unconfirmed,
            c.refuted,
        )


if __name__ == "__main__":
    main()
