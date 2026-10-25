"""Метрики отбора на размеченных парах «сюжет × клиент» против бейзлайна «свежесть + вес источника».

nDCG@5 и precision@5 считаются по каждому клиенту (его пары ранжируются между собой), затем усредняются.
Эталон пары — доля разметчиков, отметивших «важно» (≥ 0,5 — важная). Пропуск — важная пара, которую
система не поставила в топ-5 клиента или отсекла порогом ленты; отдельно по отраслям, которые аналитики
рынка покрывают (≥ coverage_min независимых каналов), и по непокрытым (У18).

`python -m backend.eval.metrics labels.csv` — эталон из выгрузки /labeling/export.csv, сюжеты и клиенты
из базы, отчёт — reports/metrics.md.
"""

import csv
import math
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
    # nDCG и precision отдельно по клиентам из покрытых и непокрытых отраслей (У18):
    # covered → (клиентов, nDCG, nDCG бейзлайна, precision, precision бейзлайна)
    by_coverage: dict[bool, tuple[int, float, float, float, float]] = field(default_factory=dict)


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


def _ranked(group: Sequence[PairEval], k: int) -> tuple[float | None, float | None, float, float]:
    # равные оценки — по свежести и качеству, как в ленте (rank: score, затем freshness)
    sys_order = sorted(group, key=lambda p: (-p.system, -p.baseline, p.story_id))
    base_order = sorted(group, key=lambda p: (-p.baseline, p.story_id))
    gs, gb = [p.gold for p in sys_order], [p.gold for p in base_order]
    return ndcg_at(gs, k), ndcg_at(gb, k), precision_at(gs, k), precision_at(gb, k)


def evaluate(pairs: Sequence[PairEval], *, k: int = K, cfg: ScoringConfig | None = None) -> Metrics:
    min_score = (cfg or load_scoring()).feed.min_score
    by_client: dict[str, list[PairEval]] = defaultdict(list)
    for p in pairs:
        by_client[p.company_id].append(p)
    rows: dict[bool, list[tuple[float | None, float | None, float, float]]] = defaultdict(list)
    misses = {True: [0, 0], False: [0, 0]}
    for group in by_client.values():
        covered = group[0].covered  # отрасль у клиента одна — покрытие общее на все его пары
        rows[covered].append(_ranked(group, k))
        top = {p.pair_id for p in sorted(group, key=lambda p: (-p.system, -p.baseline, p.story_id))[:k]}
        for p in group:
            if p.gold >= 0.5:
                misses[p.covered][1] += 1
                if p.pair_id not in top or p.system < min_score:
                    misses[p.covered][0] += 1

    def summary(rs: Sequence[tuple[float | None, float | None, float, float]]) -> tuple[float, ...]:
        return (
            _mean([r[0] for r in rs if r[0] is not None]),
            _mean([r[1] for r in rs if r[1] is not None]),
            _mean([r[2] for r in rs]),
            _mean([r[3] for r in rs]),
        )

    nd, nd_b, pr, pr_b = summary([r for rs in rows.values() for r in rs])
    return Metrics(
        pairs=len(pairs),
        clients=len(by_client),
        ndcg=nd,
        ndcg_baseline=nd_b,
        precision=pr,
        precision_baseline=pr_b,
        misses_covered=(misses[True][0], misses[True][1]),
        misses_uncovered=(misses[False][0], misses[False][1]),
        by_coverage={c: (len(rs), *summary(rs)) for c, rs in rows.items()},  # type: ignore[misc]
    )


def interpret(m: Metrics) -> list[str]:
    """3–5 строк: где скоринг лучше бейзлайна, где хуже и почему (для отчёта и слайда)."""

    def f(x: float) -> str:  # 0,81 — запятая, как в остальном тексте
        return f"{x:.2f}".replace(".", ",")

    def cmp(a: float, b: float) -> str:
        return "лучше" if a > b + 0.02 else "хуже" if a < b - 0.02 else "на уровне"

    out = [
        f"В целом скоринг {cmp(m.ndcg, m.ndcg_baseline)} бейзлайна по nDCG@{K} "
        f"({f(m.ndcg)} против {f(m.ndcg_baseline)}) и {cmp(m.precision, m.precision_baseline)} "
        f"по precision@{K} ({f(m.precision)} против {f(m.precision_baseline)}): "
        "бейзлайн не знает клиента — свежая новость "
        "для всех одна, скоринг поднимает то, что задевает продукты, отрасль и контрагентов компании."
    ]
    for covered, name in ((True, "покрытых аналитиками рынка"), (False, "непокрытых")):
        if covered in m.by_coverage:
            n, nd, nd_b, _, _ = m.by_coverage[covered]
            out.append(
                f"В {name} отраслях (клиентов: {n}) — {cmp(nd, nd_b)} бейзлайна: "
                f"nDCG {f(nd)} против {f(nd_b)}."
            )
    missed = m.misses_covered[0] + m.misses_uncovered[0]
    total = m.misses_covered[1] + m.misses_uncovered[1]
    if total:
        out.append(
            f"Пропущено важного: {missed} из {total}, больше — в непокрытых отраслях: приоров аналитиков "
            "там нет, работает только экспертная матрица (У18)."
            if m.misses_uncovered[0] > m.misses_covered[0]
            else f"Пропущено важного: {missed} из {total}."
        )
    return out


def gold_from_csv(path: Path) -> dict[str, tuple[str, str, float]]:
    """pair_id → (story_id, company_id, доля «важно»)."""
    votes: dict[str, list[int]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            votes[r["pair_id"]].append(int(r["important"]))
            meta[r["pair_id"]] = (r["story_id"], r["company_id"])
    return {pid: (*meta[pid], sum(v) / len(v)) for pid, v in votes.items()}


def covered_sectors(rows: Sequence[Mapping], min_channels: int = COVERAGE_MIN) -> set[str]:
    """Отрасли, о которых писали не меньше min_channels независимых каналов аналитиков."""
    channels: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        for sector in r.get("sectors_okved") or []:
            channels[sector].add(r.get("source_id", ""))
    return {s for s, ch in channels.items() if len(ch) >= min_channels}


def format_report(m: Metrics, *, note: str = "") -> str:
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
        "| Пропуски важного (не в топ-5 или ниже порога ленты) | |",
        "|---|---|",
        f"| отрасли, покрытые аналитиками рынка | {miss(m.misses_covered)} |",
        f"| непокрытые отрасли | {miss(m.misses_uncovered)} |",
    ]
    if m.by_coverage:
        lines += [
            "",
            "| Отрасли клиентов | Клиентов | nDCG@5 | бейзлайн | precision@5 | бейзлайн |",
            "|---|---|---|---|---|---|",
        ]
        for covered, name in ((True, "покрытые аналитиками"), (False, "непокрытые")):
            if covered in m.by_coverage:
                n, nd, nd_b, pr, pr_b = m.by_coverage[covered]
                lines.append(f"| {name} | {n} | {nd:.3f} | {nd_b:.3f} | {pr:.3f} | {pr_b:.3f} |")
    lines += ["", "## Интерпретация", "", *[f"- {x}" for x in interpret(m)]]
    if note:
        lines += ["", note]
    return "\n".join(lines) + "\n"


def load_pairs(labels_csv: Path) -> list[PairEval]:  # pragma: no cover — нужна живая база Р1
    from sqlalchemy import select

    from backend.db import models as m
    from backend.db.engine import get_engine
    from backend.ingest.registry import load_sources
    from backend.profile.exposures import with_exposures
    from backend.ranking.score import freshness, quality, score_pair
    from backend.schemas import CompanyProfile, CounterpartyGraph, EventFacts, Story

    gold = gold_from_csv(labels_csv)
    story_ids = {s for s, _, _ in gold.values()}
    company_ids = {c for _, c, _ in gold.values()}
    cfg = load_scoring()
    sources = {s.id: s for s in load_sources()}
    with get_engine().connect() as conn:
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
    for pid, (sid, cid, g) in gold.items():
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
    m = evaluate(load_pairs(labels))
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(format_report(m, note=f"Источник разметки: `{labels.name}`."), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
