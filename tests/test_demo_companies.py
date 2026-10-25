"""Витрина демо-входа: компании кейса из config/demo_companies.yaml, только витринные поля, остальные — 403."""

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.showcase import showcase
from backend.security.audit import AuditLog, get_audit
from backend.security.session import demo_companies

FORBIDDEN = {
    "pay_amt",
    "pay_cnt",
    "group_members",
    "linked_via_persons",
    "revenue",
    "revenue_rank_in_okved",
    "negative_mark",
    "counterparties",
    "exposures",
    "group_id",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit")
    app.dependency_overrides[get_audit] = lambda: log
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CARD_STORE", "fixture")
    yield TestClient(app, raise_server_exceptions=False), log
    app.dependency_overrides.pop(get_audit, None)


def _keys(obj, out: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)
    return out


def test_showcase_has_case_companies_and_archetypes():
    items = showcase()
    case = [c for c in items if c.kind == "case"]
    demo = [c for c in items if c.kind == "demo"]
    assert 6 <= len(case) <= 8
    assert {c.company_id for c in demo} == {"U_DEMO_BORROWER", "U_DEMO_DEPOSITOR"}
    # разные отрасли и продукты
    assert len({c.okved_code[:2] for c in case}) >= 5
    assert any(c.products.loans for c in case) and any(c.products.deposits for c in case)
    assert all(c.okved_name and c.region for c in items)


def test_demo_companies_include_showcase(monkeypatch):
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    allowed = demo_companies()
    assert {c.company_id for c in showcase()} <= set(allowed)
    assert "U_123" not in allowed


def test_login_case_company_ok_random_forbidden(client):
    http, log = client
    assert http.post("/session", json={"company_id": "U_26030", "role": "cfo"}).status_code == 200
    r = http.post("/session", json={"company_id": "U_123", "role": "cfo"})
    assert r.status_code == 403
    assert [a.requested_company for a in log.records] == ["U_123"]


def test_demo_companies_endpoint_only_showcase_fields(client):
    http, _ = client
    r = http.get("/demo/companies")
    assert r.status_code == 200
    data = r.json()
    assert [c["company_id"] for c in data] == [c.company_id for c in showcase()]
    assert not (_keys(data, set()) & FORBIDDEN)
    first = data[0]
    assert {"company_id", "title", "okved_code", "okved_name", "region", "products", "default_role"} <= set(first)


def test_demo_companies_endpoint_respects_env(client, monkeypatch):
    http, _ = client
    monkeypatch.setenv("DEMO_COMPANIES", "U_26030")
    assert [c["company_id"] for c in http.get("/demo/companies").json()] == ["U_26030"]
