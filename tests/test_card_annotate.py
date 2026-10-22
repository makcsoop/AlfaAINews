"""Итог проверки кодом в карточке: claim.verified, evidence.verified и url цитат, флаг глубокого разбора."""

from datetime import UTC, datetime

import pytest

from backend.agents import steps
from backend.agents.dossier import Dossier
from backend.agents.steps import make_handlers, run_for_client
from backend.agents.tools.source import FixtureDataSource
from backend.cards.verify import CardCheck, CardFailure, annotate_card
from backend.fixtures import load_bundle

STORY = "st_2026-09-11_keyrate"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("rate_14")


def _run(bundle, company: str, role: str, **kw):
    sector = next(p for p in bundle["company_profile"] if p.company_id == company).sector
    return run_for_client(STORY, company, role, sector=sector, **kw)


@pytest.mark.parametrize(("company", "role"), [("U_DEMO_BORROWER", "cfo"), ("U_DEMO_DEPOSITOR", "treasurer")])
def test_card_carries_verification_and_urls(bundle, company, role):
    card, _ = _run(bundle, company, role)
    assert card.relevance.deep_analysis is True
    for c in card.claims:
        assert c.verified == {"refs": True, "label": True, "numbers": True, "dates": True}
        for ev in c.evidence:
            assert ev.verified is not False, (c.claim_id, ev.source_id)
    facts = [c for c in card.claims if c.type == "fact"]
    assert facts and all(ev.url for c in facts for ev in c.evidence)
    assert any(ev.verified for c in card.claims for ev in c.evidence)


def test_annotate_marks_failed_groups_and_unknown_quote(bundle):
    card, _ = _run(bundle, "U_DEMO_BORROWER", "cfo")
    target = next(c for c in card.claims if c.evidence)
    ev = target.evidence[0].model_copy(update={"quote": "такой цитаты нет нигде", "url": None})
    one = card.model_copy(update={"claims": [target.model_copy(update={"evidence": [ev]})]})
    empty = Dossier(story_id=STORY, sector="x", story_published_at=datetime(2026, 9, 11, tzinfo=UTC), trace_id="t")
    check = CardCheck(
        ok=False, failures=[CardFailure(code="number_not_in_source", claim_id=target.claim_id, detail="")]
    )
    out = annotate_card(one, empty, check, urls={ev.source_id: "https://example.org/a"})
    c = out.claims[0]
    assert c.verified["numbers"] is False and c.verified["refs"] is True
    assert c.evidence[0].verified is False and c.evidence[0].url == "https://example.org/a"


def test_make_handlers_uses_db_source_when_requested(monkeypatch):
    marker = FixtureDataSource("rate_14")
    monkeypatch.setenv("TOOL_SOURCE", "db")
    monkeypatch.setattr(steps, "get_tool_source", lambda: marker)
    used: list[object] = []
    monkeypatch.setattr(steps, "card_verify_step", lambda ctx, src: used.append(src) or ctx)
    from backend.agents.pipeline import State

    make_handlers("rate_14")[State.CARD_VERIFIED](object())
    assert used == [marker]
