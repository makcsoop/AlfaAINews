"""Инструмент разметки: снимок клиента и валидация метки."""

import importlib.util

import pytest
from pydantic import ValidationError

from backend.api.labeling import LabelIn
from tests.conftest import ROOT

spec = importlib.util.spec_from_file_location("make_pairs", ROOT / "eval" / "labeling" / "make_pairs.py")
make_pairs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_pairs)


def test_top_sectors_sorted_by_share_not_key_order():
    # JSONB отдаёт ключи по алфавиту — «outside» оказывался последним, хотя это 92%
    shares = {"33": 0.001, "43": 0.002, "46": 0.08, "outside": 0.92}
    assert make_pairs._top_sectors(shares) == ["вне базы: 92%", "ОКВЭД 46: 8%", "ОКВЭД 43: 0%"]
    assert make_pairs._top_sectors(None) == []


def test_label_validation():
    LabelIn(pair_id="p", labeler="Кирилл", important=True, main_channel="key_rate", sign=-1)
    with pytest.raises(ValidationError):
        LabelIn(pair_id="p", labeler="", important=True)
    with pytest.raises(ValidationError):
        LabelIn(pair_id="p", labeler="x", important=True, sign=2)
