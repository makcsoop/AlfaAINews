"""POST /feedback: своя карточка, компания и роль из сессии, чужая — 404 + журнал, БД Р1 и память."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from backend.api.feedback import DbFeedbackStore, MemoryFeedbackStore, get_feedback_store
from backend.api.main import app
from backend.schemas import Feedback
from backend.security.audit import AuditLog, get_audit
from backend.security.ratelimit import feedback_limiter
from tests.test_qa import BORROWER, CARD, DEPOSITOR_CARD


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit")


@pytest.fixture
def fb_store():
    return MemoryFeedbackStore()


@pytest.fixture
def http(audit, fb_store, monkeypatch, tmp_path):
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CARD_STORE", "fixture")
    monkeypatch.setenv("LLM_MODE", "fixture")
    app.dependency_overrides[get_audit] = lambda: audit
    app.dependency_overrides[get_feedback_store] = lambda: fb_store
    feedback_limiter.reset()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_audit, None)
    app.dependency_overrides.pop(get_feedback_store, None)
    feedback_limiter.reset()


def login(c, company=BORROWER, role="cfo"):
    assert c.post("/session", json={"company_id": company, "role": role}).status_code == 200


def test_useful_saved_with_company_and_role_from_session(http, fb_store, audit):
    login(http)
    r = http.post("/feedback", json={"card_id": CARD, "kind": "useful"})
    assert r.status_code == 200 and r.json() == {"ok": True, "id": 1}
    (row,) = fb_store.list(BORROWER)
    assert row["company_id"] == BORROWER and row["user_role"] == "cfo" and row["kind"] == "useful"
    assert row["card_id"] == CARD and row["story_id"] and row["user_id"]
    assert not audit.records


def test_wrong_on_claim_and_unknown_claim(http, fb_store):
    login(http)
    card = http.get(f"/card/{CARD}").json()
    claim = card["claims"][0]["claim_id"]
    ok = http.post("/feedback", json={"card_id": CARD, "kind": "wrong", "claim_id": claim, "comment": "не та дата"})
    assert ok.status_code == 200 and fb_store.list(BORROWER)[0]["claim_id"] == claim
    bad = http.post("/feedback", json={"card_id": CARD, "kind": "wrong", "claim_id": "Z99"})
    assert bad.status_code == 404 and len(fb_store.list(BORROWER)) == 1


def test_foreign_card_404_and_audited(http, fb_store, audit):
    login(http)
    r = http.post("/feedback", json={"card_id": DEPOSITOR_CARD, "kind": "not_relevant"})
    assert r.status_code == 404 and "DEPOSITOR" not in r.text
    assert audit.records and fb_store.rows == []


def test_unknown_card_404_without_audit(http, audit):
    login(http)
    assert http.post("/feedback", json={"card_id": "card_nope", "kind": "useful"}).status_code == 404
    assert not audit.records


@pytest.mark.parametrize(
    "body",
    [
        {"card_id": CARD, "kind": "useful", "user_role": "owner"},  # роль только из сессии
        {"card_id": CARD, "kind": "expert_edit"},  # правка эксперта — не через пользовательский API
        {"card_id": CARD, "kind": "like"},
        {"card_id": CARD, "kind": "useful", "comment": "x" * 1001},
    ],
)
def test_invalid_body_rejected(http, fb_store, body):
    login(http)
    assert http.post("/feedback", json=body).status_code == 422
    assert fb_store.rows == []


def test_company_id_in_body_forbidden(http, fb_store):
    login(http)
    r = http.post("/feedback", json={"card_id": CARD, "kind": "useful", "company_id": "U_OTHER"})
    assert r.status_code == 403 and fb_store.rows == []  # компания только из сессии


def test_needs_session(http, fb_store):
    assert http.post("/feedback", json={"card_id": CARD, "kind": "useful"}).status_code == 401
    assert fb_store.rows == []


def test_rate_limited(http, monkeypatch):
    login(http)
    monkeypatch.setattr(feedback_limiter, "limit", 2)
    codes = [http.post("/feedback", json={"card_id": CARD, "kind": "click_source"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_db_store_writes_feedback_table():
    from backend.db import models as m

    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    m.feedback.create(engine)
    store = DbFeedbackStore(engine)
    fb = Feedback(card_id=CARD, user_role="cfo", kind="not_relevant", at=datetime.now(UTC))
    new_id = store.add(fb, {"company_id": BORROWER, "user_id": "u1", "story_id": "st_1"})
    store.add(fb, {"company_id": "U_OTHER", "user_id": "u2", "story_id": "st_1"})
    assert new_id == 1
    with engine.connect() as conn:
        kinds = conn.execute(select(m.feedback.c.kind)).scalars().all()
    assert kinds == ["not_relevant", "not_relevant"]
    (row,) = store.list(BORROWER)  # только своя компания
    assert row["user_id"] == "u1" and row["kind"] == "not_relevant"
