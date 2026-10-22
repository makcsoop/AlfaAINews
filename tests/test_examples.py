"""Примеры разборов аналитиков рынка в подсказке: фильтры по дате, origin, лимит, одинаковость, refs."""

from datetime import UTC, date, datetime

from backend.agents.analysts import (
    EXAMPLES_NOTE,
    AnalystOutput,
    Driver,
    build_messages,
    load_channels,
    run_analysts_detailed,
    validate_output,
)
from backend.agents.examples import estimate_tokens, select_examples
from backend.agents.tools.source import FixtureDataSource
from backend.schemas import MarketKnowledge

from .test_analysts import fixture_client, rate14_dossier

STORY_AT = datetime(2026, 9, 11, 10, 30, tzinfo=UTC)


def mk(id_, published, *, text="Ставку сохранили. Рынок ждал паузы.", **kw) -> MarketKnowledge:
    base = {
        "id": id_,
        "kind": "post_event_review",
        "source_id": "chan_a",
        "item_id": f"item_{id_}",
        "published_at": published,
        "event_type": "rate_decision",
        "statement": text,
        "origin": "site",
        "timing": "after_event",
    }
    base.update(kw)
    return MarketKnowledge(**base)


class Src(FixtureDataSource):
    def __init__(self, items: list[MarketKnowledge]) -> None:
        super().__init__("rate_14")
        self._items = items

    def market_knowledge(self) -> list[MarketKnowledge]:
        return list(self._items)


def sel(items, **kw):
    args = {"event_type": "rate_decision", "sector": "46", "story_published_at": STORY_AT}
    args.update(kw)
    return select_examples(Src(items), **args)


def test_filters_after_story_and_same_event():
    items = [
        mk("old", datetime(2026, 7, 24, 18, tzinfo=UTC)),
        mk("after", datetime(2026, 9, 12, 9, tzinfo=UTC)),  # после даты сюжета
        mk("same_event", datetime(2026, 9, 10, 18, tzinfo=UTC), target_event_date=date(2026, 9, 11)),
        mk("same_moment", STORY_AT),  # не строго раньше
        mk("other_kind", datetime(2026, 7, 1, tzinfo=UTC), kind="mechanism"),
        mk("other_type", datetime(2026, 7, 2, tzinfo=UTC), event_type="fx_shock"),
    ]
    assert [e.id for e in sel(items)] == ["old"]


def test_ranking_sector_then_fresh_and_k():
    items = [
        mk("fresh_other", datetime(2026, 8, 1, tzinfo=UTC), sectors_okved=["10"]),
        mk("old_sector", datetime(2026, 3, 1, tzinfo=UTC), sectors_okved=["46.1"]),
        mk("mid_other", datetime(2026, 6, 1, tzinfo=UTC)),
        mk("fresh_sector", datetime(2026, 7, 1, tzinfo=UTC), sectors_okved=["46"]),
    ]
    assert [e.id for e in sel(items, k=3)] == ["fresh_sector", "old_sector", "fresh_other"]


def test_token_limit_and_sentence_cut():
    sentence = "Рынок разобрал решение по ставке подробно. "
    long_text = sentence * 40  # ~1700 символов каждый
    items = [mk(f"m{i}", datetime(2026, 7, 1 + i, tzinfo=UTC), text=long_text) for i in range(5)]
    out = sel(items, max_tokens=600, k=3)
    assert out and sum(estimate_tokens(e.text) for e in out) <= 600
    assert sum(len(e.text) for e in out) // 4 <= 600
    assert all(e.text.endswith(".") for e in out)  # обрезка по предложению
    small = sel(items, max_tokens=100, k=3)
    assert sum(len(e.text) for e in small) // 4 <= 100 and len(small) >= 1


def test_telegram_origin_filter():
    items = [
        mk("tg", datetime(2026, 8, 1, tzinfo=UTC), origin="telegram"),
        mk("site", datetime(2026, 7, 1, tzinfo=UTC), origin="site"),
    ]
    assert [e.id for e in sel(items)] == ["tg", "site"]
    assert [e.id for e in sel(items, allow_telegram_origin=False)] == ["site"]


def test_same_examples_in_both_analysts_inside_data(tmp_path):
    examples = sel([mk("old", datetime(2026, 7, 24, tzinfo=UTC), text="Уникальный разбор прошлой паузы.")])
    dossier, channels = rate14_dossier(), load_channels()
    users = {a: build_messages(a, dossier, channels, examples)[1]["content"] for a in ("business", "finance")}
    tails = {a: u[u.index(EXAMPLES_NOTE) :] for a, u in users.items()}
    assert tails["business"] == tails["finance"]
    for u in users.values():
        start = u.index("<data>", u.index(EXAMPLES_NOTE))
        end = u.index("</data>", start)
        assert "Уникальный разбор прошлой паузы." in u[start:end]
    assert "ссылаться на них нельзя" in EXAMPLES_NOTE
    # без примеров сообщение прежнее
    assert EXAMPLES_NOTE not in build_messages("business", dossier, channels)[1]["content"]
    res = run_analysts_detailed(dossier, client=fixture_client(tmp_path), trace_id="t-ex", examples=examples)
    assert all(r.failure is None and r.report is not None for r in res.values())


def test_ref_to_example_is_dropped():
    dossier, channels = rate14_dossier(), load_channels()
    ex_id = "mk_example_old"
    out = AnalystOutput(
        drivers=[
            Driver(
                channel="rate_cost",
                direction="up",
                strength="high",
                horizon="quarter",
                refs=[ex_id],
                rationale="Ссылка на пример",
            ),
            Driver(
                channel="fx_rub",
                direction="flat",
                strength="low",
                horizon="days",
                refs=["fact:st_2026-09-11_keyrate:2", ex_id],
                rationale="Смешанные ссылки",
            ),
        ],
        summary="Ставка выше ожиданий.",
    )
    kept, notes, _ = validate_output(out, dossier, channels)
    assert kept == [] and len(notes) == 2 and all(ex_id in n for n in notes)


def test_pipeline_step_passes_examples_to_analysts(monkeypatch):
    from backend.agents.pipeline import PipelineContext
    from backend.agents.steps import analysts_step
    from backend.llm import reset_client

    monkeypatch.setenv("LLM_MODE", "fixture")
    reset_client()
    src = Src([mk("past_review", datetime(2026, 7, 24, 18, tzinfo=UTC))])
    ctx = PipelineContext(
        story_id="st_2026-09-11_keyrate", story_version=1, sector="46", priority=0.9, trace_id="t-examples"
    )
    ctx.dossier = rate14_dossier().model_dump(mode="json")
    ctx = analysts_step(ctx, src)
    assert ctx.artifacts["examples"] == ["past_review"]
    assert ctx.reports
    reset_client()
