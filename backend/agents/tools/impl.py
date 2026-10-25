"""Реализации инструментов. Только чтение; компания и граница даты — из ToolContext."""

import re

from backend.agents.tools.calc import safe_eval
from backend.agents.tools.context import ToolContext, ToolError
from backend.agents.tools.models import (
    BankPositionOut,
    BankPositionsOut,
    CalculatorIn,
    CalculatorOut,
    CounterpartiesOut,
    CounterpartyOut,
    ExposureOut,
    FindSimilarCasesIn,
    GetBankPositionsIn,
    GetCounterpartiesIn,
    GetMarketKnowledgeIn,
    GetProfileIn,
    MarketKnowledgeListOut,
    MarketKnowledgeOut,
    NewsHit,
    ProfileOut,
    SearchNewsIn,
    SearchNewsOut,
    SimilarCase,
    SimilarCasesOut,
)

SNIPPET_LEN = 300
_WORD = re.compile(r"[\w%]+", re.UNICODE)


def _stems(text: str) -> set[str]:
    return {w[:5] for w in _WORD.findall(text.lower()) if len(w) > 1}


def _overlap(query: str, text: str) -> int:
    return len(_stems(query) & _stems(text))


def get_profile(ctx: ToolContext, _: GetProfileIn) -> ProfileOut:
    p = ctx.source.profile(ctx.company_id)
    if p is None:
        raise ToolError("not_found", "профиль компании не найден")
    top_share = None
    if p.revenue_rank_in_okved and p.revenue_peers_in_okved:
        top_share = round(min(1.0, p.revenue_rank_in_okved / p.revenue_peers_in_okved), 3)
    return ProfileOut(
        okved_code=p.okved_code,
        okved_name=p.okved_name,
        sector=p.sector,
        region=p.region,
        client_type=p.client_type,
        products=p.products,
        has_group=p.group_id is not None,
        foreign_trade=p.fts_flag,
        foreign_trade_country=p.fts_country,
        exposures=[
            ExposureOut(
                channel=e.channel, sensitivity=e.sensitivity, drivers=e.drivers, is_assumption=e.is_assumption
            )
            for e in p.exposures
        ],
        revenue_rank_in_okved=p.revenue_rank_in_okved,
        revenue_peers_in_okved=p.revenue_peers_in_okved,
        revenue_top_share=top_share,
        completeness=p.completeness,
    )


def get_counterparties(ctx: ToolContext, inp: GetCounterpartiesIn) -> CounterpartiesOut:
    g = ctx.source.counterparties(ctx.company_id)
    if g is None:
        raise ToolError("not_found", "граф контрагентов не найден")
    rows = []
    if inp.direction in (None, "supplier"):
        rows += sorted(g.suppliers, key=lambda c: -c.share)[: inp.top]
    if inp.direction in (None, "buyer"):
        rows += sorted(g.buyers, key=lambda c: -c.share)[: inp.top]
    out = [
        CounterpartyOut(
            rank=i + 1,
            direction=c.direction,
            # id физлица/связи через физлицо не отдаём; суммы и число платежей не отдаём вовсе (инвариант 8)
            counterparty_id=None if c.via_person else c.company_id,
            share=c.share,
            okved_code=c.okved_code,
            sector=c.sector,
            same_group=c.same_group,
            via_person=c.via_person,
        )
        for i, c in enumerate(rows)
    ]
    # ранг считаем внутри направления
    seen: dict[str, int] = {}
    for item in out:
        seen[item.direction] = seen.get(item.direction, 0) + 1
        item.rank = seen[item.direction]
    return CounterpartiesOut(
        counterparties=out,
        hhi_suppliers=g.hhi_suppliers,
        hhi_buyers=g.hhi_buyers,
        top_supplier_share=g.top_supplier_share,
        top_buyer_share=g.top_buyer_share,
        sector_shares_suppliers=g.sector_shares_suppliers,
        sector_shares_buyers=g.sector_shares_buyers,
        has_hidden_group_hypothesis=bool(g.linked_via_persons),
        high_dependency=g.high_dependency,
    )


def search_news(ctx: ToolContext, inp: SearchNewsIn) -> SearchNewsOut:
    story = next((s for s in ctx.source.stories() if s.id == ctx.story_id), None)
    story_items = set(story.item_ids) if story else set()
    # материалы самого сюжета — целиком; прочие новости — не позже последнего материала сюжета
    news_until = story.last_seen if story else ctx.story_published_at
    tiers = {s.id: s.tier for s in ctx.source.sources()}
    scored = []
    for it in ctx.source.raw_items():
        if it.dropped_reason or (it.id not in story_items and it.published_at > news_until):
            continue
        in_story = it.id in story_items
        score = _overlap(inp.query, f"{it.title} {it.lead or ''} {it.text or ''}")
        if score == 0 and not in_story:
            continue
        scored.append((in_story, score, it.published_at, it, tiers.get(it.source_id)))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    hits = [
        NewsHit(
            item_id=it.id,
            source_id=it.source_id,
            tier=tier,
            title=it.title,
            published_at=it.published_at,
            snippet=(it.lead or it.text or "")[:SNIPPET_LEN],
            in_story=in_story,
        )
        for in_story, _, _, it, tier in scored[: inp.limit]
    ]
    return SearchNewsOut(hits=hits)


def get_bank_positions(ctx: ToolContext, inp: GetBankPositionsIn) -> BankPositionsOut:
    boundary = ctx.story_published_at.date()
    rows = []
    for bp in ctx.source.bank_positions():
        if bp.doc_date > boundary:
            continue
        if inp.channel and bp.channel != inp.channel:
            continue
        score = 0
        if inp.query:
            score = _overlap(inp.query, f"{bp.thesis} {bp.quote} {bp.doc_title}")
            if score == 0:
                continue
        rows.append((score, bp.doc_date, bp))
    rows.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return BankPositionsOut(
        positions=[
            BankPositionOut(
                id=bp.id,
                thesis=bp.thesis,
                channel=bp.channel,
                direction=bp.direction,
                forecast=bp.forecast,
                doc_id=bp.doc_id,
                doc_title=bp.doc_title,
                doc_date=bp.doc_date,
                page=bp.page,
                quote=bp.quote,
                valid_until=bp.valid_until,
                expired=bp.valid_until is not None and bp.valid_until < boundary,
                superseded_by=bp.superseded_by,
            )
            for _, _, bp in rows[:20]
        ]
    )


def get_market_knowledge(ctx: ToolContext, inp: GetMarketKnowledgeIn) -> MarketKnowledgeListOut:
    rows = []
    for mk in ctx.source.market_knowledge():
        # инвариант 10: только опубликованное раньше сюжета и до события
        # (разбор случая — kind=case — по определению написан после своего события, но до нашего сюжета)
        if mk.published_at >= ctx.story_published_at or (mk.timing != "before_event" and mk.kind != "case"):
            continue
        if mk.origin == "telegram" and not ctx.allow_telegram_origin:
            continue
        if inp.channel and mk.channel != inp.channel:
            continue
        if inp.event_type and mk.event_type != inp.event_type:
            continue
        if inp.kind and mk.kind != inp.kind:
            continue
        rows.append(mk)
    rows.sort(key=lambda m: m.published_at, reverse=True)
    return MarketKnowledgeListOut(
        knowledge=[
            MarketKnowledgeOut(
                id=m.id,
                kind=m.kind,
                published_at=m.published_at,
                event_type=m.event_type,
                channel=m.channel,
                sectors_okved=m.sectors_okved,
                statement=m.statement,
                direction=m.direction,
                expected_value=m.expected_value,
                expected_unit=m.expected_unit,
                origin=m.origin,
                horizon=m.horizon,
                timing=m.timing,
                channel_weight=m.channel_weight,
                one_sided=m.one_sided,
            )
            for m in rows[:20]
        ]
    )


def find_similar_cases(ctx: ToolContext, inp: FindSimilarCasesIn) -> SimilarCasesOut:
    facts = {f.story_id: f for f in ctx.source.event_facts()}
    rows = []
    for st in ctx.source.stories():
        if st.id == ctx.story_id or st.event_type != inp.event_type:
            continue
        if st.first_seen >= ctx.story_published_at:
            continue
        f = facts.get(st.id)
        if inp.sector and f is not None and inp.sector[:2] not in {s[:2] for s in f.sectors_okved}:
            continue
        rows.append((st, f))
    rows.sort(key=lambda t: t[0].first_seen, reverse=True)
    return SimilarCasesOut(
        cases=[
            SimilarCase(
                story_id=st.id,
                title=st.title,
                event_type=st.event_type,
                first_seen=st.first_seen,
                summary=f.summary if f else None,
                sectors_okved=f.sectors_okved if f else [],
            )
            for st, f in rows[: inp.limit]
        ]
    )


def calculator(ctx: ToolContext, inp: CalculatorIn) -> CalculatorOut:
    return CalculatorOut(expression=inp.expression, value=float(safe_eval(inp.expression)))
