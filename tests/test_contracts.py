"""Контракты для других разработчиков: выгруженная JSON Schema и API."""

import json

from fastapi.testclient import TestClient

from backend.api.main import app
from backend.schemas import ALL_SCHEMAS, SCHEMA_VERSION
from tests.conftest import ROOT


def test_schemas_json_up_to_date():
    # если упал — запусти `uv run python scripts/export_json_schemas.py` и закоммить schemas_json/
    for name, model in ALL_SCHEMAS.items():
        expected = model.model_json_schema()
        expected["$comment"] = f"schema {SCHEMA_VERSION}"
        got = json.loads((ROOT / "schemas_json" / f"{name}.json").read_text(encoding="utf-8"))
        assert got == expected, f"schemas_json/{name}.json устарел"


def test_raw_items_limit_validated(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    client = TestClient(app)
    assert client.get("/raw_items?limit=0").status_code == 401  # служебный эндпоинт — только с сессией
    assert client.post("/session", json={"company_id": "U_DEMO_BORROWER", "role": "cfo"}).status_code == 200
    assert client.get("/raw_items?limit=0").status_code == 422
    assert client.get("/raw_items?limit=-5").status_code == 422
    assert client.get("/raw_items?limit=501").status_code == 422


def test_no_api_keys_in_repo():
    import re
    import subprocess

    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    pattern = re.compile(r"sk-[A-Za-z0-9_-]{16,}")
    leaks = [
        f
        for f in files.splitlines()
        if (ROOT / f).is_file() and pattern.search((ROOT / f).read_text(encoding="utf-8", errors="ignore"))
    ]
    assert not leaks, f"похоже на API-ключ в файлах: {leaks}"
