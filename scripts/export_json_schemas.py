"""Выгружает JSON Schema всех объектов в schemas_json/ — для фронтенда и промптов LLM."""

import json
from pathlib import Path

from newsai.schemas import ALL_SCHEMAS, SCHEMA_VERSION

OUT = Path(__file__).resolve().parent.parent / "schemas_json"

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, model in ALL_SCHEMAS.items():
        schema = model.model_json_schema()
        schema["$comment"] = f"newsai schema {SCHEMA_VERSION}"
        (OUT / f"{name}.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(ALL_SCHEMAS)} schemas -> {OUT}")
