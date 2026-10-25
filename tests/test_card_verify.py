"""Финальная проверка карточки кодом: числа, даты, метки, голос банка, перегенерация и шаблон."""

import json
import shutil
from pathlib import Path

import pytest

from backend.agents.pipeline import PipelineContext, State
from backend.agents.steps import card_verify_step, make_handlers, run_for_client
from backend.cards import template_draft, verify_card, write_card, write_verified_card
from backend.cards.card import CardUnavailable
from backend.cards.features import allowed_features
from backend.fixtures import load_bundle
from backend.llm import LLMClient
from tests.test_cards import CASES, FIXTURES, assemble, env, fixture_client

BUNDLE_KEYS = ("story", "dossier", "relevance", "profile", "role", "analyses", "graph")


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("rate_14")


def _check(e: dict, card, **kw):
    return verify_card(
        card, e["dossier"], profile_features=allowed_features(e["profile"], e["graph"], e["relevance"]),
        profile=kw.pop("profile", e["profile"]), analyses=kw.pop("analyses", e["analyses"]),
    )


def _llm_card(bundle, tmp_path, case):
    e = env(bundle, *case)
    res = write_card(**e, client=fixture_client(tmp_path), sources=bundle["source"])
    assert res.source == "llm"
    return e, res.card


def _codes(check) -> set[str]:
    return {f.code for f in check.failures}


def _with_text(card, claim_id: str, text: str):
    claims = [c.model_copy(update={"text": text}) if c.claim_id == claim_id else c for c in card.claims]
    return card.model_copy(update={"claims": claims})


@pytest.mark.parametrize("case", CASES)
def test_both_rate14_cards_pass(bundle, tmp_path, case):
    e, card = _llm_card(bundle, tmp_path, case)
    check = _check(e, card)
    assert check.ok, check.messages()


@pytest.mark.parametrize("case", CASES)
def test_template_card_always_passes(bundle, case):
    e = env(bundle, *case)
    draft = template_draft(**{k: e[k] for k in BUNDLE_KEYS})
    card = assemble(e, draft, sources=bundle["source"])
    check = _check(e, card)
    assert check.ok, check.messages()


def test_number_absent_from_quotes_fails(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    fid = card.what_happened[0]
    bad = _with_text(card, fid, "Совет директоров сохранил ключевую ставку на уровне 15% годовых.")
    check = _check(e, bad)
    assert not check.ok and "number_not_in_source" in _codes(check)
    assert any(f.claim_id == fid and "15" in f.detail for f in check.failures)


def test_date_absent_from_sources_fails(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bad = _with_text(card, card.what_happened[0], "Совет директоров 12 сентября 2026 года сохранил ставку.")
    assert "date_not_in_source" in _codes(_check(e, bad))


@pytest.mark.parametrize(
    "text",
    ["Ставка осталась на уровне 14,0%.", "Ставка осталась на уровне 14 процентов.", "Решение принято 11 сентября."],
)
def test_number_forms_are_equivalent(bundle, tmp_path, text):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    assert _check(e, _with_text(card, card.what_happened[0], text)).ok


def test_inference_number_must_come_from_supporting_data(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bad = _with_text(card, card.why_it_matters[0], "Расходы на обслуживание долга вырастут на 37 млн рублей.")
    assert "number_not_in_source" in _codes(_check(e, bad))


def test_bank_view_on_market_source_fails(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bid = card.bank_and_market[0]
    claims = [
        c.model_copy(update={"evidence": [c.evidence[0].model_copy(update={"source_id": "mk_unknown"})]})
        if c.claim_id == bid
        else c
        for c in card.claims
    ]
    check = _check(e, card.model_copy(update={"claims": claims}))
    assert {"bank_view_source", "unknown_source"} <= _codes(check)


def test_bank_voice_outside_bank_view_fails(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bad = _with_text(card, card.why_it_matters[0], "Банк ожидает сохранения ставки, поэтому долг дорожает.")
    assert "bank_voice" in _codes(_check(e, bad))


def test_missing_sections_fail(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    check = _check(e, card.model_copy(update={"what_would_change": [], "bank_and_market": []}))
    assert not check.ok and sum(f.code == "empty_section" for f in check.failures) == 2


def test_why_needs_two_company_features(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    claims = [
        c.model_copy(update={"based_on": {**c.based_on, "client_features": ["CL_PRODUCT_LOA_MARK=1"]}})
        if c.claim_id in card.why_it_matters
        else c
        for c in card.claims
    ]
    assert "why_features" in _codes(_check(e, card.model_copy(update={"claims": claims})))


def test_bank_offer_needs_trigger(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    assert any(c.type == "bank_offer" for c in card.claims) and _check(e, card).ok
    other = env(bundle, *CASES[1])  # у вкладчика триггер предложения по займам не сработает
    check = _check(e, card, profile=other["profile"], analyses=other["analyses"])
    assert "offer_no_trigger" in _codes(check)


# ---------- перегенерация и шаблон ----------


def _fixtures_copy(tmp_path: Path, with_retry: bool) -> Path:
    root = tmp_path / "fx"
    shutil.copytree(FIXTURES.parent, root)
    data = json.loads((FIXTURES / "U_DEMO_BORROWER_cfo.json").read_text(encoding="utf-8"))
    data["what_happened"][0]["text"] = "Совет директоров сохранил ключевую ставку на уровне 15% годовых."
    (root / "card" / "num_bad.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if with_retry:
        shutil.copy(FIXTURES / "U_DEMO_BORROWER_cfo.json", root / "card" / "num_bad_retry.json")
    return root


def _client(tmp_path: Path, root: Path) -> LLMClient:
    client = fixture_client(tmp_path)
    client.settings.llm_fixtures_dir = str(root)
    client.transport = type(client.transport)(root)
    return client


def test_bad_draft_regenerates_once(bundle, tmp_path):
    root = _fixtures_copy(tmp_path, with_retry=True)
    e = env(bundle, *CASES[0])
    res = write_verified_card(
        **e, client=_client(tmp_path, root), sources=bundle["source"], fixture_key="num_bad"
    )
    assert res.source == "llm" and res.regenerations == 1 and res.checked
    assert any("number_not_in_source" in f for f in res.verify_failures)


def test_still_bad_after_retry_falls_to_template(bundle, tmp_path):
    root = _fixtures_copy(tmp_path, with_retry=False)
    e = env(bundle, *CASES[0])
    res = write_verified_card(
        **e, client=_client(tmp_path, root), sources=bundle["source"], fixture_key="num_bad"
    )
    assert res.source == "template" and res.regenerations == 1
    assert "проверка карточки не пройдена" in (res.fallback_reason or "")
    assert _check(e, res.card).ok


def test_good_card_has_no_regeneration(bundle, tmp_path):
    e = env(bundle, *CASES[1])
    res = write_verified_card(**e, client=fixture_client(tmp_path), sources=bundle["source"])
    assert res.source == "llm" and res.regenerations == 0 and res.checked and not res.verify_failures


def test_llm_failure_goes_to_checked_template(bundle, tmp_path):
    e = env(bundle, *CASES[1])
    res = write_verified_card(
        **e, client=fixture_client(tmp_path), sources=bundle["source"], fixture_key="bad_schema"
    )
    assert res.source == "template" and res.regenerations == 0 and res.checked


def test_unbuildable_template_raises(bundle, tmp_path):
    e = env(bundle, *CASES[1])
    e["dossier"] = e["dossier"].model_copy(update={"items": []})
    with pytest.raises(CardUnavailable):
        write_verified_card(**e, client=fixture_client(tmp_path), fixture_key="bad_schema")


# ---------- шаг автомата ----------


def test_verify_step_wired_and_pipeline_green(bundle):
    assert make_handlers("rate_14")[State.CARD_VERIFIED].__name__ == "verified"
    card, trace = run_for_client(
        bundle["story"][0].id, "U_DEMO_BORROWER", "cfo", sector=bundle["company_profile"][0].sector or ""
    )
    assert card.company_id == "U_DEMO_BORROWER" and trace.verify_rounds >= 1 and not trace.verify_failures


def test_verify_step_reports_failure(bundle, tmp_path):
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bad = _with_text(card, card.what_happened[0], "Ставка выросла до 21%.")
    ctx = PipelineContext(
        story_id=card.event.story_id, story_version=1, sector="x", company_id="U_DEMO_BORROWER", role="cfo"
    )
    ctx.card, ctx.dossier = bad, e["dossier"].model_dump(mode="json")
    ctx = card_verify_step(ctx)
    assert ctx.verify_ok is False and ctx.verify_errors and "number_not_in_source" in ctx.verify_errors[0]


def test_bank_view_on_expired_position_fails(bundle, tmp_path):
    """CRITERIA_CHECK 5А: позиция банка с истёкшим сроком (valid_until раньше сюжета) — не текущая."""
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bid = card.bank_and_market[0]
    src = next(c for c in card.claims if c.claim_id == bid).evidence[0].source_id
    items = [
        i.model_copy(update={"data": {**i.data, "valid_until": "2026-09-01", "expired": True}})
        if i.id == src
        else i
        for i in e["dossier"].items
    ]
    stale = {**e, "dossier": e["dossier"].model_copy(update={"items": items})}
    check = _check(stale, card)
    assert "bank_view_expired" in _codes(check)
    assert any(f.claim_id == bid for f in check.failures if f.code == "bank_view_expired")


def test_safe_dossier_drops_expired_positions(bundle):
    from backend.cards.prompt import _safe_dossier

    e = env(bundle, *CASES[0])
    first = e["dossier"].by_kind("bank_position")[0]
    items = [
        i.model_copy(update={"data": {**i.data, "expired": True}}) if i.id == first.id else i
        for i in e["dossier"].items
    ]
    safe = _safe_dossier(e["dossier"].model_copy(update={"items": items}))
    assert first.id not in safe.ids()
    assert all(not i.data.get("expired") for i in safe.by_kind("bank_position"))


def test_bank_view_with_review_date_in_words_passes(bundle, tmp_path):
    """Правило 10 промпта: «В обзоре от 11 сентября ЦМИ …» проходит проверку дат по doc_date."""
    e, card = _llm_card(bundle, tmp_path, CASES[0])
    bid = card.bank_and_market[0]
    claim = next(c for c in card.claims if c.claim_id == bid)
    day = claim.evidence[0].doc_date
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]  # fmt: skip
    text = f"В обзоре от {day.day} {months[day.month - 1]} ЦМИ: {claim.text}"
    assert _check(e, _with_text(card, bid, text)).ok
