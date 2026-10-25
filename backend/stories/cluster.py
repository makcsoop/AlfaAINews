"""Склейка материалов в сюжеты и статус подтверждения.

`python -m backend.stories.cluster [--hours 72]` — пересобрать сюжеты по материалам за окно.

1. Копии: SimHash, расстояние Хэмминга ≤ 3 — присоединяются к оригиналу и не считаются
   независимым подтверждением.
2. Похожесть: символьный TF-IDF (запасной путь из ТЗ; эмбеддинги USER-bge-m3 — если организаторы
   разрешат энкодеры). Материал сравнивается с сюжетом целиком — среднее по всем материалам сюжета,
   а не самый похожий материал: иначе цепочка похожих склеивает разные события.
3. Присоединение: похожесть ≥ порога и хотя бы одна общая сущность (Natasha + словари), окно 72 ч.
   Одно решение ЦБ — один сюжет; прогнозы и заявления ЦБ того же дня вливаются в сюжет решения.
   Второй проход: пересказы одного события разными словами (похожесть ≥ 0,20, ниже порога) сливаются,
   если у них общая отличительная сущность (не регулятор) и общее число из заголовка или лида («200%»).
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
from backend.ingest.private import PASTE_SOURCE_ID
from backend.ingest.registry import load_sources
from backend.schemas import RawItem, Story

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=72)
SIM_THRESHOLD = 0.30  # ТЗ: символьный TF-IDF ≥ 0,30; подбирается на размеченных парах (день 4)
SIM_NO_ENTITY = 0.55  # без сущностей у одной из сторон склеиваем только почти одинаковые тексты
# второй проход: общая отличительная сущность + общее число + похожесть ≥ SIM_RELATED в окне RELATED_WINDOW.
# Порог SIM_THRESHOLD глобально не снижается: «Трамп и Канада» против «Трамп, Россия и Украина» — 0,185
SIM_RELATED = 0.20
RELATED_WINDOW = timedelta(hours=36)
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


def _chunks(ids: list[str], size: int = 5000) -> list[list[str]]:
    return [ids[k : k + size] for k in range(0, len(ids), size)]


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
    drafts = merge_decisions(drafts)
    return merge_related(drafts, sim, keys, [_numbers(t) for t in texts])


# решение Банка России по ставке: «ЦБ сохранил ключевую ставку на уровне 14%», «ставка осталась 14%»
# глаголы — только прошедшее время: «сохранится долго» — прогноз, не решение
_PAST = (
    r"(сохранил\w*|сохранена|оставил\w*|оставлена|осталась|снизил\w*|снижена|понизил\w*|повысил\w*"
    r"|повышена)"
)
_RATE_DECISION = re.compile(
    rf"ключев\w* ставк\w*.{{0,60}}?\b{_PAST}|\b({_PAST}|принял\w* решение \w+)\s+ключев\w* ставк",
    re.IGNORECASE,
)
# решение ЦБ в придаточном: «Сбербанк снизил ставки по вкладам после того, как ЦБ сохранил…»
_SUBORDINATE = re.compile(
    r"после того,? как|вслед за|на фоне (решени|сохранени|снижени|повышени)", re.IGNORECASE
)
# ставка — число после «до», «на уровне», «ставку»: «снизил на 0,5% до 16%» → 16, «при инфляции 6% … 14%» → 14
_RATE_VALUE = re.compile(
    r"(?:\bдо|уровн\w*|ставк\w*|\bна)\s+(\d{1,2}(?:[,.]\d{1,2})?)\s*%(?!\s*(?:г/г|годовых\s+инфляц))",
    re.IGNORECASE,
)
_MSK = timedelta(hours=3)
# пресс-события ЦБ в день решения: прогнозы, заявление Председателя, пресс-конференция.
# Чужие прогнозы («Сбербанк ухудшил прогноз») сюда не попадают: субъект — ЦБ или Набиуллина
_CB = r"(?:(?<![\w-])ЦБ(?:\s+РФ)?(?![\w-])|Центробанк\w*|Банк\w* России)"
_NABIULLINA = r"(?:(?:Эльвир\w+\s+)?Набиуллин\w*)"
_CBR_PRESS = re.compile(
    rf"{_CB}\s+(?:\S+\s+){{0,3}}?(?:повысил|понизил|ухудшил|улучшил|сузил|расширил|скорректировал"
    rf"|пересмотрел|сохранил|изменил|прогнозиру|ожида|дал)\w*\s+(?:\S+\s+){{0,2}}?прогноз"
    rf"|{_CB}\s+прогнозиру|прогнозиру\w+\s+{_CB}|прогноз\w*\s+(?:ЦБ|Банка России|Центробанка)"
    rf"|заявлени\w+\s+(?:Председател\w+|{_NABIULLINA})|{_NABIULLINA}\s*:"
    rf"|(?:заявил|сообщил|отметил|рассказал|пояснил)\w*\s+(?:(?:глав|председател)\w+\s+(?:ЦБ|Банка России"
    rf"|регулятора)|{_NABIULLINA})|пресс-конференци\w*[^.!?]{{0,60}}(?:ЦБ|Банка России|{_NABIULLINA})"
    rf"|{_NABIULLINA}[^.!?]{{0,80}}пресс-конференц",
    re.IGNORECASE,
)


def cbr_press_day(item: RawItem) -> str | None:
    """Московская дата пресс-события ЦБ (прогноз, заявление, пресс-конференция) по заголовку; иначе None."""
    from backend.extract.rules import _FOREIGN_CB

    if item.source_id == PASTE_SOURCE_ID:
        return None
    t = item.title
    if not _CBR_PRESS.search(t) or _FOREIGN_CB.search(t) or _SUBORDINATE.search(t):
        return None
    return (item.published_at + _MSK).date().isoformat()


def decision_key(item: RawItem, title_only: bool = False) -> str | None:
    """Ключ решения ЦБ: дата по Москве и ставка из заголовка или лида; анонсы и ожидания — без ключа."""
    from backend.extract.rules import _ANNOUNCE, _DECIDED, _FOREIGN_CB

    if item.source_id == PASTE_SOURCE_ID:
        return None  # дата вставки — время вставки, а не публикации решения
    for part in (item.title,) if title_only else (item.title, item.lead or ""):
        if not _RATE_DECISION.search(part) or _FOREIGN_CB.search(part) or _SUBORDINATE.search(part):
            continue
        if _ANNOUNCE.search(part) and not _DECIDED.search(part):
            continue
        values = [m for m in _RATE_VALUE.finditer(part)]
        # «на 0,5% до 16%»: «на» — шаг; берём число после «до», «уровне», «ставку», «на» — в последнюю очередь
        named = [m for m in values if not m.group(0).lower().startswith("на ")]
        day = (item.published_at + _MSK).date().isoformat()
        if found := (named or values):
            return f"{day}:{float(found[-1].group(1).replace(',', '.')):g}"
        return f"{day}:?"  # «Банк России принял решение по ключевой ставке» — без числа в заголовке
    return None


def merge_decisions(drafts: list[_Draft]) -> list[_Draft]:
    """Одно решение ЦБ — один сюжет: сюжеты с одним ключом решения сливаются в самый крупный.

    Склейка по TF-IDF разводит «ЦБ сохранил ставку 14%» и «Банк России принял решение сохранить
    ключевую ставку на уровне 14,00% годовых» по разным сюжетам — в ленте это выглядело дублями.
    """
    # сюжеты с общим ключом — одна группа (union-find): в сюжете может оказаться и материал другого дня
    parent = list(range(len(drafts)))

    def root(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    owner: dict[str, int] = {}
    for n, d in enumerate(drafts):
        # Банк России принимает не больше одного решения в день: группа — по дню, ставка в ключе — для журнала
        for k in {k.split(":")[0] for i in d.items if (k := decision_key(i))}:
            if k in owner:
                parent[root(n)] = root(owner[k])
            else:
                owner[k] = n
    # прогнозы и заявления ЦБ в день решения — часть решения (среднесрочный прогноз, пресс-конференция)
    for n, d in enumerate(drafts):
        for day in {k for i in d.items if (k := cbr_press_day(i))}:
            if day in owner:
                parent[root(n)] = root(owner[day])
    groups: dict[int, list[int]] = {}
    for n in range(len(drafts)):
        groups.setdefault(root(n), []).append(n)
    absorbed: set[int] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        main, *rest = sorted(group, key=lambda n: (-len(drafts[n].items), drafts[n].items[0].published_at))
        for n in rest:
            _absorb(drafts[main], drafts[n])
            absorbed.add(n)
    return [d for n, d in enumerate(drafts) if n not in absorbed]


def _absorb(target: _Draft, src: _Draft) -> None:
    """Материалы src переходят в target; порядок — по времени публикации."""
    target.items += src.items
    target.idx += src.idx
    target.copies |= src.copies
    target.keys |= src.keys
    its = target.items
    order = sorted(range(len(its)), key=lambda k: (its[k].published_at, its[k].id))
    target.items = [target.items[k] for k in order]
    target.idx = [target.idx[k] for k in order]


_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b")
_MONTHS = r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
_NUMBER = re.compile(rf"(?<![\d.,])(\d+(?:[.,]\d+)?)(?![.,]?\d)(\s*%)?(?!\s*{_MONTHS})", re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    """Отличительные числа текста: «200%», «12,5», «117». Годы, даты и целые < 100 без % — не в счёт."""
    out: set[str] = set()
    for mt in _NUMBER.finditer(_DATE.sub(" ", text)):
        raw, pct = mt.group(1).replace(",", "."), bool(mt.group(2))
        v = float(raw)
        # целые без % меньше 100 — это дни, туры, «Ми-24», «12 тыс.»: совпадают у разных событий
        if not pct and raw.isdigit() and (v < 100 or 1900 <= v <= 2100):
            continue
        out.add(f"{v:g}{'%' if pct else ''}")
    # «200%-ных» и «200% пошлин» — одно число; «200» без знака тоже совпадёт с «200%»
    return out | {n.rstrip("%") for n in out}


def _distinctive(keys: set[str]) -> set[str]:
    """Сущности, которые различают события: регуляторы (Банк России, Минфин) есть почти везде."""
    from backend.extract.entities import regulators

    common = {f"org:{r}" for r in regulators()}
    return keys - common


def merge_related(
    drafts: list[_Draft], sim: np.ndarray, keys: list[set[str]], numbers: list[set[str]]
) -> list[_Draft]:
    """Второй проход: сливает пересказы одного события, которые жадная склейка развела по порогу 0,30.

    Условия для пары сюжетов: окно 36 ч, общая отличительная сущность, общее число из заголовка или лида
    и средняя похожесть всех пар материалов ≥ SIM_RELATED. Пары перебираются от самой похожей; условия
    перепроверяются на уже слитых группах, чтобы слабая цепочка не склеила разные события.
    """
    if len(drafts) < 2:
        return drafts

    # числа — только из первого материала каждого исходного сюжета: у крупного сюжета (сводки ПВО за 3 дня)
    # объединение чисел велико и совпадает с чем угодно
    heads: dict[int, set[str]] = {id(d): set(numbers[d.idx[0]]) for d in drafts}

    def feats(d: _Draft) -> tuple[set[str], set[str]]:
        return _distinctive(set().union(*(keys[i] for i in d.idx))), heads[id(d)]

    def close(a: _Draft, b: _Draft) -> bool:
        first = max(a.items[0].published_at, b.items[0].published_at)
        last = min(a.items[-1].published_at, b.items[-1].published_at)
        return first - last <= RELATED_WINDOW

    def avg(a: _Draft, b: _Draft) -> float:
        return float(np.mean(sim[np.ix_(a.idx, b.idx)]))

    by_key: dict[str, list[int]] = {}
    fs = [feats(d) for d in drafts]
    for n, (ks, _) in enumerate(fs):
        for k in ks:
            by_key.setdefault(k, []).append(n)
    candidates: list[tuple[float, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for ns in by_key.values():
        for x, a in enumerate(ns):
            for b in ns[x + 1 :]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                da, db = drafts[a], drafts[b]
                if fs[a][1] & fs[b][1] and close(da, db) and (s := avg(da, db)) >= SIM_RELATED:
                    candidates.append((s, a, b))
    if not candidates:
        return drafts

    parent = list(range(len(drafts)))

    def root(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for _, a, b in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        ra, rb = root(a), root(b)
        if ra == rb:
            continue
        da, db = drafts[ra], drafts[rb]
        (ka, na), (kb, nb) = feats(da), feats(db)
        if ka & kb and na & nb and close(da, db) and avg(da, db) >= SIM_RELATED:
            main, other = (ra, rb) if (-len(da.items), da.items[0].published_at) <= (
                -len(db.items), db.items[0].published_at) else (rb, ra)
            _absorb(drafts[main], drafts[other])
            heads[id(drafts[main])] |= heads[id(drafts[other])]
            parent[other] = main
    return [d for n, d in enumerate(drafts) if root(n) == n]


def publisher_of(source_id: str, sources: dict) -> str:
    """Издатель ленты из config/sources.yaml; без поля — сама лента."""
    src = sources.get(source_id)
    return (src.publisher if src is not None and src.publisher else None) or source_id


def to_story(d: _Draft, sources: dict) -> Story:
    first = d.items[0]
    # вставка пользователя (/analyze) — не независимый источник: подтверждать сюжет она не может
    independent = {i.source_id for i in d.items if i.id not in d.copies and i.source_id != PASTE_SOURCE_ID}
    tiers = {s: sources[s].tier if s in sources else 3 for s in independent}
    official = any(sources[s].official for s in independent if s in sources)
    # независимость — по издателю: сайт и Telegram «Коммерсанта», пресс-релизы и события ЦБ — один источник
    publishers = {publisher_of(s, sources) for s in independent}
    strong = len({publisher_of(s, sources) for s, t in tiers.items() if t <= 1})
    rumor = any(RUMOR.search(_text(i)) for i in d.items)
    if official or strong >= 2:
        status = "confirmed"
    elif rumor:
        status = "rumor"
    elif len(publishers) <= 1:
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
        corroboration=len(publishers),
        source_ids=sorted({i.source_id for i in d.items}),
        first_seen=first.published_at,
        last_seen=max(i.published_at for i in d.items),
        first_source_id=first.source_id,
        entities=sorted(k.split(":", 1)[1] for k in d.keys)[:30],
    )


def _assign_ids(drafts: list[_Draft], prior: dict[str, str], first_seen: dict[str, datetime]) -> list[str]:
    """id сюжета переживает пересборку: черновик наследует сюжет, с которым у него больше общих материалов.

    Каждый прежний сюжет достаётся одному черновику (больше общих материалов, при равенстве — раньше начатый
    сюжет). Без наследника — новый id от первого материала; совпал с занятым — от следующего материала.
    """
    pairs = []
    for n, d in enumerate(drafts):
        votes: dict[str, int] = {}
        for i in d.items:
            if sid := prior.get(i.id):
                votes[sid] = votes.get(sid, 0) + 1
        late = datetime.max.replace(tzinfo=UTC)
        pairs += [(-cnt, first_seen.get(sid, late), sid, n) for sid, cnt in votes.items()]
    ids: list[str | None] = [None] * len(drafts)
    taken: set[str] = set()
    for _, _, sid, n in sorted(pairs):
        if ids[n] is None and sid not in taken:
            ids[n] = sid
            taken.add(sid)
    for n, d in enumerate(drafts):
        if ids[n] is None:
            free = (c for i in d.items if (c := story_id(i.id)) not in taken)
            sid = next(free, story_id(d.items[0].id + "#"))
            ids[n] = sid
            taken.add(sid)
    return [sid for sid in ids if sid is not None]


def rebuild(conn: Connection, hours: float = 72, now: datetime | None = None) -> dict:
    """Пересобирает сюжеты по неотсеянным материалам за окно.

    id сюжета стабилен: черновик наследует прежний сюжет по общим материалам, поэтому выход первого
    материала из окна или запоздавший материал с ранней датой не плодят дубли и не теряют факты и тип.
    Материалы унаследованного сюжета, вышедшие из окна, остаются в нём. Прежний сюжет без наследника
    удаляется, если весь был в окне, иначе в нём остаются только материалы вне окна.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=hours)
    rows = conn.execute(
        select(m.raw_items.c.data).where(
            m.raw_items.c.published_at >= since, m.raw_items.c.dropped_reason.is_(None)
        )
    ).scalars()
    # вставки пользователей личные: в общие сюжеты не склеиваются, их сюжеты пересборка не трогает
    items = [i for i in (RawItem.model_validate(r) for r in rows) if i.source_id != PASTE_SOURCE_ID]
    in_window = {i.id for i in items}
    sources = {s.id: s for s in load_sources()}
    drafts = cluster_items(items)

    prior: dict[str, str] = {}
    for chunk in _chunks(sorted(in_window)):
        prior.update(
            conn.execute(
                select(m.raw_items.c.id, m.raw_items.c.story_id).where(
                    m.raw_items.c.id.in_(chunk), m.raw_items.c.story_id.is_not(None)
                )
            ).all()
        )
    touched = set(prior.values())
    old_rows = {
        r.id: r
        for r in conn.execute(
            select(m.stories.c.id, m.stories.c.first_seen, m.stories.c.event_type, m.stories.c.data).where(
                m.stories.c.id.in_(touched)
            )
        )
    }
    ids = _assign_ids(drafts, prior, {sid: r.first_seen for sid, r in old_rows.items()})

    # материалы прежних сюжетов, вышедшие из окна: остаются в своём сюжете
    outside_ids = {
        iid for r in old_rows.values() for iid in r.data.get("item_ids", []) if iid not in in_window
    }
    outside_rows = conn.execute(select(m.raw_items.c.data).where(m.raw_items.c.id.in_(outside_ids))).scalars()
    outside = {
        it.id: it
        for it in (RawItem.model_validate(d) for d in outside_rows)
        if it.source_id != PASTE_SOURCE_ID
    }

    def kept_outside(sid: str) -> list[RawItem]:
        r = old_rows.get(sid)
        return [outside[i] for i in (r.data.get("item_ids", []) if r else []) if i in outside]

    stories = []
    for sid, d in zip(ids, drafts, strict=True):
        if old := kept_outside(sid):
            d = _Draft(
                items=sorted(old + d.items, key=lambda i: (i.published_at, i.id)),
                copies=d.copies,
                keys=d.keys,
            )
        s = to_story(d, sources)
        s.id = sid
        if sid in old_rows:  # тип события ведёт экстрактор: пересборка его не сбрасывает
            s.event_type = old_rows[sid].event_type
        stories.append(s)
    new_ids = {s.id for s in stories}
    # прежний сюжет без наследника, но с материалами вне окна — остаётся с ними
    for sid in sorted(touched - new_ids):
        if old := kept_outside(sid):
            s = to_story(_Draft(items=sorted(old, key=lambda i: (i.published_at, i.id))), sources)
            s.id = sid
            s.event_type = old_rows[sid].event_type
            s.entities = old_rows[sid].data.get("entities", [])
            stories.append(s)
            new_ids.add(sid)

    recent = conn.execute(select(m.stories.c.id, m.stories.c.data).where(m.stories.c.first_seen >= since))
    # личный сюжет вставки (только user_paste) склейка не ведёт и не удаляет
    old_ids = {sid for sid, data in recent if (data or {}).get("source_ids") != [PASTE_SOURCE_ID]}
    old_ids |= touched
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
