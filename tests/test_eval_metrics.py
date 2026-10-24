"""Метрики отбора: nDCG@5, precision@5, пропуски; согласие на бинарной разметке Р1."""

from pathlib import Path

import pytest

from backend.eval.agreement import agreement, read_labels
from backend.eval.metrics import (
    PairEval,
    covered_sectors,
    evaluate,
    format_report,
    gold_from_csv,
    ndcg_at,
    precision_at,
)


def test_ndcg_and_precision():
    assert ndcg_at([1, 1, 0, 0, 0]) == 1.0
    assert ndcg_at([0, 0, 0]) is None
    assert ndcg_at([0, 1, 1]) < 1.0
    assert precision_at([1, 0, 1, 0, 0]) == pytest.approx(0.4)
    assert precision_at([1, 1]) == 1.0  # у клиента меньше 5 пар — делим на число пар


def _pairs():
    # у клиента A скоринг ставит важные выше, бейзлайн (свежесть) — наоборот
    out = []
    for i in range(8):
        important = i < 3
        out.append(
            PairEval(
                f"A{i}",
                "A",
                f"s{i}",
                1.0 if important else 0.0,
                system=0.9 - i * 0.1,
                baseline=0.1 + i * 0.1,
                covered=True,
            )
        )
    # у клиента B одна важная пара, система её пропустила (ниже порога ленты)
    out += [
        PairEval("B0", "B", "s0", 1.0, system=0.01, baseline=0.9, covered=False),
        PairEval("B1", "B", "s1", 0.0, system=0.5, baseline=0.1, covered=False),
    ]
    return out


def test_evaluate_against_baseline():
    m = evaluate(_pairs())
    assert m.clients == 2 and m.pairs == 10
    assert m.ndcg > m.ndcg_baseline and m.precision > m.precision_baseline
    assert m.misses_covered == (0, 3) and m.misses_uncovered == (1, 1)
    text = format_report(m)
    assert "nDCG@5" in text and "1 из 1" in text


def test_equal_scores_ordered_by_freshness_like_feed():
    # у системы обе пары 0 — порядок как в ленте (свежее выше), а не по story_id
    pairs = [
        PairEval("C0", "C", "a", 0.0, system=0.0, baseline=0.2, covered=False),
        PairEval("C1", "C", "b", 1.0, system=0.0, baseline=0.9, covered=False),
    ]
    assert evaluate(pairs, k=1).precision == 1.0


def test_covered_sectors_needs_five_channels():
    rows = [{"source_id": f"tg_{i}", "sectors_okved": ["24"]} for i in range(5)]
    rows += [{"source_id": "tg_0", "sectors_okved": ["46"]} for _ in range(9)]  # один канал много раз
    assert covered_sectors(rows) == {"24"}


EXPORT = (
    "pair_id,story_id,company_id,archetype,event_type,story_title,labeler,important,main_channel,sign,comment,labeled_at\n"
    "p1,s1,A,опт,rate_decision,ставка,ann,1,key_rate,-1,,2026-10-22\n"
    "p1,s1,A,опт,rate_decision,ставка,bob,1,key_rate,-1,,2026-10-22\n"
    "p2,s2,A,опт,sanctions,санкции,ann,0,,,,2026-10-22\n"
    "p2,s2,A,опт,sanctions,санкции,bob,1,,,,2026-10-22\n"
    "p3,s3,A,опт,other,прочее,ann,0,,,,2026-10-22\n"
    "p3,s3,A,опт,other,прочее,bob,0,,,,2026-10-22\n"
)


def test_labeling_export_format(tmp_path: Path):
    f = tmp_path / "labels-oct-60.csv"
    f.write_text("﻿" + EXPORT, encoding="utf-8")
    gold = gold_from_csv(f)
    assert gold["p1"] == ("s1", "A", 1.0) and gold["p2"][2] == 0.5
    rep = agreement(read_labels(f))
    assert rep.pairs[0].n == 3 and rep.disputed == [("p2", {"ann": 0, "bob": 1})]
    assert rep.pairs[0].exact == pytest.approx(0.667, abs=0.001)
