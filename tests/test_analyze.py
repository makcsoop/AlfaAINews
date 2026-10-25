"""POST /analyze: вставка → сюжет (функция Р1 подменена) → быстрая карточка своей компании, она же в ленте."""

import pytest
from fastapi.testclient import TestClient

from backend.api.analyze import get_paster
from backend.api.feedback import MemoryFeedbackStore, get_feedback_store, rank_with_feedback
from backend.api.main import app
from backend.api.store import PipelineCardStore, feed_deep_per_request, feed_min_score, get_store
from backend.ingest.paste import PasteError
from backend.ranking.config import load_scoring
from backend.security.audit import AuditLog, get_audit
from backend.security.ratelimit import analyze_limiter
from tests.test_feed_multi import BORROWER, DEPOSITOR, MultiSource

PASTED = "s_2"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.setenv("FEED_MIN_SCORE", "0.99")  # лента пуста: карточка вставки появляется только по запросу
    return PipelineCardStore(source=MultiSource(), deep_per_request=0)


@pytest.fixture
def pasted():
    return []


@pytest.fixture
def http(store, pasted, monkeypatch, tmp_path):
    monkeypatch.delenv("DEMO_COMPANIES", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "test-secret")

    def paster(url, text, title, company_id):
        assert company_id in (BORROWER, DEPOSITOR)  # владелец вставки — компания из сессии
        pasted.append((url, text, title))
        if url == "http://10.0.0.1/":
            raise PasteError("ссылка ведёт во внутреннюю сеть")
        return PASTED

    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_paster] = lambda: paster
    app.dependency_overrides[get_audit] = lambda: AuditLog(tmp_path / "audit")
    app.dependency_overrides[get_feedback_store] = MemoryFeedbackStore
    analyze_limiter.reset()
    yield TestClient(app, raise_server_exceptions=False)
    for dep in (get_store, get_paster, get_audit, get_feedback_store):
        app.dependency_overrides.pop(dep, None)
    analyze_limiter.reset()


def login(c, company=BORROWER, role="cfo"):
    assert c.post("/session", json={"company_id": company, "role": role}).status_code == 200


def test_pasted_story_becomes_fast_card_in_own_feed(http, pasted):
    login(http)
    assert http.get("/feed").json() == []
    r = http.post("/analyze", json={"url": "https://www.cbr.ru/press/pr/", "title": "Решение ЦБ"})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["company_id"] == BORROWER and card["user_role"] == "cfo" and card["stage"] == "fast"
    assert card["event"]["story_id"] == PASTED
    assert pasted == [("https://www.cbr.ru/press/pr/", None, "Решение ЦБ")]
    assert [c["card_id"] for c in http.get("/feed").json()] == [card["card_id"]]
    assert http.get(f"/card/{card['card_id']}").status_code == 200


def test_pasted_card_not_visible_to_other_company(http):
    login(http)
    card = http.post("/analyze", json={"text": "ЦБ сохранил ключевую ставку 14%"}).json()
    login(http, DEPOSITOR, "treasurer")
    assert http.get(f"/card/{card['card_id']}").status_code == 404
    assert all(c["company_id"] == DEPOSITOR for c in http.get("/feed").json())


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, 422),
        ({"text": "   "}, 422),
        ({"text": "x", "role": "owner"}, 422),
        ({"text": "x", "company_id": "U_OTHER"}, 403),
        ({"url": "http://10.0.0.1/"}, 400),
    ],
)
def test_bad_requests(http, body, code):
    login(http)
    assert http.post("/analyze", json=body).status_code == code


def test_needs_session_and_db(http):
    assert http.post("/analyze", json={"text": "x"}).status_code == 401
    login(http)
    app.dependency_overrides[get_paster] = lambda: None  # стенд без базы
    r = http.post("/analyze", json={"text": "x"})
    assert r.status_code == 503 and "базой" in r.text


def test_rate_limited(http, monkeypatch):
    login(http)
    monkeypatch.setattr(analyze_limiter, "limit", 1)
    codes = [http.post("/analyze", json={"text": "ставка"}).status_code for _ in range(2)]
    assert codes == [200, 429]


# --- настройки ленты и отклик ----------------------------------------------------------------------


def test_feed_settings_from_scoring_yaml_env_overrides(monkeypatch):
    monkeypatch.delenv("FEED_MIN_SCORE", raising=False)
    monkeypatch.delenv("FEED_DEEP_PER_REQUEST", raising=False)
    cfg = load_scoring().feed
    assert feed_min_score() == cfg.min_score and feed_deep_per_request() == cfg.deep_per_request
    monkeypatch.setenv("FEED_MIN_SCORE", "0.2")
    monkeypatch.setenv("FEED_DEEP_PER_REQUEST", "3")
    assert feed_min_score() == 0.2 and feed_deep_per_request() == 3


def test_not_relevant_lowers_same_event_type(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("FEED_MIN_SCORE", "0")
    store = PipelineCardStore(source=MultiSource(), deep_per_request=0)
    base = store.list_cards(BORROWER, "cfo")[0]

    def card(story_id: str, event_type: str, score: float):
        rel = base.relevance.model_copy(update={"story_id": story_id, "score": score, "contributions": []})
        ev = base.event.model_copy(update={"story_id": story_id, "event_type": event_type})
        return base.model_copy(update={"card_id": f"c_{story_id}", "event": ev, "relevance": rel})

    rate, rate2, duty = card("a", "rate_decision", 0.5), card("b", "rate_decision", 0.45), card("c", "tariff_duty", 0.4)
    assert [c.card_id for c in rank_with_feedback([rate, rate2, duty], [])] == ["c_a", "c_b", "c_c"]
    rows = [{"company_id": BORROWER, "story_id": "a", "kind": "not_relevant"}]
    ranked = rank_with_feedback([rate, rate2, duty], rows)
    assert [c.card_id for c in ranked] == ["c_c", "c_a", "c_b"]  # 0,4 > 0,35 > 0,315
    assert ranked[1].relevance.score == 0.5  # значимость в карточке не меняется, только порядок
    useful = [{"company_id": BORROWER, "story_id": "c", "kind": "useful"}]
    assert rank_with_feedback([rate, rate2, duty], useful)[0].card_id == "c_c"  # 0,4 × 1,3 = 0,52


def test_pasted_story_kept_after_deep_below_threshold(store):
    """Сюжет, который прислал пользователь, остаётся в ленте и после разбора с низкой значимостью."""
    from datetime import UTC, datetime

    from backend.schemas import RunTrace

    def runner(story_id, company_id, role, *, sector, story_version, handlers):
        fast = store._cards[f"card_{story_id}_{company_id}_{role}"]
        now = datetime.now(UTC)
        trace = RunTrace(run_id=f"run_{story_id}", story_id=story_id, company_id=company_id,
                         started_at=now, finished_at=now)
        rel = fast.relevance.model_copy(update={"score": 0.0, "deep_analysis": True})
        return fast.model_copy(update={"trace_id": trace.run_id, "relevance": rel, "stage": "deep"}), trace

    store._runner = runner
    card = store.add_story(PASTED, BORROWER, "cfo")
    store._run_deep(store._feeds[(BORROWER, "cfo")], PASTED)
    assert [c.event.story_id for c in store.list_cards(BORROWER, "cfo")] == [PASTED]
    assert store.deep_runs(BORROWER)[-1].status == "published" and card is not None
