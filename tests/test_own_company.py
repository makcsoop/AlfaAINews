"""Своя компания по анкете: профиль, чувствительности, хранилище с ключом владельца, вход и лента через API."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.agents.tools import FixtureDataSource
from backend.api.main import app
from backend.api.store import reset_store
from backend.profile.exposures import with_exposures
from backend.profile.own import (
    OwnCompanies,
    OwnCompanyForm,
    WithOwnCompanies,
    build_own,
    okved_sectors,
    reset_own_companies,
)
from backend.profile.taxonomy import load_matrix
from backend.security.audit import AuditLog, get_audit
from backend.security.ratelimit import own_limiter

FUEL = {
    "title": "Мой топливный опт",
    "okved_code": "46.71",
    "region": "Нижегородская область",
    "products": {"loans": True, "leasing": True},
    "fea": True,
    "fea_countries": ["Казахстан"],
    "buyers": {"41": 40, "49": 20},
    "suppliers": {"19": 70},
}


def test_okved_sectors_cover_matrix():
    """Каждая строка матрицы каналов есть в анкете с названием: иначе отрасль нельзя выбрать."""
    assert set(load_matrix().okved) <= set(okved_sectors())


def test_build_own_profile_and_exposures():
    profile, graph = build_own(OwnCompanyForm.model_validate(FUEL), "U_OWN_TEST")
    assert profile.sector == "46" and profile.okved_name == "Оптовая торговля топливом"
    assert profile.products["loans"] and profile.products["rko"] and not profile.products["deposits"]
    assert profile.fts_flag and profile.fts_country == "Казахстан"
    assert graph is not None and graph.sector_shares_buyers == {"41": 0.4, "49": 0.2}
    exp = {e.channel: e for e in with_exposures(profile, graph).exposures}
    # кредит → ставка против компании; подотрасль 46.71 → цены на топливо; покупатели-стройки → стройка
    assert exp["key_rate"].sensitivity < 0 and "CL_PRODUCT_LOA_MARK=1" in exp["key_rate"].drivers
    assert "petroleum_products" in exp
    assert any(d.startswith("payments: 40% входящих от ОКВЭД 41") for e in exp.values() for d in e.drivers)


def test_build_own_without_counterparties_has_no_graph():
    _, graph = build_own(OwnCompanyForm(title="Кафе", okved_code="56"), "U_OWN_X")
    assert graph is None


@pytest.mark.parametrize(
    "patch, msg",
    [
        ({"okved_code": "4671"}, "ОКВЭД"),
        ({"okved_code": "99.1"}, "справочник"),
        ({"buyers": {"41": 80, "49": 30}}, "больше 100"),
        ({"suppliers": {"77777": 10}}, "справочник"),
        ({"inn": "7700000000"}, "Extra inputs"),
    ],
)
def test_form_rejects_bad_input(patch, msg):
    with pytest.raises(ValidationError, match=msg):
        OwnCompanyForm.model_validate({**FUEL, **patch})


def test_countries_imply_fea():
    form = OwnCompanyForm.model_validate({**FUEL, "fea": False})
    assert form.fea


def test_registry_owner_key_and_source_overlay():
    reg = OwnCompanies()
    cid, key = reg.create(OwnCompanyForm.model_validate(FUEL))
    assert cid.startswith("U_OWN_") and reg.verify(cid, key)
    assert not reg.verify(cid, "чужой ключ") and not reg.verify(cid, None)
    assert not reg.verify("U_DEMO_BORROWER", key)
    other, _ = reg.create(OwnCompanyForm.model_validate(FUEL))
    assert other != cid and not reg.verify(other, key)  # ключ одной анкеты не открывает другую
    assert reg.profile(cid).okved_code == "46.71" and reg.title(cid) == FUEL["title"]


def test_registry_survives_via_kv():
    class FakeKV(dict):
        def set(self, key, value, ex=None):
            self[key] = value

    kv = FakeKV()
    cid, key = OwnCompanies(kv).create(OwnCompanyForm.model_validate(FUEL))
    fresh = OwnCompanies(kv)  # другой процесс или перезапуск API
    assert fresh.verify(cid, key) and fresh.profile(cid) is not None


def test_overlay_keeps_case_companies():
    src = WithOwnCompanies(FixtureDataSource("rate_14"))
    assert src.profile("U_DEMO_BORROWER") is not None
    assert src.profile("U_OWN_NOPE") is None
    assert src.stories()  # остальное — у исходного источника


@pytest.fixture
def client(tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit")
    app.dependency_overrides[get_audit] = lambda: log
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("CARD_STORE", "pipeline")
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("FEED_MIN_SCORE", "0")
    reset_own_companies()
    reset_store()
    own_limiter.reset()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_audit, None)
    reset_store()
    reset_own_companies()


def test_own_session_feed_and_relogin(client):
    r = client.post("/session/own", json={"form": FUEL, "role": "cfo"})
    assert r.status_code == 200, r.text
    data = r.json()
    cid, key = data["company_id"], data["owner_key"]
    assert cid.startswith("U_OWN_") and data["title"] == FUEL["title"] and "session" in client.cookies

    feed = client.get("/feed")
    assert feed.status_code == 200
    cards = feed.json()
    assert cards and all(c["company_id"] == cid for c in cards)

    # смена роли — повторный вход по ключу владельца; без ключа и с чужим ключом — 403
    assert client.post("/session", json={"company_id": cid, "role": "treasurer", "owner_key": key}).status_code == 200
    assert client.post("/session", json={"company_id": cid, "role": "cfo"}).status_code == 403
    assert client.post("/session", json={"company_id": cid, "role": "cfo", "owner_key": "x" * 32}).status_code == 403


def test_own_session_cannot_read_other_company(client):
    client.post("/session/own", json={"form": FUEL})
    r = client.get("/card/card_st_2026-09-11_keyrate_U_DEMO_BORROWER_cfo")
    assert r.status_code == 404  # чужая карточка не отдаётся


def test_own_session_rate_limited(client):
    codes = [client.post("/session/own", json={"form": FUEL}).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_own_sectors_list(client):
    r = client.get("/own/sectors")
    assert r.status_code == 200
    assert {"code": "46.71", "name": "Оптовая торговля топливом"} in r.json()
