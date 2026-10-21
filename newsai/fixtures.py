"""Загрузка фикстур: `{schema_name: [объекты]}`, каждый объект валидируется своей схемой."""

import json
from pathlib import Path

from pydantic import BaseModel

from newsai.schemas import ALL_SCHEMAS

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_bundle(name: str = "rate_14") -> dict[str, list[BaseModel]]:
    raw = json.loads((FIXTURES_DIR / name / "bundle.json").read_text(encoding="utf-8"))
    return {
        key: [ALL_SCHEMAS[key].model_validate(obj) for obj in objs]
        for key, objs in raw.items()
        if not key.startswith("_")
    }
