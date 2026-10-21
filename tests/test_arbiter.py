"""Арбитр кодом: споры аналитиков и сверка с позициями ЦМИ."""

import pytest

from newsai.agents.arbiter import arbitrate
from newsai.agents.dossier import DossierItem
from newsai.fixtures import load_bundle
from newsai.schemas import AnalystReport, ChannelImpact


def report(analyst: str, **impacts: float) -> AnalystReport:
    return AnalystReport(
        story_id="st",
        sector="46",
        analyst=analyst,
        model="m",
        channels=[
            ChannelImpact(channel=c, impact=v, horizon="quarters", rationale="r") for c, v in impacts.items()
        ],
        summary="s",
    )


def cmi(channel: str, stance: float | None, item_id: str = "cmi_x#p1#c1") -> DossierItem:
    return DossierItem(
        id=item_id, kind="bank_position", title="t", text="q", data={"channel": channel, "stance": stance}
    )


def test_opposite_medium_goes_on_review():
    res = arbitrate([report("business", fx_rub=0.6), report("finance", fx_rub=-0.9)], [])
    assert res.on_review
    assert [(d.channel, d.business, d.finance) for d in res.disputes] == [("fx_rub", 0.6, -0.9)]


def test_flat_vs_medium_is_no_dispute():
    res = arbitrate([report("business", fx_rub=0.0), report("finance", fx_rub=-0.9)], [])
    assert not res.on_review and res.disputes == []


def test_opposite_but_weak_is_no_dispute():
    res = arbitrate([report("business", fx_rub=0.3), report("finance", fx_rub=-0.9)], [])
    assert not res.on_review


def test_single_analyst_has_no_dispute():
    res = arbitrate([report("finance", fx_rub=-0.9)], [])
    assert not res.on_review


def test_cmi_against_analysts_noted_not_blocking():
    # stance по rate_cost — знак «для заёмщика»: -0.5 = ставка выше; аналитики: фактор вниз
    reports = [report("business", rate_cost=-0.6), report("finance", rate_cost=-0.9)]
    res = arbitrate(reports, [cmi("rate_cost", -0.5)])
    assert not res.on_review
    (row,) = res.bank_check
    assert (row.analysts_direction, row.bank_direction, row.agree) == ("down", "up", False)
    assert res.confidence_reasons and "rate_cost" in res.confidence_reasons[0]


def test_cmi_agrees_on_cost_channel_and_plain_channel():
    reports = [report("business", rate_cost=0.6, fx_rub=-0.3), report("finance", rate_cost=0.9, fx_rub=-0.6)]
    res = arbitrate(reports, [cmi("rate_cost", -0.5), cmi("fx_rub", -0.4, "cmi_y#p1#c1")])
    assert all(r.agree for r in res.bank_check) and len(res.bank_check) == 2
    assert res.confidence_reasons == []


def test_cmi_without_stance_is_skipped():
    res = arbitrate([report("business", rate_cost=0.6)], [cmi("rate_cost", None)])
    assert res.bank_check == []


@pytest.fixture
def bundle():
    return load_bundle("rate_14")


def test_rate14_bundle_reports_pass(bundle):
    reports = bundle["analyst_report"]
    res = arbitrate(reports, [])
    assert not res.on_review
