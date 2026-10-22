"""Отчёт качества карточек: точность чисел и дат, доля ссылок, метки, повторы, рубрика 1-5.

Числа и даты ищутся теми же функциями, что в `verify.py` (`extract_numbers`, `extract_dates`): число
из текста утверждения должно найтись в цитатах его evidence или в опорных утверждениях (`based_on`).
Если передано досье, дополнительно прогоняется `verify_card` и его нарушения попадают в отчёт.

Рубрика (предварительная оценка кодом, человек правит в колонке «оценка человека»). Старт — 5,
минус 1 за каждую категорию проблем (не за каждое нарушение), минимум 1:
  1. числа или даты утверждений не подтверждены цитатами (точность < 100%);
  2. не у всех утверждений есть ссылки (evidence или based_on);
  3. нарушены метки (bank_view без cmi_-источника, market_view с cmi_ или без mk_, факт без источника);
  4. пуст хотя бы один из 6 разделов ТЗ (что случилось, почему важно, последствия, действия,
     банк и рынок, что изменит вывод);
  5. есть повторы (почти одинаковый текст утверждений, сходство >= 0.9);
  6. статус «на проверке» или низкая уверенность.

uv run python -m backend.cards.quality [--cards путь/*.json | --store pipeline|fixture]
"""

from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.agents.dossier import Dossier
from backend.cards.verify import extract_dates, extract_numbers, verify_card
from backend.schemas import CompanyProfile, InsightCard, RunTrace
from backend.schemas.common import BANK_SOURCE_PREFIX

MARKET_PREFIX = "mk_"
DUP_THRESHOLD = 0.9
SECTIONS = (
    "what_happened",
    "why_it_matters",
    "consequences",
    "actions",
    "bank_and_market",
    "what_would_change",
)

RUBRIC_TEXT = (
    "Рубрика (старт 5, -1 за категорию, минимум 1): числа/даты не подтверждены цитатами; "
    "не у всех утверждений есть ссылки; нарушены метки; пуст раздел из 6 разделов ТЗ; "
    "повторы утверждений (сходство >= 0.9); статус «на проверке» или низкая уверенность."
)


class CardQuality(BaseModel):
    card_id: str
    company_id: str
    role: str
    claims: int
    numbers_total: int
    numbers_confirmed: int
    number_accuracy: float | None  # None — в утверждениях нет чисел и дат
    unconfirmed: list[str] = Field(default_factory=list)
    refs_by_kind: dict[str, dict[str, int]] = Field(default_factory=dict)  # kind -> {total, with_refs}
    ref_share: float | None = None
    label_violations: list[str] = Field(default_factory=list)
    empty_sections: list[str] = Field(default_factory=list)
    duplicates: list[tuple[str, str]] = Field(default_factory=list)
    review_status: str
    confidence: str
    llm_calls: int | None = None
    cost_rub: float | None = None
    verify_failures: list[str] | None = None
    rubric_auto: int = 5
    rubric_penalties: list[str] = Field(default_factory=list)


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.casefold())).strip()


def find_duplicates(texts: dict[str, str], threshold: float = DUP_THRESHOLD) -> list[tuple[str, str]]:
    """Пары claim_id с почти одинаковым текстом (нормализация + difflib)."""
    ids = list(texts)
    norm = {i: _norm_text(texts[i]) for i in ids}
    out: list[tuple[str, str]] = []
    for n, a in enumerate(ids):
        for b in ids[n + 1 :]:
            if norm[a] and difflib.SequenceMatcher(None, norm[a], norm[b]).ratio() >= threshold:
                out.append((a, b))
    return out


def _quote_pool(card: InsightCard, claim_id: str, seen: set[str] | None = None) -> tuple[set[str], set[Any]]:
    """Числа и даты из цитат утверждения и (рекурсивно) его опорных утверждений."""
    seen = seen if seen is not None else set()
    if claim_id in seen:
        return set(), set()
    seen.add(claim_id)
    by_id = {c.claim_id: c for c in card.claims}
    claim = by_id.get(claim_id)
    numbers: set[str] = set()
    dates: set[Any] = set()
    if claim is None:
        return numbers, dates
    for ev in claim.evidence:
        d, rest = extract_dates(ev.quote or "")
        dates |= d
        numbers |= extract_numbers(rest)
    for refs in claim.based_on.values():
        for ref in refs:
            if ref in by_id:
                n, d = _quote_pool(card, ref, seen)
                numbers |= n
                dates |= d
    return numbers, dates


def _date_known(key: Any, dates: set[Any]) -> bool:
    d, m, y = key
    return any(dd == d and mm == m and (y is None or yy is None or yy == y) for dd, mm, yy in dates)


def number_accuracy(card: InsightCard) -> tuple[int, int, list[str]]:
    """(всего чисел и дат, подтверждённых, список неподтверждённых) по утверждениям карточки."""
    total = confirmed = 0
    bad: list[str] = []
    for c in card.claims:
        numbers, dates = _quote_pool(card, c.claim_id)
        found_dates, rest = extract_dates(c.text)
        for key in sorted(found_dates, key=str):
            total += 1
            if _date_known(key, dates):
                confirmed += 1
            else:
                bad.append(f"{c.claim_id}: дата {key[0]:02d}.{key[1]:02d}")
        for n in sorted(extract_numbers(rest)):
            total += 1
            if n in numbers:
                confirmed += 1
            else:
                bad.append(f"{c.claim_id}: число {n}")
    return total, confirmed, bad


def label_violations(card: InsightCard) -> list[str]:
    out: list[str] = []
    for c in card.claims:
        srcs = [e.source_id for e in c.evidence]
        if c.type == "fact" and not srcs:
            out.append(f"{c.claim_id}: факт без источника")
        if c.type == "bank_view" and not (srcs and all(s.startswith(BANK_SOURCE_PREFIX) for s in srcs)):
            out.append(f"{c.claim_id}: bank_view без {BANK_SOURCE_PREFIX}")
        if c.type == "market_view" and (
            not srcs
            or any(s.startswith(BANK_SOURCE_PREFIX) for s in srcs)
            or not all(s.startswith(MARKET_PREFIX) for s in srcs)
        ):
            out.append(f"{c.claim_id}: market_view без {MARKET_PREFIX}")
        if c.type == "model_inference" and not any(c.based_on.values()):
            out.append(f"{c.claim_id}: model_inference без based_on")
    return out


def assess_card(
    card: InsightCard,
    *,
    trace: RunTrace | None = None,
    dossier: Dossier | None = None,
    profile_features: Sequence[str] = (),
    profile: CompanyProfile | None = None,
) -> CardQuality:
    total, confirmed, bad = number_accuracy(card)
    kinds: dict[str, dict[str, int]] = {}
    for c in card.claims:
        b = kinds.setdefault(c.type, {"total": 0, "with_refs": 0})
        b["total"] += 1
        b["with_refs"] += 1 if (c.evidence or any(c.based_on.values())) else 0
    n_claims = len(card.claims)
    with_refs = sum(b["with_refs"] for b in kinds.values())
    empty = [s for s in SECTIONS if not getattr(card, s)]
    dups = find_duplicates({c.claim_id: c.text for c in card.claims})
    labels = label_violations(card)

    q = CardQuality(
        card_id=card.card_id,
        company_id=card.company_id,
        role=card.user_role,
        claims=n_claims,
        numbers_total=total,
        numbers_confirmed=confirmed,
        number_accuracy=(confirmed / total) if total else None,
        unconfirmed=bad,
        refs_by_kind=kinds,
        ref_share=(with_refs / n_claims) if n_claims else None,
        label_violations=labels,
        empty_sections=empty,
        duplicates=dups,
        review_status=card.review_status,
        confidence=card.confidence,
    )
    if trace is not None:
        q.llm_calls = len(trace.llm_calls)
        q.cost_rub = round(trace.cost_rub or sum(c.cost_rub for c in trace.llm_calls), 6)
    if dossier is not None:
        q.verify_failures = verify_card(
            card, dossier, profile_features=profile_features, profile=profile
        ).messages()

    penalties = []
    if bad:
        penalties.append("числа/даты не подтверждены")
    if q.ref_share is not None and q.ref_share < 1:
        penalties.append("есть утверждения без ссылок")
    if labels:
        penalties.append("нарушены метки")
    if empty:
        penalties.append("пустые разделы: " + ", ".join(empty))
    if dups:
        penalties.append("повторы утверждений")
    if card.review_status == "on_review" or card.confidence == "low":
        penalties.append("на проверке или низкая уверенность")
    q.rubric_penalties = penalties
    q.rubric_auto = max(1, 5 - len(penalties))
    return q


def summarize(rows: Sequence[CardQuality]) -> dict[str, Any]:
    n = len(rows)
    tot = sum(r.numbers_total for r in rows)
    ok = sum(r.numbers_confirmed for r in rows)
    claims = sum(r.claims for r in rows)
    kinds: dict[str, dict[str, int]] = {}
    for r in rows:
        for k, b in r.refs_by_kind.items():
            a = kinds.setdefault(k, {"total": 0, "with_refs": 0})
            a["total"] += b["total"]
            a["with_refs"] += b["with_refs"]
    with_refs = sum(b["with_refs"] for b in kinds.values())
    costs = [r.cost_rub for r in rows if r.cost_rub is not None]
    return {
        "cards": n,
        "claims": claims,
        "number_accuracy": (ok / tot) if tot else None,
        "numbers_total": tot,
        "numbers_confirmed": ok,
        "ref_share": (with_refs / claims) if claims else None,
        "ref_share_by_kind": {
            k: (b["with_refs"] / b["total"] if b["total"] else None) for k, b in kinds.items()
        },
        "label_violations": sum(len(r.label_violations) for r in rows),
        "cards_with_duplicates": sum(1 for r in rows if r.duplicates),
        "cards_with_empty_sections": sum(1 for r in rows if r.empty_sections),
        "on_review": sum(1 for r in rows if r.review_status == "on_review"),
        "low_confidence": sum(1 for r in rows if r.confidence == "low"),
        "llm_calls": sum(r.llm_calls or 0 for r in rows),
        "cost_rub": round(sum(costs), 6) if costs else None,
        "rubric_auto_mean": (sum(r.rubric_auto for r in rows) / n) if n else None,
        "rubric_auto_hist": {str(s): sum(1 for r in rows if r.rubric_auto == s) for s in range(1, 6)},
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def render_markdown(rows: Sequence[CardQuality], summary: dict[str, Any]) -> str:
    by_kind = ", ".join(f"{k} {_pct(v)}" for k, v in summary["ref_share_by_kind"].items()) or "—"
    cost = "—" if summary["cost_rub"] is None else f"{summary['cost_rub']:.4f}"
    mean = "—" if summary["rubric_auto_mean"] is None else f"{summary['rubric_auto_mean']:.2f}"
    lines = [
        "# Отчёт качества карточек",
        "",
        RUBRIC_TEXT,
        "",
        "## Сводка",
        "",
        f"- Карточек: {summary['cards']}, утверждений: {summary['claims']}",
        f"- Точность чисел и дат: {_pct(summary['number_accuracy'])} "
        f"({summary['numbers_confirmed']}/{summary['numbers_total']})",
        f"- Доля утверждений со ссылками: {_pct(summary['ref_share'])}; по меткам: {by_kind}",
        f"- Нарушений меток: {summary['label_violations']}; карточек с повторами: "
        f"{summary['cards_with_duplicates']}; с пустыми разделами: {summary['cards_with_empty_sections']}",
        f"- На проверке: {summary['on_review']}; низкая уверенность: {summary['low_confidence']}",
        f"- LLM-вызовов: {summary['llm_calls']}; ₽ по трассам: {cost}",
        f"- Рубрика (авто), среднее: {mean}",
        "",
        "## По карточкам",
        "",
        "| карточка | компания / роль | утв. | числа | ссылки | метки | пустые разделы | повторы "
        "| статус / увер. | вызовы | ₽ | авто | оценка человека |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        calls = "—" if r.llm_calls is None else r.llm_calls
        rub = "—" if r.cost_rub is None else f"{r.cost_rub:.4f}"
        lines.append(
            f"| {r.card_id} | {r.company_id} / {r.role} | {r.claims} | {_pct(r.number_accuracy)} "
            f"({r.numbers_confirmed}/{r.numbers_total}) | {_pct(r.ref_share)} | {len(r.label_violations)} "
            f"| {len(r.empty_sections)} | {len(r.duplicates)} | {r.review_status} / {r.confidence} "
            f"| {calls} | {rub} | {r.rubric_auto} |  |"
        )
    problems = [r for r in rows if r.rubric_penalties or r.unconfirmed]
    if problems:
        lines += ["", "## Замечания", ""]
        for r in problems:
            lines.append(f"- {r.card_id}: " + "; ".join(r.rubric_penalties + r.unconfirmed[:5]))
    return "\n".join(lines) + "\n"


def load_cards(pattern: str) -> list[InsightCard]:
    cards: list[InsightCard] = []
    for path in sorted(glob.glob(pattern)):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for item in data if isinstance(data, list) else [data]:
            cards.append(InsightCard.model_validate(item))
    return cards


def cards_from_store(mode: str) -> list[tuple[InsightCard, RunTrace | None]]:
    from backend.api.store import DEMO_PAIRS, FixtureCardStore, PipelineCardStore

    store = PipelineCardStore() if mode == "pipeline" else FixtureCardStore()
    out: list[tuple[InsightCard, RunTrace | None]] = []
    seen: set[str] = set()
    for company_id, role in DEMO_PAIRS:
        for card in store.list_cards(company_id, role):  # type: ignore[arg-type]
            if card.card_id in seen:
                continue
            seen.add(card.card_id)
            out.append((card, store.get_trace(card.trace_id) if card.trace_id else None))
    return out


def build_report(
    pairs: Iterable[tuple[InsightCard, RunTrace | None]],
) -> tuple[list[CardQuality], dict[str, Any]]:
    rows = [assess_card(c, trace=t) for c, t in pairs]
    return rows, summarize(rows)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Отчёт качества карточек")
    ap.add_argument("--cards", help="glob с JSON-файлами InsightCard (объект или список)")
    ap.add_argument("--store", choices=("pipeline", "fixture"), default="pipeline")
    ap.add_argument("--out", default="reports/quality.md")
    args = ap.parse_args(argv)

    if args.cards:
        pairs: list[tuple[InsightCard, RunTrace | None]] = [(c, None) for c in load_cards(args.cards)]
    else:
        os.environ.setdefault("LLM_MODE", "fixture")  # без сети
        pairs = cards_from_store(args.store)
    rows, summary = build_report(pairs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(rows, summary), encoding="utf-8")
    payload = {"summary": summary, "cards": [r.model_dump(mode="json") for r in rows]}
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} карточек -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
