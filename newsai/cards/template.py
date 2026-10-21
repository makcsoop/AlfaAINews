"""Шаблонная карточка без LLM: из фактов досье, вкладов скоринга и отчётов аналитиков.

Используется, когда модель недоступна, бюджет исчерпан или её черновик не прошёл сборку.
Возвращает тот же CardDraft, поэтому проходит ту же сборку и те же правила меток и ссылок.
"""

from collections.abc import Sequence
from datetime import datetime

from newsai.agents.dossier import Dossier, DossierItem
from newsai.cards.build import CardAssemblyError, _as_datetime
from newsai.cards.features import channel_label, company_features, is_sector_feature
from newsai.cards.llm_schema import CardDraft, DraftAction, DraftItem
from newsai.cards.signals import channel_horizon, channel_mean
from newsai.schemas import (
    AnalystReport,
    CompanyProfile,
    Contribution,
    CounterpartyGraph,
    RelevanceScore,
    Story,
)
from newsai.schemas.common import BANK_SOURCE_PREFIX, UserRole

_SIGN_WORD = {-1: "риск", 0: "нейтральный знак", 1: "возможность"}

_HORIZON_RU = {"days": "дни", "weeks": "недели", "months": "месяцы", "quarters": "кварталы", "years": "годы"}

ROLE_QUESTION: dict[UserRole, str] = {
    "cfo": "Как это отражается на плане процентных расходов и обязательствах по долгу?",
    "treasurer": "Нужно ли пересмотреть сроки и валюту размещения свободных остатков?",
    "chief_accountant": "Затрагивает ли событие налоговые расчёты и отчётность компании?",
    "ceo": "Как это влияет на спрос и на ключевых покупателей и поставщиков компании?",
    "owner": "Как это влияет на спрос и на ключевых покупателей и поставщиков компании?",
    "other": "Какие договоры и расчёты компании затрагивает это событие?",
}

CHANNEL_QUESTION: dict[str, str] = {
    "rate_cost": "Какая доля долга и лизинга компании привязана к плавающей ставке?",
    "deposit_yield": "Какую часть свободных остатков можно разместить на более длинный срок без "
    "потери ликвидности?",
    "fx_rub": "Какая часть валютной выручки и платежей по ВЭД не закрыта по текущему курсу?",
}


def _strength(x: float) -> str:
    a = abs(x)
    return "сильное" if a >= 0.6 else "умеренное" if a >= 0.3 else "слабое"


def _top(relevance: RelevanceScore, n: int = 2) -> list[Contribution]:
    return sorted(relevance.contributions, key=lambda c: -c.value)[:n]


def _channel_of(c: Contribution) -> str | None:
    return None if c.factor.startswith("counterparty:") else c.factor


def _factor_label(factor: str) -> str:
    if factor.startswith("counterparty:"):
        return f"покупатели и поставщики из отрасли {factor.split(':', 1)[1]}"
    return channel_label(factor)


def _bank_candidates(dossier: Dossier, channel: str | None, story_at: datetime) -> list[DossierItem]:
    out = []
    for i in dossier.by_kind("bank_position"):
        if not i.id.startswith(BANK_SOURCE_PREFIX) or i.data.get("superseded_by"):
            continue
        if channel and i.data.get("channel") != channel:
            continue
        until = i.data.get("valid_until")
        if until:
            d = _as_datetime(until)
            if d is not None and d.date() < story_at.date():
                continue
        out.append(i)
    far = datetime.min.replace(tzinfo=story_at.tzinfo)
    out.sort(key=lambda i: i.published_at or _as_datetime(i.data.get("doc_date")) or far, reverse=True)
    return out[:2]


def template_draft(
    *,
    story: Story,
    dossier: Dossier,
    relevance: RelevanceScore,
    profile: CompanyProfile,
    role: UserRole,
    analyses: Sequence[AnalystReport] = (),
    graph: CounterpartyGraph | None = None,
) -> CardDraft:
    facts = dossier.by_kind("fact") or dossier.by_kind("news")
    if not facts:
        raise CardAssemblyError("в досье нет ни фактов, ни материалов: шаблон собрать не из чего")
    fact_refs = [i.id for i in facts[:2]]
    main = next((i for i in facts if i.text), facts[0])
    fact0 = [fact_refs[0]]

    tops = _top(relevance)
    why: list[DraftItem] = []
    seen_feats: set[str] = set()
    for c in tops:
        feats = [f for f in c.client_features if not is_sector_feature(f)]
        if not feats:
            continue
        seen_feats.update(feats)
        why.append(
            DraftItem(
                text=(
                    f"Событие затрагивает вашу компанию по каналу «{_factor_label(c.factor)}»: "
                    f"{'; '.join(feats)}."
                ),
                refs=fact0,
                client_features=feats,
            )
        )
    if len(seen_feats) < 2:
        extra = [
            f for f in company_features(profile, graph) if not is_sector_feature(f) and f not in seen_feats
        ]
        if extra:
            why.append(
                DraftItem(
                    text=f"Профиль компании: {'; '.join(extra[:3])}.", refs=fact0, client_features=extra[:3]
                )
            )
    if not why:
        raise CardAssemblyError("нет признаков компании для раздела «почему важно»")

    consequences: list[DraftItem] = []
    for c in tops:
        ch = _channel_of(c)
        mean = channel_mean(analyses, ch) if ch else None
        horizon = channel_horizon(analyses, ch) if ch else None
        if mean is not None:
            tail = f"аналитики оценивают воздействие как {_strength(mean)}"
            tail += f", горизонт — {_HORIZON_RU[horizon]}" if horizon else ""
        else:
            tail = "оценка аналитиков по этому каналу недоступна"
        consequences.append(
            DraftItem(
                text=(
                    f"Канал «{_factor_label(c.factor)}»: {tail}; для вашей компании по профилю "
                    f"это — {_SIGN_WORD[c.sign]}."
                ),
                refs=fact0,
                client_features=[f for f in c.client_features if not is_sector_feature(f)],
            )
        )
    if not consequences:
        consequences.append(
            DraftItem(
                text="Оценка последствий для компании пока недоступна: нет вкладов каналов.", refs=fact0
            )
        )

    top_channel = next((ch for c in tops if (ch := _channel_of(c))), None)
    actions = [DraftAction(type="analysis_question", text=ROLE_QUESTION[role], refs=fact0)]
    if top_channel in CHANNEL_QUESTION:
        actions.append(DraftAction(type="analysis_question", text=CHANNEL_QUESTION[top_channel], refs=fact0))

    bank_items = _bank_candidates(dossier, top_channel, dossier.story_published_at)
    if not bank_items and top_channel:  # по главному каналу позиции нет: берём ближайшую по теме сюжета
        bank_items = _bank_candidates(dossier, None, dossier.story_published_at)
    bank = [DraftItem(text=i.title or i.text, refs=[i.id], kind="bank_view") for i in bank_items]
    for i in dossier.by_kind("market_knowledge")[:1]:
        text = i.title or i.text
        real = i.id.startswith("mk_") and text and not text.upper().startswith("ЗАГЛУШКА")
        if real and (top_channel is None or i.data.get("channel") in (None, top_channel)):
            bank.append(DraftItem(text=text, refs=[i.id], kind="market_view"))

    label = _factor_label(tops[0].factor) if tops else "связанным каналам"
    return CardDraft(
        headline=story.title,
        what_happened=[DraftItem(text=main.text or main.title, refs=fact_refs)],
        why_it_matters=why[:4],
        consequences=consequences[:4],
        actions=actions,
        bank_and_market=bank,
        what_would_change=[
            DraftItem(
                text=f"Новые данные по каналу «{label}» или пересмотр оценок аналитиков изменят вывод.",
                refs=fact0,
            )
        ],
    )
