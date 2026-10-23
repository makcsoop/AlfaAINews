"""Отклик пользователя в скоринге: только явные «полезно» и «не про нас» (клики — только в метрики, У21).

Отметка переносится на похожие сюжеты этой же компании: тот же тип события — полностью,
тот же главный фактор — наполовину. Итоговый множитель 1 ± вес × отметки, не дальше feedback_cap.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from backend.ranking.config import ScoringConfig, load_scoring
from backend.schemas import RelevanceScore

EXPLICIT = {"useful": 1, "not_relevant": -1}


def _main_factor(r: RelevanceScore) -> str | None:
    best = max(r.contributions, key=lambda c: c.value, default=None)
    return best.factor if best else None


def apply_feedback(
    scores: Sequence[RelevanceScore],
    rows: Iterable[Mapping[str, Any]],
    event_type_of: Mapping[str, str | None],
    *,
    cfg: ScoringConfig | None = None,
) -> list[RelevanceScore]:
    """rows — записи таблицы feedback этой компании (company_id, story_id, kind).
    event_type_of — тип события по story_id (для сюжетов из ленты и из отметок)."""
    f = (cfg or load_scoring()).feed
    by_story = {r.story_id: r for r in scores}
    marks = []
    for row in rows:
        sign = EXPLICIT.get(str(row.get("kind")))
        story_id = row.get("story_id")
        if sign and story_id:
            rated = by_story.get(story_id)
            marks.append((sign, event_type_of.get(story_id), _main_factor(rated) if rated else None))
    if not marks:
        return list(scores)
    out = []
    for r in scores:
        et, factor = event_type_of.get(r.story_id), _main_factor(r)
        delta = 0.0
        for sign, m_et, m_factor in marks:
            similarity = 1.0 if et and et == m_et else 0.5 if factor and factor == m_factor else 0.0
            delta += sign * similarity * f.feedback_weight
        delta = max(-f.feedback_cap, min(f.feedback_cap, delta))
        out.append(r.model_copy(update={"score": round(min(1.0, r.score * (1 + delta)), 3)}) if delta else r)
    return out
