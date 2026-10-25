"""Служебные эндпоинты (/raw_items, /stories*, /stats/ingest) — только с сессией (B4)."""

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

PATHS = [
    "/raw_items",
    "/raw_items?source_id=user_paste",
    "/stats/ingest",
    "/stories",
    "/stories/st_x",
    "/stories/st_x/bank_market",
    "/stories/st_x/statistician?sector=46",
]


@pytest.mark.parametrize("path", PATHS)
def test_service_endpoints_need_session(path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(path)
    assert r.status_code == 401, (path, r.status_code)
