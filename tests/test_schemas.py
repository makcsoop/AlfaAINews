import pytest
from pydantic import ValidationError

from backend.fixtures import load_bundle
from backend.schemas import ALL_SCHEMAS, Claim, InsightCard


def test_fixture_covers_every_schema():
    bundle = load_bundle("rate_14")
    assert set(bundle) == set(ALL_SCHEMAS)
    assert all(bundle.values())


@pytest.mark.parametrize("idx", [0, 1])
def test_card_claim_refs_resolve(idx):
    card: InsightCard = load_bundle("rate_14")["insight_card"][idx]
    ids = {c.claim_id for c in card.claims}
    for section in (card.what_happened, card.why_it_matters, card.consequences, card.bank_and_market):
        assert set(section) <= ids
    assert all(c.type == "fact" for c in card.claims if c.claim_id in card.what_happened)
    assert all(
        c.type in {"bank_view", "market_view"} for c in card.claims if c.claim_id in card.bank_and_market
    )
    # позиция банка — только со ссылкой на материал ЦМИ
    for c in card.claims:
        if c.type == "bank_view":
            assert c.evidence and all(e.source_id.startswith("cmi_") for e in c.evidence)


def test_same_story_differs_by_client():
    a, b = load_bundle("rate_14")["insight_card"]
    assert a.event.story_id == b.event.story_id
    assert a.relevance.polarity == "risk" and b.relevance.polarity == "opportunity"


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        Claim.model_validate({"claim_id": "X", "type": "fact", "text": "t", "made_up": 1})


def test_unknown_claim_type_rejected():
    with pytest.raises(ValidationError):
        Claim.model_validate({"claim_id": "X", "type": "opinion", "text": "t"})


def test_json_schema_export():
    for model in ALL_SCHEMAS.values():
        assert model.model_json_schema()["type"] == "object"


def test_bank_refs_resolve_to_registry():
    from pathlib import Path

    import yaml

    registry = yaml.safe_load(Path("config/bank_docs.yaml").read_text(encoding="utf-8"))
    docs = {d["id"] for d in registry["docs"]}
    b = load_bundle("rate_14")
    positions = {p.id: p for p in b["bank_position"]}
    assert all(p.doc_id in docs for p in positions.values())
    for card in b["insight_card"]:
        for c in card.claims:
            if c.type == "bank_view":
                assert all(e.source_id in positions for e in c.evidence)
        if card.surprise and card.surprise.bank_ref:
            assert card.surprise.bank_ref in positions
    # устаревшая позиция помечена сюжетом, который её перекрыл
    assert positions["cmi_macro_1h26#p1#c1"].superseded_by == "st_2026-09-11_keyrate"
