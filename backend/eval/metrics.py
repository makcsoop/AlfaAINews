"""Метрики отбора на размеченных парах «сюжет × клиент» против бейзлайна «свежесть + вес источника».

nDCG@5 и precision@5 считаются по каждому клиенту (его пары ранжируются между собой), затем усредняются.
Эталон пары — доля разметчиков, отметивших «важно» (≥ 0,5 — важная). Пропуск — важная пара, которую
система не поставила в топ-5 клиента или отсекла порогом ленты; отдельно по отраслям, которые аналитики
рынка покрывают (≥ coverage_min независимых каналов), и по непокрытым (У18).

`python -m backend.eval.metrics labels.csv` — эталон из выгрузки /labeling/export.csv, сюжеты и клиенты
из базы, отчёт — reports/metrics.md.

Пересклейка меняет id сюжетов, а материалы остаются: если в CSV есть колонка `item_ids` (материалы сюжета
на момент разметки), пара без своего сюжета перепривязывается к сюжету, в котором сейчас эти материалы
(raw_items.story_id). Отчёт честно пишет, сколько пар найдено, перепривязано и потеряно.
"""

import csv
import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from backend.ranking.config import ScoringConfig, load_scoring

K = 5
COVERAGE_MIN = 5  # независимых каналов аналитиков на отрасль — правило У18
REPORT = Path(__file__).resolve().parents[2] / "reports" / "metrics.md"


@dataclass(frozen=True)
class PairEval:
    pair_id: str
    company_id: str
    story_id: str
    gold: float  # доля «важно» у разметчиков
    system: float  # score_pair
    baseline: float  # quality × freshness — без клиента
    covered: bool  # отрасль клиента покрыта аналитиками рынка


@dataclass(frozen=True)
class Metrics:
    pairs: int
    clients: int
    ndcg: float
    ndcg_baseline: float
    precision: float
    precision_baseline: float
    misses_covered: tuple[int, int]  # пропущено, всего важных
    misses_uncovered: tuple[int, int]
    # потолок precision@K: даже идеальный ранжировщик не выше, если важных пар у клиента меньше K
    precision_ceiling: float = 1.0


@dataclass(frozen=True)
class LinkStats:
    """Сколько размеченных пар нашлось в базе: по своему story_id, по материалам или никак."""

    total: int
    direct: int
    relinked: int

    @property
    def lost(self) -> int:
        return self.total - self.direct - self.relinked


def dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at(ranked: Sequence[float], k: int = K) -> float | None:
    """ranked — эталонные важности в порядке системы. None — у клиента нет важных пар."""
    ideal = dcg(sorted(ranked, reverse=True)[:k])
    return None if ideal == 0 else dcg(ranked[:k]) / ideal


def precision_at(ranked: Sequence[float], k: int = K) -> float:
    top = ranked[:k]
    return sum(g >= 0.5 for g in top) / len(top) if top else 0.0


def _mean(xs: Sequence[float]) -> float:
    return round(sum(xs) / len(xs), 3) if xs else 0.0


def evaluate(pairs: Sequence[PairEval], *, k: int = K, cfg: ScoringConfig | None = None) -> Metrics:
    min_score = (cfg or load_scoring()).feed.min_score
    by_client: dict[str, list[PairEval]] = defaultdict(list)
    for p in pairs:
        by_client[p.company_id].append(p)
    nd: list[float] = []
    nd_b: list[float] = []
    pr: list[float] = []
    pr_b: list[float] = []
    ceil: list[float] = []
    misses = {True: [0, 0], False: [0, 0]}
    for group in by_client.values():
        # равные оценки — по свежести и качеству, как в ленте (rank: score, затем freshness)
        sys_order = sorted(group, key=lambda p: (-p.system, -p.baseline, p.story_id))
        base_order = sorted(group, key=lambda p: (-p.baseline, p.story_id))
        for order, nd_list, pr_list in ((sys_order, nd, pr), (base_order, nd_b, pr_b)):
            gains = [p.gold for p in order]
            if (v := ndcg_at(gains, k)) is not None:
                nd_list.append(v)
            pr_list.append(precision_at(gains, k))
        important = sum(1 for p in group if p.gold >= 0.5)
        ceil.append(min(k, important) / min(k, len(group)))
        top = {p.pair_id for p in sys_order[:k]}
        for p in group:
            if p.gold >= 0.5:
                misses[p.covered][1] += 1
                if p.pair_id not in top or p.system < min_score:
                    misses[p.covered][0] += 1
    return Metrics(
        pairs=len(pairs),
        clients=len(by_client),
        ndcg=_mean(nd),
        ndcg_baseline=_mean(nd_b),
        precision=_mean(pr),
        precision_baseline=_mean(pr_b),
        misses_covered=(misses[True][0], misses[True][1]),
        misses_uncovered=(misses[False][0], misses[False][1]),
        precision_ceiling=_mean(ceil),
    )


def gold_from_csv(path: Path) -> dict[str, tuple[str, str, float]]:
    """pair_id → (story_id, company_id, доля «важно»)."""
    votes: dict[str, list[int]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            votes[r["pair_id"]].append(int(r["important"]))
            meta[r["pair_id"]] = (r["story_id"], r["company_id"])
    return {pid: (*meta[pid], sum(v) / len(v)) for pid, v in votes.items()}


def items_from_csv(path: Path) -> dict[str, list[str]]:
    """pair_id → материалы сюжета на момент разметки (колонка item_ids через пробел; нет колонки — пусто)."""
    out: dict[str, list[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if ids := (r.get("item_ids") or "").split():
                out[r["pair_id"]] = ids
    return out


def resolve_stories(
    gold: Mapping[str, tuple[str, str, float]],
    items: Mapping[str, Sequence[str]],
    existing: set[str],
    item_story: Mapping[str, str | None],
) -> tuple[dict[str, str], LinkStats]:
    """pair_id → текущий story_id: свой, если сюжет жив; иначе сюжет, где сейчас больше его материалов."""
    out: dict[str, str] = {}
    direct = relinked = 0
    for pid, (sid, _, _) in gold.items():
        if sid in existing:
            out[pid] = sid
            direct += 1
            continue
        now = [item_story.get(i) for i in items.get(pid, ())]
        counts: dict[str, int] = defaultdict(int)
        for st in now:
            if st:
                counts[st] += 1
        if counts:
            out[pid] = max(counts, key=lambda st: (counts[st], st))
            relinked += 1
    return out, LinkStats(total=len(gold), direct=direct, relinked=relinked)


def covered_sectors(rows: Sequence[Mapping], min_channels: int = COVERAGE_MIN) -> set[str]:
    """Отрасли, о которых писали не меньше min_channels независимых каналов аналитиков."""
    channels: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        for sector in r.get("sectors_okved") or []:
            channels[sector].add(r.get("source_id", ""))
    return {s for s, ch in channels.items() if len(ch) >= min_channels}


def format_report(m: Metrics, *, note: str = "", link: LinkStats | None = None) -> str:
    def miss(x: tuple[int, int]) -> str:
        return f"{x[0]} из {x[1]}" if x[1] else "—"

    lines = [
        "# Метрики отбора",
        "",
        f"Пар: {m.pairs}, клиентов: {m.clients}. Эталон — разметка «важно / неважно» (доля голосов ≥ 0,5).",
        "Бейзлайн — «свежесть + вес источника» без учёта клиента.",
        "",
        "| Метрика | Скоринг v0 | Бейзлайн |",
        "|---|---|---|",
        f"| nDCG@{K} | {m.ndcg:.3f} | {m.ndcg_baseline:.3f} |",
        f"| precision@{K} | {m.precision:.3f} | {m.precision_baseline:.3f} |",
        "",
        f"Потолок precision@{K} на этой разметке: {m.precision_ceiling:.3f} — столько получил бы идеальный "
        "ранжировщик (важных пар у клиента меньше пяти).",
        "",
        "| Пропуски важного (не в топ-5 или ниже порога ленты) | |",
        "|---|---|",
        f"| отрасли, покрытые аналитиками рынка | {miss(m.misses_covered)} |",
        f"| непокрытые отрасли | {miss(m.misses_uncovered)} |",
    ]
    if link is not None:
        lines += [
            "",
            f"Пар найдено в базе: {link.direct + link.relinked} из {link.total} "
            f"(по своему сюжету — {link.direct}, перепривязано по материалам после пересклейки — "
            f"{link.relinked}, потеряно — {link.lost}).",
        ]
        if link.lost > 0.1 * link.total:
            lines.append("**Внимание:** потеряно больше 10% пар — цифры выше считаются не на всей разметке.")
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def load_pairs(  # pragma: no cover — нужна живая база Р1
    labels_csv: Path, stats: list[LinkStats] | None = None
) -> list[PairEval]:
    from sqlalchemy import select

    from backend.db import models as m
    from backend.db.engine import get_engine
    from backend.ingest.registry import load_sources
    from backend.profile.exposures import with_exposures
    from backend.ranking.score import freshness, quality, score_pair
    from backend.schemas import CompanyProfile, CounterpartyGraph, EventFacts, Story

    gold = gold_from_csv(labels_csv)
    items = items_from_csv(labels_csv)
    company_ids = {c for _, c, _ in gold.values()}
    cfg = load_scoring()
    sources = {s.id: s for s in load_sources()}
    with get_engine().connect() as conn:
        wanted = {s for s, _, _ in gold.values()}
        existing = set(conn.execute(select(m.stories.c.id).where(m.stories.c.id.in_(wanted))).scalars())
        all_items = {i for ids in items.values() for i in ids}
        rows = conn.execute(
            select(m.raw_items.c.id, m.raw_items.c.story_id).where(m.raw_items.c.id.in_(all_items))
        ).all()
        item_story = {i: st for i, st in rows}
        linked, link = resolve_stories(gold, items, existing, item_story)
        if stats is not None:
            stats.append(link)
        story_ids = set(linked.values())
        stories = {
            d["id"]: Story.model_validate(d)
            for d in conn.execute(select(m.stories.c.data).where(m.stories.c.id.in_(story_ids))).scalars()
        }
        facts = {
            d["story_id"]: EventFacts.model_validate(d)
            for d in conn.execute(
                select(m.event_facts.c.data).where(m.event_facts.c.story_id.in_(story_ids))
            ).scalars()
        }
        profiles = {
            d["company_id"]: CompanyProfile.model_validate(d)
            for d in conn.execute(
                select(m.company_profiles.c.data).where(m.company_profiles.c.company_id.in_(company_ids))
            ).scalars()
        }
        graphs = {
            d["company_id"]: CounterpartyGraph.model_validate(d)
            for d in conn.execute(
                select(m.counterparty_graphs.c.data).where(
                    m.counterparty_graphs.c.company_id.in_(company_ids)
                )
            ).scalars()
        }
        knowledge = list(conn.execute(select(m.market_knowledge.c.data)).scalars())
    covered = covered_sectors(knowledge)
    # одно время оценки для всех пар — как будто лента собирается сразу после последнего сюжета
    now = max((s.last_seen for s in stories.values()), default=datetime.now()) + timedelta(hours=1)
    out = []
    for pid, (_, cid, g) in gold.items():
        sid = linked.get(pid, "")
        story, profile = stories.get(sid), profiles.get(cid)
        if story is None or profile is None:
            continue
        graph = graphs.get(cid)
        if not profile.exposures:
            profile = with_exposures(profile, graph)
        f = facts.get(sid)
        rel = score_pair(story, profile, now=now, facts=f, graph=graph, sources=sources, cfg=cfg)
        base = quality(story, f, sources, cfg) * freshness(story, now, cfg)
        out.append(PairEval(pid, cid, sid, g, rel.score, base, profile.sector in covered))
    return out


def main() -> None:  # pragma: no cover
    labels = Path(sys.argv[1])
    stats: list[LinkStats] = []
    m = evaluate(load_pairs(labels, stats))
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(
        format_report(m, note=f"Источник разметки: `{labels.name}`.", link=stats[0] if stats else None),
        encoding="utf-8",
    )
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
