"""Аналитики бизнеса и финансов на фикстуре rate_14 (LLM_MODE=fixture, сеть не нужна)."""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from newsai.agents.analysts import (
    AnalystOutput,
    agreement,
    build_messages,
    find_evaluative,
    load_channels,
    load_prompt,
    run_analysts,
    run_analysts_detailed,
    validate_output,
)
from newsai.agents.dossier import Dossier, DossierItem
from newsai.fixtures import load_bundle
from newsai.llm import BudgetExceeded, LLMClient, LLMUnavailable
from newsai.llm.budget import BudgetManager, InMemoryBudgetStore
from newsai.llm.cache import ResponseCache
from newsai.llm.client import extract_json
from newsai.llm.journal import Journal
from newsai.llm.settings import LLMSettings
from newsai.schemas import AnalystReport

FIXTURES = Path("newsai/llm/fixtures")


def fixture_client(tmp_path) -> LLMClient:
    s = LLMSettings(
        _env_file=None,
        llm_mode="fixture",
        llm_journal_dir=str(tmp_path / "journal"),
        llm_cache_dir=str(tmp_path / "cache"),
    )
    return LLMClient(
        settings=s,
        budget=BudgetManager(settings=s, store=InMemoryBudgetStore()),
        journal=Journal(s.llm_journal_dir),
        cache=ResponseCache(s.llm_cache_dir, False),
    )


def rate14_dossier() -> Dossier:
    """Досье пары «rate_14 × отрасль 46», собранное вручную из bundle (без планировщика)."""
    b = load_bundle("rate_14")
    story = b["story"][0]
    facts = b["event_facts"][0]
    d = Dossier(
        story_id=story.id,
        sector="46",
        event_type=story.event_type,
        story_published_at=datetime.fromisoformat("2026-09-11T10:30:00+00:00"),
        trace_id="t-rate14",
    )
    d.add(DossierItem(id=story.id, kind="story", title=story.title))
    d.add(
        DossierItem(
            id="fact:st_2026-09-11_keyrate:1", kind="fact", title="Суть события", text=facts.summary
        )
    )
    d.add(
        DossierItem(
            id="fact:st_2026-09-11_keyrate:2",
            kind="fact",
            title="Ключевая ставка сохранена",
            data={"value": 14.0, "unit": "%"},
            text=facts.numbers[0].quote,
        )
    )
    d.add(
        DossierItem(
            id="fact:st_2026-09-11_keyrate:3",
            kind="fact",
            title="Первая пауза после снижений",
            data={"value": 10, "unit": "снижений"},
            text=facts.numbers[1].quote,
        )
    )
    for r in b["raw_item"]:
        d.add(DossierItem(id=r.id, kind="news", title=r.title, text=r.lead or ""))
    for p in b["bank_position"]:
        d.add(DossierItem(id=p.id, kind="bank_position", title=p.thesis, text=p.quote or ""))
    for m in b["market_knowledge"]:
        d.add(DossierItem(id=m.id, kind="market_knowledge", title=m.kind, text=m.statement))
    # элемент, который аналитикам не показывается
    d.add(
        DossierItem(
            id="profile:U_DEMO_BORROWER", kind="profile", title="профиль клиента", text="SECRET_PROFILE"
        )
    )
    return d


@pytest.fixture
def dossier() -> Dossier:
    return rate14_dossier()


# --- оба аналитика отвечают по схеме ---


def test_both_analysts_answer_on_rate14(tmp_path, dossier):
    res = run_analysts_detailed(dossier, client=fixture_client(tmp_path), trace_id="t1")
    for name in ("business", "finance"):
        r = res[name]
        assert r.failure is None and r.report is not None
        assert r.prompt_version == f"analyst_{name}@1"
        assert r.notes == [] and r.check_failures == 0
        assert r.drivers_total == len(r.report.channels) >= 3
        assert r.cost_rub == 0.0
    fin = {c.channel: c for c in res["finance"].report.channels}
    assert fin["rate_cost"].impact == pytest.approx(0.9)  # ставка ↑ высокая сила — направление фактора
    assert fin["deposit_yield"].impact == pytest.approx(0.6)
    assert fin["fx_rub"].impact == pytest.approx(0.3)
    assert fin["consumer_demand"].impact == pytest.approx(-0.3)


def test_conversion_to_analyst_report(tmp_path, dossier):
    reports = run_analysts(dossier, client=fixture_client(tmp_path), trace_id="t2")
    rep = reports["finance"]
    assert isinstance(rep, AnalystReport)
    AnalystReport.model_validate(rep.model_dump(mode="json"))
    assert rep.analyst == "finance" and rep.sector == "46" and rep.story_id == dossier.story_id
    rate = next(c for c in rep.channels if c.channel == "rate_cost")
    assert rate.horizon == "quarters"
    assert {e.source_id for e in rate.evidence} == {
        "fact:st_2026-09-11_keyrate:2",
        "6d3f1b2a9c0e4f7a8b1c2d3e4f5a6b7c8d9e0f1a",
        "cmi_keyrate_2026_09#p1#c1",
    }
    assert all(e.quote for e in rate.evidence)  # у этих элементов в досье есть текст
    assert next(c for c in reports["business"].channels if c.channel == "construction").horizon == "years"
    assert next(c for c in reports["finance"].channels if c.channel == "fx_rub").horizon == "days"
    # flat -> 0
    assert next(c for c in reports["business"].channels if c.channel == "fx_rub").impact == 0.0


def test_agreement_for_arbiter(tmp_path, dossier):
    reports = run_analysts(dossier, client=fixture_client(tmp_path), trace_id="t3")
    ag = {a.channel: a for a in agreement(reports)}
    assert set(ag) == {"rate_cost", "consumer_demand", "fx_rub"}
    assert ag["rate_cost"].same_direction and ag["rate_cost"].strength_diff == pytest.approx(0.3)
    assert ag["consumer_demand"].same_direction and ag["consumer_demand"].strength_diff == 0
    assert not ag["fx_rub"].same_direction  # бизнес: flat, финансы: up
    assert agreement({"business": reports["business"], "finance": None}) == []


# --- проверки кодом ---


def test_evaluative_words_detected():
    for text in (
        "это хорошо",
        "Плохо для заёмщиков",
        "позитивный сигнал",
        "негативно",
        "выгодно держать вклады",
        "риск для отрасли",
        "риски для экспортёров",
        "благоприятные условия",
        "неблагоприятно",
        "возможность для роста",
    ):
        assert find_evaluative(text), text
    assert find_evaluative("Ставка ↑: стоимость заёмных средств остаётся на уровне 14%") == []
    assert find_evaluative("спрос ↓, курс рубля ↑, горизонт квартал") == []


def test_evaluative_driver_is_flagged_and_excluded_from_report(tmp_path, dossier):
    res = run_analysts_detailed(
        dossier, client=fixture_client(tmp_path), trace_id="t4", fixture_keys={"finance": "bad_evaluative"}
    )["finance"]
    assert res.report is not None
    flagged = [d for d in res.drivers if d.flags]
    assert [d.driver.channel for d in flagged] == ["rate_cost"]
    assert {f.split(":")[0] for f in flagged[0].flags} == {"evaluative"}
    assert res.summary_flags  # «негатив…», «неблагоприятн…» в summary
    assert res.check_failures == 2
    assert [c.channel for c in res.report.channels] == ["deposit_yield"]  # оценочный драйвер в отчёт не идёт
    # оценочная сводка заменена нейтральной, собранной кодом из драйверов
    assert not find_evaluative(res.report.summary)
    assert "deposit_yield" in res.report.summary and "средняя" in res.report.summary
    assert "кварталы" in res.report.summary and "rate_cost" not in res.report.summary


def test_missing_ref_and_unknown_channel_dropped(tmp_path, dossier):
    res = run_analysts_detailed(
        dossier, client=fixture_client(tmp_path), trace_id="t5", fixture_keys={"finance": "bad_ref"}
    )["finance"]
    assert res.drivers_total == 3
    assert [c.channel for c in res.report.channels] == ["rate_cost"]
    assert any("cmi_nonexistent_doc#p9#c9" in n for n in res.notes)
    assert any("crypto_mining" in n and "таксономии" in n for n in res.notes)


def test_hidden_profile_ref_is_not_valid(tmp_path):
    d = rate14_dossier()
    out = AnalystOutput.model_validate(
        {
            "drivers": [
                {
                    "channel": "rate_cost",
                    "direction": "up",
                    "strength": "low",
                    "horizon": "days",
                    "refs": ["profile:U_DEMO_BORROWER"],
                    "rationale": "ссылка на скрытый от аналитика элемент",
                }
            ],
            "summary": "s",
        }
    )
    visible = {i.id for i in d.items if i.kind != "profile"}
    kept, notes, _ = validate_output(out, d, load_channels(), visible_ids=visible)
    assert kept == [] and notes


def test_schema_rejects_bad_shapes():
    base = {
        "channel": "rate_cost",
        "direction": "up",
        "strength": "low",
        "horizon": "days",
        "refs": ["x"],
        "rationale": "ok",
    }
    for patch in (
        {"refs": []},
        {"direction": "good"},
        {"horizon": "months"},
        {"rationale": "я" * 301},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            AnalystOutput.model_validate({"drivers": [{**base, **patch}], "summary": "s"})


# --- устойчивость ---


def test_one_analyst_fails_other_works(tmp_path, dossier):
    res = run_analysts_detailed(
        dossier, client=fixture_client(tmp_path), trace_id="t6", fixture_keys={"finance": "bad_json"}
    )
    assert res["finance"].report is None and "LLMUnavailable" in (res["finance"].failure or "")
    assert res["business"].report is not None and res["business"].failure is None
    reports = run_analysts(
        dossier, client=fixture_client(tmp_path), trace_id="t6b", fixture_keys={"finance": "bad_json"}
    )
    assert reports["finance"] is None and reports["business"] is not None


def test_budget_exceeded_does_not_kill_other(tmp_path, dossier):
    client = fixture_client(tmp_path)
    real = client.budget.check

    def check(step, priority=None):
        if step == "analyst_business":
            raise BudgetExceeded(step, "test")
        return real(step, priority)

    client.budget.check = check  # type: ignore[method-assign]
    res = run_analysts_detailed(dossier, client=client, trace_id="t7")
    assert res["business"].report is None and "BudgetExceeded" in res["business"].failure
    assert res["finance"].report is not None


def test_missing_fixture_is_unavailable_not_crash(tmp_path, dossier):
    res = run_analysts_detailed(
        dossier, client=fixture_client(tmp_path), trace_id="t8", fixture_keys={"business": "nope"}
    )
    assert res["business"].report is None and res["finance"].report is not None
    assert issubclass(LLMUnavailable, Exception)


# --- промпты и независимость ---


def test_prompt_versions_in_headers():
    for step in ("analyst_business", "analyst_finance"):
        text, version = load_prompt(step)
        assert version == f"{step}@1"
        assert text.startswith(f"<!-- version: {step}@1 -->")
        assert "<data>" in text  # правило про блок данных
        assert "refs" in text


def test_news_only_inside_data_block_and_profile_hidden(dossier):
    channels = load_channels()
    for analyst in ("business", "finance"):
        msgs = build_messages(analyst, dossier, channels)
        user = msgs[1]["content"]
        assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
        head, _, data = user.partition("<data>")
        body = data.split("</data>")[0]
        title = dossier.get("6d3f1b2a9c0e4f7a8b1c2d3e4f5a6b7c8d9e0f1a").title
        assert title in body and title not in head  # новость только внутри <data>
        assert "ЗАГЛУШКА: консенсус" in body  # знание рынка тоже внутри <data>
        assert "SECRET_PROFILE" not in user and "profile:U_DEMO_BORROWER" not in user
        for cid in channels:  # все каналы таксономии, не только типа события
            assert f"- {cid}:" in user
    assert len(channels) == 15


def test_analysts_get_identical_input_and_do_not_see_each_other(tmp_path, dossier):
    channels = load_channels()
    mb = build_messages("business", dossier, channels)
    mf = build_messages("finance", dossier, channels)
    assert mb[1] == mf[1]  # одно досье, один список каналов
    assert mb[0] != mf[0]
    # реальные вызовы: в сообщениях каждого нет текста ответа другого
    client = fixture_client(tmp_path)
    seen: dict[str, list[dict[str, str]]] = {}
    real = client.transport.complete

    def spy(req):
        seen[req.step] = req.messages
        return real(req)

    client.transport.complete = spy  # type: ignore[method-assign]
    run_analysts_detailed(dossier, client=client, trace_id="t9")
    assert set(seen) == {"analyst_business", "analyst_finance"}
    ans_b = AnalystOutput.model_validate_json((FIXTURES / "analyst_business/default.json").read_text("utf-8"))
    ans_f = AnalystOutput.model_validate_json((FIXTURES / "analyst_finance/default.json").read_text("utf-8"))
    text_b = "\n".join(m["content"] for m in seen["analyst_business"])
    text_f = "\n".join(m["content"] for m in seen["analyst_finance"])
    assert ans_f.summary not in text_b and ans_b.summary not in text_f
    assert all(d.rationale not in text_b for d in ans_f.drivers)
    assert all(d.rationale not in text_f for d in ans_b.drivers)


def test_journal_has_prompt_version(tmp_path, dossier):
    client = fixture_client(tmp_path)
    run_analysts_detailed(dossier, client=client, trace_id="t10")
    entries = client.journal._entries
    versions = {e.step: e.prompt_version for e in entries}
    assert versions == {"analyst_business": "analyst_business@1", "analyst_finance": "analyst_finance@1"}
    assert all(e.cost_rub == 0.0 for e in entries)


# --- метрики на фикстурах ---


def test_metrics_valid_json_and_refs_share(dossier, capsys):
    files = sorted(FIXTURES.glob("analyst_*/*.json"))
    valid = 0
    ref_total = ref_ok = 0
    for f in files:
        try:
            out = AnalystOutput.model_validate_json(extract_json(f.read_text("utf-8")))
        except ValueError:
            continue
        valid += 1
        for d in out.drivers:
            ref_total += 1
            ref_ok += all(r in dossier.ids() for r in d.refs)
    default_files = [f for f in files if f.name == "default.json"]
    print(
        f"\nМЕТРИКИ: валидный JSON {valid}/{len(files)}; "
        f"утверждений с существующими refs {ref_ok}/{ref_total}"
    )
    assert len(default_files) == 2
    assert valid == len(files) - 1  # не проходит только bad_json
    # на «хороших» фикстурах (default) все refs существуют
    for f in default_files:
        out = AnalystOutput.model_validate_json(f.read_text("utf-8"))
        assert all(r in dossier.ids() for d in out.drivers for r in d.refs)


def test_evaluative_open_questions_dropped_and_clean_summary_kept(tmp_path, dossier):
    from newsai.agents.analysts import to_report

    out = AnalystOutput(
        drivers=[],
        summary="Ставка остаётся на уровне 14%.",
        open_questions=["Как это плохо скажется на спросе?", "Когда ЦБ начнёт снижать ставку?"],
    )
    rep = to_report(out, [], dossier, "finance", "m")
    assert rep.summary == "Ставка остаётся на уровне 14%."  # чистая сводка не трогается
    assert rep.open_questions == ["Когда ЦБ начнёт снижать ставку?"]
    bad = out.model_copy(update={"summary": "Это негативно."})
    assert not find_evaluative(to_report(bad, [], dossier, "finance", "m").summary)
