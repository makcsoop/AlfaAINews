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
