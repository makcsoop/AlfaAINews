"""Хранилище карточек за протоколом: фикстура rate_14 или конвейер (по env CARD_STORE)."""

import logging
import os
import queue
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from backend.agents.cache import KV, RedisJsonStore, cache_ttl_s, make_kv
from backend.agents.pipeline import PipelineContext, State
from backend.agents.steps import AnalysisCache, get_tool_source, make_handlers, profile_v1, run_for_client
from backend.agents.tools import FixtureDataSource, ToolDataSource
from backend.fixtures import load_bundle
from backend.llm import get_client
from backend.ranking.config import FeedCfg, load_scoring
from backend.ranking.fast_card import fast_card
from backend.ranking.score import score_pair
from backend.ranking.select import select_deep
from backend.ranking.surprise import find_surprise
from backend.schemas import (
    BankPosition,
    CompanyProfile,
    CounterpartyGraph,
    EventFacts,
    MarketKnowledge,
    RawItem,
    RelevanceScore,
    Source,
    Story,
)
from backend.schemas.common import UserRole
from backend.schemas.insight import InsightCard, RunTrace


class CardStore(Protocol):
    def list_cards(self, company_id: str, role: UserRole | None) -> list[InsightCard]:
        """Карточки только этой компании; при заданной роли — карточки этой роли."""
        ...

    def get_card(self, card_id: str, company_id: str) -> InsightCard | None:
        """Карточка, только если принадлежит company_id; иначе None."""
        ...

    def card_owner(self, card_id: str) -> str | None:
        """company_id владельца карточки — только для журнала безопасности, наружу не отдаётся."""
        ...

    def get_trace(self, trace_id: str) -> RunTrace | None:
        """Трассировка по id; проверку принадлежности делает вызывающий (по trace.company_id)."""
        ...


class FixtureCardStore:
    def __init__(self, bundle_name: str = "rate_14") -> None:
        bundle = load_bundle(bundle_name)
        self._cards: dict[str, InsightCard] = {c.card_id: c for c in bundle["insight_card"]}  # type: ignore[attr-defined,misc]
        self._traces: dict[str, RunTrace] = {t.run_id: t for t in bundle["run_trace"]}  # type: ignore[attr-defined,misc]

    def list_cards(self, company_id: str, role: UserRole | None) -> list[InsightCard]:
        own = [c for c in self._cards.values() if c.company_id == company_id]
        by_role = [c for c in own if role is None or c.user_role == role]
        # нет карточек под эту роль — показываем карточки компании, а не пустую ленту
        return by_role or own

    def get_card(self, card_id: str, company_id: str) -> InsightCard | None:
        card = self._cards.get(card_id)
        return card if card and card.company_id == company_id else None

    def card_owner(self, card_id: str) -> str | None:
        card = self._cards.get(card_id)
        return card.company_id if card else None

    def get_trace(self, trace_id: str) -> RunTrace | None:
        return self._traces.get(trace_id)


log = logging.getLogger(__name__)

# демо-пары «компания × роль», для которых есть фикстуры ответов модели
DEMO_PAIRS: tuple[tuple[str, str], ...] = (("U_DEMO_BORROWER", "cfo"), ("U_DEMO_DEPOSITOR", "treasurer"))

# запасные значения, если в config/scoring.yaml (раздел feed, зона Р2) их нет;
# переменные окружения — переопределение для стенда
DEFAULT_DEEP_PER_REQUEST = 1  # глубоких разборов на один запрос ленты; остальное — быстрые карточки
DEFAULT_FEED_MIN_SCORE = 0.05  # ниже — сюжет в ленту клиента не попадает


DEFAULT_FEED_REFRESH_S = 60.0  # как часто лента сверяется с базой: новые сюжеты, новые версии, пересклейка


def feed_refresh_s() -> float:
    """FEED_REFRESH_S: 0 — при каждом запросе ленты, меньше 0 — лента собирается один раз (как раньше)."""
    return _env_float("FEED_REFRESH_S", DEFAULT_FEED_REFRESH_S)


def feed_background() -> bool:
    """Глубокие разборы в фоне: /feed отдаёт ленту сразу, разбор догружается (фронт перечитывает ленту).
    На фикстуре (тесты) — синхронно, чтобы результат был детерминированным.
    FEED_BACKGROUND=false — синхронно и на живых моделях, как раньше."""
    if os.environ.get("LLM_MODE", "").strip().lower() == "fixture":
        return False
    return os.environ.get("FEED_BACKGROUND", "true").strip().lower() not in ("0", "false", "no")


def _feed_cfg() -> FeedCfg | None:
    try:
        return load_scoring().feed
    except Exception as e:  # битый или старый scoring.yaml — не роняем ленту
        log.warning("scoring.yaml: раздел feed не прочитан (%r), запасные значения", e)
        return None


def feed_deep_per_request() -> int:
    cfg = _feed_cfg()
    return _env_int("FEED_DEEP_PER_REQUEST", getattr(cfg, "deep_per_request", DEFAULT_DEEP_PER_REQUEST))


def feed_min_score() -> float:
    cfg = _feed_cfg()
    return _env_float("FEED_MIN_SCORE", getattr(cfg, "min_score", DEFAULT_FEED_MIN_SCORE))


def _is_degraded(trace: RunTrace) -> bool:
    return any(step.startswith("pipeline_failed") for step in trace.steps)


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class DeepRun(BaseModel):
    """Запись журнала глубоких разборов ленты: что разбирали, чем кончилось и сколько стоило."""

    story_id: str
    company_id: str
    role: str
    status: Literal["published", "on_review", "dropped", "failed"]
    reason: str | None = None
    score: float | None = None  # значимость после разбора
    trace_id: str | None = None
    cost_rub: float = 0.0
    at: datetime


@dataclass
class _Ctx:
    """Данные для оценки сюжетов одной компании: профиль, факты, материалы, источники, граф, банк и рынок."""

    profile: CompanyProfile
    facts: dict[str, EventFacts]
    items: dict[str, RawItem]
    sources: dict[str, Source]
    graph: CounterpartyGraph | None
    positions: list[BankPosition]
    market: list[MarketKnowledge]
    fixture_mode: bool


@dataclass
class _Feed:
    """Лента одной пары «компания × роль»: оценки сюжетов, быстрые и глубокие карточки."""

    company_id: str
    role: str
    sector: str
    scores: dict[str, RelevanceScore] = field(default_factory=dict)  # story_id -> значимость
    story_version: dict[str, int] = field(default_factory=dict)
    cards: dict[str, str] = field(default_factory=dict)  # story_id -> card_id (сейчас в ленте)
    deep: set[str] = field(default_factory=set)  # сюжеты с глубокой карточкой
    attempted: set[str] = field(default_factory=set)  # по ним глубокий разбор уже запускали или он не нужен
    pending: set[str] = field(default_factory=set)  # в очереди фонового разбора или разбираются сейчас
    seen: dict[str, int] = field(default_factory=dict)  # все оценённые сюжеты (и ниже порога) -> версия
    checked_at: float = 0.0  # time.monotonic() последней сверки с базой
    pinned: set[str] = field(default_factory=set)  # прислал пользователь (/analyze): из ленты не убираем


class PipelineCardStore:
    """Лента из многих сюжетов: быстрые карточки кодом для всех, глубокий разбор конвейером для лучших.

    Сюжеты берутся из источника данных (`TOOL_SOURCE=db` — БД Р1, иначе фикстура). Для пары
    «компания из сессии × роль» все сюжеты оцениваются `score_pair`, быстрая карточка есть у каждого
    сюжета выше порога; глубокий разбор — только для `select_deep(...)` по остатку бюджета и не более
    K за обращение (`FEED_DEEP_PER_REQUEST`). На живых моделях разбор идёт в фоновом потоке
    (`FEED_BACKGROUND`): /feed отвечает сразу, в очереди у ленты не больше K.
    Глубокая карточка заменяет быструю того же сюжета.
    Разбор отрасли переиспользуется кешем «сюжет × отрасль × версия». Ошибка конвейера не роняет запрос:
    остаётся быстрая карточка со статусом «на проверке».
    """

    def __init__(
        self,
        bundle_name: str = "rate_14",
        kv: KV | None = None,
        *,
        source: ToolDataSource | None = None,
        runner: Callable[..., tuple[InsightCard, RunTrace]] | None = None,
        deep_per_request: int | None = None,
        background: bool | None = None,
        refresh_s: float | None = None,
    ) -> None:
        # kv=None — взять из CACHE_BACKEND (redis -> клиент, иначе память)
        kv = kv if kv is not None else make_kv()
        self._redis = RedisJsonStore(kv, cache_ttl_s()) if kv is not None else None
        self._bundle_name = bundle_name
        self._explicit_source = source
        self._runner = runner or run_for_client
        self._deep_per_request = (
            deep_per_request
            if deep_per_request is not None
            else feed_deep_per_request()
        )
        self.deep_log: list[DeepRun] = []  # журнал глубоких разборов, в т. ч. неудачных и отброшенных
        self._fallback = FixtureCardStore(bundle_name)
        self._handlers: Mapping[State, Callable[[PipelineContext], Any]] | None = None
        self.analysis_cache = AnalysisCache(backend=self._redis)
        self._cards: dict[str, InsightCard] = {}
        self._traces: dict[str, RunTrace] = {}
        self._feeds: dict[tuple[str, str], _Feed] = {}
        self._lock = threading.RLock()
        self._demo_done = False
        self.runs = 0  # сколько раз запускался конвейер (для тестов и мониторинга)
        self._background = feed_background() if background is None else background
        self._refresh_s = feed_refresh_s() if refresh_s is None else refresh_s
        # один фоновый поток на хранилище: разборы идут по одному, бюджет и лимит модели не перегружаются
        self._jobs: queue.Queue[tuple[_Feed, str]] = queue.Queue()
        self._worker: threading.Thread | None = None

    # --- данные ---

    def _src(self) -> ToolDataSource:
        if self._explicit_source is not None:
            return self._explicit_source
        if os.environ.get("TOOL_SOURCE", "").strip().lower() == "db":
            return get_tool_source()
        return FixtureDataSource(self._bundle_name)

    # --- вычисление ---

    def _context(self, company_id: str) -> _Ctx | None:
        src = self._src()
        profile = profile_v1(src, company_id)
        if profile is None:
            return None
        return _Ctx(
            profile=profile,
            facts={f.story_id: f for f in src.event_facts()},
            items={i.id: i for i in src.raw_items()},
            sources={s.id: s for s in src.sources()},
            graph=src.counterparties(company_id),
            positions=src.bank_positions(),
            market=src.market_knowledge(),
            fixture_mode=os.environ.get("LLM_MODE", "").strip().lower() == "fixture",
        )

    def _score_story(self, ctx: _Ctx, role: str, story: Story) -> tuple[RelevanceScore, InsightCard]:
        facts = ctx.facts.get(story.id)
        surprise = find_surprise(story, facts, bank_positions=ctx.positions, market=ctx.market)
        rel = score_pair(
            story, ctx.profile, now=story.last_seen if ctx.fixture_mode else datetime.now(UTC),
            facts=facts, graph=ctx.graph, sources=ctx.sources, surprise=surprise,
            role=role,  # type: ignore[arg-type]
        )
        card = fast_card(
            story, rel, role,  # type: ignore[arg-type]
            facts=facts, items=ctx.items, sources=ctx.sources,
            bank_positions=ctx.positions, profile=ctx.profile, market=ctx.market,
        )
        return rel, card

    def _put_fast(self, feed: _Feed, ctx: _Ctx, story: Story, rel: RelevanceScore, card: InsightCard) -> None:
        feed.scores[story.id] = rel
        feed.story_version[story.id] = story.version
        self._cards[card.card_id] = card
        feed.cards[story.id] = card.card_id
        if ctx.facts.get(story.id) is None:
            feed.attempted.add(story.id)  # без фактов глубокий разбор не запускаем
        elif self._load_cached(feed, story.id):
            feed.attempted.add(story.id)

    def _build_feed(self, company_id: str, role: str) -> _Feed | None:
        ctx = self._context(company_id)
        if ctx is None:
            return None
        min_score = self._min_score()
        feed = _Feed(company_id, role, ctx.profile.sector or "", checked_at=time.monotonic())
        for story, rel, card in self._score_all(ctx, role, self._src().stories()):
            feed.seen[story.id] = story.version
            if rel.score >= min_score:
                self._put_fast(feed, ctx, story, rel, card)
        return feed

    def _score_all(
        self, ctx: _Ctx, role: str, stories: list[Story]
    ) -> list[tuple[Story, RelevanceScore, InsightCard]]:
        out = []
        for story in stories:
            try:
                out.append((story, *self._score_story(ctx, role, story)))
            except Exception as e:  # один плохой сюжет не ломает ленту
                log.warning("сюжет %s для %s/%s пропущен: %r", story.id, ctx.profile.company_id, role, e)
        return out

    def _refresh(self, feed: _Feed) -> None:
        """Сверка ленты с базой (сборщик работает непрерывно): новые сюжеты и новые версии — быстрые карточки
        и снова в очередь глубокого разбора; сюжеты, исчезнувшие при пересклейке, — из ленты. Глубокие
        карточки неизменившихся сюжетов остаются. Чтение базы и оценка — вне блокировки."""
        try:
            ctx = self._context(feed.company_id)
            stories = self._src().stories()
        except Exception as e:  # база недоступна — показываем ленту как есть
            log.warning("лента %s/%s не обновлена: %r", feed.company_id, feed.role, e)
            return
        if ctx is None:
            return
        with self._lock:
            seen = dict(feed.seen)
        changed = [st for st in stories if seen.get(st.id) != st.version]
        scored = self._score_all(ctx, feed.role, changed)
        ids = {st.id for st in stories}
        min_score = self._min_score()
        with self._lock:
            for sid in [sid for sid in feed.seen if sid not in ids]:
                if sid not in feed.pending and sid not in feed.pinned:
                    self._forget(feed, sid)
                    feed.seen.pop(sid, None)
            for story, rel, card in scored:
                if story.id in feed.pending:
                    continue  # разбор прежней версии идёт — новую возьмём на следующей сверке
                if story.id in feed.story_version:
                    self._forget(feed, story.id)
                feed.seen[story.id] = story.version
                if rel.score >= min_score or story.id in feed.pinned:
                    self._put_fast(feed, ctx, story, rel, card)
        if changed:
            log.info("лента %s/%s: обновлено сюжетов %d", feed.company_id, feed.role, len(changed))

    def _forget(self, feed: _Feed, story_id: str) -> None:
        """Убрать сюжет из ленты со всеми его карточками: пересклейка или новая версия."""
        old = feed.cards.pop(story_id, None)
        if old:
            self._cards.pop(old, None)
        feed.scores.pop(story_id, None)
        feed.story_version.pop(story_id, None)
        feed.deep.discard(story_id)
        feed.attempted.discard(story_id)

    def add_story(self, story_id: str, company_id: str, role: str) -> InsightCard | None:
        """Сюжет, который прислал пользователь («вставить ссылку или текст»): быстрая карточка сразу и в ленту
        пары из сессии — даже ниже порога ленты (пользователь сам попросил). Глубокий разбор — как у ленты,
        из `/feed` по очереди значимости. None — сюжета нет в источнике или нет профиля компании."""
        with self._lock:
            self._ensure(company_id, role, upgrade=False)
            feed = self._feeds.get((company_id, role))
            ctx = self._context(company_id)
            story = next((s for s in self._src().stories() if s.id == story_id), None)
            if feed is None or ctx is None or story is None:
                return None
            rel, card = self._score_story(ctx, role, story)
            old = feed.cards.get(story_id)
            if old and story_id in feed.deep:
                return self._cards.get(old, card)  # глубокая карточка уже есть — её и отдаём
            if old and old != card.card_id:
                self._cards.pop(old, None)
            self._put_fast(feed, ctx, story, rel, card)
            feed.pinned.add(story_id)
            return self._cards.get(feed.cards[story_id], card)

    def _load_cached(self, feed: _Feed, story_id: str) -> bool:
        if self._redis is None:
            return False
        found = self._redis.get_card(story_id, feed.company_id, feed.role)
        if found is None:
            return False
        card, run_id = found
        self._put_deep(feed, story_id, card)
        trace = self._redis.get_trace(run_id) if run_id else None
        if trace is not None:
            self._traces[trace.run_id] = trace
        return True

    def _put_deep(self, feed: _Feed, story_id: str, card: InsightCard) -> None:
        old = feed.cards.get(story_id)
        if old and old != card.card_id:
            self._cards.pop(old, None)
        self._cards[card.card_id] = card
        feed.cards[story_id] = card.card_id
        feed.deep.add(story_id)

    @staticmethod
    def _budget() -> tuple[float, float]:
        budget = get_client().budget
        return budget.spent_rub(), budget.daily_limit_rub

    def _upgrade(self, feed: _Feed) -> None:
        """Глубокий разбор лучших ещё не разобранных сюжетов: не более K за обращение, по остатку бюджета."""
        if self._deep_per_request <= 0 or not feed.sector:
            return
        todo = [r for sid, r in feed.scores.items() if sid not in feed.attempted]
        if not todo:
            return
        spent, limit = self._budget()
        pairs = select_deep(todo, {feed.company_id: feed.sector}, spent_rub=spent, limit_rub=limit)
        if not self._background:
            for pair in pairs[: self._deep_per_request]:
                self._run_deep(feed, pair.story_id)
            return
        # в очереди у ленты не больше K: фронт перечитывает ленту, пока есть быстрые карточки, и каждый
        # запрос не должен добавлять новые разборы, пока прежние не сделаны
        free = self._deep_per_request - len(feed.pending)
        for pair in pairs[: max(0, free)]:
            feed.attempted.add(pair.story_id)
            feed.pending.add(pair.story_id)
            self._jobs.put((feed, pair.story_id))
        if free > 0 and pairs:
            self._start_worker()

    def _start_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="deep-analysis", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            feed, story_id = self._jobs.get()
            try:
                self._run_deep_background(feed, story_id)
            except Exception as e:  # поток не должен умирать: следующий разбор очереди важнее
                log.warning("фоновый разбор %s для %s/%s: %r", story_id, feed.company_id, feed.role, e)
            finally:
                with self._lock:
                    feed.pending.discard(story_id)
                self._jobs.task_done()

    def _run_deep_background(self, feed: _Feed, story_id: str) -> None:
        """Модель — вне блокировки (лента, /card и /trace не ждут), результат — под блокировкой."""
        with self._lock:
            rel = feed.scores.get(story_id)
            if rel is None:
                return  # сюжет уже убран из ленты
            spent, limit = self._budget()  # бюджет мог кончиться, пока разбор ждал в очереди
            if not select_deep([rel], {feed.company_id: feed.sector}, spent_rub=spent, limit_rub=limit):
                feed.attempted.discard(story_id)  # бюджет освободится — разберём позже
                return
            handlers = self._get_handlers()
            self.runs += 1
        try:
            result = self._runner(
                story_id, feed.company_id, feed.role, sector=feed.sector,
                story_version=feed.story_version.get(story_id, 1), handlers=handlers,
            )
        except Exception as e:
            with self._lock:
                self._apply_failure(feed, story_id, e)
            return
        with self._lock:
            self._apply_result(feed, story_id, *result)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Дождаться, пока очередь фоновых разборов опустеет (тесты, прогрев кеша). False — не дождались."""
        done = threading.Event()

        def join() -> None:
            self._jobs.join()
            done.set()

        threading.Thread(target=join, daemon=True).start()
        return done.wait(timeout)

    def _log_run(
        self, feed: _Feed, story_id: str, status: str, *, reason: str | None = None,
        trace: RunTrace | None = None, score: float | None = None,
    ) -> None:
        run = DeepRun(
            story_id=story_id, company_id=feed.company_id, role=feed.role, status=status,  # type: ignore[arg-type]
            reason=reason, score=score, trace_id=trace.run_id if trace else None,
            cost_rub=round(trace.cost_rub, 6) if trace else 0.0, at=datetime.now(UTC),
        )
        self.deep_log.append(run)
        if status in ("failed", "dropped"):
            log.warning("глубокий разбор %s для %s/%s: %s (%s)", story_id, feed.company_id, feed.role,
                        status, reason)

    def deep_runs(self, company_id: str) -> list[DeepRun]:
        with self._lock:
            return [r for r in self.deep_log if r.company_id == company_id]

    def _drop(self, feed: _Feed, story_id: str) -> None:
        """Разбор показал, что сюжет клиенту не важен: убрать его карточку из ленты."""
        old = feed.cards.pop(story_id, None)
        if old:
            self._cards.pop(old, None)
        feed.scores.pop(story_id, None)

    def _get_handlers(self) -> Mapping[State, Callable[[PipelineContext], Any]]:
        if self._handlers is None:
            self._handlers = make_handlers(self._bundle_name, source=self._src(), cache=self.analysis_cache)
        return self._handlers

    def _run_deep(self, feed: _Feed, story_id: str) -> None:
        feed.attempted.add(story_id)
        handlers = self._get_handlers()
        self.runs += 1
        try:
            card, trace = self._runner(
                story_id, feed.company_id, feed.role, sector=feed.sector,
                story_version=feed.story_version.get(story_id, 1), handlers=handlers,
            )
        except Exception as e:
            self._apply_failure(feed, story_id, e)
            return
        self._apply_result(feed, story_id, card, trace)

    def _apply_failure(self, feed: _Feed, story_id: str, e: Exception) -> None:
        log.warning("конвейер для %s/%s/%s упал: %r", story_id, feed.company_id, feed.role, e)
        failed = self._degraded(feed, story_id, e)
        reason = f"{type(e).__name__}: {str(e)[:200]}"
        self._log_run(feed, story_id, "failed", reason=reason, trace=failed)

    def _apply_result(self, feed: _Feed, story_id: str, card: InsightCard, trace: RunTrace) -> None:
        self._traces[trace.run_id] = trace
        if _is_degraded(trace):
            self._log_run(feed, story_id, "failed", reason="pipeline_failed", trace=trace)
        if card.relevance.score < self._min_score() and story_id not in feed.pinned:
            self._drop(feed, story_id)
            self._log_run(
                feed, story_id, "dropped", reason="значимость после разбора ниже порога ленты",
                trace=trace, score=card.relevance.score,
            )
            return
        self._put_deep(feed, story_id, card)
        if not _is_degraded(trace):
            self._log_run(feed, story_id, card.review_status, trace=trace, score=card.relevance.score)
        # в общий кеш — только настоящие результаты, не деградацию
        if self._redis is not None and not _is_degraded(trace):
            self._redis.put_card(story_id, feed.company_id, feed.role, card, trace.run_id)
            self._redis.put_trace(trace)

    @staticmethod
    def _min_score() -> float:
        return feed_min_score()

    def _degraded(self, feed: _Feed, story_id: str, exc: Exception) -> RunTrace:
        trace = getattr(exc, "trace", None)
        if trace is None:
            now = datetime.now(UTC)
            trace = RunTrace(
                run_id=f"run_failed_{story_id}_{feed.company_id}_{feed.role}", story_id=story_id,
                company_id=feed.company_id, started_at=now, finished_at=now,
            )
        trace.steps.append(f"pipeline_failed:{type(exc).__name__}")
        self._traces[trace.run_id] = trace
        card = self._cards.get(feed.cards.get(story_id, ""))
        if card is None:
            cards = self._fallback.list_cards(feed.company_id, feed.role)  # type: ignore[arg-type]
            card = cards[0] if cards else None
        if card is not None:
            degraded = card.model_copy(update={"review_status": "on_review", "trace_id": trace.run_id})
            old = feed.cards.get(story_id)
            if old and old != degraded.card_id:
                self._cards.pop(old, None)
            self._cards[degraded.card_id] = degraded
            feed.cards[story_id] = degraded.card_id
        return trace

    def _ensure(self, company_id: str, role: str, *, upgrade: bool = True) -> None:
        with self._lock:
            feed = self._feeds.get((company_id, role))
            due = False
            if feed is None:
                feed = self._build_feed(company_id, role)
                if feed is None:
                    return
                self._feeds[(company_id, role)] = feed
            elif self._refresh_s >= 0 and time.monotonic() - feed.checked_at >= self._refresh_s:
                feed.checked_at = time.monotonic()  # сверку делает один запрос, остальные не ждут
                due = True
        if due:
            self._refresh(feed)
        if upgrade:
            with self._lock:
                self._upgrade(feed)

    def _ensure_demo(self) -> None:
        """Ленты демо-пар для /card и /trace. Глубокий разбор отсюда — только на фикстуре (бесплатно):
        на живых моделях его запускает только /feed пары из сессии, иначе лимит K за запрос не работает."""
        with self._lock:
            if self._demo_done:
                return
            fixture = get_client().settings.llm_mode == "fixture"
            for company_id, role in DEMO_PAIRS:
                self._ensure(company_id, role, upgrade=fixture)
            self._demo_done = True

    # --- протокол CardStore ---

    def list_cards(self, company_id: str, role: UserRole | None) -> list[InsightCard]:
        if role is not None:
            self._ensure(company_id, role)
        else:
            for c, r in DEMO_PAIRS:
                if c == company_id:
                    self._ensure(c, r)
        with self._lock:
            own = [c for c in self._cards.values() if c.company_id == company_id]
        by_role = [c for c in own if role is None or c.user_role == role]
        return sorted(by_role or own, key=lambda c: c.relevance.score, reverse=True)

    def get_card(self, card_id: str, company_id: str) -> InsightCard | None:
        self._ensure_demo()
        with self._lock:
            card = self._cards.get(card_id)
        return card if card and card.company_id == company_id else None

    def card_owner(self, card_id: str) -> str | None:
        self._ensure_demo()
        with self._lock:
            card = self._cards.get(card_id)
        return card.company_id if card else None

    def get_trace(self, trace_id: str) -> RunTrace | None:
        self._ensure_demo()
        with self._lock:
            return self._traces.get(trace_id)


_stores: dict[str, CardStore] = {}
_stores_lock = threading.Lock()


def store_mode() -> str:
    return "fixture" if os.environ.get("CARD_STORE", "pipeline").strip().lower() == "fixture" else "pipeline"


def reset_store() -> None:
    """Сбрасывает кеш хранилищ (тесты, смена фикстур)."""
    with _stores_lock:
        _stores.clear()


def get_store() -> CardStore:
    """FastAPI-зависимость: CARD_STORE=pipeline (по умолчанию) или fixture."""
    mode = store_mode()
    with _stores_lock:
        if mode not in _stores:
            _stores[mode] = PipelineCardStore() if mode == "pipeline" else FixtureCardStore()
        return _stores[mode]
