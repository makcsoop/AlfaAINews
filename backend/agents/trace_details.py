"""Промежуточные результаты прогона — в трассу (`RunTrace`, схема 0.2.3) для экрана «Как разбирали».

План досье, сводка того, что нашли инструменты, отчёты аналитиков по каналам, итог арбитра, круги
критика, источник текста карточки и экономия кеша. Только сводки: id, вид и заголовок элемента досье,
без сырых данных. Признаки компании и контрагенты — только числом (там доли платежей и отрасли
контрагентов), суммы платежей и данные физлиц в трассу не попадают никогда.
"""

from typing import Any

from backend.agents.pipeline import PipelineContext
from backend.schemas import RunTrace
from backend.schemas.insight import TraceArbiter, TraceCriticRound, TracePlanStep, TraceToolResult

# в аргументах и данных трассы этих ключей быть не должно
FORBIDDEN_KEYS = frozenset({"pay_amt", "pay_cnt", "revenue", "company_id", "inn", "person", "persons"})
# виды досье, которые показываем только числом: это признаки компании и её контрагенты
COUNT_ONLY_KINDS = frozenset({"profile", "counterparties"})
MAX_ITEMS_PER_TOOL = 12
TITLE_LEN = 140


def _clean_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in FORBIDDEN_KEYS and v not in (None, "", [], {})}


def _plan(dossier: dict[str, Any]) -> list[TracePlanStep]:
    out: list[TracePlanStep] = []
    for s in dossier.get("plan") or []:
        out.append(
            TracePlanStep(
                tool=str(s.get("tool", "")),
                args=_clean_args(dict(s.get("args") or {})),
                why=str(s.get("why") or ""),
                origin=str(s.get("origin") or "template"),
            )
        )
    return out


def _tool_results(dossier: dict[str, Any]) -> list[TraceToolResult]:
    groups: dict[str, TraceToolResult] = {}
    for it in dossier.get("items") or []:
        tool = str(it.get("source_tool") or f"досье:{it.get('kind')}")
        res = groups.setdefault(tool, TraceToolResult(tool=tool))
        res.found += 1
        kind = str(it.get("kind") or "")
        if kind in COUNT_ONLY_KINDS or len(res.items) >= MAX_ITEMS_PER_TOOL:
            continue
        title = str(it.get("title") or "")[:TITLE_LEN]
        res.items.append({"id": str(it.get("id")), "kind": kind, "title": title})
    return list(groups.values())


def _arbiter(ctx: PipelineContext) -> TraceArbiter | None:
    raw = ctx.artifacts.get("arbiter")
    if raw is None and ctx.analysis is None:
        return None
    raw = raw or {}
    disputes = [
        f"{d.get('channel')}: бизнес {float(d.get('business') or 0):+.1f}, "
        f"финансы {float(d.get('finance') or 0):+.1f} — {d.get('reason')}"
        for d in raw.get("disputes") or []
    ]
    bank = [
        f"{b.get('channel')}: аналитики {b.get('analysts_direction')}, ЦМИ {b.get('bank_direction')} — "
        + ("совпадает" if b.get("agree") else "расходится")
        for b in raw.get("bank_check") or []
    ]
    return TraceArbiter(
        agreement=dict(ctx.analysis.agreement) if ctx.analysis is not None else {},
        disputes=disputes,
        bank_check=bank,
        on_review=bool(raw.get("on_review", ctx.on_review)),
    )


def _critic(ctx: PipelineContext) -> list[TraceCriticRound]:
    out: list[TraceCriticRound] = []
    for r in ctx.artifacts.get("critic_rounds") or []:
        out.append(
            TraceCriticRound(
                round=int(r.get("round", len(out) + 1)),
                ok=bool(r.get("ok", True)),
                failures=[
                    f"{f.get('code')}: {f.get('analyst')}{'/' + f['channel'] if f.get('channel') else ''}"
                    f": {f.get('detail')}"
                    for f in r.get("failures") or []
                ],
                planner_task=r.get("planner_task"),
            )
        )
    return out


def call_cost(trace: RunTrace) -> float:
    """₽ вызовов прогона, считая ответы из кеша по исходной стоимости (сколько стоил бы разбор)."""
    return round(
        sum((c.original_cost_rub or 0.0) if c.cached else c.cost_rub for c in trace.llm_calls), 6
    )


def fill_trace_details(trace: RunTrace, ctx: PipelineContext) -> RunTrace:
    """Дописывает в трассу промежуточные результаты из контекста прогона (на месте и возвращает её)."""
    dossier = ctx.dossier or {}
    trace.plan = _plan(dossier)
    trace.gaps = [str(g) for g in dossier.get("gaps") or []]
    trace.tool_results = _tool_results(dossier)
    trace.analyst_reports = list(ctx.reports)
    trace.arbiter = _arbiter(ctx)
    trace.critic_rounds = _critic(ctx)
    source = (ctx.artifacts.get("card") or {}).get("source")
    trace.card_source = source if source in ("llm", "template", "fixture") else None
    trace.analysis_cached = bool(ctx.artifacts.get("analysis_cached"))
    saved = sum(c.original_cost_rub or 0.0 for c in trace.llm_calls if c.cached)
    if trace.analysis_cached:
        saved += float(ctx.artifacts.get("analysis_cost_rub") or 0.0)
    trace.saved_rub = round(saved, 6)
    return trace
