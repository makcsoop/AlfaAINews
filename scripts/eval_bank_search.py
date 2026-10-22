"""Проверка поиска по реестру ЦМИ на 10 вопросах «что банк думает о…» (ТЗ, день 3).

`uv run python scripts/eval_bank_search.py` → docs/bank_search_eval.md. Метрика — hit@3: в первых трёх
результатах есть позиция из обзора, где по вопросу есть ответ (ожидаемые обзоры размечены вручную).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.bank.search import BankIndex
from backend.db.engine import get_engine

QUESTIONS = [
    ("Какой прогноз банка по ключевой ставке на конец 2026 года?", {"cmi_keyrate_2026_09", "cmi_macro_1h26"}),
    (
        "Что банк ждёт от инфляции по итогам года?",
        {"cmi_macro_1h26", "cmi_inflation_2026_08w5", "cmi_keyrate_2026_09"},
    ),
    ("Прогноз курса рубля к доллару на конец года", {"cmi_macro_1h26", "cmi_fi_strategy_2026_q3"}),
    ("Какой рост ВВП ожидает банк в 2026 году?", {"cmi_macro_1h26", "cmi_consumption_2026_08"}),
    (
        "Что происходит с рынком труда и безработицей?",
        {"cmi_macro_7m26", "cmi_consumption_2026_08", "cmi_macro_1h26"},
    ),
    ("Как банк оценивает доходности ОФЗ?", {"cmi_bondbrief_2026_09_14", "cmi_fi_strategy_2026_q3"}),
    ("Что банк думает о золоте?", {"cmi_gold_2026_08"}),
    ("Охлаждается ли потребительский спрос?", {"cmi_consumption_2026_08", "cmi_macro_7m26"}),
    (
        "Как будут расти номинальные зарплаты?",
        {"cmi_consumption_2026_08", "cmi_macro_1h26", "cmi_macro_7m26"},
    ),
    (
        "Какие облигации интересны в третьем квартале?",
        {"cmi_fi_strategy_2026_q3", "cmi_bondbrief_2026_09_14"},
    ),
]


def main() -> None:
    with get_engine().connect() as conn:
        index = BankIndex.load(conn)
    registry = [p for p in index.positions if "#r" in p.id]
    lines = [
        "# Поиск по реестру позиций ЦМИ: 10 вопросов",
        "",
        f"Позиций в реестре: {len(registry)}. Метрика — hit@3: в первых трёх результатах есть позиция",
        "из обзора,"
        " где по вопросу есть ответ. Поиск — BM25 (лемматизация) + символьный TF-IDF, слияние RRF (k = 60).",
        "",
        "| # | Вопрос | hit@3 | Первый результат (обзор, стр.) |",
        "|---|---|---|---|",
    ]
    hits = 0
    for i, (q, expected) in enumerate(QUESTIONS, 1):
        res = index.search(q, k=3)
        ok = any(h.position.doc_id in expected for h in res)
        hits += ok
        top = res[0].position if res else None
        cell = f"{top.thesis[:90]} ({top.doc_id}, с. {top.page})" if top else "—"
        lines.append(f"| {i} | {q} | {'да' if ok else 'нет'} | {cell.replace('|', '/')} |")
    lines += ["", f"**hit@3 = {hits}/{len(QUESTIONS)}**"]
    Path("docs/bank_search_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
