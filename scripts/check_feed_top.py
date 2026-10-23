"""Проверка ленты на своей базе (критерии А1 и А2): топ-N по `score_pair` за последние 72 ч.

`uv run python scripts/check_feed_top.py [--company U_DEMO_BORROWER] [--role cfo] [--n 20]`

Считает так же, как лента API (`PipelineCardStore._build_feed`): профиль v1, сюрприз кодом, `score_pair`.
Печатает тип события, статус и заголовок; отдельно — сюрприз по сюжетам о решении ЦБ.
"""

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows — cp1251

from backend.agents.steps import profile_v1  # noqa: E402
from backend.agents.tools.db_source import DbDataSource  # noqa: E402
from backend.db.engine import get_engine  # noqa: E402
from backend.ranking.score import score_pair  # noqa: E402
from backend.ranking.surprise import find_surprise  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="U_DEMO_BORROWER")
    ap.add_argument("--role", default="cfo")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--hours", type=float, default=72)
    args = ap.parse_args()
    src = DbDataSource(get_engine())
    profile = profile_v1(src, args.company)
    since = datetime.now(UTC) - timedelta(hours=args.hours)
    facts = {f.story_id: f for f in src.event_facts()}
    sources = {s.id: s for s in src.sources()}
    graph = src.counterparties(args.company)
    positions, market = src.bank_positions(), src.market_knowledge()
    rows = []
    for story in src.stories():
        if story.last_seen < since:
            continue
        f = facts.get(story.id)
        surprise = find_surprise(story, f, bank_positions=positions, market=market)
        rel = score_pair(
            story, profile, now=datetime.now(UTC), facts=f, graph=graph, sources=sources,
            surprise=surprise, role=args.role,
        )
        rows.append((rel.score, story, f, surprise))
    rows.sort(key=lambda r: -r[0])
    print(f"{args.company}/{args.role}: сюжетов за {args.hours:g} ч — {len(rows)}; топ-{args.n}:")
    for i, (score, story, f, surprise) in enumerate(rows[: args.n], 1):
        et = f.event_type if f else story.event_type
        llm = "LLM" if f and f.extractor_model and not f.extractor_model.startswith("rules") else "правила"
        print(f"{i:2}. {score:.3f}  {et:<22} {llm:<7} {story.status:<11} {story.title[:90]}")
        if surprise:
            print(f"      сюрприз: рынок ждал {surprise.market_expected} ({surprise.market_ref}), "
                  f"банк ждал {surprise.bank_expected}, вышло {surprise.actual} → {surprise.direction}")
    rate = [r for r in rows if r[2] and r[2].event_type == "rate_decision" and r[3]]
    for score, story, _f, s in rate[:3]:
        place = next(i for i, r in enumerate(rows, 1) if r[1].id == story.id)
        print(f"решение ЦБ: место {place}, {score:.3f}, «{story.title[:80]}»")
        print(f"      рынок ждал {s.market_expected}, вышло {s.actual} ({s.direction})")


if __name__ == "__main__":
    main()
