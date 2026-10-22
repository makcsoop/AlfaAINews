"""Итог бюджета по журналу: суммы по шагам, граница суток 03:00 МСК, флаг резерва."""

from datetime import UTC, datetime

from newsai.llm.journal import Journal, JournalEntry
from newsai.llm.report import build_report, load_entries, main, render_markdown


def _e(at, step="card", cost=1.0, trace="t1", model="m", dev="r3", **kw):
    return JournalEntry(
        trace_id=trace,
        step=step,
        model=model,
        dev=dev,
        cost_rub=cost,
        tokens_in=10,
        tokens_out=5,
        at=at,
        **kw,
    )


def test_day_boundary_and_steps():
    # 02:59 МСК (23:59 UTC) — ещё вчерашние сутки, 03:00 МСК (00:00 UTC) — новые
    before = datetime(2026, 10, 21, 23, 59, tzinfo=UTC)
    after = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
    entries = [
        _e(before, "card", 2.0),
        _e(after, "card", 3.0, trace="t2"),
        _e(after, "analyst_finance", 4.0, trace="t2", model="glm", fallback_used=True),
    ]
    rep = build_report(entries, "2026-10-22")
    assert rep["by_day_rub"] == {"2026-10-21": 2.0, "2026-10-22": 7.0}
    d = rep["day_summary"]
    assert d["cost_rub"] == 7.0 and d["calls"] == 2
    assert d["by_step"]["card"]["cost_rub"] == 3.0
    assert d["fallback_share"] == 0.5
    assert d["analysis"]["avg_rub"] == 7.0
    assert rep["all_summary"]["cost_rub"] == 9.0


def test_reserve_flag():
    at = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    assert build_report([_e(at, cost=349.0)], daily_limit_rub=500)["demo_reserve_ok"] is True
    assert build_report([_e(at, cost=351.0)], daily_limit_rub=500)["demo_reserve_ok"] is False


def test_cli_reads_journal(tmp_path):
    j = Journal(tmp_path / "j")
    j.record(_e(datetime(2026, 10, 22, 12, 0, tzinfo=UTC), cost=1.5, cached=True, mode="live"))
    j.record(_e(datetime(2026, 10, 22, 12, 1, tzinfo=UTC), cost=0.7, mode="fixture"))
    j.record(_e(datetime(2026, 10, 22, 12, 2, tzinfo=UTC), cost=0.3))  # старая запись без base — фикстура
    entries = load_entries(tmp_path / "j")
    assert len(entries) == 1  # ответы фикстур в расход ключа не входят
    assert len(load_entries(tmp_path / "j", live_only=False)) == 3
    out = tmp_path / "b.md"
    assert main(["--journal", str(tmp_path / "j"), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "резерв на день демо" in text and "key_spend" in text
    assert out.with_suffix(".json").exists()
    assert "Итог бюджета" in render_markdown(build_report(entries))
