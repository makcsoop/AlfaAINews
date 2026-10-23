"""Сравнение моделей для экстрактора на N сюжетах.

ТЗ, день 2: «Gemma 4 или Qwen3.6 лучше держит JSON на русском».

`uv run python scripts/compare_extract_models.py [--n 10] [--max-calls 50]`

Метрики на модель: валидный JSON, доля проверенных полей (цитата в тексте, число в цитате), сколько полей
отброшено проверкой, тип события (совпадение с правилами), время, токены,
стоимость в ₽ по журналу общего LLM-клиента.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db import models as m
from backend.db.engine import get_engine
from backend.extract.extractor import extract, extract_rules
from backend.llm.port import ClientJsonLLM
from backend.schemas import RawItem, Story

MODELS = ["google/gemma-4-26b-a4b-it", "qwen/qwen3.6-35b-a3b"]


def pick_stories(conn, n: int) -> list[tuple[Story, list[RawItem]]]:
    """Экономические сюжеты с текстом: по одному на тип события, затем самые подтверждённые."""
    rows = conn.execute(
        select(m.stories.c.data, m.event_facts.c.event_type)
        .join(m.event_facts, m.event_facts.c.story_id == m.stories.c.id)
        .where(m.event_facts.c.event_type != "other")
        .order_by(m.stories.c.corroboration.desc(), m.stories.c.last_seen.desc())
    ).all()
    chosen, seen = [], set()
    for data, et in rows + rows:
        story = Story.model_validate(data)
        if story.id in {s.id for s, _ in chosen} or (et in seen and len(seen) < n):
            continue
        items = [
            RawItem.model_validate(r)
            for r in conn.execute(
                select(m.raw_items.c.data).where(m.raw_items.c.id.in_(story.item_ids))
            ).scalars()
        ]
        if not any(i.text or i.lead for i in items):
            continue
        chosen.append((story, items))
        seen.add(et)
        if len(chosen) >= n:
            break
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--max-calls", type=int, default=50)
    args = ap.parse_args()
    with get_engine().connect() as conn:
        sample = pick_stories(conn, args.n)
    print(f"сюжетов: {len(sample)}; типы по правилам: {[extract_rules(s, i).event_type for s, i in sample]}")
    report = {}
    for model in MODELS:
        llm = ClientJsonLLM(max_calls=args.max_calls)
        t0 = time.monotonic()
        agree = changed = verified_numbers = channels = unverified = with_llm = 0
        type_changes = []
        for story, items in sample:
            base = extract_rules(story, items)
            facts = extract(story, items, llm, model=model)
            with_llm += facts.extractor_model == model or facts.extractor_model.startswith(
                model.split("/")[0]
            )
            agree += facts.event_type == base.event_type
            if facts.event_type != base.event_type:
                changed += 1
                type_changes.append(f"{base.event_type} → {facts.event_type}: {story.title[:70]}")
            verified_numbers += len(facts.numbers) - len(base.numbers)
            channels += sum(1 for c in facts.channels if c.evidence)
            unverified += len(facts.unverified_fields)
        u = llm.usage
        report[model] = {
            "вызовов": u.calls,
            "валидный JSON": f"{len(u.log)}/{u.calls}",
            "сюжетов с ответом модели": f"{with_llm}/{len(sample)}",
            "повторов из-за провалов проверки": u.calls - len(sample),
            "отброшено полей проверкой": unverified,
            "добавлено проверенных чисел": verified_numbers,
            "факторов с цитатой": channels,
            "тип совпал с правилами": agree,
            "тип уточнён моделью": changed,
            "токены вход/выход": f"{u.tokens_in}/{u.tokens_out}",
            "стоимость, ₽ (журнал клиента)": round(u.cost, 5),
            "секунд": round(time.monotonic() - t0, 1),
            "смены типа": type_changes,
        }
    out = json.dumps(report, ensure_ascii=False, indent=2)
    print(out)
    Path("docs/model_comparison.json").write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
