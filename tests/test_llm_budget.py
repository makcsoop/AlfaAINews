from datetime import UTC, datetime

import pytest

from backend.llm.budget import (
    BudgetExceeded,
    BudgetManager,
    InMemoryBudgetStore,
    RedisBudgetStore,
    budget_day,
)
from backend.llm.models import default_models_config, parse_models_config
from backend.llm.settings import LLMSettings


def make(limit: float = 100.0, archive_cap: float = 20.0) -> BudgetManager:
    s = LLMSettings(_env_file=None, llm_daily_budget_rub=limit, llm_archive_daily_cap_rub=archive_cap)
    return BudgetManager(settings=s, store=InMemoryBudgetStore())


def test_models_config_fallback_other_family():
    cfg = default_models_config()
    assert len(cfg.models) == 6
    for name, step in cfg.steps.items():
        if step.fallback:
            assert cfg.model(step.fallback).family != cfg.model(step.model).family, name
    assert cfg.step("vision").fallback is None
    assert cfg.step("knowledge_archive").budget_group == "archive"


def test_models_config_rejects_same_family_fallback():
    raw = {
        "models": {
            "a": {"provider_name": "qwen/qwen3.6-35b-a3b", "family": "qwen",
                  "price_in_rub_per_m": 1, "price_out_rub_per_m": 1},
            "b": {"provider_name": "qwen/qwen3-vl-30b-a3b-instruct", "family": "qwen",
                  "price_in_rub_per_m": 1, "price_out_rub_per_m": 1},
        },
        "steps": {"x": {"model": "a", "fallback": "b", "budget_group": "g"}},
    }
    with pytest.raises(ValueError, match="другого семейства"):
        parse_models_config(raw)


def test_models_config_rejects_unknown_provider_model():
    raw = {"models": {"a": {"provider_name": "openai/gpt-x", "family": "gpt",
                            "price_in_rub_per_m": 1, "price_out_rub_per_m": 1}}, "steps": {}}
    with pytest.raises(ValueError, match="разрешённых"):
        parse_models_config(raw)


def test_modes_by_share():
    b = make()
    assert b.mode() == "normal"
    b.record("extract", "r1", 10, 10, 69.0)
    assert b.mode() == "normal"
    b.record("extract", "r1", 10, 10, 1.0)
    assert b.mode() == "save70"
    b.record("extract", "r1", 10, 10, 20.0)
    assert b.mode() == "save90"
    assert b.remaining_rub() == pytest.approx(10.0)


def test_save70_blocks_archive_and_low_priority_deep():
    b = make()
    b.record("card", "r3", 1, 1, 75.0)
    assert b.mode() == "save70"
    with pytest.raises(BudgetExceeded) as e:
        b.check("knowledge_archive")
    assert e.value.mode == "save70"
    with pytest.raises(BudgetExceeded):
        b.check("planner", priority=0.2)
    with pytest.raises(BudgetExceeded):
        b.check("analyst_business", priority=None)
    b.check("planner", priority=0.8)
    b.check("critic_llm", priority=0.5)
    b.check("extract")
    b.check("card")
    b.check("qa")


def test_save90_only_qa():
    b = make()
    b.record("card", "r3", 1, 1, 92.0)
    assert b.mode() == "save90"
    for step in ("extract", "planner", "card", "knowledge_stream", "vision"):
        with pytest.raises(BudgetExceeded):
            b.check(step, priority=1.0)
    b.check("qa")


def test_full_exhaustion_blocks_even_qa():
    b = make()
    b.record("qa", "r3", 1, 1, 100.0)
    with pytest.raises(BudgetExceeded):
        b.check("qa")


def test_archive_cap_separate():
    b = make(limit=1000.0, archive_cap=20.0)
    b.check("knowledge_archive")
    b.record("knowledge_archive", "r1", 100, 100, 20.0)
    assert b.mode() == "normal"
    with pytest.raises(BudgetExceeded) as e:
        b.check("knowledge_archive")
    assert e.value.mode == "archive_cap"
    b.check("knowledge_stream")  # другая группа не затронута


def test_summary_breakdown():
    b = make()
    b.record("extract", "r1", 100, 50, 1.5)
    b.record("card", "r3", 200, 80, 2.5)
    s = b.summary()
    assert s["spent_rub"] == pytest.approx(4.0)
    assert s["tokens_in"] == 300 and s["tokens_out"] == 130 and s["calls"] == 2
    assert s["by_step"]["extract"]["rub"] == pytest.approx(1.5)
    assert s["by_group"]["card"]["tin"] == 200
    assert s["by_dev"]["r1"]["calls"] == 1
    assert s["mode"] == "normal"


def test_budget_day_resets_at_0300_msk():
    # 02:59 МСК = 23:59 UTC предыдущего дня -> ещё вчерашние сутки
    before = datetime(2026, 10, 20, 23, 59, tzinfo=UTC)
    after = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    assert budget_day(before) == "2026-10-20"
    assert budget_day(after) == "2026-10-21"


def test_reset_with_clock():
    now = [datetime(2026, 10, 20, 12, 0, tzinfo=UTC)]
    s = LLMSettings(_env_file=None, llm_daily_budget_rub=100.0)
    b = BudgetManager(settings=s, clock=lambda: now[0])
    b.record("card", "r3", 1, 1, 95.0)
    assert b.mode() == "save90"
    now[0] = datetime(2026, 10, 21, 0, 30, tzinfo=UTC)  # 03:30 МСК
    assert b.mode() == "normal"


class FakeRedis:
    def __init__(self):
        self.h: dict[str, dict[str, float]] = {}

    def hincrbyfloat(self, k, f, v):
        self.h.setdefault(k, {})
        self.h[k][f] = self.h[k].get(f, 0.0) + v

    def expire(self, k, ttl):
        pass

    def hgetall(self, k):
        return {a: str(b) for a, b in self.h.get(k, {}).items()}


def test_redis_store_with_fake_client():
    store = RedisBudgetStore("redis://unused", client=FakeRedis())
    store.incr("2026-10-21", "rub", 1.5)
    store.incr("2026-10-21", "rub", 2.0)
    assert store.snapshot("2026-10-21") == {"rub": 3.5}
    assert store.snapshot("2026-10-22") == {}


def test_key_spend_from_provider_drives_modes():
    """Расход ключа всей командой (заголовок LiteLLM) важнее своего учёта процесса."""
    from backend.llm.budget import BudgetManager, InMemoryBudgetStore
    from backend.llm.settings import LLMSettings

    b = BudgetManager(settings=LLMSettings(llm_daily_budget_rub=100.0), store=InMemoryBudgetStore())
    b.record("card", "r3", 10, 10, 1.0)
    assert b.spent_rub() == 1.0 and b.mode() == "normal"
    b.observe_key_spend(75.0)  # коллеги уже потратили
    assert b.spent_rub() == 75.0 and b.mode() == "save70"
    b.observe_key_spend(50.0)  # значения не убывают в пределах суток
    assert b.spent_rub() == 75.0
    s = b.summary()
    assert s["own_spent_rub"] == 1.0 and s["key_spend_rub"] == 75.0 and s["remaining_rub"] == 25.0
