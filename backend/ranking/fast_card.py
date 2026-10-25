"""Быстрая карточка без LLM: появляется сразу после скоринга, глубокий разбор потом догружается в неё же.

Только то, что уже проверено кодом: факт с цитатой, статус сюжета, вклады скоринга словами и ближайшая
позиция ЦМИ по главному фактору и теме сюжета. Выводы — шаблоны из вкладов, без генерации.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime

from backend.bank.search import topic_overlap
from backend.profile.taxonomy import load_matrix
from backend.ranking.confidence import confidence
from backend.ranking.select import factor_label, top_contributions
from backend.ranking.surprise import find_surprise
from backend.schemas import (
    BankPosition,
    Claim,
    CompanyProfile,
    Contribution,
    EventFacts,
    InsightCard,
    MarketKnowledge,
    RawItem,
    RelevanceScore,
    Source,
    Story,
)
from backend.schemas.common import Evidence, UserRole
from backend.schemas.insight import CardEvent, StorySource

SIGN_WORD = {-1: "это риск", 1: "это возможность", 0: "знак станет ясен после разбора"}
STATUS_REASON = {
    "confirmed": "сюжет подтверждён официальным или двумя независимыми источниками",
    "single": "пока один источник",
    "unconfirmed": "сюжет не подтверждён",
    "rumor": "это слух",
    "contradictory": "источники расходятся",
}


def card_id(story_id: str, company_id: str, role: UserRole) -> str:
    return f"card_{story_id}_{company_id}_{role}"


def _fact(story: Story, facts: EventFacts | None, items: Mapping[str, RawItem]) -> Claim:
    if facts is not None and facts.summary_evidence:
        # у цитаты экстрактора дописываем ссылку и ленту материала: во фронте — «ПРАЙМ», а не «первоисточник»
        evidence = [
            e.model_copy(update={"url": e.url or it.url, "feed_id": e.feed_id or it.source_id})
            if (it := items.get(e.source_id)) is not None
            else e
            for e in facts.summary_evidence
        ]
        return Claim(claim_id="F1", type="fact", text=facts.summary, evidence=evidence)
    # без фактов экстрактора — заголовок первого материала сюжета как цитата
    first = next((items[i] for i in story.item_ids if i in items), None)
    if first is None:
        raise ValueError(f"{story.id}: нет ни фактов с цитатой, ни материалов — быструю карточку не собрать")
    ev = Evidence(
        source_id=first.id, quote=first.title, url=first.url, doc_date=first.published_at,
        feed_id=first.source_id,
    )
    return Claim(claim_id="F1", type="fact", text=facts.summary if facts else first.title, evidence=[ev])


# флаги CSV словами — только для текста; в based_on остаются исходные признаки (их сверяет проверка карточки)
FEATURE_WORDS = {
    "CL_PRODUCT_LOA_MARK=1": "кредиты в банке",
    "CL_PRODUCT_LEA_MARK=1": "лизинг",
    "CL_PRODUCT_DEP_MARK=1": "депозиты",
    "CL_PRODUCT_GAR_MARK=1": "гарантии",
    "CL_PRODUCT_ZPP_MARK=1": "зарплатный проект",
    "CL_PRODUCT_RKO_MARK=1": "расчётный счёт",
    "FACTORING_MARK=1": "факторинг",
    "FTS_FLAG=1": "ВЭД",
}


def _words(features: Sequence[str]) -> str:
    out = []
    for f in features:
        if f.startswith("MAINOKVED_CODE="):
            out.append(f"отрасль ОКВЭД {f.split('=', 1)[1]}")
        elif f.startswith("FTS_COUNTRY_NAME="):
            out.append(f"ВЭД: {f.split('=', 1)[1]}")
        else:
            out.append(FEATURE_WORDS.get(f, f))
    return ", ".join(out)


def _inference(n: int, c: Contribution, sensitivity: float | None) -> Claim:
    ch = load_matrix().channels.get(c.factor)
    if ch and c.sign and sensitivity:
        # знак вклада = направление фактора × знак чувствительности → направление обратно
        up = c.sign * (1 if sensitivity > 0 else -1) > 0
        moved = (
            f"событие толкает фактор вверх ({ch.up})"
            if up
            else f"событие толкает фактор вниз (рост: {ch.up})"
        )
        text = f"{ch.name}: {moved}. У компании {_words(c.client_features)} — {SIGN_WORD[c.sign]}."
    else:
        text = f"Событие {factor_label(c.factor)}: {_words(c.client_features)}; {SIGN_WORD[c.sign]}."
    return Claim(
        claim_id=f"I{n}",
        type="model_inference",
        text=text,
        based_on={"facts": ["F1"], "client_features": list(c.client_features)},
        polarity={-1: "risk", 1: "opportunity"}.get(c.sign, "neutral"),
    )


def _bank_view(
    positions: Sequence[BankPosition], channel: str | None, story_day: date, story_text: str
) -> Claim | None:
    # канал общий ещё не значит «про это»: позиция должна совпадать с сюжетом по теме (topic_overlap)
    fresh = [
        p
        for p in positions
        if p.channel == channel
        and p.doc_date <= story_day
        and (p.valid_until is None or p.valid_until >= story_day)
        and topic_overlap(p.thesis, story_text) > 0
    ]
    if not fresh:
        return None
    p = max(fresh, key=lambda p: p.doc_date)
    ev = Evidence(source_id=p.id, quote=p.quote, doc_date=datetime.combine(p.doc_date, datetime.min.time()))
    return Claim(claim_id="B1", type="bank_view", text=f"ЦМИ Альфа-Банка: {p.thesis}", evidence=[ev])


def fast_card(
    story: Story,
    relevance: RelevanceScore,
    role: UserRole,
    *,
    facts: EventFacts | None = None,
    items: Mapping[str, RawItem] | None = None,
    sources: Mapping[str, Source] | None = None,
    bank_positions: Sequence[BankPosition] = (),
    profile: CompanyProfile | None = None,
    market: Sequence[MarketKnowledge] = (),
) -> InsightCard:
    items, sources = items or {}, sources or {}
    matrix = load_matrix()
    sens = {e.channel: e.sensitivity for e in profile.exposures} if profile else {}
    claims = [_fact(story, facts, items)]
    why: list[str] = []
    for n, c in enumerate(top_contributions(relevance, 2), 1):
        claims.append(_inference(n, c, sens.get(c.factor)))
        why.append(f"I{n}")

    main_channel = next((c.factor for c in relevance.contributions if c.factor in matrix.channels), None)
    story_text = " ".join(
        [story.title, facts.summary if facts else "", *(items[i].title for i in story.item_ids if i in items)]
    )
    bank = _bank_view(bank_positions, main_channel, story.first_seen.date(), story_text)
    if bank:
        claims.append(bank)

    event = CardEvent(
        story_id=story.id,
        event_type=story.event_type,
        status=story.status,
        title=story.title,
        first_seen=story.first_seen,
        sources=[
            StorySource(
                source_id=sid,
                tier=sources[sid].tier if sid in sources else 3,
                title=sources[sid].name if sid in sources else None,
                url=next(
                    (items[i].url for i in story.item_ids if i in items and items[i].source_id == sid), None
                ),
            )
            for sid in story.source_ids
        ],
        corroboration=story.corroboration,
    )
    surprise = find_surprise(story, facts, bank_positions=bank_positions, market=market)
    level, reasons = confidence(
        story=story,
        relevance=relevance,
        profile=profile or CompanyProfile(company_id=relevance.company_id),
        sources=dict(sources),
        claims=claims,
        bank_positions=bank_positions,
        market=market,
    )
    reasons.insert(0, STATUS_REASON.get(story.status, story.status))
    return InsightCard(
        card_id=card_id(story.id, relevance.company_id, role),
        company_id=relevance.company_id,
        user_role=role,
        stage="fast",
        event=event,
        relevance=relevance.model_copy(update={"role": role}),
        what_happened=["F1"],
        why_it_matters=why,
        consequences=[],
        bank_and_market=["B1"] if bank else [],
        surprise=surprise,
        claims=claims,
        confidence=level,
        confidence_reasons=reasons,
    )
