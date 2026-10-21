"""Карточка: промпт, сборка, предложение банка, шаблон. Все вызовы LLM — LLM_MODE=fixture."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from newsai.agents.dossier import Dossier, DossierItem
from newsai.cards import CardAssemblyError, CardDraft, assemble_card, find_offer, template_draft, write_card
from newsai.cards.features import ROLE_FOCUS
from newsai.cards.prompt import build_messages, load_prompt
from newsai.fixtures import load_bundle
from newsai.llm import BudgetExceeded, LLMClient
from newsai.llm.budget import BudgetManager, InMemoryBudgetStore
from newsai.llm.cache import ResponseCache
from newsai.llm.journal import Journal
from newsai.llm.settings import LLMSettings
from newsai.schemas import InsightCard

FIXTURES = Path("newsai/llm/fixtures/card")
FACT = "fact:st_2026-09-11_keyrate:summary"
NUM = "fact:st_2026-09-11_keyrate:num0"
CBR = "6d3f1b2a9c0e4f7a8b1c2d3e4f5a6b7c8d9e0f1a"
CASES = [("U_DEMO_BORROWER", "cfo"), ("U_DEMO_DEPOSITOR", "treasurer")]


# ---------- сборка досье и окружения из бандла rate_14 ----------


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("rate_14")


def _utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def make_dossier(bundle, company_id: str) -> Dossier:
    story = bundle["story"][0]
    facts = bundle["event_facts"][0]
    profile = next(p for p in bundle["company_profile"] if p.company_id == company_id)
    ev = facts.summary_evidence[0]
    d = Dossier(
        story_id=story.id,
        sector=profile.sector or "",
        event_type=story.event_type,
        story_published_at=story.first_seen,
        trace_id="run_fixture_rate14",
    )
    d.add(DossierItem(id=story.id, kind="story", title=story.title))
    tiers = {s.id: s.tier for s in bundle["source"]}
    for r in bundle["raw_item"]:
        d.add(
            DossierItem(
                id=r.id, kind="news", title=r.title, text=r.text or "", published_at=r.published_at,
                data={"source_id": r.source_id, "url": r.url, "tier": tiers[r.source_id]},
            )
        )
    d.add(DossierItem(id=FACT, kind="fact", title="Решение по ставке", text=facts.summary,
                      data={"source_id": ev.source_id, "quote": ev.quote}))
    n = facts.numbers[0]
    d.add(DossierItem(id=NUM, kind="fact", title="Ключевая ставка", text="14% годовых",
                      data={"value": n.value, "unit": n.unit, "source_id": ev.source_id, "quote": n.quote}))
    for bp in bundle["bank_position"]:
        d.add(
            DossierItem(
                id=bp.id, kind="bank_position", title=bp.thesis, text=bp.quote, published_at=_utc(bp.doc_date),
                data={
                    "channel": bp.channel, "stance": bp.stance, "forecast_value": bp.forecast_value,
                    "forecast_unit": bp.forecast_unit, "quote": bp.quote, "doc_date": bp.doc_date.isoformat(),
                    "valid_until": bp.valid_until.isoformat() if bp.valid_until else None,
                    "superseded_by": bp.superseded_by,
                },
            )
        )
    for mk in bundle["market_knowledge"]:
        d.add(DossierItem(id=mk.id, kind="market_knowledge", title=mk.statement, text=mk.statement,
                          published_at=mk.published_at, data={"channel": mk.channel,
                                                               "expected_value": mk.expected_value}))
    d.add(DossierItem(id=f"profile:{company_id}", kind="profile", title="Профиль",
                      data={"okved": profile.okved_code, "revenue": profile.revenue}))
    # граф платежей в досье: суммы не должны попасть в промпт карточки
    d.add(DossierItem(id=f"counterparties:{company_id}", kind="counterparties", title="Контрагенты",
                      data={"top": [{"share": 0.35, "pay_amt": 5200000000, "pay_cnt": 412}]}))
    return d


def env(bundle, company_id: str, role: str) -> dict:
    return {
        "story": bundle["story"][0],
        "dossier": make_dossier(bundle, company_id),
        "relevance": next(r for r in bundle["relevance_score"] if r.company_id == company_id),
        "profile": next(p for p in bundle["company_profile"] if p.company_id == company_id),
        "graph": next((g for g in bundle["counterparty_graph"] if g.company_id == company_id), None),
        "analyses": bundle["analyst_report"],
        "role": role,
        "trace_id": f"trace_{company_id}",
    }


def fixture_client(tmp_path) -> LLMClient:
    s = LLMSettings(
        _env_file=None, llm_mode="fixture", llm_journal_dir=str(tmp_path / "journal"),
        llm_cache_dir=str(tmp_path / "cache"), llm_fixtures_dir="newsai/llm/fixtures",
    )
    return LLMClient(
        settings=s, budget=BudgetManager(settings=s, store=InMemoryBudgetStore()),
        journal=Journal(s.llm_journal_dir), cache=ResponseCache(s.llm_cache_dir, s.llm_cache_enabled),
    )


def draft_of(name: str) -> CardDraft:
    return CardDraft.model_validate_json((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def assemble(e: dict, draft: CardDraft, **kw) -> InsightCard:
    return assemble_card(draft, **{**e, **kw})


def write(e: dict, client, **kw):
    return write_card(**{**e, **kw}, client=client)


# ---------- обе карточки rate_14 ----------


@pytest.mark.parametrize(("company", "role"), CASES)
def test_card_valid_and_labelled(bundle, tmp_path, company, role):
    e = env(bundle, company, role)
    res = write(e, fixture_client(tmp_path), sources=bundle["source"])
    assert res.source == "llm" and res.fallback_reason is None
    card = res.card
    InsightCard.model_validate(card.model_dump(mode="json"))  # валидна как контракт
    assert card.company_id == company and card.user_role == role and card.trace_id == e["trace_id"]
    ids = {c.claim_id for c in card.claims}
    for sect in (card.what_happened, card.why_it_matters, card.consequences, card.bank_and_market):
        assert sect and set(sect) <= ids
    dossier_ids = e["dossier"].ids()
    for c in card.claims:
        if c.type == "fact":
            assert c.evidence and all(ev.quote for ev in c.evidence), c.claim_id
        elif c.type == "bank_view":
            assert c.evidence and all(ev.source_id.startswith("cmi_") for ev in c.evidence)
            assert all(ev.source_id in dossier_ids for ev in c.evidence)
        elif c.type == "market_view":
            assert c.evidence and all(ev.source_id.startswith("mk_") for ev in c.evidence)
        elif c.type == "model_inference":
            assert any(c.based_on.values()), c.claim_id
            assert c.polarity in ("risk", "opportunity", "neutral", "mixed")
            for key in ("facts", "bank_views", "market_views"):
                assert set(c.based_on.get(key, [])) <= ids  # based_on — id утверждений карточки
        else:
            assert c.type == "bank_offer" and c.is_advertising
    # метки и обоснование: у каждого утверждения есть либо источник, либо based_on
    assert all(c.evidence or any(c.based_on.values()) for c in card.claims)
    assert res.cost_rub == 0.0 and res.prompt_version == "card-v1"


def test_confidence_and_surprise_set_by_code(bundle, tmp_path):
    cfo = write(env(bundle, *CASES[0]), fixture_client(tmp_path), sources=bundle["source"]).card
    tre = write(env(bundle, *CASES[1]), fixture_client(tmp_path), sources=bundle["source"]).card
    assert cfo.confidence == "high" and tre.confidence == "medium"
    assert any("первоисточник" in r for r in cfo.confidence_reasons)
    assert cfo.relevance.polarity == "risk" and tre.relevance.polarity == "opportunity"
    for card in (cfo, tre):
        s = card.surprise
        assert s and s.actual == 14.0 and s.bank_expected == 14.0 and s.direction == "in_line"
        assert s.bank_ref == "cmi_keyrate_2026_09#p1#c4" and s.market_expected is None
    # знак вывода — из вкладов скоринга по признакам, не от модели
    pol = {c.claim_id: c.polarity for c in tre.claims if c.type == "model_inference"}
    assert "risk" in pol.values() and "opportunity" in pol.values()
    assert any(c.horizon for c in cfo.claims if c.type == "model_inference")  # из отчёта аналитиков


def test_two_clients_get_different_cards(bundle, tmp_path):
    a = write(env(bundle, *CASES[0]), fixture_client(tmp_path), sources=bundle["source"])
    b = write(env(bundle, *CASES[1]), fixture_client(tmp_path), sources=bundle["source"])
    assert a.headline != b.headline
    texts_a = {c.text for c in a.card.claims if c.type == "model_inference"}
    texts_b = {c.text for c in b.card.claims if c.type == "model_inference"}
    assert texts_a.isdisjoint(texts_b)
    offers = {}
    for res in (a, b):
        acts = [x for x in res.card.actions if x.type == "bank_product"]
        assert len(acts) == 1 and acts[0].is_advertising
        assert [c for c in res.card.claims if c.type == "bank_offer"]
        offers[res.card.company_id] = acts[0].text
    assert offers["U_DEMO_BORROWER"] == "Фиксация ставки по новым траншам"
    assert offers["U_DEMO_DEPOSITOR"].startswith("Депозит на более длинный срок")


# ---------- сбои -> шаблон ----------


def test_bad_fixture_falls_back_to_template(bundle, tmp_path):
    e = env(bundle, *CASES[0])
    res = write(e, fixture_client(tmp_path), sources=bundle["source"], fixture_key="bad_bank_view_mk")
    assert res.source == "template" and "CardAssemblyError" in (res.fallback_reason or "")
    InsightCard.model_validate(res.card.model_dump(mode="json"))
    assert res.card.what_happened and res.card.why_it_matters and res.card.consequences
    assert all(c.evidence or any(c.based_on.values()) for c in res.card.claims)


@pytest.mark.parametrize("key", ["bad_schema", "no_such_fixture"])
def test_unavailable_or_invalid_llm_falls_back(bundle, tmp_path, key):
    res = write(env(bundle, *CASES[1]), fixture_client(tmp_path), fixture_key=key)
    assert res.source == "template" and "LLMUnavailable" in (res.fallback_reason or "")
    InsightCard.model_validate(res.card.model_dump(mode="json"))


def test_budget_exceeded_falls_back(bundle, tmp_path, monkeypatch):
    client = fixture_client(tmp_path)

    def boom(**_):
        raise BudgetExceeded("card", "stop")

    monkeypatch.setattr(client, "call", boom)
    res = write(env(bundle, *CASES[0]), client)
    assert res.source == "template" and "BudgetExceeded" in (res.fallback_reason or "")


def test_template_card_has_two_features_and_no_bank_voice(bundle):
    for company, role in CASES:
        e = env(bundle, company, role)
        draft = template_draft(**{k: e[k] for k in ("story", "dossier", "relevance", "profile", "role", "analyses", "graph")})
        card = assemble(e, draft)
        feats = {f for c in card.claims if c.claim_id in card.why_it_matters for f in c.based_on.get("client_features", [])}
        assert len(feats) >= 2
        # «Банк ожидает» только в разделе банка: свёрстанный шаблон не нарушает правил
        assert all(c.type in ("bank_view", "market_view") for c in card.claims if c.claim_id in card.bank_and_market)


# ---------- правила сборки ----------


def test_bad_draft_rejected_by_assembly(bundle):
    e = env(bundle, *CASES[0])
    with pytest.raises(CardAssemblyError, match="метка bank_view"):
        assemble(e, draft_of("bad_bank_view_mk"))


def mutate(name: str, fn) -> CardDraft:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    fn(data)
    return CardDraft.model_validate(data)


def test_bank_voice_outside_bank_section_rejected(bundle):
    e = env(bundle, *CASES[0])
    bad = mutate("U_DEMO_BORROWER_cfo", lambda d: d["consequences"][0].update(text="По мнению банка, ставка снизится."))
    with pytest.raises(CardAssemblyError, match="вне раздела"):
        assemble(e, bad)
    ok = mutate("U_DEMO_BORROWER_cfo", lambda d: d["bank_and_market"][0].update(text="Банк ожидает паузу до конца года."))
    assemble(e, ok)  # в разделе банка — можно


def test_model_cannot_write_bank_offer(bundle):
    e = env(bundle, *CASES[0])
    bad = mutate("U_DEMO_BORROWER_cfo", lambda d: d["actions"][0].update(text="Предложение банка: фиксация ставки."))
    with pytest.raises(CardAssemblyError, match="добавляет код"):
        assemble(e, bad)


def test_unknown_ref_and_wrong_kind_rejected(bundle):
    e = env(bundle, *CASES[0])
    with pytest.raises(CardAssemblyError, match="нет id"):
        assemble(e, mutate("U_DEMO_BORROWER_cfo", lambda d: d["consequences"][0].update(refs=["fact:выдумка"])))
    with pytest.raises(CardAssemblyError, match="факт не может опираться"):
        assemble(e, mutate("U_DEMO_BORROWER_cfo", lambda d: d["what_happened"][0].update(refs=["cmi_keyrate_2026_09#p1#c1"])))
    with pytest.raises(CardAssemblyError, match="метка"):
        assemble(e, mutate("U_DEMO_BORROWER_cfo", lambda d: d["bank_and_market"][0].update(kind="market_view")))


def test_market_view_by_mk_ref(bundle):
    e = env(bundle, *CASES[0])
    e["dossier"].add(DossierItem(
        id="mk_demo_1", kind="market_knowledge", title="Большинство аналитиков ждали снижения ставки",
        text="Большинство аналитиков ждали снижения ставки", published_at=datetime(2026, 9, 10, tzinfo=UTC),
        data={"channel": "rate_cost", "expected_value": 13.5},
    ))

    def add(d):
        d["bank_and_market"].append({"kind": "market_view", "text": "Консенсус аналитиков ждал снижения ставки.",
                                     "refs": ["mk_demo_1"], "client_features": []})
        d["surprise"]["market_ref"] = "mk_demo_1"

    card = assemble(e, mutate("U_DEMO_BORROWER_cfo", add))
    mv = [c for c in card.claims if c.type == "market_view"]
    assert len(mv) == 1 and mv[0].claim_id in card.bank_and_market
    assert card.surprise and card.surprise.market_expected == 13.5 and card.surprise.direction == "above"


def test_invented_features_dropped_and_two_features_required(bundle):
    e = env(bundle, *CASES[0])

    def fn(d):
        d["why_it_matters"][0]["client_features"] = ["ОКВЭД 46.69", "ВЫДУМАННЫЙ ПРИЗНАК"]
        d["why_it_matters"][1]["client_features"] = ["тоже выдумка"]

    with pytest.raises(CardAssemblyError, match="≥ 2 признаков"):
        assemble(e, mutate("U_DEMO_BORROWER_cfo", fn))
    card = assemble(e, draft_of("U_DEMO_BORROWER_cfo"))
    feats = {f for c in card.claims for f in c.based_on.get("client_features", [])}
    assert "ВЫДУМАННЫЙ ПРИЗНАК" not in feats


def test_surprise_only_with_expectations(bundle):
    e = env(bundle, *CASES[0])
    none = mutate("U_DEMO_BORROWER_cfo", lambda d: d["surprise"].update(bank_ref=None))
    assert assemble(e, none).surprise is None
    # ожидание, опубликованное после события, не годится (инвариант 10)
    late = e["dossier"]
    old = late.get("cmi_keyrate_2026_09#p1#c4")
    late.add(old.model_copy(update={"published_at": datetime(2026, 9, 12, tzinfo=UTC)}))
    assert assemble(e, draft_of("U_DEMO_BORROWER_cfo")).surprise is None


def test_confidence_rules(bundle):
    e = env(bundle, *CASES[0])
    d = draft_of("U_DEMO_BORROWER_cfo")
    assert assemble(e, d, sources=bundle["source"]).confidence == "high"
    # нет первичного источника: ниже high
    weak = [s.model_copy(update={"tier": 2}) for s in bundle["source"]]
    card = assemble(e, d, sources=weak)
    assert card.confidence == "medium" and any("нет первичного" in r for r in card.confidence_reasons)
    # слух -> low
    rumor = e["story"].model_copy(update={"status": "rumor"})
    assert assemble({**e, "story": rumor}, d, sources=bundle["source"]).confidence == "low"


# ---------- предложение банка только по триггеру ----------


def test_offer_only_with_trigger(bundle):
    e = env(bundle, *CASES[0])
    prof, rel, an = e["profile"], e["relevance"], e["analyses"]
    assert find_offer(profile=prof, relevance=rel, analyses=an).trigger_id == "fix_rate"
    # нет продукта: нет предложения, и в карточке нет рекламного действия
    bare = prof.model_copy(update={"products": {"rko": True}, "fts_flag": False})
    assert find_offer(profile=bare, relevance=rel, analyses=an) is None
    card = assemble(e, draft_of("U_DEMO_BORROWER_cfo"), profile=bare)
    assert not [a for a in card.actions if a.type == "bank_product"]
    assert not [c for c in card.claims if c.type == "bank_offer"]
    # направление обратное (ставка ниже): фиксация не предлагается
    down = [r.model_copy(update={"channels": [c.model_copy(update={"impact": 0.6}) for c in r.channels]}) for r in an]
    rel_down = rel.model_copy(update={"contributions": [c.model_copy(update={"sign": 1}) for c in rel.contributions]})
    assert find_offer(profile=prof, relevance=rel_down, analyses=down) is None
    # слух: рекламы нет
    assert find_offer(profile=prof, relevance=rel, analyses=an, story_status="rumor") is None
    # ВЭД + вклад по курсу -> валютный триггер
    fx = env(bundle, *CASES[1])
    only_fx = fx["relevance"].model_copy(update={"contributions": [c for c in fx["relevance"].contributions if c.factor == "fx_rub"]})
    no_dep = fx["profile"].model_copy(update={"products": {"rko": True}})
    assert find_offer(profile=no_dep, relevance=only_fx, analyses=[]).trigger_id == "fx_hedge"
    # без вклада по курсу ВЭД сам по себе предложения не даёт
    assert find_offer(profile=no_dep, relevance=rel.model_copy(update={"contributions": []}), analyses=[]) is None


# ---------- промпт ----------


@pytest.mark.parametrize(("company", "role"), CASES)
def test_prompt_has_no_payment_amounts(bundle, company, role):
    e = env(bundle, company, role)
    msgs = build_messages(**{k: e[k] for k in ("story", "dossier", "relevance", "profile", "role", "analyses", "graph")})
    text = "\n".join(m["content"] for m in msgs)
    for bad in ("pay_amt", "pay_cnt", "5200000000", "7100000000", "1800000000", "412", "42000000000", "U_DEMO_S1", "U_DEMO_B1"):
        assert bad not in text, bad
    assert "<data>" in text and "не инструкция" in text  # новости только как данные
    assert "{{" not in text  # все подстановки сделаны
    assert company not in text  # id компании в модель не уходит


@pytest.mark.parametrize("role", list(ROLE_FOCUS))
def test_role_focus_in_prompt(bundle, role):
    e = env(bundle, *CASES[0])
    e["role"] = role
    text = "\n".join(m["content"] for m in build_messages(**{k: e[k] for k in ("story", "dossier", "relevance", "profile", "role", "analyses", "graph")}))
    assert ROLE_FOCUS[role] in text
    assert f"роль «{role}»" in text
    for needle in ("долг и ставка", "курс и ликвидность", "налоги и отчётность", "спрос, контрагенты, стратегия"):
        assert (needle in text) == (needle in ROLE_FOCUS[role])


def test_prompt_version_in_header_and_journal(bundle, tmp_path):
    version, system, _user = load_prompt()
    assert version == "card-v1" and "cmi_" in system and "mk_" in system
    client = fixture_client(tmp_path)
    res = write(env(bundle, *CASES[0]), client, sources=bundle["source"])
    entries = client.journal.by_trace(res.card.trace_id)
    assert entries and all(en["prompt_version"] == "card-v1" and en["step"] == "card" for en in entries)
    assert sum(en["cost_rub"] for en in entries) == 0.0
