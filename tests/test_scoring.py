"""Таксономия Р2, профиль v1, скоринг v0, быстрая карточка и отбор на глубокий разбор."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.fixtures import load_bundle
from backend.profile.archetypes import resolve
from backend.profile.exposures import compute_exposures, with_exposures
from backend.profile.load import load_profiles
from backend.profile.taxonomy import load_event_types, load_matrix
from backend.ranking.config import load_scoring
from backend.ranking.fast_card import fast_card
from backend.ranking.score import freshness, quality, rank, score_pair, story_drivers
from backend.ranking.select import deep_k, mark_deep, select_deep, why_you_see
from backend.schemas import CompanyProfile, CounterpartyGraph, EventFacts, InsightCard, Story
from backend.schemas.common import ChannelImpact, Evidence

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _story(sid: str, event_type: str, title: str, hours_ago: float = 2, **kw) -> Story:
    at = NOW - timedelta(hours=hours_ago)
    data = {"status": "confirmed", "corroboration": 2, "quality": 0.9} | kw
    return Story(
        id=sid,
        title=title,
        event_type=event_type,
        item_ids=[f"it_{sid}"],
        first_seen=at,
        last_seen=at,
        **data,
    )


def _facts(story: Story, sectors=(), channels=(), **kw) -> EventFacts:
    return EventFacts(
        story_id=story.id,
        event_type=story.event_type or "other",
        status="fact",
        summary=story.title,
        summary_evidence=[Evidence(source_id=f"it_{story.id}", quote=story.title)],
        sectors_okved=list(sectors),
        channels=[ChannelImpact(channel=c, direction=d, strength=s) for c, d, s in channels],
        **kw,
    )


def _company(cid: str, okved: str, **products) -> CompanyProfile:
    p = CompanyProfile(
        company_id=cid,
        okved_code=okved,
        sector=okved[:2],
        products=products,
        fts_flag=products.pop("fts", None),
        revenue=50e9,
    )
    return with_exposures(p)


# Сюжеты одного дня: у каждого клиента своя лента
STORIES = {
    "sanctions_oil": (
        _story("sanctions_oil", "sanctions", "США ввели санкции против нефтяных компаний"),
        {"sectors": ["06"]},
    ),
    "rate": (
        _story("rate", "rate_decision", "ЦБ сохранил ключевую ставку 14%"),
        {"channels": [("key_rate", "up", 0.6), ("fx_rub", "up", 0.3)]},
    ),
    "grain_duty": (
        _story("grain_duty", "export_duty", "Правительство повысило пошлину на экспорт зерна"),
        {"sectors": ["01"]},
    ),
    "mortgage": (
        _story("mortgage", "mortgage_program", "Льготную ипотеку продлили до 2027 года"),
        {"sectors": ["41"], "channels": [("construction", "up", 0.5), ("subsidized_lending", "up", 0.6)]},
    ),
    "retail": (
        _story("retail", "macro_data", "Оборот розницы вырос на 6%"),
        {"sectors": ["47"], "channels": [("consumer_demand", "up", 0.4)]},
    ),
    "metals": (
        _story("metals", "commodity_price", "Сталь на мировом рынке подешевела на 8%"),
        {"sectors": ["24"], "channels": [("metals", "down", 0.6)]},
    ),
    "fx_import": (
        _story("fx_import", "import_duty", "Пошлины на ввоз оборудования из Китая вырастут"),
        {"sectors": ["46", "28"]},
    ),
}


def _feed(profile: CompanyProfile) -> list:
    scores = [score_pair(s, profile, now=NOW, facts=_facts(s, **kw)) for s, kw in STORIES.values()]
    return rank(scores)


# ---------- таксономия ----------


def test_taxonomy_sizes_and_references():
    m = load_matrix()
    ets = load_event_types()
    assert len(m.channels) == 30 and len(ets) == 40
    for sector, row in m.okved.items():
        assert set(row) <= set(m.channels), sector
    for sector, links in m.sector_links.items():
        assert sector in m.okved and all(0 < w <= 1 for w in links.values()), sector
    for name, et in ets.items():
        assert set(et.channels) <= set(m.channels) and set(et.prior) <= set(m.channels), name


def test_scoring_config_loads():
    cfg = load_scoring()
    m = load_matrix()
    for row in [*cfg.profile.products.values(), cfg.profile.fea.any, cfg.profile.fea.exporter]:
        assert set(row) <= set(m.channels)


# ---------- профиль v1 ----------


def test_products_give_sign():
    borrower = _company("B", "46.69", loans=True, leasing=True, fts=True)
    depositor = _company("D", "24.10", deposits=True, loans=False, fts=True)
    sb = {e.channel: e for e in borrower.exposures}
    sd = {e.channel: e for e in depositor.exposures}
    assert sb["key_rate"].sensitivity < 0 and "CL_PRODUCT_LOA_MARK=1" in sb["key_rate"].drivers
    assert sd["key_rate"].sensitivity > 0 and "CL_PRODUCT_DEP_MARK=1" in sd["key_rate"].drivers
    # импортёр выигрывает от крепкого рубля, экспортёр проигрывает; это предположение по отрасли
    assert sb["fx_rub"].sensitivity > 0 and sd["fx_rub"].sensitivity < 0
    # курс у металлурга — и строка отрасли (твёрдо), и догадка «экспортёр»: канал не предположение
    assert not sd["fx_rub"].is_assumption and "ОКВЭД 24: скорее экспортёр" in sd["fx_rub"].drivers
    assert all(-1 <= e.sensitivity <= 1 for e in [*borrower.exposures, *depositor.exposures])


def test_counterparties_add_channels_in_card_format():
    p = CompanyProfile(company_id="X", okved_code="46.69", sector="46")
    graph = CounterpartyGraph(company_id="X", sector_shares_buyers={"41": 0.4, "62": 0.05}, hhi_buyers=0.4)
    ex = {e.channel: e for e in compute_exposures(p, graph)}
    assert "payments: 40% входящих от ОКВЭД 41" in ex["construction"].drivers
    assert ex["construction"].is_assumption
    assert not any("ОКВЭД 62" in d for e in ex.values() for d in e.drivers)  # доля ниже порога
    assert ex["counterparty_defaults"].sensitivity < -0.4  # сектор 46 + высокая концентрация


def test_synthetic_profiles_v1_cover_all_companies():
    profiles = load_profiles(Path("data/case_synthetic"))
    v1 = [with_exposures(p) for p in profiles.values()]
    assert all(p.exposures for p in v1)


# ---------- скоринг ----------


def test_rate_14_sign_is_direction_times_sensitivity():
    b = load_bundle("rate_14")
    story, facts = b["story"][0], b["event_facts"][0]
    now = story.last_seen + timedelta(hours=1)
    res = {p.company_id: score_pair(story, p, now=now, facts=facts) for p in b["company_profile"]}
    borrower, depositor = res["U_DEMO_BORROWER"], res["U_DEMO_DEPOSITOR"]
    kb = next(c for c in borrower.contributions if c.factor == "key_rate")
    kd = next(c for c in depositor.contributions if c.factor == "key_rate")
    assert (kb.sign, kd.sign) == (-1, 1)
    assert kb.value == pytest.approx(0.6 * 0.9)  # сила × |чувствительность|, как в фикстуре
    assert borrower.polarity == "risk" and depositor.polarity == "opportunity"
    assert 0 < borrower.score <= 1


def test_sanctions_against_oil_top_for_oilfield_services():
    oilfield = _company("OFS", "09.10", loans=True, leasing=True)
    feed = _feed(oilfield)
    assert feed[0].story_id == "sanctions_oil", [(r.story_id, r.score) for r in feed]
    factors = {c.factor for c in feed[0].contributions}
    assert {"trade_restrictions", "linked_sector:06"} <= factors
    assert feed[0].polarity == "risk"
    assert any("заказчики" in line for line in why_you_see(feed[0]))


def test_wholesaler_and_exporter_feeds_differ_and_are_explained():
    profiles = load_profiles(Path("data/case_synthetic"))
    picked = resolve(profiles)
    opt = with_exposures(picked["wholesale_borrower"])  # type: ignore[arg-type]
    exp = with_exposures(picked["exporter_depositor"])  # type: ignore[arg-type]
    fo, fe = _feed(opt), _feed(exp)
    assert [r.story_id for r in fo[:3]] != [r.story_id for r in fe[:3]]
    rate_o = next(r for r in fo if r.story_id == "rate")
    rate_e = next(r for r in fe if r.story_id == "rate")
    assert {c.factor: c.sign for c in rate_o.contributions}["fx_rub"] == 1  # импортёр: крепкий рубль — плюс
    assert {c.factor: c.sign for c in rate_e.contributions}["fx_rub"] == -1
    for r in [*fo, *fe]:
        if r.score > 0:
            assert r.contributions and len(why_you_see(r)) <= 3
            assert all(c.client_features for c in r.contributions)


def test_drivers_priority_analysis_facts_prior():
    s = STORIES["sanctions_oil"][0]
    assert {d.origin for d in story_drivers(s)} == {"prior"}
    f = _facts(s, channels=[("trade_restrictions", "up", 0.9)])
    assert [(d.channel, d.origin) for d in story_drivers(s, f)] == [("trade_restrictions", "facts")]
    assert story_drivers(_story("x", "rate_decision", "ставка")) == []  # направление неизвестно без фактов


def test_impact_is_strength_without_freshness():
    """Сила влияния для показа: свежесть не срезает её (это порядок ленты), шкала линейная и с потолком 1."""
    b = load_bundle("rate_14")
    story, facts = b["story"][0], b["event_facts"][0]
    profile = next(p for p in b["company_profile"] if p.company_id == "U_DEMO_BORROWER")
    fresh = score_pair(story, profile, now=story.last_seen, facts=facts, role="cfo")
    old = score_pair(story, profile, now=story.last_seen + timedelta(hours=48), facts=facts, role="cfo")
    assert old.score < fresh.score * 0.6  # за двое суток значимость для порядка падает вдвое
    assert old.impact == fresh.impact
    raw = sum(c.value for c in fresh.contributions)
    full_at = load_scoring().impact.full_at
    assert fresh.impact == pytest.approx(fresh.quality * min(1.0, raw / full_at), abs=1e-3)
    # решение ЦБ для заёмщика с кредитами выглядит сильным, а не «21 из 100»
    assert fresh.impact is not None and fresh.impact >= 0.67 and fresh.impact > fresh.score


def test_impact_scale_weak_for_small_contributions():
    b = load_bundle("rate_14")
    story, facts = b["story"][0], b["event_facts"][0]
    profile = next(p for p in b["company_profile"] if p.company_id == "U_DEMO_BORROWER")
    cfg = load_scoring().model_copy(update={"impact": load_scoring().impact.model_copy(update={"full_at": 100.0})})
    r = score_pair(story, profile, now=story.last_seen, facts=facts, role="cfo", cfg=cfg)
    assert r.impact is not None and r.impact < 0.05 and r.score == score_pair(
        story, profile, now=story.last_seen, facts=facts, role="cfo"
    ).score  # шкала показа не меняет порядок ленты


def test_freshness_and_quality():
    cfg = load_scoring()
    fresh, old = _story("a", "other", "a", hours_ago=0), _story("b", "other", "b", hours_ago=96)
    assert freshness(fresh, NOW, cfg) == 1.0
    assert freshness(old, NOW, cfg) == pytest.approx(0.25, abs=0.01)
    rumor = _story("r", "other", "r", status="rumor", quality=None, source_ids=["unknown"])
    confirmed = _story("c", "other", "c", status="confirmed", quality=None)
    assert quality(rumor, None, {}, cfg) < quality(confirmed, None, {}, cfg)
    assert quality(rumor, _facts(rumor, is_rumor=True), {}, cfg) < quality(rumor, None, {}, cfg)


def test_direct_company_and_counterparty_match():
    p = _company("ME", "46.69", loans=True)
    graph = CounterpartyGraph(company_id="ME", sector_shares_suppliers={"24": 0.5})
    s = _story("d", "corporate_results", "Отчётность компании", entities=["ME"])
    r = score_pair(s, p, now=NOW, facts=_facts(s, sectors=["24"]), graph=graph)
    factors = {c.factor for c in r.contributions}
    assert {"direct:company", "counterparty:24"} <= factors


# ---------- быстрая карточка ----------


def test_fast_card_valid_and_labelled():
    b = load_bundle("rate_14")
    story, facts = b["story"][0], b["event_facts"][0]
    profile = next(p for p in b["company_profile"] if p.company_id == "U_DEMO_BORROWER")
    rel = score_pair(story, profile, now=story.last_seen, facts=facts)
    items = {i.id: i for i in b["raw_item"]}
    sources = {s.id: s for s in b["source"]}
    card = fast_card(
        story,
        rel,
        "cfo",
        facts=facts,
        items=items,
        sources=sources,
        bank_positions=b["bank_position"],
        profile=profile,
    )
    InsightCard.model_validate(card.model_dump())
    kinds = {c.claim_id: c.type for c in card.claims}
    assert card.stage == "fast" and kinds["F1"] == "fact"
    assert card.why_it_matters and all(kinds[i] == "model_inference" for i in card.why_it_matters)
    assert card.bank_and_market == ["B1"] and kinds["B1"] == "bank_view"
    i1 = next(c for c in card.claims if c.claim_id == "I1")
    assert "кредиты в банке" in i1.text and "риск" in i1.text
    assert all(s.title for s in card.event.sources)


def test_fast_card_without_facts_uses_item_title():
    b = load_bundle("rate_14")
    story = b["story"][0]
    rel = score_pair(story, b["company_profile"][0], now=story.last_seen)
    card = fast_card(story, rel, "cfo", items={i.id: i for i in b["raw_item"]})
    assert card.claims[0].evidence[0].quote


def test_fast_card_fact_checked_by_code():
    """Факт быстрой карточки сверяется с цитатой кодом: фронт пишет «проверено», а не «не проверено»."""
    b = load_bundle("rate_14")
    story, facts = b["story"][0], b["event_facts"][0]
    rel = score_pair(story, b["company_profile"][0], now=story.last_seen, facts=facts)
    items = {i.id: i for i in b["raw_item"]}
    for card in (
        fast_card(story, rel, "cfo", facts=facts, items=items),
        fast_card(story, rel, "cfo", items=items),  # без фактов — заголовок материала
    ):
        f1 = card.claims[0]
        assert f1.verified == {"refs": True, "numbers": True, "dates": True}
        assert all(ev.verified for ev in f1.evidence)
        # выводы — быстрая оценка скоринга: код их не проверял, фронт так и подписывает
        assert all(not c.verified for c in card.claims if c.type == "model_inference")


def test_fast_card_fact_check_catches_number_and_foreign_quote():
    from backend.ranking.fast_card import _checked_fact
    from backend.schemas import Claim

    b = load_bundle("rate_14")
    item = b["raw_item"][0]
    ev = Evidence(source_id=item.id, quote=item.title)
    wrong_number = Claim(claim_id="F1", type="fact", text=f"{item.title} Ставка 99%.", evidence=[ev])
    checked = _checked_fact(wrong_number, {item.id: item})
    assert checked.verified["numbers"] is False and checked.verified["refs"] is True
    foreign = Claim(
        claim_id="F1", type="fact", text="Ставка 14%", evidence=[ev.model_copy(update={"quote": "Ставка 14%"})]
    )
    checked = _checked_fact(foreign, {item.id: item.model_copy(update={"title": "x", "lead": None, "text": None})})
    assert checked.verified["refs"] is False and checked.evidence[0].verified is False


# ---------- топ-K на глубокий разбор ----------


def test_deep_k_from_budget():
    k_full, thr_full = deep_k(0, 500)
    k_eco, thr_eco = deep_k(400, 500)
    assert k_full == 60 and (k_eco, deep_k(460, 500)[0]) == (k_eco, 0)
    assert 0 < k_eco < k_full and thr_eco > thr_full


def test_select_deep_groups_by_sector():
    a = _company("A", "46.69", loans=True, fts=True)
    a2 = _company("A2", "46.90", loans=True)
    c = _company("C", "41.20", loans=True)
    scores = [r for p in (a, a2, c) for r in _feed(p)]
    sector_of = {p.company_id: p.sector for p in (a, a2, c)}
    pairs = select_deep(scores, sector_of, spent_rub=0, limit_rub=500)
    keys = [(p.story_id, p.sector) for p in pairs]
    assert len(keys) == len(set(keys)) and all(p.priority >= load_scoring().deep.min_score for p in pairs)
    assert any(p.clients == 2 for p in pairs)  # два оптовика — одна пара на сектор 46
    marked = mark_deep(scores, pairs, sector_of)
    assert sum(r.deep_analysis for r in marked) >= len(pairs)
    assert select_deep(scores, sector_of, spent_rub=480, limit_rub=500) == []


def test_off_sector_event_is_damped():
    steel = _company("ST", "24.10", fts=True)
    grain, kw = STORIES["grain_duty"]
    r_steel = score_pair(grain, steel, now=NOW, facts=_facts(grain, **kw))
    farmer = _company("AG", "01.11", fts=True)
    r_farm = score_pair(grain, farmer, now=NOW, facts=_facts(grain, **kw))
    assert r_farm.score > 2 * r_steel.score


def test_assumption_only_without_firm_driver():
    # у заёмщика ставка — прямой канал через кредит, даже если к ней добавились отрасли контрагентов
    p = CompanyProfile(company_id="L", okved_code="46.69", sector="46", products={"loans": True})
    graph = CounterpartyGraph(company_id="L", sector_shares_buyers={"41": 0.5})
    ex = {e.channel: e for e in compute_exposures(p, graph)}
    assert not ex["key_rate"].is_assumption and any(d.startswith("payments:") for d in ex["key_rate"].drivers)
    assert ex["tax_burden"].is_assumption  # общий фактор без признака компании


def test_unscoped_sanctions_prior_is_damped():
    p = _company("IMP", "46.69", fts=True)
    s = _story("pol", "sanctions", "Политик заявил о новых санкциях")
    vague = score_pair(s, p, now=NOW, facts=_facts(s))
    named = score_pair(s, p, now=NOW, facts=_facts(s, sectors=["46"]))
    explicit = score_pair(s, p, now=NOW, facts=_facts(s, channels=[("trade_restrictions", "up", 0.8)]))
    assert vague.score < named.score and vague.score < explicit.score


def test_oil_price_reaches_fuel_dependent_clients():
    # разметка oct-60: Brent ↑ важен перевозчику, а скоринг давал 0 — в профиле топливо, а не нефть
    truck = _company("TR", "49.41")
    brent = _story("brent", "commodity_price", "Нефть Brent подорожала до $111")
    r = score_pair(brent, truck, now=NOW, facts=_facts(brent, channels=[("oil_gas", "up", 0.6)]))
    fuel = [c for c in r.contributions if c.factor == "petroleum_products"]
    assert r.score > 0 and fuel and fuel[0].sign < 0  # топливо дороже — перевозчику минус
    # явный фактор «топливо» сюжета производный не перебивает
    both = _facts(brent, channels=[("oil_gas", "up", 0.6), ("petroleum_products", "down", 0.3)])
    r2 = score_pair(brent, truck, now=NOW, facts=both)
    assert [c.sign for c in r2.contributions if c.factor == "petroleum_products"] == [1]


def test_unscoped_state_support_prior_is_damped():
    # IT-ипотека без отраслей в фактах не должна вставать у инфраструктурной стройки выше решения ЦБ
    p = _company("CON", "42.99", guarantees=True, loans=True)
    s = _story("it", "state_support", "Обсуждают досрочное завершение IT-ипотеки")
    vague = score_pair(s, p, now=NOW, facts=_facts(s))
    named = score_pair(s, p, now=NOW, facts=_facts(s, sectors=["42"]))
    assert vague.score < named.score


def test_fuel_wholesaler_gets_subsector_row():
    fuel = {e.channel: e for e in _company("FW", "46.71.2").exposures}
    other = {e.channel: e for e in _company("OW", "46.69").exposures}
    assert fuel["petroleum_products"].sensitivity > 0 and "petroleum_products" not in other


# ---------- шум ленты: регион и общий фактор (CRITERIA_CHECK 1, 7в) ----------


def _regional(profile: CompanyProfile, region: str = "Москва"):
    p = profile.model_copy(update={"region": region})
    s = _story("hall", "other", "Москва примет чемпионат мира по тяжёлой атлетике")
    return p, s, _facts(s, geography=[region])


def test_region_alone_does_not_pass_feed_threshold():
    cfg = load_scoring()
    p, s, f = _regional(_company("U_FUEL", "46.71", leasing=True, deposits=True, fts=True))
    r = score_pair(s, p, now=NOW, facts=f)
    assert r.score < cfg.feed.min_score
    assert all(c.factor != "direct:region" or c.value == 0 for c in r.contributions)


def test_region_kept_when_economic_channel_present():
    p = _company("U_BUILD", "42.99", loans=True).model_copy(update={"region": "Иркутская область"})
    s = _story("reg_rate", "rate_decision", "Иркутская область: ставка по кредитам выросла")
    f = _facts(s, channels=[("key_rate", "up", 0.5)], geography=["Иркутская область"])
    r = score_pair(s, p, now=NOW, facts=f)
    assert any(c.factor == "direct:region" for c in r.contributions)


def test_rate_decision_reaches_company_with_leasing_and_deposits():
    """U_57706-подобный профиль: лизинг и депозиты гасят друг друга по знаку, но ставка ему важна."""
    cfg = load_scoring()
    p = _company("U_FUEL", "46.71", leasing=True, deposits=True, fts=True)
    key = next(e for e in p.exposures if e.channel == "key_rate")
    assert abs(key.sensitivity) >= cfg.profile.rate_products_min
    s = _story("rate14", "rate_decision", "ЦБ сохранил ключевую ставку 14%", hours_ago=48)
    r = score_pair(s, p, now=NOW, facts=_facts(s, channels=[("key_rate", "up", 0.4)]), role="treasurer")
    assert r.score >= cfg.feed.min_score


def test_baseline_only_story_below_rate_decision_and_not_a_reason():
    from backend.ranking.select import top_contributions

    p = _company("U_DEP", "24.10", deposits=True, fts=True)
    tax = _story("windfall", "tax_change", "Минфин готов обсудить налог на сверхдоходы")
    rate = _story("rate", "rate_decision", "ЦБ сохранил ключевую ставку 14%")
    r_tax = score_pair(tax, p, now=NOW, facts=_facts(tax))
    r_rate = score_pair(rate, p, now=NOW, facts=_facts(rate, channels=[("key_rate", "up", 0.6)]))
    assert r_tax.score < r_rate.score
    # общий фактор не показывается причиной, когда есть конкретные
    mixed = score_pair(
        rate, p, now=NOW, facts=_facts(rate, channels=[("key_rate", "up", 0.6), ("tax_burden", "up", 0.6)])
    )
    assert any(c.factor == "tax_burden" for c in mixed.contributions)
    assert all(c.factor != "tax_burden" for c in top_contributions(mixed))
