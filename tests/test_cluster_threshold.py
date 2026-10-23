"""Порог склейки: подсчёт точности и полноты по размеченным парам."""

import importlib.util

from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("ct", ROOT / "eval" / "labeling" / "cluster_threshold.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


def test_metrics_per_threshold():
    rows = [
        {"sim": 0.7, "same": True},
        {"sim": 0.45, "same": True},
        {"sim": 0.35, "same": False},
        {"sim": 0.25, "same": True},
        {"sim": 0.15, "same": False},
    ]
    by_t = {r["t"]: r for r in ct.metrics(rows)}
    assert (by_t[0.3]["tp"], by_t[0.3]["fp"], by_t[0.3]["fn"]) == (2, 1, 1)
    assert by_t[0.3]["precision"] == 2 / 3 and by_t[0.3]["recall"] == 2 / 3
    assert by_t[0.5]["precision"] == 1.0 and by_t[0.5]["recall"] == 1 / 3
    assert "← сейчас" in ct.report(rows, ct.metrics(rows), "тест")
