"""Итог бюджета LLM по журналу вызовов (JSONL): за бюджетные сутки и за весь период.

Бюджетные сутки идут с 03:00 МСК до 03:00 МСК (`budget_day` из budget.py). Журнал пишет только свой учёт
(`cost_rub` по вызовам); расход ключа всей командой (`key_spend`) в журнал не попадает — это помечается
в отчёте. Резерв на день демо: лимит суток минус потрачено >= 150 ₽.

uv run python -m newsai.llm.report [--journal data/llm_journal] [--day YYYY-MM-DD] [--out reports/budget.md]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from newsai.llm.budget import budget_day
from newsai.llm.journal import JournalEntry
from newsai.llm.settings import LLMSettings

DEMO_RESERVE_RUB = 150.0
ANALYSIS_STEPS = frozenset({"planner", "analyst_business", "analyst_finance", "critic_llm"})
CARD_STEPS = frozenset({"card"})


def load_entries(directory: str | Path) -> list[JournalEntry]:
    entries: list[JournalEntry] = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(JournalEntry.model_validate_json(line))
            except ValueError:
                continue  # битая строка не должна ронять отчёт
    return entries


def _bucket() -> dict[str, float]:
    return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_rub": 0.0}


def _group(entries: Iterable[JournalEntry], key: Any) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(_bucket)
    for e in entries:
        b = out[key(e)]
        b["calls"] += 1
        b["tokens_in"] += e.tokens_in
        b["tokens_out"] += e.tokens_out
        b["cost_rub"] += e.cost_rub
    return {k: {m: round(v, 6) for m, v in b.items()} for k, b in sorted(out.items())}


def _avg_per_trace(entries: Sequence[JournalEntry], steps: frozenset[str]) -> dict[str, float | int | None]:
    """Средняя стоимость по trace_id, в которых были вызовы указанных шагов (стоимость всего trace_id)."""
    by_trace: dict[str, float] = defaultdict(float)
    hit: set[str] = set()
    for e in entries:
        by_trace[e.trace_id] += e.cost_rub
        if e.step in steps:
            hit.add(e.trace_id)
    costs = [by_trace[t] for t in hit]
    return {
        "traces": len(costs),
        "avg_rub": round(sum(costs) / len(costs), 6) if costs else None,
        "max_rub": round(max(costs), 6) if costs else None,
    }


def summarize_entries(entries: Sequence[JournalEntry]) -> dict[str, Any]:
    n = len(entries)
    total = sum(e.cost_rub for e in entries)
    return {
        "calls": n,
        "tokens_in": sum(e.tokens_in for e in entries),
        "tokens_out": sum(e.tokens_out for e in entries),
        "cost_rub": round(total, 6),
        "fallback_share": (sum(1 for e in entries if e.fallback_used) / n) if n else None,
        "cached_share": (sum(1 for e in entries if e.cached) / n) if n else None,
        "invalid_share": (sum(1 for e in entries if e.validation != "ok") / n) if n else None,
        "by_step": _group(entries, lambda e: e.step),
        "by_model": _group(entries, lambda e: e.model),
        "by_dev": _group(entries, lambda e: e.dev),
        "analysis": _avg_per_trace(entries, ANALYSIS_STEPS),
        "card": _avg_per_trace(entries, CARD_STEPS),
    }


def build_report(
    entries: Sequence[JournalEntry], day: str | None = None, *, daily_limit_rub: float | None = None
) -> dict[str, Any]:
    limit = daily_limit_rub if daily_limit_rub is not None else 500.0
    days: dict[str, list[JournalEntry]] = defaultdict(list)
    for e in entries:
        days[budget_day(e.at)].append(e)
    if day is None:
        day = max(days) if days else None
    day_entries = days.get(day, []) if day else []
    day_sum = summarize_entries(day_entries)
    spent = day_sum["cost_rub"]
    remaining = max(0.0, limit - spent)
    return {
        "day": day,
        "daily_limit_rub": limit,
        "day_summary": day_sum,
        "all_summary": summarize_entries(entries),
        "by_day_rub": {d: round(sum(e.cost_rub for e in es), 6) for d, es in sorted(days.items())},
        "remaining_rub": round(remaining, 6),
        "demo_reserve_rub": DEMO_RESERVE_RUB,
        "demo_reserve_ok": remaining >= DEMO_RESERVE_RUB,
        "key_spend_note": "расход ключа всей командой (key_spend) в журнал не пишется: учтены только вызовы этого журнала",  # noqa: E501
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _rub(x: float | None) -> str:
    return "—" if x is None else f"{x:.4f}"


def _table(title: str, data: dict[str, dict[str, float]]) -> list[str]:
    lines = [f"### {title}", "", "| ключ | вызовы | токены in | токены out | ₽ |", "|---|---|---|---|---|"]
    for k, b in data.items():
        lines.append(
            f"| {k} | {int(b['calls'])} | {int(b['tokens_in'])} | {int(b['tokens_out'])} | {b['cost_rub']:.4f} |"  # noqa: E501
        )
    return [*lines, ""]


def render_markdown(rep: dict[str, Any]) -> str:
    d, a = rep["day_summary"], rep["all_summary"]
    reserve = "да" if rep["demo_reserve_ok"] else "нет"
    lines = [
        "# Итог бюджета LLM",
        "",
        f"## Для слайда (бюджетные сутки {rep['day']}, с 03:00 МСК)",
        "",
        f"- Потрачено за сутки: {_rub(d['cost_rub'])} ₽ из {rep['daily_limit_rub']:.0f} ₽; вызовов: {d['calls']}",  # noqa: E501
        f"- Осталось: {_rub(rep['remaining_rub'])} ₽; резерв на день демо >= {rep['demo_reserve_rub']:.0f} ₽: {reserve}",  # noqa: E501
        f"- Средняя стоимость разбора (сюжет × отрасль): {_rub(d['analysis']['avg_rub'])} ₽ "
        f"({d['analysis']['traces']} шт.)",
        f"- Средняя стоимость карточки: {_rub(d['card']['avg_rub'])} ₽ ({d['card']['traces']} шт.)",
        f"- Запасная модель: {_pct(d['fallback_share'])}; из кеша: {_pct(d['cached_share'])}; "
        f"невалидных ответов: {_pct(d['invalid_share'])}",
        f"- За весь период: {_rub(a['cost_rub'])} ₽, вызовов {a['calls']}, "
        f"токенов {a['tokens_in']} / {a['tokens_out']}",
        f"- Примечание: {rep['key_spend_note']}.",
        "",
        "## Полная таблица (сутки)",
        "",
        *_table("По шагам", d["by_step"]),
        *_table("По моделям", d["by_model"]),
        *_table("По разработчикам", d["by_dev"]),
        "## Весь период",
        "",
        "| сутки | ₽ |",
        "|---|---|",
        *[f"| {k} | {v:.4f} |" for k, v in rep["by_day_rub"].items()],
        "",
        *_table("По шагам", a["by_step"]),
        *_table("По моделям", a["by_model"]),
        *_table("По разработчикам", a["by_dev"]),
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    settings = LLMSettings()
    ap = argparse.ArgumentParser(description="Итог бюджета LLM по журналу")
    ap.add_argument("--journal", default=settings.llm_journal_dir)
    ap.add_argument("--day", help="бюджетные сутки YYYY-MM-DD (по умолчанию — последние в журнале)")
    ap.add_argument("--out", default="reports/budget.md")
    ap.add_argument("--limit", type=float, default=500.0, help="лимит суток на общий ключ, ₽")
    args = ap.parse_args(argv)

    entries = load_entries(args.journal)
    rep = build_report(entries, args.day, daily_limit_rub=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(rep), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(entries)} вызовов -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
