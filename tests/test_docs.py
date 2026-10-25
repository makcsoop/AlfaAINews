"""Документы сопровождения не устаревают: пути и ссылки из них существуют (CRITERIA_CHECK 2, 3, 6)."""

import re

import pytest
import yaml

from tests.conftest import ROOT

DOCS = ["docs/scaling_and_ops.md", "docs/monitoring.md", "docs/security.md", "docs/deploy.md"]
# пути в `кавычках`: начинаются с известной папки проекта и не содержат шаблонов
_PATH = re.compile(r"`((?:backend|config|docs|eval|frontend|prompts|scripts|tests|reports)/[\w./-]+)`")
_LINK = re.compile(r"\]\(([\w./-]+\.md)\)")


@pytest.mark.parametrize("doc", DOCS)
def test_paths_in_doc_exist(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    paths = set(_PATH.findall(text))
    assert paths, f"{doc}: нет ни одного пути"
    missing = [p for p in paths if not (ROOT / p.rstrip("/")).exists()]
    assert not missing, f"{doc}: нет файлов {missing}"


@pytest.mark.parametrize("doc", [*DOCS, "README.md", "docs/architecture.md"])
def test_links_in_doc_exist(doc):
    base = (ROOT / doc).parent
    text = (ROOT / doc).read_text(encoding="utf-8")
    missing = [ln for ln in _LINK.findall(text) if not (base / ln).exists()]
    assert not missing, f"{doc}: битые ссылки {missing}"


def test_security_doc_names_existing_functions():
    text = (ROOT / "docs/security.md").read_text(encoding="utf-8")
    for row in re.findall(r"`(backend/[\w/]+\.py)`: ([^|;]+)", text):
        path, names = row
        code = (ROOT / path).read_text(encoding="utf-8")
        for name in re.findall(r"`(\w+)`", names):
            assert re.search(rf"\b{name}\b", code), f"{path}: нет {name}"


def test_every_event_type_has_a_plan():
    types = yaml.safe_load((ROOT / "config/event_types.yaml").read_text(encoding="utf-8"))["event_types"]
    plans = yaml.safe_load((ROOT / "backend/agents/plan_templates.yaml").read_text(encoding="utf-8"))
    assert "default" in plans["templates"]  # тип без своего шаблона идёт по default
    assert set(plans["templates"]) - {"default"} <= set(types)
