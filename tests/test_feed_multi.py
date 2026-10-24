"""Лента из многих сюжетов: быстрые карточки для всех, глубокий разбор не более K, чужая компания не видна."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from backend.agents.dossier import Dossier
from backend.agents.r1_dossier import bank_market_items, enrich_dossier, statistician_items
from backend.agents.tools.source import FixtureDataSource
from backend.api.store import PipelineCardStore
from backend.schemas import RunTrace

BORROWER, DEPOSITOR = "U_DEMO_BORROWER", "U_DEMO_DEPOSITOR"


class MultiSource(FixtureDataSource):
    """rate_14 + синтетические сюжеты на тех же материалах."""

    def __init__(self) -> None:
        super().__init__("rate_14")
        base_story, base_facts = self._b["story"][0], self._b["event_facts"][0]
        self.extra_ids = ["s_2", "s_3", "s_4"]
        quality = {"s_2": 0.9, "s_3": 0.2, "s_4": 0.5}
        for sid in self.extra_ids:
            self._b["story"].append(
                base_story.model_copy(update={"id": sid, "title": f"{base_story.title} ({sid})",
                                              "quality": quality[sid]})
            )
            if sid != "s_3":  # у s_3 нет фактов: только быстрая карточка
                self._b["event_facts"].append(base_facts.model_copy(update={"story_id": sid}))


@pytest.fixture(autouse=True)
def fixture_mode(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.delenv("CACHE_BACKEND", raising=False)
    monkeypatch.setenv("FEED_MIN_SCORE", "0")


def make_store(k: int = 2):
    calls: list[str] = []
    holder: dict = {}

    def runner(story_id, company_id, role, *, sector, story_version, handlers):
        calls.append(story_id)
        fast = holder["store"]._cards[f"card_{story_id}_{company_id}_{role}"]
        now = datetime.now(UTC)
        trace = RunTrace(run_id=f"run_{story_id}", story_id=story_id, company_id=company_id,
                         started_at=now, finished_at=now)
        deep = fast.model_copy(
            update={
                "trace_id": trace.run_id,
                "relevance": fast.relevance.model_copy(update={"deep_analysis": True}),
            }
        )
        return deep, trace

    store = PipelineCardStore(source=MultiSource(), runner=runner, deep_per_request=k)
    holder["store"] = store
    return store, calls


def test_feed_has_card_per_story_sorted_with_limited_deep():
    store, calls = make_store(k=2)
    cards = store.list_cards(BORROWER, "cfo")
    assert {c.event.story_id if hasattr(c, "event") else None for c in cards} or cards
    assert len(cards) == 4  # rate_14 + три синтетических
    scores = [c.relevance.score for c in cards]
    assert scores == sorted(scores, reverse=True)
    deep = [c for c in cards if c.relevance.deep_analysis]
    assert len(deep) == len(calls) == 2  # K=2, остальные — быстрые
    assert "s_3" not in calls  # без фактов глубокий разбор не запускается
    assert len(cards) - len(deep) == 2


def test_next_requests_upgrade_remaining_without_repeats():
    store, calls = make_store(k=2)
    store.list_cards(BORROWER, "cfo")
    cards = store.list_cards(BORROWER, "cfo")
    assert len(calls) == len(set(calls)) == 3  # третий факт-сюжет добран, s_3 без фактов остался быстрым
    assert sum(c.relevance.deep_analysis for c in cards) == 3
    store.list_cards(BORROWER, "cfo")
    assert len(calls) == 3 and store.runs == 3


def test_deep_card_replaces_fast_of_same_story():
    store, calls = make_store(k=1)
    cards = store.list_cards(BORROWER, "cfo")
    ids = [c.card_id for c in cards]
    assert len(ids) == len(set(ids)) == 4
    assert len(calls) == 1


def test_foreign_company_not_visible():
    store, _ = make_store(k=3)
    mine = store.list_cards(BORROWER, "cfo")
    theirs = store.list_cards(DEPOSITOR, "treasurer")
    assert {c.company_id for c in mine} == {BORROWER}
    assert {c.company_id for c in theirs} == {DEPOSITOR}
    assert all(store.get_card(c.card_id, BORROWER) is None for c in theirs)
    assert store.list_cards("U_UNKNOWN", "cfo") == []


def test_pipeline_failure_keeps_fast_card_on_review():
    def boom(*a, **kw):
        raise RuntimeError("модель недоступна")

    store = PipelineCardStore(source=MultiSource(), runner=boom, deep_per_request=1)
    cards = store.list_cards(BORROWER, "cfo")
    assert len(cards) == 4
    assert sum(c.review_status == "on_review" for c in cards) == 1
    again = store.list_cards(BORROWER, "cfo")
    assert len(again) == 4 and store.runs == 2  # упавший сюжет не перезапускается, берётся следующий


def test_zero_k_gives_only_fast_cards():
    store, calls = make_store(k=0)
    cards = store.list_cards(BORROWER, "cfo")
    assert len(cards) == 4 and not calls
    assert not any(c.relevance.deep_analysis for c in cards)


def test_fixture_source_without_db_single_story():
    store = PipelineCardStore(source=None, runner=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    cards = store.list_cards(BORROWER, "cfo")
    assert len(cards) == 1


# --- адаптер модулей Р1 --------------------------------------------------------------------------


def _dossier() -> Dossier:
    return Dossier(
        story_id="s_1", sector="64", story_published_at=datetime(2025, 10, 24, tzinfo=UTC), trace_id="t"
    )


def test_bank_market_adapter_items():
    view = SimpleNamespace(
        position_id="cmi_1", doc_title="Обзор", quote="Ставка снизится", thesis="t", channel="key_rate",
        direction=-1.0, forecast=None, page=3, doc_date=datetime(2025, 9, 1).date(), stale=False,
        before_event=True,
    )
    late = SimpleNamespace(**{**view.__dict__, "position_id": "cmi_2", "before_event": False})
    market = SimpleNamespace(meeting=datetime(2025, 10, 24).date(), statement="Ждут 16%", expected_value=16.0)
    items = bank_market_items(SimpleNamespace(bank=[view, late], market=market), _dossier())
    assert {i.id for i in items} == {"cmi_1", "mk_consensus_2025-10-24"}
    assert next(i for i in items if i.id.startswith("mk_")).data["kind"] == "expectation"


def test_statistician_adapter_skips_future_and_self():
    d = _dossier()
    mk = lambda sid, fs: SimpleNamespace(  # noqa: E731
        story_id=sid, title=sid, event_type="rate_decision", first_seen=fs, similarity=0.8,
        analyst_notes=[SimpleNamespace(excerpt="заметка")],
    )
    rep = SimpleNamespace(similar=[mk("old", "2025-06-01T00:00:00"), mk("new", "2025-12-01T00:00:00"), mk("s_1", "2025-01-01")])
    assert [i.id for i in statistician_items(rep, d)] == ["old"]


def test_enrich_without_db_tables_skips_with_flag():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    d = _dossier()
    flags = enrich_dossier(d, engine)
    assert flags == ["r1:bank_market:skipped:OperationalError", "r1:statistician:skipped:OperationalError"]
    assert d.items == []
    _ = timedelta


# --- лимит глубоких за запрос, отбрасывание по значимости, журнал разборов ---


def test_card_and_trace_lookups_do_not_start_deep_runs_on_live_models(monkeypatch):
    import backend.api.store as store_mod

    store, calls = make_store(k=1)
    live = SimpleNamespace(settings=SimpleNamespace(llm_mode="live"), budget=store_mod.get_client().budget)
    monkeypatch.setattr(store_mod, "get_client", lambda: live)
    store.get_trace("нет такой")
    store.card_owner("нет такой")
    store.get_card("нет такой", BORROWER)
    assert calls == []  # глубокий разбор запускает только /feed пары из сессии
    store.list_cards(DEPOSITOR, "treasurer")
    assert len(calls) == 1  # ровно K за запрос, без добора из демо-пар


def test_deep_card_below_threshold_dropped_from_feed_and_logged(monkeypatch):
    def runner(story_id, company_id, role, *, sector, story_version, handlers):
        fast = store._cards[f"card_{story_id}_{company_id}_{role}"]
        now = datetime.now(UTC)
        trace = RunTrace(run_id=f"run_{story_id}", story_id=story_id, company_id=company_id,
                         started_at=now, finished_at=now, cost_rub=0.4)
        low = fast.relevance.model_copy(update={"score": 0.0, "deep_analysis": True})
        return fast.model_copy(update={"relevance": low, "trace_id": trace.run_id}), trace

    monkeypatch.setenv("FEED_MIN_SCORE", "0.01")
    store = PipelineCardStore(source=MultiSource(), runner=runner, deep_per_request=1)
    cards = store.list_cards(BORROWER, "cfo")
    (run,) = store.deep_runs(BORROWER)
    assert cards and not any(c.relevance.deep_analysis for c in cards)
    assert run.story_id not in {c.event.story_id for c in cards}  # отброшенный сюжет убран из ленты
    assert run.status == "dropped" and run.score == 0.0 and run.cost_rub == 0.4
    assert store.get_trace(run.trace_id) is not None  # трасса разбора остаётся для журнала
    assert all(r.company_id == DEPOSITOR for r in store.deep_runs(DEPOSITOR))  # журнал только своей компании


def test_failed_deep_run_logged_with_reason():
    def boom(*a, **kw):
        raise RuntimeError("модель недоступна")

    store = PipelineCardStore(source=MultiSource(), runner=boom, deep_per_request=1)
    store.list_cards(BORROWER, "cfo")
    (run,) = store.deep_runs(BORROWER)
    assert run.status == "failed" and "модель недоступна" in run.reason and run.trace_id


def test_published_run_logged():
    store, _ = make_store(k=1)
    store.list_cards(BORROWER, "cfo")
    (run,) = store.deep_runs(BORROWER)
    assert run.status == "published" and run.trace_id


# ---------- фоновые разборы (живые модели): /feed не ждёт модель ----------
def make_background_store(k: int = 1, fail: bool = False):
    """Разбор в фоне; модель «отвечает», только когда тест откроет gate."""
    import threading

    gate, started = threading.Event(), threading.Event()
    calls: list[str] = []
    holder: dict = {}

    def runner(story_id, company_id, role, *, sector, story_version, handlers):
        calls.append(story_id)
        started.set()
        assert gate.wait(5), "тест не открыл gate"
        if fail:
            raise RuntimeError("модель недоступна")
        fast = holder["store"]._cards[f"card_{story_id}_{company_id}_{role}"]
        now = datetime.now(UTC)
        trace = RunTrace(run_id=f"run_{story_id}", story_id=story_id, company_id=company_id,
                         started_at=now, finished_at=now)
        rel = fast.relevance.model_copy(update={"deep_analysis": True})
        return fast.model_copy(update={"trace_id": trace.run_id, "relevance": rel}), trace

    store = PipelineCardStore(source=MultiSource(), runner=runner, deep_per_request=k, background=True)
    holder["store"] = store
    return store, calls, gate, started


def test_background_feed_answers_before_model_and_upgrades_later():
    store, calls, gate, started = make_background_store(k=1)
    cards = store.list_cards(BORROWER, "cfo")  # разбор ещё идёт — лента уже есть
    assert len(cards) == 4 and not any(c.relevance.deep_analysis for c in cards)
    assert started.wait(5)
    # пока модель думает, блокировка свободна: карточка и трасса отвечают сразу
    assert store.get_card(cards[0].card_id, BORROWER) is not None
    gate.set()
    assert store.wait_idle(5)
    deep = [c for c in store.list_cards(BORROWER, "cfo") if c.relevance.deep_analysis]
    assert [c.event.story_id for c in deep] == calls[:1]
    assert store.deep_runs(BORROWER)[0].status == "published"


def test_background_polling_does_not_queue_more_than_k_or_repeat():
    store, calls, gate, started = make_background_store(k=1)
    for _ in range(5):  # фронт перечитывает ленту, пока разбор не готов
        store.list_cards(BORROWER, "cfo")
    assert started.wait(5)
    gate.set()
    assert store.wait_idle(5)
    assert len(calls) == 1  # пять запросов — один разбор
    store.list_cards(BORROWER, "cfo")  # следующий запрос — следующий сюжет, не повтор
    assert store.wait_idle(5)
    assert len(calls) == 2 and len(set(calls)) == 2


def test_background_skips_when_budget_ran_out_while_queued(monkeypatch):
    store, calls, gate, _ = make_background_store(k=1)
    monkeypatch.setattr(store, "_budget", lambda: (0.0, 500.0))
    store._start_worker = lambda: None  # поток не стартует, пока тест не исчерпает бюджет
    store.list_cards(BORROWER, "cfo")
    feed = store._feeds[(BORROWER, "cfo")]
    ((_, story_id),) = list(store._jobs.queue)
    monkeypatch.setattr(store, "_budget", lambda: (500.0, 500.0))
    gate.set()
    del store._start_worker
    store._start_worker()
    assert store.wait_idle(5)
    assert calls == [] and story_id not in feed.attempted  # бюджет вернётся — разберём позже
    assert not feed.pending


def test_background_failure_logged_and_worker_survives():
    store, _, gate, _ = make_background_store(k=1, fail=True)
    gate.set()
    store.list_cards(BORROWER, "cfo")
    assert store.wait_idle(5)
    (run,) = store.deep_runs(BORROWER)
    assert run.status == "failed" and "модель недоступна" in (run.reason or "")
    on_review = store.get_card(store._feeds[(BORROWER, "cfo")].cards[run.story_id], BORROWER)
    assert on_review is not None and on_review.review_status == "on_review"  # быстрая карточка осталась
    store.list_cards(BORROWER, "cfo")  # поток жив и берёт следующий сюжет
    assert store.wait_idle(5)
    assert len(store.deep_runs(BORROWER)) == 2


def test_background_off_in_fixture_mode():
    assert PipelineCardStore(source=MultiSource())._background is False


# ---------- лента сверяется с базой без перезапуска: сборщик работает непрерывно ----------
def make_refresh_store(refresh_s: float = 0.0):
    store, calls = make_store(k=1)
    store._refresh_s = refresh_s
    return store, calls, store._explicit_source


def _add_story(src, sid: str, **update):
    base_story, base_facts = src._b["story"][0], src._b["event_facts"][0]
    src._b["story"].append(base_story.model_copy(update={"id": sid, "title": f"новый {sid}", **update}))
    src._b["event_facts"].append(base_facts.model_copy(update={"story_id": sid}))


def _ids(cards):
    return [c.event.story_id for c in cards]


def test_refresh_adds_new_story_and_keeps_deep_cards():
    store, _, src = make_refresh_store()
    first = store.list_cards(BORROWER, "cfo")
    deep_before = {c.card_id for c in first if c.relevance.deep_analysis}
    _add_story(src, "s_new", quality=0.95)
    cards = store.list_cards(BORROWER, "cfo")
    assert "s_new" in _ids(cards) and len(cards) == len(first) + 1
    assert deep_before <= {c.card_id for c in cards}  # готовый разбор не потерян
    assert len(_ids(cards)) == len(set(_ids(cards)))  # одна карточка на сюжет


def test_refresh_new_version_gets_fast_card_and_new_analysis():
    store, calls, src = make_refresh_store()
    store.list_cards(BORROWER, "cfo")
    (sid,) = calls
    i = next(n for n, st in enumerate(src._b["story"]) if st.id == sid)
    src._b["story"][i] = src._b["story"][i].model_copy(update={"version": 2})
    cards = store.list_cards(BORROWER, "cfo")
    assert calls.count(sid) == 2  # новая версия — новый разбор
    assert len(_ids(cards)) == len(set(_ids(cards)))


def test_refresh_drops_story_merged_away():
    store, _, src = make_refresh_store()
    store.list_cards(BORROWER, "cfo")
    src._b["story"] = [st for st in src._b["story"] if st.id != "s_3"]  # пересклейка: s_3 влился в другой
    cards = store.list_cards(BORROWER, "cfo")
    assert "s_3" not in _ids(cards)


def test_refresh_respects_interval_and_can_be_off():
    store, _, src = make_refresh_store(refresh_s=3600)
    n = len(store.list_cards(BORROWER, "cfo"))
    _add_story(src, "s_new")
    assert len(store.list_cards(BORROWER, "cfo")) == n  # сверка раз в час — ещё рано
    off, _, src2 = make_refresh_store(refresh_s=-1)
    m = len(off.list_cards(BORROWER, "cfo"))
    _add_story(src2, "s_new")
    off._feeds[(BORROWER, "cfo")].checked_at = 0
    assert len(off.list_cards(BORROWER, "cfo")) == m
