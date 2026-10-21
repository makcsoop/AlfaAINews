"""Факторы согласованы между таксономией, фикстурой и реестром ЦМИ; знак для клиента — по правилу У17."""

import yaml

from newsai.fixtures import load_bundle
from tests.conftest import ROOT

TAXONOMY = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
CHANNELS = set(TAXONOMY["channels"])
DIRECTION = {"up": 1, "down": -1, "flat": 0}


def test_event_types_reference_known_channels():
    for name, et in TAXONOMY["event_types"].items():
        assert set(et["channels"]) <= CHANNELS, name


def test_bank_docs_reference_known_channels():
    docs = yaml.safe_load((ROOT / "config" / "bank_docs.yaml").read_text(encoding="utf-8"))["docs"]
    for d in docs:
        assert set(d["channels"]) <= CHANNELS, d["id"]


def test_fixture_channels_known():
    b = load_bundle("rate_14")
    used = {c.channel for e in b["event_facts"] for c in e.channels}
    used |= {d.channel for a in b["analysis"] for d in a.drivers}
    used |= {c.channel for r in b["analyst_report"] for c in r.channels}
    used |= {x.channel for p in b["company_profile"] for x in p.exposures}
    used |= {p.channel for p in b["bank_position"] if p.channel}
    assert used <= CHANNELS, used - CHANNELS


def test_client_sign_is_direction_times_sensitivity():
    # У17: разбор даёт направление фактора, «риск / возможность» появляется только умножением на профиль
    b = load_bundle("rate_14")
    drivers = {c.channel: DIRECTION[c.direction] for c in b["event_facts"][0].channels}
    profiles = {p.company_id: {x.channel: x.sensitivity for x in p.exposures} for p in b["company_profile"]}
    checked = 0
    for card in b["insight_card"]:
        for c in card.relevance.contributions:
            if c.factor in drivers and c.factor in profiles[card.company_id]:
                expected = drivers[c.factor] * (1 if profiles[card.company_id][c.factor] > 0 else -1)
                assert c.sign == expected, (card.card_id, c.factor)
                checked += 1
    assert checked >= 3
