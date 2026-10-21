"""Контракты для других разработчиков: выгруженная JSON Schema и API."""

import json

from fastapi.testclient import TestClient

from newsai.api.main import app
from newsai.schemas import ALL_SCHEMAS, SCHEMA_VERSION
from tests.conftest import ROOT


def test_schemas_json_up_to_date():
    # если упал — запусти `uv run python scripts/export_json_schemas.py` и закоммить schemas_json/
    for name, model in ALL_SCHEMAS.items():
        expected = model.model_json_schema()
        expected["$comment"] = f"newsai schema {SCHEMA_VERSION}"
        got = json.loads((ROOT / "schemas_json" / f"{name}.json").read_text(encoding="utf-8"))
        assert got == expected, f"schemas_json/{name}.json устарел"


def test_raw_items_limit_validated():
    client = TestClient(app)
    assert client.get("/raw_items?limit=0").status_code == 422
    assert client.get("/raw_items?limit=-5").status_code == 422
    assert client.get("/raw_items?limit=501").status_code == 422
