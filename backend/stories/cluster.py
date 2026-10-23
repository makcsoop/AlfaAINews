"""Склейка материалов в сюжеты и статус подтверждения.

`python -m backend.stories.cluster [--hours 72]` — пересобрать сюжеты по материалам за окно.

1. Копии: SimHash, расстояние Хэмминга ≤ 3 — присоединяются к оригиналу и не считаются
   независимым подтверждением.
2. Похожесть: символьный TF-IDF (запасной путь из ТЗ; эмбеддинги USER-bge-m3 — если организаторы
   разрешат энкодеры). Материал сравнивается с сюжетом целиком — среднее по всем материалам сюжета,
   а не самый похожий материал: иначе цепочка похожих склеивает разные события.
3. Присоединение: похожесть ≥ порога и хотя бы одна общая сущность (Natasha + словари), окно 72 ч.
4. Статус: официальный источник или два независимых источника уровня 0–1 → confirmed;
   маркеры слуха без подтверждения → rumor; один источник → single; иначе unconfirmed.
"""

import argparse
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import Connection, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from backend.db import models as m
from backend.extract.entities import extract_entities
from backend.ingest.normalize import hamming
from backend.ingest.registry import load_sources
from backend.schemas import RawItem, Story

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=72)
SIM_THRESHOLD = 0.30  # ТЗ: символьный TF-IDF ≥ 0,30; подбирается на размеченных парах (день 4)
SIM_NO_ENTITY = 0.55  # без сущностей у одной из сторон склеиваем только почти одинаковые тексты
SIMHASH_COPY = 3
RUMOR = re.compile(
    r"по (данным|информации|словам) (источник|собеседник)|источники? (в|из|знаком|близк)|якобы|по слухам"
    r"|сообщают (сми|источники)|по неофициальным данным",
    re.IGNORECASE,
)


@dataclass
class _Draft:
    items: list[RawItem] = field(default_factory=list)
    idx: list[int] = field(default_factory=list)
    copies: set[str] = field(default_factory=set)  # id материалов-копий
    keys: set[str] = field(default_factory=set)


def _text(item: RawItem) -> str:
    body = item.lead or (item.text or "")[:400]
    return f"{item.title}. {body}"


def story_id(first_item_id: str) -> str:
    return "st_" + hashlib.sha1(first_item_id.encode()).hexdigest()[:12]


def cluster_items(items: list[RawItem], *, entity_keys: list[set[str]] | None = None) -> list[_Draft]:
    """Жадная склейка материалов в порядке публикации. Детерминирована при одинаковом входе."""
    items = sorted(items, key=lambda i: (i.published_at, i.id))
    if not items:
        return []
    texts = [_text(i) for i in items]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)
    x = vec.fit_transform(texts)
    sim = (x @ x.T).toarray()  # строки TF-IDF нормированы → это косинус
    keys = entity_keys if entity_keys is not None else [extract_entities(t).keys() for t in texts]

    drafts: list[_Draft] = []
    story_of: dict[int, int] = {}
    for i, item in enumerate(items):
        # 1. копия более раннего материала — сразу в его сюжет
        copy_of = next(
            (
                j
                for j in range(i - 1, -1, -1)
                if items[i].published_at - items[j].published_at <= WINDOW
                and item.simhash is not None
                and (other := items[j].simhash) is not None
                and hamming(item.simhash, other) <= SIMHASH_COPY
            ),
            None,
        )
        if copy_of is not None:
            d = drafts[story_of[copy_of]]
            d.items.append(item)
            d.idx.append(i)
            d.copies.add(item.id)
            d.keys |= keys[i]
            story_of[i] = story_of[copy_of]
            continue
        # 2. лучший открытый сюжет по средней похожести на все его материалы
        best, best_sim = None, 0.0
        for s, d in enumerate(drafts):
            if item.published_at - d.items[-1].published_at > WINDOW:
                continue
            avg = float(np.mean(sim[i, d.idx]))
            shared = bool(keys[i] & d.keys)
            # без общей сущности — только почти одинаковый текст и только если у одной из сторон
            # сущностей нет: разные компании в шаблонных заголовках («X объявила о дивидендах»)
            # — это разные события
            unknown = not keys[i] or not d.keys
            ok = (avg >= SIM_THRESHOLD and shared) or (unknown and avg >= SIM_NO_ENTITY)
            if ok and avg > best_sim:
                best, best_sim = s, avg
        if best is None:
            drafts.append(_Draft(items=[item], idx=[i], keys=set(keys[i])))
            story_of[i] = len(drafts) - 1
        else:
            d = drafts[best]
            d.items.append(item)
            d.idx.append(i)
            d.keys |= keys[i]
            story_of[i] = best
    return drafts


def to_story(d: _Draft, sources: dict) -> Story:
    first = d.items[0]
    independent = {i.source_id for i in d.items if i.id not in d.copies}
    tiers = {s: sources[s].tier if s in sources else 3 for s in independent}
    official = any(sources[s].official for s in independent if s in sources)
    strong = sum(1 for t in tiers.values() if t <= 1)
    rumor = any(RUMOR.search(_text(i)) for i in d.items)
    if official or strong >= 2:
        status = "confirmed"
    elif rumor:
        status = "rumor"
    elif len(independent) == 1:
        status = "single"
    else:
        status = "unconfirmed"
    # заголовок — от самого сильного источника, при равенстве — от самого раннего
    head = min(d.items, key=lambda i: (tiers.get(i.source_id, 3), i.published_at))
    return Story(
        id=story_id(first.id),
        title=head.title,
        status=status,
        item_ids=[i.id for i in d.items],
        corroboration=len(independent),
        source_ids=sorted({i.source_id for i in d.items}),
        first_seen=first.published_at,
        last_seen=max(i.published_at for i in d.items),
        first_source_id=first.source_id,
        entities=sorted(k.split(":", 1)[1] for k in d.keys)[:30],
    )


def rebuild(conn: Connection, hours: float = 72, now: datetime | None = None) -> dict:
    """Пересобирает сюжеты по неотсеянным материалам за окно. Сюжеты, начавшиеся раньше окна, не трогает."""
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=hours)
    rows = conn.execute(
        select(m.raw_items.c.data).where(
            m.raw_items.c.published_at >= since, m.raw_items.c.dropped_reason.is_(None)
        )
    ).scalars()
    items = [RawItem.model_validate(r) for r in rows]
    sources = {s.id: s for s in load_sources()}
    drafts = cluster_items(items)
    stories = [to_story(d, sources) for d in drafts]

    old_ids = set(conn.execute(select(m.stories.c.id).where(m.stories.c.first_seen >= since)).scalars())
    new_ids = {s.id for s in stories}
    # версию ведёт экстрактор (существенное изменение фактов) — пересборка её не сбрасывает
    versions = dict(
        conn.execute(select(m.stories.c.id, m.stories.c.version).where(m.stories.c.id.in_(new_ids))).all()
    )
    for s in stories:
        s.version = versions.get(s.id) or 1
    if old_ids - new_ids:
        conn.execute(
            update(m.raw_items).where(m.raw_items.c.story_id.in_(old_ids - new_ids)).values(story_id=None)
        )
        conn.execute(delete(m.event_facts).where(m.event_facts.c.story_id.in_(old_ids - new_ids)))
        conn.execute(delete(m.stories).where(m.stories.c.id.in_(old_ids - new_ids)))
    for s in stories:
        row = {
            "id": s.id,
            "title": s.title,
            "event_type": s.event_type,
            "status": s.status,
            "corroboration": s.corroboration,
            "first_seen": s.first_seen,
            "last_seen": s.last_seen,
            "version": s.version,
            "data": s.model_dump(mode="json"),
        }
        stmt = insert(m.stories).values(row)
        keep = {"id", "event_type", "version"}  # тип события и версию ведут экстрактор и обновление сюжета
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["id"], set_={k: v for k, v in row.items() if k not in keep}
            )
        )
        conn.execute(update(m.raw_items).where(m.raw_items.c.id.in_(s.item_ids)).values(story_id=s.id))
    by_status: dict[str, int] = {}
    for s in stories:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    multi = sum(1 for s in stories if len(s.item_ids) > 1)
    return {"items": len(items), "stories": len(stories), "multi_item": multi, **by_status}


def main() -> None:
    from backend.db.engine import get_engine

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72)
    args = ap.parse_args()
    t0 = time.monotonic()
    with get_engine().begin() as conn:
        stats = rebuild(conn, args.hours)
    log.info("склейка за %.0f ч: %s (%.1f с)", args.hours, stats, time.monotonic() - t0)


if __name__ == "__main__":
    main()
