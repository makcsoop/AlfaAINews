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
    assert {c.event.story_id for c in cards} == {"st_2026-09-11_keyrate", "s_2", "s_3", "s_4"}
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
    assert run.status == "failed" and run.reason == "pipeline_failed:RuntimeError" and run.trace_id


def test_failed_run_reason_hides_exception_text(caplog):
    """B15: текст исключения (хосты, порты БД) наружу через /feed/runs не уходит, только в лог."""
    def boom(*a, **kw):
        raise RuntimeError("connection to server at 10.0.0.7, port 5432 failed")

    store = PipelineCardStore(source=MultiSource(), runner=boom, deep_per_request=1)
    with caplog.at_level("WARNING"):
        store.list_cards(BORROWER, "cfo")
    (run,) = store.deep_runs(BORROWER)
    assert "10.0.0.7" not in (run.reason or "") and "5432" not in (run.reason or "")
    assert "10.0.0.7" in caplog.text


def test_degraded_run_without_story_card_does_not_borrow_fixture_card():
    """B14: сюжета уже нет в ленте — чужую карточку rate_14 на его место не подставляем."""
    store, _ = make_store(k=0)
    store.list_cards(BORROWER, "cfo")
    feed = store._feeds[(BORROWER, "cfo")]
    before = {cid: c.model_copy() for cid, c in store._cards.items()}
    store._drop(feed, "s_2")
    store._apply_failure(feed, "s_2", RuntimeError("модель недоступна"))
    assert "s_2" not in feed.cards
    for cid, card in store._cards.items():
        assert card.review_status == before[cid].review_status and card.trace_id == before[cid].trace_id


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
    assert run.status == "failed" and run.reason == "pipeline_failed:RuntimeError"
    on_review = store.get_card(store._feeds[(BORROWER, "cfo")].cards[run.story_id], BORROWER)
    assert on_review is not None and on_review.review_status == "on_review"  # быстрая карточка осталась
    store.list_cards(BORROWER, "cfo")  # поток жив и берёт следующий сюжет
    assert store.wait_idle(5)
    assert len(store.deep_runs(BORROWER)) == 2


def test_background_off_in_fixture_mode():
    assert PipelineCardStore(source=MultiSource())._background is False


def test_feed_picks_up_new_and_changed_stories_without_restart(monkeypatch):
    # лента досчитывает новые сюжеты из ingest; изменившийся сюжет — новая быстрая карточка и новый разбор
    monkeypatch.setenv("FEED_REFRESH_S", "0")
    store, calls = make_store(k=10)
    src = store._explicit_source
    assert len(store.list_cards(BORROWER, "cfo")) == 4 and calls.count("s_2") == 1
    base_story, base_facts = src._b["story"][0], src._b["event_facts"][0]
    src._b["story"].append(base_story.model_copy(update={"id": "s_5", "title": "Новый сюжет (s_5)"}))
    src._b["event_facts"].append(base_facts.model_copy(update={"story_id": "s_5"}))
    stories = src._b["story"]
    i = next(n for n, s in enumerate(stories) if s.id == "s_2")
    stories[i] = stories[i].model_copy(update={"version": 2})
    cards = store.list_cards(BORROWER, "cfo")
    assert len(cards) == 5 and "s_5" in calls
    assert calls.count("s_2") == 2  # новая версия — разбор заново
    assert calls.count("s_4") == 1  # без изменений — не трогаем


def test_feed_not_rescanned_before_refresh_interval(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_S", "3600")
    store, _ = make_store(k=0)
    src = store._explicit_source
    store.list_cards(BORROWER, "cfo")
    src._b["story"].append(src._b["story"][0].model_copy(update={"id": "s_6"}))
    assert len(store.list_cards(BORROWER, "cfo")) == 4


# ---------- деградация разбора (B1, B2): не публикуется и не кешируется ----------
class _KV:
    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value


def make_degraded_store(transition: str, review_status: str = "on_review", kv=None):
    """Раннер возвращает карточку, но автомат закончил в DEGRADED (как run_for_client при сбое модели)."""
    calls: list[str] = []
    holder: dict = {}

    def runner(story_id, company_id, role, *, sector, story_version, handlers):
        calls.append(story_id)
        fast = holder["store"]._cards[f"card_{story_id}_{company_id}_{role}"]
        now = datetime.now(UTC)
        trace = RunTrace(run_id=f"run_{story_id}_{len(calls)}", story_id=story_id, company_id=company_id,
                         started_at=now, finished_at=now,
                         steps=[f"{transition}->DEGRADED (сбой)"])
        return fast.model_copy(update={"trace_id": trace.run_id, "review_status": review_status}), trace

    store = PipelineCardStore(kv=kv, source=MultiSource(), runner=runner, deep_per_request=1)
    holder["store"] = store
    return store, calls


def test_degraded_run_not_cached_and_not_deep():
    kv = _KV()
    store, _ = make_degraded_store("PLANNED", kv=kv)
    cards = store.list_cards(BORROWER, "cfo")
    (run,) = store.deep_runs(BORROWER)
    assert run.status == "failed"
    feed = store._feeds[(BORROWER, "cfo")]
    assert run.story_id not in feed.deep  # сюжет не считается глубоко разобранным
    assert not [k for k in kv.data if k.startswith("card:")]  # деградация не идёт в общий кеш
    card = next(c for c in cards if c.event.story_id == run.story_id)
    assert card.review_status == "on_review"


def test_degraded_unverified_card_shown_on_review():
    # B1: даже если раннер вернул карточку как «published», после DEGRADED она уходит «на проверке»
    store, _ = make_degraded_store("CARD_VERIFIED", review_status="published")
    cards = store.list_cards(BORROWER, "cfo")
    (run,) = store.deep_runs(BORROWER)
    assert run.status == "failed"
    card = next(c for c in cards if c.event.story_id == run.story_id)
    assert card.review_status == "on_review"


def test_degraded_story_retried_after_cooldown(monkeypatch):
    monkeypatch.setenv("FEED_RETRY_S", "0")
    store, calls = make_degraded_store("PLANNED")
    store.list_cards(BORROWER, "cfo")
    first = calls[0]
    store.list_cards(BORROWER, "cfo")
    assert calls.count(first) == 2  # после паузы деградировавший сюжет разбирается заново


def test_degraded_story_not_retried_before_cooldown(monkeypatch):
    monkeypatch.setenv("FEED_RETRY_S", "3600")
    store, calls = make_degraded_store("PLANNED")
    store.list_cards(BORROWER, "cfo")
    store.list_cards(BORROWER, "cfo")
    assert len(calls) == len(set(calls))


# ---------- вставка пользователя личная (B3) ----------
def _add_private_paste(src, owner: str, story_id: str = "st_paste_secret"):
    from backend.ingest.paste import make_item

    base = src._b["story"][0]
    now = base.first_seen
    item = make_item(url=None, text="КОНФИДЕНЦИАЛЬНО: наш план сделки M&A с Банком России, ставка 14%",
                     title=None, now=now, owner=owner)
    src._b["raw_item"].append(item)
    src._b["story"].append(base.model_copy(update={
        "id": story_id, "title": item.title, "item_ids": [item.id], "source_ids": ["user_paste"],
        "corroboration": 0,
    }))
    return story_id


def test_private_paste_visible_only_to_owner(monkeypatch):
    monkeypatch.setenv("FEED_REFRESH_S", "0")
    store, _ = make_store(k=0)
    sid = _add_private_paste(store._explicit_source, BORROWER)
    assert store.add_story(sid, BORROWER, "cfo") is not None
    assert sid in {c.event.story_id for c in store.list_cards(BORROWER, "cfo")}
    theirs = store.list_cards(DEPOSITOR, "treasurer")
    assert sid not in {c.event.story_id for c in theirs}
    assert not any("КОНФИДЕНЦИАЛЬНО" in c.event.title for c in theirs)
    assert store.add_story(sid, DEPOSITOR, "treasurer") is None  # чужую вставку закрепить нельзя


def test_new_version_during_background_run_picked_up_later(monkeypatch):
    """B5: новая версия сюжета, пришедшая во время фонового разбора, досчитывается после него."""
    store, calls, gate, started = make_background_store(k=1)
    store.list_cards(BORROWER, "cfo")
    assert started.wait(5)
    feed = store._feeds[(BORROWER, "cfo")]
    (sid,) = tuple(feed.pending)
    stories = store._explicit_source._b["story"]
    i = next(n for n, s in enumerate(stories) if s.id == sid)
    stories[i] = stories[i].model_copy(update={"version": 2, "title": "Новая версия сюжета"})
    monkeypatch.setenv("FEED_REFRESH_S", "0")
    store.list_cards(BORROWER, "cfo")  # разбор прежней версии ещё идёт
    gate.set()
    assert store.wait_idle(5)
    for _ in range(3):
        store.list_cards(BORROWER, "cfo")
        assert store.wait_idle(5)
    assert feed.story_version[sid] == 2
    assert store._cards[feed.cards[sid]].event.title == "Новая версия сюжета"
    assert calls.count(sid) == 2  # новая версия разобрана заново


def test_add_story_scores_without_global_lock():
    """B10: /analyze считает ленту и карточку вне общей блокировки — /card и /feed других не ждут."""
    import threading

    store, _ = make_store(k=0)
    store.list_cards(BORROWER, "cfo")
    free: list[bool] = []
    orig = store._score_story

    def probe(ctx, role, story):
        got: list[bool] = []

        def other():
            ok = store._lock.acquire(timeout=1)
            got.append(ok)
            if ok:
                store._lock.release()

        t = threading.Thread(target=other)
        t.start()
        t.join()
        free.append(bool(got and got[0]))
        return orig(ctx, role, story)

    store._score_story = probe
    assert store.add_story("s_2", BORROWER, "cfo") is not None
    assert free and all(free)


# --- окно свежести живой ленты (TOOL_SOURCE=db) -------------------------------------------------


class LiveLikeSource(MultiSource):
    """Как DbDataSource: живые данные, лента режет сюжеты старше окна. Фикстура 11.09 — «старая»."""

    live_feed_window = True

    def __init__(self) -> None:
        super().__init__()
        fresh = datetime.now(UTC) - timedelta(hours=2)
        stories = self._b["story"]
        for n, s in enumerate(stories):
            if s.id in ("s_2", "s_4"):
                stories[n] = s.model_copy(update={"first_seen": fresh, "last_seen": fresh})


def test_db_feed_skips_stories_older_than_window(monkeypatch):
    monkeypatch.delenv("FEED_MAX_AGE_HOURS", raising=False)
    src = LiveLikeSource()
    store = PipelineCardStore(source=src, runner=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
                              deep_per_request=0)
    ids = {c.event.story_id for c in store.list_cards(BORROWER, "cfo")}
    assert ids == {"s_2", "s_4"}  # фикстура 11.09 и s_3 той же даты — вне окна 72 ч


def test_feed_window_from_env_and_disabled(monkeypatch):
    monkeypatch.setenv("FEED_MAX_AGE_HOURS", "0")  # 0 — окно выключено
    store = PipelineCardStore(source=LiveLikeSource(), runner=lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("x")), deep_per_request=0)
    assert len(store.list_cards(BORROWER, "cfo")) == 4


def test_fixture_source_has_no_window(monkeypatch):
    monkeypatch.delenv("FEED_MAX_AGE_HOURS", raising=False)
    store, _ = make_store(k=0)  # фикстура: «сейчас» — дата сюжета, окно не применяется
    assert "st_2026-09-11_keyrate" in {c.event.story_id for c in store.list_cards(BORROWER, "cfo")}


def test_story_aging_out_leaves_feed(monkeypatch):
    monkeypatch.delenv("FEED_MAX_AGE_HOURS", raising=False)
    monkeypatch.setenv("FEED_REFRESH_S", "0")
    src = LiveLikeSource()
    store = PipelineCardStore(source=src, runner=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
                              deep_per_request=0)
    assert "s_2" in {c.event.story_id for c in store.list_cards(BORROWER, "cfo")}
    old = datetime.now(UTC) - timedelta(hours=100)
    stories = src._b["story"]
    for n, s in enumerate(stories):
        if s.id == "s_2":
            stories[n] = s.model_copy(update={"last_seen": old})
    assert "s_2" not in {c.event.story_id for c in store.list_cards(BORROWER, "cfo")}


def test_db_source_declares_live_window():
    from backend.agents.tools.db_source import DbDataSource

    assert DbDataSource.live_feed_window is True


def test_warm_builds_feeds_and_runs_deep_only_for_default_pairs(monkeypatch):
    """Прогрев: ленты всех пар готовы до первого захода, глубокий разбор — только у пары по умолчанию."""
    store, calls = make_store(k=1)
    store.warm([(BORROWER, "cfo"), (BORROWER, "treasurer")], {(BORROWER, "cfo")})
    assert {(BORROWER, "cfo"), (BORROWER, "treasurer")} <= set(store._feeds)
    assert store.runs == len(calls) == 1 and {r.role for r in store.deep_runs(BORROWER)} == {"cfo"}
    # бюджет выше доли прогрева — новые разборы не ставятся, ленты всё равно обновляются
    monkeypatch.setenv("FEED_WARM_DEEP_SHARE", "0")
    store.warm([(BORROWER, "cfo")], {(BORROWER, "cfo")})
    assert store.runs == 1


def test_warmer_off_on_fixture(monkeypatch):
    from backend.api.store import start_feed_warmer

    monkeypatch.setenv("LLM_MODE", "fixture")
    assert start_feed_warmer() is None
