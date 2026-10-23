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
    LabelIn(pair_id="p", labeler="Кирилл", relevance=3, main_channel="key_rate", sign=-1)
    with pytest.raises(ValidationError):
        LabelIn(pair_id="p", labeler="", relevance=1)
    with pytest.raises(ValidationError):
        LabelIn(pair_id="p", labeler="x", relevance=2, sign=2)
    with pytest.raises(ValidationError):
        LabelIn(pair_id="p", labeler="x", relevance=4)  # шкала каппы Р2 — 0…3
    with pytest.raises(ValidationError):
        LabelIn(pair_id="p", labeler="x", important=True)  # без значимости метка не принимается


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.labeling import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/labeling", "/labeling/channels", "/labeling/export.csv"])
def test_labeling_closed_without_flag(monkeypatch, path):
    # инвариант 6: на демо страница с профилями компаний без сессии не открывается
    monkeypatch.delenv("LABELING_ENABLED", raising=False)
    assert _client().get(path).status_code == 404


def test_labeling_open_with_flag(monkeypatch):
    monkeypatch.setenv("LABELING_ENABLED", "true")
    c = _client()
    assert c.get("/labeling").status_code == 200
    assert any(ch["id"] == "key_rate" for ch in c.get("/labeling/channels").json())
