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


@pytest.mark.parametrize(("company", "role"), [("U_DEMO_BORROWER", "cfo"), ("U_DEMO_DEPOSITOR", "treasurer")])
def test_deep_card_headline_and_linked_actions(bundle, company, role):
    """Схемы 0.2.1: заголовок модели в карточке, у действий и условий — метка и ссылки (инвариант 4)."""
    card, _ = _run(bundle, company, role)
    assert card.headline and len(card.headline) <= 200
    claim_ids = {c.claim_id for c in card.claims}
    assert card.actions and all(a.kind and a.refs for a in card.actions)
    for a in card.actions:
        if a.type == "bank_product":
            assert a.kind == "bank_offer" and set(a.refs) <= claim_ids
        else:
            assert a.kind == "model_inference"
    assert card.what_would_change == [c.text for c in card.conditions]
    assert all(c.kind == "model_inference" and c.refs for c in card.conditions)



def test_verify_flags_action_and_condition_refs(bundle, tmp_path):
    from tests.test_card_verify import CASES, _check, _llm_card

    e, card = _llm_card(bundle, tmp_path, CASES[0])
    act = card.actions[0].model_copy(update={"refs": ["nope_1"]})
    cond = card.conditions[0].model_copy(update={"refs": []})
    bad = card.model_copy(update={"actions": [act, *card.actions[1:]], "conditions": [cond, *card.conditions[1:]]})
    msgs = " ".join(_check(e, bad).messages())
    assert "actions[0]" in msgs and "nope_1" in msgs and "conditions[0]: нет ссылок" in msgs


def test_analysis_saved_when_source_has_db(bundle, monkeypatch):
    """После арбитра `Analysis` пишется в таблицу Р1 по ключу `analysis_ref` карточки; сбой записи не роняет разбор."""
    import contextlib

    import backend.db.analyses as analyses_db

    saved = []

    class FakeEngine:
        @contextlib.contextmanager
        def begin(self):
            yield "conn"

        def connect(self):
            raise RuntimeError("нет БД")  # модули Р1 «Банк и рынок» / «Статистик» пропускаются

    class DbLike(FixtureDataSource):
        engine = FakeEngine()

    def fake_save(conn, analysis):
        saved.append(analysis)
        return f"{analysis.story_id}:{analysis.sector}:{analysis.version}"

    monkeypatch.setattr(analyses_db, "save_analysis", fake_save)
    card, trace = _run(bundle, "U_DEMO_BORROWER", "cfo", handlers=make_handlers("rate_14", source=DbLike("rate_14")))
    assert len(saved) == 1 and card.analysis_ref == f"{saved[0].story_id}:{saved[0].sector}:{saved[0].version}"
    assert f"analysis:saved:{card.analysis_ref}" in trace.steps

    def broken(conn, analysis):
        raise RuntimeError("таблицы нет")

    monkeypatch.setattr(analyses_db, "save_analysis", broken)
    card2, _ = _run(bundle, "U_DEMO_BORROWER", "cfo", handlers=make_handlers("rate_14", source=DbLike("rate_14")))
    assert card2.review_status == card.review_status
