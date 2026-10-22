"""Скоринг Р2 в конвейере Р3: настоящая значимость, пересчёт по разбору, уверенность и сюрприз кодом."""

from datetime import datetime

import pytest

from backend.agents.steps import make_handlers, run_for_client
from backend.cards.analysis import build_analysis
from backend.cards.features import channel_label
from backend.fixtures import load_bundle

STORY = "st_2026-09-11_keyrate"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("rate_14")


def _run(bundle, company: str, role: str, **kw):
    sector = next(p for p in bundle["company_profile"] if p.company_id == company).sector
    return run_for_client(STORY, company, role, sector=sector, **kw)


@pytest.mark.parametrize(("company", "role"), [("U_DEMO_BORROWER", "cfo"), ("U_DEMO_DEPOSITOR", "treasurer")])
def test_pipeline_uses_real_scoring(bundle, company, role):
    card, trace = _run(bundle, company, role)
    fixture_rel = next(r for r in bundle["relevance_score"] if r.company_id == company)
    assert card.review_status == "published" and card.stage == "deep"
    assert card.relevance.role == role
    assert card.relevance.contributions and card.relevance.contributions[0].factor == "key_rate"
    assert card.relevance != fixture_rel  # не копия фикстуры
    assert card.surprise and card.surprise.bank_ref  # сюрприз кодом
    assert any("ЦМИ" in r for r in card.confidence_reasons)  # уверенность из ranking.confidence
    assert not any(f.startswith("fast_card_failed") for f in trace.steps)


def test_depositor_confidence_high(bundle):
    card, _ = _run(bundle, "U_DEMO_DEPOSITOR", "treasurer")
    assert card.confidence == "high"


def test_scoring_deterministic_with_now(bundle):
    now = datetime(2026, 9, 11, 12, 0).astimezone()
    h = make_handlers("rate_14", now=now)
    a, _ = _run(bundle, "U_DEMO_BORROWER", "cfo", handlers=h)
    b, _ = _run(bundle, "U_DEMO_BORROWER", "cfo", handlers=make_handlers("rate_14", now=now))
    assert a.relevance.score == b.relevance.score


def test_build_analysis_from_reports(bundle):
    an = build_analysis(STORY, "x", 1, bundle["analyst_report"])
    rate = next(d for d in an.drivers if d.channel == "key_rate")
    assert rate.direction == "up" and an.agreement["key_rate"] == "agree"


def test_labels_cover_new_channels_and_contribution_kinds():
    assert channel_label("import_prices") != "import_prices"
    assert channel_label("direct:company") == "о вашей компании"
    assert "46" in channel_label("counterparty:46")
    assert channel_label("surprise") == "неожиданный исход"
