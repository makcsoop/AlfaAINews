"""Правила меток карточки живут в схемах: кривой ответ LLM не пройдёт валидацию."""

import copy
import json

import pytest
from pydantic import ValidationError

from backend.fixtures import FIXTURES_DIR
from backend.schemas import Action, Claim, InsightCard


@pytest.fixture
def card() -> dict:
    raw = json.loads((FIXTURES_DIR / "rate_14" / "bundle.json").read_text(encoding="utf-8"))
    return copy.deepcopy(raw["insight_card"][0])


def _claim(**kw) -> dict:
    return {"claim_id": "X", "text": "t", **kw}


@pytest.mark.parametrize(
    ("claim", "msg"),
    [
        (_claim(type="fact"), "факт без источника"),
        (_claim(type="bank_view", evidence=[{"source_id": "prime:1"}]), "только на материалы ЦМИ"),
        (_claim(type="bank_view"), "только на материалы ЦМИ"),
        (
            _claim(type="bank_view", evidence=[{"source_id": "cmi_a#p1#c1"}, {"source_id": "tg:x:1"}]),
            "только на материалы ЦМИ",
        ),
        (_claim(type="market_view", evidence=[{"source_id": "cmi_a#p1#c1"}]), "это позиция банка"),
        (_claim(type="model_inference"), "без based_on"),
        (_claim(type="model_inference", based_on={"facts": []}), "без based_on"),
        (_claim(type="bank_offer"), "как реклама"),
        (_claim(type="bank_offer", is_advertising=True), "по продуктовому триггеру"),
        (_claim(type="market_view"), "без ссылки на пост"),
    ],
)
def test_claim_label_rules(claim, msg):
    with pytest.raises(ValidationError, match=msg):
        Claim.model_validate(claim)


def test_valid_claims_pass():
    Claim.model_validate(_claim(type="bank_view", evidence=[{"source_id": "cmi_keyrate_2026_09#p1#c1"}]))
    Claim.model_validate(_claim(type="market_view", evidence=[{"source_id": "mk_consensus"}]))
    Claim.model_validate(_claim(type="bank_offer", is_advertising=True, trigger="fix_rate"))
    Claim.model_validate(_claim(type="model_inference", based_on={"facts": ["F1"]}))


def test_bank_product_action_must_be_ad():
    with pytest.raises(ValidationError, match="как реклама"):
        Action.model_validate({"type": "bank_product", "text": "депозит"})


def test_card_fixture_valid(card):
    InsightCard.model_validate(card)


def test_card_dangling_ref(card):
    card["why_it_matters"].append("NOPE")
    with pytest.raises(ValidationError, match="нет утверждения NOPE"):
        InsightCard.model_validate(card)


def test_card_inference_in_facts_section(card):
    card["what_happened"].append("I1")
    with pytest.raises(ValidationError, match="what_happened"):
        InsightCard.model_validate(card)


def test_card_inference_in_bank_section(card):
    card["bank_and_market"].append("I1")
    with pytest.raises(ValidationError, match="bank_and_market"):
        InsightCard.model_validate(card)


def test_card_duplicate_claim_ids(card):
    card["claims"].append(copy.deepcopy(card["claims"][0]))
    with pytest.raises(ValidationError, match="повторяющиеся"):
        InsightCard.model_validate(card)


def test_card_relevance_for_other_client(card):
    card["relevance"]["company_id"] = "U_OTHER"
    with pytest.raises(ValidationError, match="другой паре"):
        InsightCard.model_validate(card)


def test_trigger_is_offer_id_not_free_text():
    # Б3: формат триггера — id из таблицы триггеров Р3 (fix_rate, long_deposit, fx_hedge)
    with pytest.raises(ValidationError):
        Claim.model_validate(_claim(type="bank_offer", is_advertising=True, trigger="loa+rate_up"))


def test_action_refs_and_offer_kind():
    Action.model_validate({"type": "scenario", "text": "проверить план", "kind": "model_inference", "refs": ["I1"]})
    with pytest.raises(ValidationError, match="bank_offer"):
        Action.model_validate({"type": "bank_product", "text": "депозит", "is_advertising": True, "kind": "fact"})
    with pytest.raises(ValidationError, match="bank_offer"):
        Action.model_validate({"type": "scenario", "text": "депозит", "kind": "bank_offer"})


def test_conditions_fill_and_match_what_would_change(card):
    card["what_would_change"] = []
    filled = InsightCard.model_validate(card)
    assert filled.what_would_change == [c.text for c in filled.conditions] and filled.conditions[0].refs
    card["what_would_change"] = ["другой текст"]
    with pytest.raises(ValidationError, match="расходится"):
        InsightCard.model_validate(card)
