"""Отчёт качества карточек: точность чисел, повторы, рубрика."""

from backend.cards.quality import (
    assess_card,
    build_report,
    cards_from_store,
    find_duplicates,
    main,
    render_markdown,
)
from backend.schemas import Claim, Evidence


def _card():
    pairs = cards_from_store("fixture")
    assert pairs
    return pairs[0][0]


def _with_claims(card, claims):
    return card.model_copy(update={"claims": claims})


def test_fixture_cards_report():
    rows, summary = build_report(cards_from_store("fixture"))
    assert summary["cards"] == len(rows) >= 1
    assert all(1 <= r.rubric_auto <= 5 for r in rows)


def test_number_accuracy_counts_unconfirmed():
    card = _card()
    ev = Evidence(source_id="raw1", quote="Ставка сохранена на уровне 14% годовых")
    good = Claim(claim_id="g", type="fact", text="Ставка 14% годовых", evidence=[ev])
    bad = Claim(claim_id="b", type="fact", text="Ставка 21% годовых", evidence=[ev])
    q = assess_card(_with_claims(card, [good, bad]))
    assert q.numbers_total == 2 and q.numbers_confirmed == 1
    assert q.number_accuracy == 0.5
    assert any("21" in u for u in q.unconfirmed)
    assert "числа/даты не подтверждены" in q.rubric_penalties


def test_duplicates_found():
    d = find_duplicates(
        {
            "a": "ЦБ сохранил ставку на уровне 14%.",
            "b": "ЦБ сохранил ставку на уровне 14 %",
            "c": "Рубль укрепился",
        }
    )
    assert d == [("a", "b")]


def test_rubric_drops_for_problems():
    card = _card()
    ev = Evidence(source_id="raw1", quote="ставка 14%")
    clean = Claim(claim_id="a", type="fact", text="Ставка 14%", evidence=[ev])
    dup = Claim(claim_id="b", type="fact", text="Ставка 14%", evidence=[ev])
    wrong = Claim(claim_id="c", type="fact", text="Ставка 99%", evidence=[ev])
    base = assess_card(_with_claims(card, [clean]))
    worse = assess_card(_with_claims(card, [clean, dup, wrong]))
    assert "повторы утверждений" in worse.rubric_penalties
    assert "числа/даты не подтверждены" in worse.rubric_penalties
    assert worse.rubric_auto <= base.rubric_auto - 2 or worse.rubric_auto == 1


def test_markdown_and_cli(tmp_path):
    out = tmp_path / "q.md"
    assert main(["--store", "fixture", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "оценка человека" in text and "Рубрика" in text
    assert out.with_suffix(".json").exists()
    rows, summary = build_report(cards_from_store("fixture"))
    assert "Сводка" in render_markdown(rows, summary)
