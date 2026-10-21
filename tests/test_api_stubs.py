"""API-заглушки: вход, лента, карточка, трассировка, вопрос; защита company_id; формат ошибок."""

import json

import pytest
from fastapi.testclient import TestClient

from newsai.api.main import app
from newsai.security.audit import AuditLog, get_audit
from newsai.security.session import Session, issue_token, verify_token

BORROWER = "U_DEMO_BORROWER"
DEPOSITOR = "U_DEMO_DEPOSITOR"
BORROWER_CARD = "card_st_2026-09-11_keyrate_U_DEMO_BORROWER_cfo"
DEPOSITOR_CARD = "card_st_2026-09-11_keyrate_U_DEMO_DEPOSITOR_treasurer"
TRACE = "run_fixture_rate14"


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit")
    app.dependency_overrides[get_audit] = lambda: log
    yield log
    app.dependency_overrides.pop(get_audit, None)


@pytest.fixture
def client(audit, monkeypatch):
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    return TestClient(app, raise_server_exceptions=False)


def login(client, company=BORROWER, role="cfo"):
    r = client.post("/session", json={"company_id": company, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


def assert_error(r, status, code):
    assert r.status_code == status
    err = r.json()["error"]
    assert set(err) == {"code", "message", "trace_id"}
    assert err["code"] == code
    assert err["trace_id"] == r.headers["X-Trace-Id"]
    assert "Traceback" not in r.text


def test_login_sets_cookie_and_feed(client):
    data = login(client)
    assert data["company_id"] == BORROWER and data["token"]
    assert "session" in client.cookies
    r = client.get("/feed")
    assert r.status_code == 200
    cards = r.json()
    assert [c["card_id"] for c in cards] == [BORROWER_CARD]
    assert all(c["company_id"] == BORROWER for c in cards)
    assert r.headers["X-Trace-Id"]


def test_feed_via_bearer_sorted(client):
    token = login(client, DEPOSITOR, "treasurer")["token"]
    client.cookies.clear()
    r = client.get("/feed", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    scores = [c["relevance"]["score"] for c in r.json()]
    assert scores == sorted(scores, reverse=True)
    assert r.json()[0]["card_id"] == DEPOSITOR_CARD


def test_login_rejects_unknown_company(client, audit):
    r = client.post("/session", json={"company_id": "U_REAL_CLIENT", "role": "cfo"})
    assert_error(r, 403, "forbidden")
    assert len(audit.records) == 1


def test_login_company_list_from_env(client, monkeypatch):
    monkeypatch.setenv("DEMO_COMPANIES", "A, B")
    assert client.post("/session", json={"company_id": BORROWER, "role": "cfo"}).status_code == 403
    assert client.post("/session", json={"company_id": "B", "role": "cfo"}).status_code == 200


def test_card_and_trace_own_company(client):
    login(client)
    r = client.get(f"/card/{BORROWER_CARD}")
    assert r.status_code == 200 and r.json()["card_id"] == BORROWER_CARD
    r = client.get(f"/trace/{TRACE}")
    assert r.status_code == 200 and r.json()["run_id"] == TRACE


def test_foreign_card_is_404_and_audited(client, audit):
    login(client)
    r = client.get(f"/card/{DEPOSITOR_CARD}")
    assert_error(r, 404, "not_found")
    assert [a.requested_company for a in audit.records] == [DEPOSITOR]
    missing = client.get("/card/nope")
    assert_error(missing, 404, "not_found")
    assert missing.json()["error"]["message"] == r.json()["error"]["message"]
    assert len(audit.records) == 1  # несуществующая карточка в журнал не попадает


def test_foreign_trace_is_404_and_audited(client, audit):
    login(client, DEPOSITOR, "treasurer")
    r = client.get(f"/trace/{TRACE}")
    assert_error(r, 404, "not_found")
    assert audit.records and audit.records[0].requested_company == BORROWER


def test_foreign_company_query_is_403_and_audited(client, audit):
    login(client)
    r = client.get("/feed", params={"company_id": DEPOSITOR})
    assert_error(r, 403, "forbidden")
    rec = audit.records[0]
    assert rec.session_company == BORROWER and rec.requested_company == DEPOSITOR
    assert rec.endpoint == "GET /feed" and rec.trace_id == r.headers["X-Trace-Id"]
    lines = next(iter(audit.directory.glob("*.jsonl"))).read_text(encoding="utf-8").splitlines()
    saved = json.loads(lines[0])
    assert set(saved) == {"at", "user_id", "session_company", "requested_company", "endpoint", "trace_id"}
    # своя компания в параметре допустима
    assert client.get("/feed", params={"company_id": BORROWER}).status_code == 200


def test_ask_stub(client, audit):
    login(client)
    r = client.post("/ask", json={"question": "Что делать?", "card_id": BORROWER_CARD})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "stub" and body["refs"] == [BORROWER_CARD]
    assert body["trace_id"] == r.headers["X-Trace-Id"]
    assert client.post("/ask", json={"question": "?"}).status_code == 200
    assert not audit.records


def test_ask_company_in_body(client, audit):
    login(client)
    own = client.post("/ask", json={"question": "?", "company_id": BORROWER})
    assert_error(own, 422, "validation_error")
    foreign = client.post("/ask", json={"question": "?", "company_id": DEPOSITOR})
    assert_error(foreign, 403, "forbidden")
    assert audit.records[0].requested_company == DEPOSITOR
    assert audit.records[0].endpoint == "POST /ask"


def test_ask_foreign_card(client, audit):
    login(client)
    r = client.post("/ask", json={"question": "?", "card_id": DEPOSITOR_CARD})
    assert_error(r, 404, "not_found")
    assert len(audit.records) == 1


def test_no_session_is_401(client):
    for method, path in [("get", "/feed"), ("get", f"/card/{BORROWER_CARD}"), ("get", f"/trace/{TRACE}")]:
        assert_error(getattr(client, method)(path), 401, "unauthorized")
    assert_error(client.post("/ask", json={"question": "?"}), 401, "unauthorized")


def test_forged_and_expired_tokens(client):
    login(client)
    client.cookies.clear()
    good = issue_token(Session(company_id=BORROWER, role="cfo", user_id="u"))
    _payload, sig = good.split(".")
    forged = Session(company_id=DEPOSITOR, role="cfo", user_id="u")
    forged_payload = issue_token(forged).split(".")[0]
    for bad in [f"{forged_payload}.{sig}", good + "x", "garbage", ""]:
        r = client.get("/feed", headers={"Authorization": f"Bearer {bad}"})
        assert_error(r, 401, "unauthorized")
    expired = issue_token(Session(company_id=BORROWER, role="cfo", user_id="u"), ttl=-10)
    assert_error(client.get("/feed", headers={"Authorization": f"Bearer {expired}"}), 401, "unauthorized")
    assert verify_token(good).company_id == BORROWER


def test_wrong_secret_rejected(client, monkeypatch):
    token = login(client)["token"]
    client.cookies.clear()
    monkeypatch.setenv("SESSION_SECRET", "other")
    assert client.get("/feed", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_validation_error_format(client):
    login(client)
    r = client.get("/feed", params={"role": "hacker"})
    assert_error(r, 422, "validation_error")
    assert "hacker" not in r.text
    assert_error(client.post("/ask", json={}), 422, "validation_error")


def test_unhandled_error_hides_internals(client, audit):
    from newsai.api.store import get_store

    class Broken:
        def list_cards(self, *a):
            raise RuntimeError("secret internal detail")

    app.dependency_overrides[get_store] = lambda: Broken()
    try:
        login(client)
        r = client.get("/feed")
    finally:
        app.dependency_overrides.pop(get_store, None)
    assert_error(r, 500, "internal_error")
    assert "secret" not in r.text


def test_openapi_has_all_paths(client):
    spec = client.get("/openapi.json").json()
    for path in ["/feed", "/card/{card_id}", "/trace/{trace_id}", "/ask", "/session", "/health"]:
        assert path in spec["paths"]
    assert "post" in spec["paths"]["/ask"]
    assert "AskRequest" in spec["components"]["schemas"]


def test_cors_allows_post_with_credentials(client):
    r = client.options(
        "/ask",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert r.headers["access-control-allow-credentials"] == "true"
    assert "POST" in r.headers["access-control-allow-methods"]
