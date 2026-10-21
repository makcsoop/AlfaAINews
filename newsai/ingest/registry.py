from pathlib import Path

import yaml

from newsai.config import get_settings
from newsai.schemas import Source


def load_sources(path: str | Path | None = None, *, enabled_only: bool = False) -> list[Source]:
    cfg = yaml.safe_load(Path(path or get_settings().sources_config).read_text(encoding="utf-8"))
    defaults = {k: v for k, v in (cfg.get("defaults") or {}).items() if k in Source.model_fields}
    sources = [Source.model_validate({**defaults, **s}) for s in cfg["sources"]]
    ids = [s.id for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("повторяющиеся id в sources.yaml")
    return [s for s in sources if s.enabled] if enabled_only else sources
