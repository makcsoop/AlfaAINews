"""День 3 Р2: сюрприз кодом, пересчёт по разбору, веса ролей, уверенность, согласие разметчиков."""

from datetime import timedelta

import pytest

from backend.eval.agreement import Label, agreement, verdict, weighted_kappa
from backend.fixtures import load_bundle
from backend.ranking.confidence import confidence
from backend.ranking.fast_card import fast_card
from backend.ranking.score import score_pair
from backend.ranking.surprise import find_surprise
from backend.schemas import MarketKnowledge
from tests.test_scoring import NOW, STORIES, _company, _facts

B = load_bundle("rate_14")
STORY, FACTS = B["story"][0], B["event_facts"][0]
BORROWER = next(p for p in B["company_profile"] if p.company_id == "U_DEMO_BORROWER")
DEPOSITOR = next(p for p in B["company_profile"] if p.company_id == "U_DEMO_DEPOSITOR")
SOURCES = {s.id: s for s in B["source"]}


def _post(n: int, value: float, hours_before: float, **kw) -> MarketKnowledge:
    return MarketKnowledge(
        id=f"mk_post_{n}",
        kind="expectation",
        source_id=f"tg_ch{n}",
        item_id=f"it{n}",
        published_at=STORY.first_seen - timedelta(hours=hours_before),
        event_type="rate_decision",
        channel="key_rate",
        statement="ждём снижения",
        expected_value=value,
        expected_unit="%",
        target_event_date=STORY.first_seen.date(),
        origin="telegram",
        **kw,
    )


# ---------- сюрприз ----------


def test_surprise_bank_only_in_line():
    s = find_surprise(STORY, FACTS, bank_positions=B["bank_position"])
    assert s is not None
    assert (s.actual, s.bank_expected, s.direction) == (14.0, 14.0, "in_line")
    assert s.bank_ref == "cmi_keyrate_2026_09#p1#c4" and s.market_expected is None


def test_surprise_market_median_and_only_before_event():
    posts = [_post(1, 13.5, 30), _post(2, 13.5, 20), _post(3, 13.0, 10), _post(4, 16.0, -2)]  # 4-й — после
    s = find_surprise(STORY, FACTS, bank_positions=B["bank_position"], market=posts)
    assert s is not None and s.market_expected == 13.5 and s.direction == "above"
    assert s.market_ref == "mk_post_1"


def test_surprise_aggregate_consensus_wins():
    agg = _post(9, 13.25, 5).model_copy(update={"id": "mk_cons", "source_id": "consensus"})
    s = find_surprise(STORY, FACTS, market=[_post(1, 13.5, 30), agg])
    assert s is not None and (s.market_expected, s.market_ref) == (13.25, "mk_cons")


def test_surprise_needs_number_and_expectation():
    assert find_surprise(STORY, FACTS) is None  # ожиданий нет
    assert find_surprise(STORY, None, bank_positions=B["bank_position"]) is None  # числа нет
    other = STORY.model_copy(update={"event_type": "m_and_a"})
    assert (
        find_surprise(other, FACTS.model_copy(update={"event_type": "m_and_a"}), market=[_post(1, 13, 5)])
        is None
    )


def test_surprise_adds_contribution():
    s = find_surprise(STORY, FACTS, market=[_post(1, 13.5, 30)])
    plain = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS)
    with_s = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, surprise=s)
    sur = next(c for c in with_s.contributions if c.factor == "surprise")
    assert sur.value == pytest.approx(0.6)  # отклонение 0,5 п.п. — полный сюрприз × вес 0,6
    assert with_s.score > plain.score


# ---------- пересчёт по разбору и роли ----------


def test_rescore_by_analysis_uses_its_drivers():
    analysis = B["analysis"][0]
    fast = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS)
    deep = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, analysis=analysis)
    kf = next(c for c in fast.contributions if c.factor == "key_rate")
    kd = next(c for c in deep.contributions if c.factor == "key_rate")
    assert kf.value == pytest.approx(0.6 * 0.9) and kd.value == pytest.approx(0.55 * 0.9)
    assert kd.sign == -1  # знак ставит скоринг: ставка ↑ × кредит (−)


def test_role_weights_change_emphasis():
    cfo = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, role="cfo")
    tr = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, role="treasurer")
    key = {
        r: next(c.value for c in x.contributions if c.factor == "key_rate")
        for r, x in (("cfo", cfo), ("tr", tr))
    }
    fx = {
        r: next(c.value for c in x.contributions if c.factor == "fx_rub")
        for r, x in (("cfo", cfo), ("tr", tr))
    }
    assert key["cfo"] > key["tr"] and fx["tr"] > fx["cfo"]


def test_chief_accountant_sees_tax_higher():
    p = _company("ACC", "46.69", loans=True)
    tax_story = STORIES["rate"][0].model_copy(update={"id": "tax", "event_type": "tax_change_adopted"})
    tax = _facts(tax_story)
    rate_story, rate_kw = STORIES["rate"]

    def order(role):
        rs = [
            score_pair(tax_story, p, now=NOW, facts=tax, role=role),
            score_pair(rate_story, p, now=NOW, facts=_facts(rate_story, **rate_kw), role=role),
        ]
        return [r.story_id for r in sorted(rs, key=lambda r: -r.score)]

    assert order("chief_accountant")[0] == "tax" and order("cfo")[0] == "rate"


# ---------- уверенность ----------


def test_confidence_high_with_reasons():
    card = B["insight_card"][0]
    rel = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, analysis=B["analysis"][0])
    level, reasons = confidence(
        story=STORY,
        relevance=rel,
        profile=BORROWER,
        sources=SOURCES,
        analysis=B["analysis"][0],
        claims=card.claims,
        bank_positions=B["bank_position"],
    )
    assert level == "high"
    joined = " | ".join(reasons)
    assert "регулятор" in joined and "согласны" in joined and "ЦМИ" in joined and "прямая связь" in joined


def test_confidence_drops_on_disagreement_rumor_and_assumption():
    analysis = B["analysis"][0].model_copy(
        update={"agreement": {"key_rate": "disagree"}, "status": "on_review"}
    )
    rel = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, analysis=analysis)
    level, reasons = confidence(
        story=STORY, relevance=rel, profile=BORROWER, sources=SOURCES, analysis=analysis
    )
    assert level != "high" and any("расходятся" in r for r in reasons)
    rumor = STORY.model_copy(update={"status": "rumor"})
    assert confidence(story=rumor, relevance=rel, profile=BORROWER, sources=SOURCES)[0] == "low"
    # у экспортёра главный фактор — курс, а он предположение по отрасли
    rel_d = score_pair(STORY, DEPOSITOR, now=STORY.last_seen, facts=FACTS)
    _, reasons_d = confidence(story=STORY, relevance=rel_d, profile=DEPOSITOR, sources=SOURCES)
    assert "глубокого разбора ещё нет" in reasons_d


def test_fast_card_carries_surprise_and_confidence():
    rel = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, role="cfo")
    card = fast_card(
        STORY,
        rel,
        "cfo",
        facts=FACTS,
        items={i.id: i for i in B["raw_item"]},
        sources=SOURCES,
        bank_positions=B["bank_position"],
        profile=BORROWER,
        market=[_post(1, 13.5, 30)],
    )
    assert card.surprise and card.surprise.market_expected == 13.5 and card.surprise.bank_expected == 14.0
    assert card.confidence in ("medium", "high") and card.confidence_reasons[0].startswith(
        "сюжет подтверждён"
    )


# ---------- согласие разметчиков ----------


def _labels(rows):
    return [Label(p, lb, v) for p, lb, v in rows]


def test_kappa_values():
    assert weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3]) == 1.0
    assert weighted_kappa([0, 1, 2, 3], [3, 2, 1, 0]) == -1.0
    assert weighted_kappa([2, 2, 2], [2, 2, 2]) is None
    # рядом лучше, чем далеко
    assert weighted_kappa([0, 1, 2, 3], [1, 1, 2, 3]) > weighted_kappa([0, 1, 2, 3], [3, 1, 2, 3])


def test_agreement_report_on_shared_pairs():
    rows = [(f"p{i}", "r1", i % 4) for i in range(10)] + [(f"p{i}", "r2", i % 4) for i in range(10)]
    rows += [(f"p{i}", "r3", i % 4) for i in range(9)] + [("p9", "r3", 3)]  # r3 расходится на p9 (3 против 1)
    rows += [("own1", "r1", 2)]  # своя пара разметчика в общее согласие не идёт
    rep = agreement(_labels(rows))
    assert len(rep.pairs) == 3 and all(p.n == 10 for p in rep.pairs)
    assert rep.disputed == [("p9", {"r1": 1, "r2": 1, "r3": 3})]
    assert verdict(rep.mean_kappa) == "хорошее"


# ---------- отклик пользователя ----------


def test_not_relevant_lowers_similar_stories_only():
    from backend.ranking.feedback import apply_feedback

    p = _company("FB", "46.69", loans=True, fts=True)
    sanctions, kw = STORIES["sanctions_oil"]
    other = sanctions.model_copy(update={"id": "sanctions_2", "title": "Новые санкции против банков"})
    rate, rate_kw = STORIES["rate"]
    scores = [
        score_pair(sanctions, p, now=NOW, facts=_facts(sanctions, **kw)),
        score_pair(other, p, now=NOW, facts=_facts(other, **kw)),
        score_pair(rate, p, now=NOW, facts=_facts(rate, **rate_kw)),
    ]
    types = {"sanctions_oil": "sanctions", "sanctions_2": "sanctions", "rate": "rate_decision"}
    rows = [
        {"company_id": "FB", "story_id": "sanctions_oil", "kind": "not_relevant"},
        {"company_id": "FB", "story_id": "rate", "kind": "click_source"},
    ]  # клик в скоринг не идёт
    after = {r.story_id: r.score for r in apply_feedback(scores, rows, types)}
    before = {r.story_id: r.score for r in scores}
    assert after["sanctions_2"] == pytest.approx(before["sanctions_2"] * 0.7, abs=0.002)  # вес 0,3
    assert after["rate"] == before["rate"]
    useful = apply_feedback(scores, [{"story_id": "rate", "kind": "useful"}], types)
    assert next(r.score for r in useful if r.story_id == "rate") > before["rate"]
    assert apply_feedback(scores, [], types) == scores


def _flat_hold(rate: float):
    """Пауза ЦБ, совпавшая с ожиданием: key_rate flat (как у живых аналитиков 23.10), ставка в фактах."""
    from backend.schemas.common import ChannelImpact, NumberFact

    channels = [ChannelImpact(channel="key_rate", direction="flat", strength=0.0)]
    numbers = [NumberFact(value=rate, unit="%", quote=f"сохранил ключевую ставку на уровне {rate:g}% годовых")]
    return FACTS.model_copy(update={"channels": channels, "numbers": numbers})


def test_rate_hold_at_high_level_matters_for_borrower_and_depositor():
    from backend.profile.exposures import with_exposures

    now = STORY.last_seen
    b = score_pair(STORY, with_exposures(BORROWER), now=now, facts=_flat_hold(14.0), sources=SOURCES, role="cfo")
    d = score_pair(STORY, with_exposures(DEPOSITOR), now=now, facts=_flat_hold(14.0), sources=SOURCES,
                   role="treasurer")
    assert b.score > 0.05 and d.score > 0.05
    kr_b = next(c for c in b.contributions if c.factor == "key_rate")
    kr_d = next(c for c in d.contributions if c.factor == "key_rate")
    assert kr_b.sign == -1 and kr_d.sign == 1  # заёмщику дорогой долг, вкладчику доходность


def test_rate_hold_at_neutral_level_adds_nothing():
    from backend.profile.exposures import with_exposures

    r = score_pair(STORY, with_exposures(BORROWER), now=STORY.last_seen, facts=_flat_hold(7.5), sources=SOURCES)
    assert not any(c.factor == "key_rate" for c in r.contributions)


def test_surprise_bank_year_end_hold_counts_for_later_meeting():
    """23.10: прогноза ЦМИ к самому заседанию нет, есть «сохранение 14,0% до конца года» от 11.09."""
    from datetime import date

    from backend.schemas import Forecast

    oct23 = STORY.model_copy(update={"first_seen": STORY.first_seen.replace(month=10, day=23)})
    base = B["bank_position"][0]

    def pos(pid, metric, value, direction, doc_date=date(2026, 9, 11)):
        return base.model_copy(
            update={
                "id": pid,
                "doc_date": doc_date,
                "direction": direction,
                "superseded_by": None,
                "forecast": Forecast(metric=metric, value=value, unit="%", horizon="конец 2026"),
            }
        )

    hold = pos("cmi#hold", "key_rate_eoy_2026", 14.0, 0.0)
    cut = pos("cmi#cut", "key_rate_eoy_2026", 13.5, -1.0, date(2026, 9, 1))
    later = pos("cmi#later", "key_rate_eoy_2026", 14.0, 0.0, date(2026, 10, 24))  # после события — нельзя
    s = find_surprise(oct23, FACTS, bank_positions=[hold, cut, later])
    assert s is not None and (s.bank_expected, s.bank_ref) == (14.0, "cmi#hold")
    # прогноз на конец года со сменой ставки — не ожидание к заседанию
    assert find_surprise(oct23, FACTS, bank_positions=[cut]) is None


def test_rate_level_weaker_for_statements_than_decision():
    from backend.profile.exposures import with_exposures

    def kr(event_type):
        facts = _flat_hold(14.0).model_copy(update={"event_type": event_type})
        story = STORY.model_copy(update={"event_type": event_type})
        r = score_pair(story, with_exposures(BORROWER), now=STORY.last_seen, facts=facts, sources=SOURCES)
        return next((abs(c.value) for c in r.contributions if c.factor == "key_rate"), 0.0)

    assert 0 < kr("monetary_signal") < kr("rate_decision")


def test_surprise_hold_kept_when_superseded_after_story():
    from datetime import date

    from backend.schemas import Forecast

    oct23 = STORY.model_copy(update={"first_seen": STORY.first_seen.replace(month=10, day=23)})
    hold = B["bank_position"][0].model_copy(
        update={"id": "cmi#hold", "doc_date": date(2026, 9, 11), "direction": 0.0, "superseded_by": "cmi#nov",
                "forecast": Forecast(metric="key_rate_eoy_2026", value=14.0, unit="%", horizon="конец 2026")}
    )
    s = find_surprise(oct23, FACTS, bank_positions=[hold])  # cmi#nov вышел после 23.10 — не в списке «до события»
    assert s is not None and s.bank_expected == 14.0


def test_fast_card_fact_evidence_has_url_and_feed():
    """Цитата факта быстрой карточки — материал с адресом и лентой (во фронте «Банк России», не «первоисточник»)."""
    rel = score_pair(STORY, BORROWER, now=STORY.last_seen, facts=FACTS, role="cfo")
    facts = FACTS.model_copy(update={"summary_evidence": [
        e.model_copy(update={"feed_id": None, "url": None}) for e in FACTS.summary_evidence
    ]})
    card = fast_card(
        STORY, rel, "cfo", facts=facts, items={i.id: i for i in B["raw_item"]}, sources=SOURCES,
        bank_positions=B["bank_position"], profile=BORROWER, market=[],
    )
    ev = next(c for c in card.claims if c.claim_id == "F1").evidence[0]
    assert ev.feed_id == "cbr_press" and ev.url and ev.url.startswith("https://www.cbr.ru/")
