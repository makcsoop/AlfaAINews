"""Промпт шага card: файл prompts/card.md + подстановки. Версия берётся из заголовка файла."""

import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from newsai.agents.dossier import Dossier
from newsai.cards.features import ROLE_FOCUS, channel_label, company_features
from newsai.schemas import AnalystReport, CompanyProfile, CounterpartyGraph, RelevanceScore, Story
from newsai.schemas.common import UserRole

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "card.md"
# что из досье в промпт карточки не попадает: сырой профиль и граф платежей (вместо них — признаки и доли)
EXCLUDED_KINDS = frozenset({"profile", "counterparties"})
FORBIDDEN_KEYS = frozenset({"pay_amt", "pay_cnt", "revenue"})
_SIGN_WORD = {-1: "риск", 0: "нейтрально", 1: "возможность"}


@lru_cache(maxsize=1)
def load_prompt() -> tuple[str, str, str]:
    """(версия, system, user)."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    m = re.search(r"prompt_version:\s*(\S+)", text.splitlines()[0])
    if not m:
        raise ValueError("в заголовке prompts/card.md нет prompt_version")
    system = re.search(r"^## SYSTEM\n(.*?)(?=^## USER\n)", text, re.S | re.M)
    user = re.search(r"^## USER\n(.*)\Z", text, re.S | re.M)
    if not system or not user:
        raise ValueError("в prompts/card.md нужны разделы ## SYSTEM и ## USER")
    return f"card-v{m.group(1)}", system.group(1).strip(), user.group(1).strip()


def prompt_version() -> str:
    return load_prompt()[0]


def _safe_dossier(dossier: Dossier) -> Dossier:
    items = []
    for i in dossier.items:
        if i.kind in EXCLUDED_KINDS:
            continue
        clean = {k: v for k, v in i.data.items() if k not in FORBIDDEN_KEYS}
        items.append(i.model_copy(update={"data": clean}))
    return dossier.model_copy(update={"items": items})


def render_analyses(analyses: Sequence[AnalystReport]) -> str:
    if not analyses:
        return "(отчётов аналитиков нет)"
    lines = []
    for a in analyses:
        chans = "; ".join(
            f"{c.channel} ({channel_label(c.channel)}): {c.impact:+.1f}"
            + (f", горизонт {c.horizon}" if c.horizon else "")
            for c in a.channels
        )
        line = f"- аналитик «{a.analyst}»: {chans}. {a.summary}"
        if a.open_questions:
            line += " Открытые вопросы: " + "; ".join(a.open_questions)
        lines.append(line)
    return "\n".join(lines)


def render_contributions(relevance: RelevanceScore, n: int = 3) -> str:
    top = sorted(relevance.contributions, key=lambda c: -c.value)[:n]
    if not top:
        return "(вкладов нет)"
    return "\n".join(
        f"- {c.factor}: знак для клиента — {_SIGN_WORD[c.sign]}; вес {c.value:.2f}; признаки: "
        + ("; ".join(c.client_features) or "—")
        for c in top
    )


def build_messages(
    *,
    story: Story,
    dossier: Dossier,
    relevance: RelevanceScore,
    profile: CompanyProfile,
    role: UserRole,
    analyses: Sequence[AnalystReport],
    graph: CounterpartyGraph | None = None,
) -> list[dict[str, str]]:
    _, system, user = load_prompt()
    feats = company_features(profile, graph)
    for c in relevance.contributions:
        feats.extend(f for f in c.client_features if f not in feats)
    values = {
        "story_title": story.title,
        "event_type": dossier.event_type or story.event_type or "other",
        "story_status": story.status,
        "role": role,
        "role_focus": ROLE_FOCUS[role],
        "dossier": _safe_dossier(dossier).render_for_prompt(max_chars=16000),
        "analyses": render_analyses(analyses),
        "features": "\n".join(f"- {f}" for f in feats) or "(нет данных)",
        "contributions": render_contributions(relevance),
    }
    for key, val in values.items():
        user = user.replace("{{" + key + "}}", val)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
