"""Граф платежей: доли, HHI, внутригрупповые платежи, контрагенты вне базы, флаги зависимости."""

from types import SimpleNamespace

import pytest

from backend.profile.graph import MIN_COUNTERPARTIES, _by_company, _side
from backend.schemas.client import OUTSIDE_SECTOR


def edge(cp, amt, *, sector="46", in_dataset=True, same_group=False, company="U_1", direction="supplier"):
    return SimpleNamespace(
        company_id=company,
        dir=direction,
        cp=cp,
        amt=amt,
        cnt=1,
        in_dataset=in_dataset,
        okved_code=f"{sector}.10" if in_dataset else None,
        sector=sector if in_dataset else None,
        same_group=same_group,
    )


def test_shares_hhi_and_intragroup_excluded():
    side = _side(
        [
            edge("A", 60),
            edge("B", 20, sector="24"),
            edge("C", 20, in_dataset=False),
            edge("G", 900, same_group=True),
        ],
        "supplier",
        links={"B"},
    )
    assert side["total"] == 100 and side["intragroup"] == 900 and side["n"] == 3
    assert [c.company_id for c in side["top"]] == ["A", "B", "C"]
    assert side["top_share"] == pytest.approx(0.6)
    assert side["hhi"] == pytest.approx(0.6**2 + 0.2**2 + 0.2**2)
    assert side["sectors"] == {
        "46": pytest.approx(0.6),
        "24": pytest.approx(0.2),
        OUTSIDE_SECTOR: pytest.approx(0.2),
    }
    by_id = {c.company_id: c for c in side["top"]}
    assert (
        by_id["C"].in_dataset is False
        and by_id["C"].sector == OUTSIDE_SECTOR
        and by_id["C"].okved_code is None
    )
    assert by_id["B"].via_person is True and by_id["A"].via_person is False


def test_only_intragroup_payments_give_empty_side():
    side = _side([edge("G", 50, same_group=True)], "buyer", links=set())
    assert side["total"] == 0 and side["top"] == [] and side["hhi"] is None and side["intragroup"] == 50


def test_top_is_capped_at_ten():
    side = _side([edge(f"X{i}", 100 - i) for i in range(15)], "supplier", links=set())
    assert len(side["top"]) == 10 and side["n"] == 15
    assert sum(c.share for c in side["top"]) < 1


def test_by_company_groups_sorted_stream():
    rows = [
        edge("A", 1, company="U_1"),
        edge("B", 1, company="U_1", direction="buyer"),
        edge("C", 1, company="U_2"),
    ]
    grouped = dict(_by_company(rows))
    assert set(grouped) == {"U_1", "U_2"}
    assert len(grouped["U_1"]["supplier"]) == 1 and len(grouped["U_1"]["buyer"]) == 1


def test_min_counterparties_rule_is_five():
    assert MIN_COUNTERPARTIES == 5
