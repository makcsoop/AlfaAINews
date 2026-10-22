"""Согласие разметчиков на общих парах «сюжет × клиент».

Разметка — значимость 0…3 (0 — не про нас, 3 — важно сегодня). По плану каждый размечает 20 пар из 60,
10 пар общие у всех: на них проверяем, что разметчики понимают шкалу одинаково, прежде чем считать nDCG.

Вход — CSV с колонками pair_id, labeler, relevance (экспорт инструмента Р1 приводится к нему).
`python -m backend.eval.agreement labels.csv`
"""

import csv
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

SCALE = (0, 1, 2, 3)


@dataclass(frozen=True)
class Label:
    pair_id: str
    labeler: str
    relevance: int


@dataclass(frozen=True)
class PairAgreement:
    a: str
    b: str
    n: int
    exact: float
    kappa: float | None  # None — у обоих все оценки одинаковые, каппа не определена


@dataclass(frozen=True)
class Report:
    pairs: list[PairAgreement]
    mean_kappa: float | None
    disputed: list[tuple[str, dict[str, int]]]  # пары, где оценки расходятся на 2 и больше


def read_labels(path: Path) -> list[Label]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            Label(r["pair_id"].strip(), r["labeler"].strip(), int(r["relevance"]))
            for r in csv.DictReader(f)
            if r.get("relevance", "").strip()
        ]


def weighted_kappa(x: list[int], y: list[int], scale: tuple[int, ...] = SCALE) -> float | None:
    """Каппа Коэна с квадратичными весами: расхождение 0 и 3 хуже, чем 2 и 3."""
    k = len(scale)
    idx = {v: i for i, v in enumerate(scale)}
    n = len(x)
    obs = [[0.0] * k for _ in range(k)]
    for a, b in zip(x, y, strict=True):
        obs[idx[a]][idx[b]] += 1 / n
    row = [sum(r) for r in obs]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    w = [[((i - j) / (k - 1)) ** 2 for j in range(k)] for i in range(k)]
    observed = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    expected = sum(w[i][j] * row[i] * col[j] for i in range(k) for j in range(k))
    return None if expected == 0 else round(1 - observed / expected, 3)


def agreement(labels: Iterable[Label]) -> Report:
    by_pair: dict[str, dict[str, int]] = defaultdict(dict)
    for lb in labels:
        by_pair[lb.pair_id][lb.labeler] = lb.relevance
    labelers = sorted({lb for votes in by_pair.values() for lb in votes})
    result = []
    for a, b in combinations(labelers, 2):
        common = [p for p, v in by_pair.items() if a in v and b in v]
        if not common:
            continue
        x = [by_pair[p][a] for p in common]
        y = [by_pair[p][b] for p in common]
        exact = sum(i == j for i, j in zip(x, y, strict=True)) / len(common)
        result.append(PairAgreement(a, b, len(common), round(exact, 3), weighted_kappa(x, y)))
    kappas = [p.kappa for p in result if p.kappa is not None]
    disputed = sorted(
        (p, v) for p, v in by_pair.items() if len(v) > 1 and max(v.values()) - min(v.values()) >= 2
    )
    return Report(result, round(sum(kappas) / len(kappas), 3) if kappas else None, disputed)


def verdict(kappa: float | None) -> str:
    """Шкала Ландиса — Коха: ниже 0,4 шкалу надо обсудить до разметки остальных пар."""
    if kappa is None:
        return "нельзя оценить"
    return "хорошее" if kappa >= 0.6 else "умеренное" if kappa >= 0.4 else "слабое — обсудить шкалу"


def format_report(r: Report, names: Mapping[str, str] | None = None) -> str:
    names = names or {}
    lines = [f"Средняя каппа: {r.mean_kappa} ({verdict(r.mean_kappa)})"]
    for p in r.pairs:
        lines.append(
            f"  {names.get(p.a, p.a)} × {names.get(p.b, p.b)}: пар {p.n}, "
            f"совпало {round(p.exact * 100)}%, каппа {p.kappa}"
        )
    if r.disputed:
        lines.append("Спорные пары (разброс ≥ 2):")
        lines += [f"  {pid}: {votes}" for pid, votes in r.disputed]
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_report(agreement(read_labels(Path(sys.argv[1])))))
