"""Планировщик глубокого разбора: шаблон по типу события, достройка моделью при пробелах, досье с id.

Разбор считается на пару «сюжет × отрасль» и кешируется, поэтому инструменты компании
(`get_profile`, `get_counterparties`) в план не входят — профиль подставляется позже кодом.
Модель вызывается не всегда: только если после шаблона в досье остались пробелы, и один раз.
Всё, что предложила модель, проверяется кодом: инструмент из реестра, аргументы по схеме,
`company_id` в аргументах недопустим, повторы и лишние шаги отбрасываются.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from newsai.agents.dossier import Dossier, DossierItem, PlanStep
from newsai.agents.tools import TOOLS, ToolContext, ToolError, canonical_hash, describe_tools, run_tool
from newsai.llm import BudgetExceeded, LLMClient, LLMUnavailable, get_client
from newsai.schemas import EventFacts, NumberFact, Story, ToolCall

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_PATH = Path(__file__).with_name("plan_templates.yaml")
TAXONOMY_PATH = ROOT / "config" / "taxonomy.yaml"
PROMPT_PATH = ROOT / "prompts" / "planner.md"

# Инструменты разбора отрасли. Профиль и контрагенты компании сюда не входят.
PLANNER_TOOLS: frozenset[str] = frozenset(
    {"search_news", "get_bank_positions", "get_market_knowledge", "find_similar_cases", "calculator"}
)
TEXT_LEN = 600

# коды пробелов; в досье пробел хранится как "<код>: пояснение"
GAP_BANK = "no_bank_position"
GAP_CONSENSUS = "no_market_consensus"
GAP_CASES = "no_similar_cases"
GAP_NUMBERS = "no_numbers"


# --- шаблоны -------------------------------------------------------------------------------------


class TemplateStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = ""
    each_channel: bool = False
    max_channels: int = Field(default=3, ge=1, le=8)
    requires_numbers: int = Field(default=0, ge=0, le=2)
    same_unit: bool = False


class TemplatesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    max_steps: int = Field(ge=1, le=8)
    templates: dict[str, list[TemplateStep]]


@lru_cache(maxsize=1)
def load_templates() -> TemplatesFile:
    data = yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8"))
    tf = TemplatesFile.model_validate(data)
    if "default" not in tf.templates:
        raise ValueError("в plan_templates.yaml нет шаблона default")
    for et, steps in tf.templates.items():
        for s in steps:
            if s.tool not in PLANNER_TOOLS or s.tool not in TOOLS:
                raise ValueError(f"шаблон {et}: инструмент {s.tool!r} недопустим в разборе отрасли")
            if "company_id" in s.args:
                raise ValueError(f"шаблон {et}: company_id в аргументах")
    return tf


def max_steps() -> int:
    return load_templates().max_steps


@lru_cache(maxsize=1)
def taxonomy_channels() -> dict[str, list[str]]:
    """Тип события -> каналы из config/taxonomy.yaml."""
    data = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8")) or {}
    return {et: list((v or {}).get("channels") or []) for et, v in (data.get("event_types") or {}).items()}


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _subst(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: variables.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _subst(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, variables) for v in value]
    return value


def _num(value: float) -> str:
    return f"({value!r})" if value < 0 else repr(float(value))


def expand_template(
    event_type: str | None,
    *,
    title: str,
    summary: str = "",
    sector: str,
    numbers: list[NumberFact] | None = None,
    fact_channels: list[str] | None = None,
) -> list[PlanStep]:
    """Раскрывает шаблон типа события в конкретные шаги (не больше max_steps)."""
    tf = load_templates()
    et = event_type if event_type in tf.templates else "default"
    numbers = numbers or []
    tax = taxonomy_channels().get(event_type or "", [])
    # сначала каналы, которые назвал экстрактор (если они из таксономии типа), потом остальные
    named = fact_channels or []
    channels = [c for c in named if c in tax] + [c for c in tax if c not in named]
    variables = {
        "title": title[:200],
        "summary": summary[:200],
        "event_type": event_type or "other",
        "sector": sector,
    }
    if len(numbers) >= 1:
        variables["num0"] = _num(numbers[0].value)
    if len(numbers) >= 2:
        variables["num1"] = _num(numbers[1].value)

    out: list[PlanStep] = []
    seen: set[str] = set()

    def push(step: TemplateStep, extra: dict[str, str]) -> None:
        args = _subst(step.args, {**variables, **extra})
        key = step.tool + canonical_hash(args)
        if key in seen:
            return
        seen.add(key)
        out.append(PlanStep(tool=step.tool, args=args, why=step.why, origin="template"))

    for step in tf.templates[et]:
        if len(numbers) < step.requires_numbers:
            continue
        units_differ = len(numbers) < 2 or numbers[0].unit is None or numbers[0].unit != numbers[1].unit
        if step.same_unit and units_differ:
            continue
        if step.each_channel:
            for ch in channels[: step.max_channels]:
                push(step, {"channel": ch})
        else:
            push(step, {})
    return out[: tf.max_steps]


# --- досье из результатов инструментов -----------------------------------------------------------


def _cut(text: str | None, n: int = TEXT_LEN) -> str:
    return (text or "")[:n]


def story_and_fact_items(story: Story, facts: EventFacts) -> list[DossierItem]:
    """Элементы story и fact. Числа — с цитатами; id фактов `fact:<story_id>:<n>`, n с 1.

    В data фактов лежат `source_id` и `quote` (первая цитата-основание): по ним карточка строит evidence.
    """
    ev0 = facts.summary_evidence[0] if facts.summary_evidence else None
    src = ev0.source_id if ev0 else None
    items = [
        DossierItem(
            id=story.id,
            kind="story",
            title=story.title,
            data={
                "event_type": facts.event_type,
                "status": story.status,
                "independent_sources": story.independent_sources,
                "source_ids": story.source_ids,
                "first_seen": story.first_seen.isoformat(),
                "last_seen": story.last_seen.isoformat(),
            },
            published_at=story.last_seen,
        ),
        DossierItem(
            id=f"fact:{story.id}:1",
            kind="fact",
            title="Суть события",
            text=_cut(facts.summary),
            data={
                "event_type": facts.event_type,
                "status": facts.status,
                "quotes": [_cut(e.quote, 300) for e in facts.summary_evidence],
                "source_id": src,
                "quote": _cut(ev0.quote, 300) if ev0 and ev0.quote else None,
                "channels": [c.channel for c in facts.channels],
                "sectors_okved": facts.sectors_okved,
                "is_rumor": facts.is_rumor,
                "is_intent": facts.is_intent,
            },
            published_at=story.last_seen,
        ),
    ]
    for n, num in enumerate(facts.numbers, start=2):
        unit = f" {num.unit}" if num.unit else ""
        items.append(
            DossierItem(
                id=f"fact:{story.id}:{n}",
                kind="fact",
                title=f"{num.period or 'число'}: {num.value:g}{unit}",
                text=_cut(num.quote, 300),
                data={
                    "value": num.value,
                    "unit": num.unit,
                    "direction": num.direction,
                    "period": num.period,
                    "verified": num.verified,
                    "source_id": src,
                    "quote": _cut(num.quote, 300),
                },
                published_at=story.last_seen,
            )
        )
    return items


def items_from_result(tool: str, out: BaseModel) -> list[DossierItem]:
    """Результат инструмента -> элементы досье с id объектов (item, cmi_…, mk_…, calc:…)."""
    d = out.model_dump(mode="json")
    items: list[DossierItem] = []
    if tool == "search_news":
        for h in d["hits"]:
            items.append(
                DossierItem(
                    id=h["item_id"], kind="news", title=h["title"], text=_cut(h["snippet"]),
                    data={"source_id": h["source_id"], "tier": h["tier"], "in_story": h["in_story"]},
                    source_tool=tool, published_at=h["published_at"],
                )
            )
    elif tool == "get_bank_positions":
        for p in d["positions"]:
            items.append(
                DossierItem(
                    id=p["id"], kind="bank_position", title=p["doc_title"], text=_cut(p["quote"]),
                    data={k: p[k] for k in (
                        "thesis", "channel", "stance", "forecast_value", "forecast_unit",
                        "forecast_horizon", "doc_id", "page", "doc_date", "valid_until", "expired",
                    )},
                    source_tool=tool,
                )
            )
    elif tool == "get_market_knowledge":
        for m in d["knowledge"]:
            items.append(
                DossierItem(
                    id=m["id"], kind="market_knowledge", title=f"{m['kind']}: {m['channel'] or 'без канала'}",
                    text=_cut(m["statement"]),
                    data={k: m[k] for k in (
                        "kind", "channel", "event_type", "direction", "expected_value", "expected_unit",
                        "origin", "channel_weight", "one_sided",
                    )},
                    source_tool=tool, published_at=m["published_at"],
                )
            )
    elif tool == "find_similar_cases":
        for c in d["cases"]:
            items.append(
                DossierItem(
                    id=c["story_id"], kind="case", title=c["title"], text=_cut(c["summary"]),
                    data={"event_type": c["event_type"], "sectors_okved": c["sectors_okved"]},
                    source_tool=tool, published_at=c["first_seen"],
                )
            )
    elif tool == "calculator":
        digest = canonical_hash(d["expression"])[:8]
        items.append(
            DossierItem(
                id=f"calc:{digest}", kind="calc", title=d["expression"],
                text=f"{d['expression']} = {d['value']:g}",
                data={"expression": d["expression"], "value": d["value"]}, source_tool=tool,
            )
        )
    return items


# --- пробелы -------------------------------------------------------------------------------------


def compute_gaps(dossier: Dossier) -> list[str]:
    """Чего не хватает досье: позиция банка по каналу типа события, консенсус рынка, случаи, числа."""
    tax = taxonomy_channels().get(dossier.event_type or "", [])
    gaps: list[str] = []
    bank = [
        i for i in dossier.by_kind("bank_position")
        if not i.data.get("expired") and (not tax or i.data.get("channel") in tax)
    ]
    if not bank:
        where = f" по каналам типа события ({', '.join(tax)})" if tax else " по теме сюжета"
        gaps.append(f"{GAP_BANK}: нет актуальной позиции ЦМИ{where}")
    if not any(i.data.get("kind") == "expectation" for i in dossier.by_kind("market_knowledge")):
        gaps.append(f"{GAP_CONSENSUS}: нет консенсуса рынка до события")
    if not dossier.by_kind("case"):
        gaps.append(f"{GAP_CASES}: нет похожих прошлых случаев")
    if not any("value" in i.data for i in dossier.by_kind("fact")):
        gaps.append(f"{GAP_NUMBERS}: в фактах сюжета нет чисел")
    return gaps


def gap_code(gap: str) -> str:
    return gap.split(":", 1)[0].strip()


# --- достройка моделью ---------------------------------------------------------------------------


class PlannedStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    why: str = ""


class PlanExtension(BaseModel):
    """Ответ модели-планировщика: дополнительные шаги. Лишнее код отбрасывает."""

    model_config = ConfigDict(extra="ignore")

    steps: list[PlannedStep] = Field(default_factory=list)


@lru_cache(maxsize=1)
def load_prompt() -> tuple[str, str]:
    """(версия, текст) из prompts/planner.md; версия — в первой строке `<!-- prompt_version: … -->`."""
    raw = PROMPT_PATH.read_text(encoding="utf-8")
    m = re.match(r"\s*<!--\s*prompt_version:\s*([\w.\-]+)\s*-->\s*", raw)
    if not m:
        raise ValueError("в prompts/planner.md нет заголовка prompt_version")
    return m.group(1), raw[m.end():].strip()


def _forbidden_args(args: dict[str, Any]) -> bool:
    return any("company" in str(k).lower() for k in args)


def sanitize_steps(
    proposed: list[PlannedStep], existing: list[PlanStep], free: int, origin: str
) -> list[PlanStep]:
    """Оставляет только допустимые шаги: инструмент из реестра, аргументы по схеме, без повторов."""
    seen = {s.tool + canonical_hash(s.args) for s in existing}
    out: list[PlanStep] = []
    for p in proposed:
        if len(out) >= free:
            break
        spec = TOOLS.get(p.tool)
        if p.tool not in PLANNER_TOOLS or spec is None:
            log.info("планировщик: инструмент %r недопустим, шаг отброшен", p.tool)
            continue
        if _forbidden_args(p.args):
            log.info("планировщик: company_id в аргументах %s, шаг отброшен", p.tool)
            continue
        try:
            spec.input_model.model_validate(p.args)
        except ValidationError:
            log.info("планировщик: аргументы %s не по схеме, шаг отброшен", p.tool)
            continue
        key = p.tool + canonical_hash(p.args)
        if key in seen:
            continue
        seen.add(key)
        out.append(PlanStep(tool=p.tool, args=p.args, why=p.why[:200], origin=origin))  # type: ignore[arg-type]
    return out


def _tools_for_prompt() -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in describe_tools()
        if t["name"] in PLANNER_TOOLS
    ]


def _llm_steps(
    dossier: Dossier,
    *,
    client: LLMClient | None,
    priority: float | None,
    origin: str,
    critic_task: str | None = None,
) -> list[PlanStep]:
    free = max_steps() - len(dossier.plan)
    if free <= 0:
        return []
    version, system = load_prompt()
    payload: dict[str, Any] = {
        "event_type": dossier.event_type,
        "sector": dossier.sector,
        "gaps": dossier.gaps,
        "executed_plan": [{"tool": s.tool, "args": s.args} for s in dossier.plan],
        "free_slots": free,
        "tools": _tools_for_prompt(),
    }
    if critic_task:
        payload["critic_task"] = critic_task
    user = json.dumps(payload, ensure_ascii=False) + "\n\n" + dossier.render_for_prompt()
    try:
        res = (client or get_client()).call(
            step="planner",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema=PlanExtension,
            trace_id=dossier.trace_id,
            priority=priority,
            prompt_version=version,
            fixture_key="replan" if critic_task else None,
        )
    except (LLMUnavailable, BudgetExceeded) as e:
        log.warning("планировщик: достройка моделью недоступна (%s), остаёмся с шаблоном", e)
        return []
    return sanitize_steps(res.value.steps, dossier.plan, free, origin)


# --- выполнение ----------------------------------------------------------------------------------


def _execute(dossier: Dossier, steps: list[PlanStep], ctx: ToolContext) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for step in steps:
        dossier.plan.append(step)
        try:
            out, call = run_tool(ctx, step.tool, step.args)
        except ToolError as e:
            log.info("планировщик: шаг %s не выполнен: %s", step.tool, e)
            if e.call is not None:
                calls.append(e.call)
            continue
        calls.append(call)
        for item in items_from_result(step.tool, out):
            dossier.add(item)
    return calls


def build_dossier(
    story: Story,
    event_facts: EventFacts,
    sector: str,
    ctx: ToolContext,
    *,
    client: LLMClient | None = None,
    trace_id: str,
    priority: float | None = None,
) -> tuple[Dossier, list[ToolCall]]:
    """Досье на пару «сюжет × отрасль»: шаблон -> инструменты -> пробелы -> (один вызов модели)."""
    if ctx.story_id != story.id or event_facts.story_id != story.id:
        raise ValueError("сюжет, факты и контекст инструментов относятся к разным сюжетам")
    dossier = Dossier(
        story_id=story.id,
        story_version=story.version,
        sector=sector,
        event_type=event_facts.event_type,
        story_published_at=ctx.story_published_at,
        trace_id=trace_id,
    )
    for item in story_and_fact_items(story, event_facts):
        dossier.add(item)

    steps = expand_template(
        event_facts.event_type,
        title=story.title,
        summary=event_facts.summary,
        sector=sector,
        numbers=event_facts.numbers,
        fact_channels=[c.channel for c in event_facts.channels],
    )
    calls = _execute(dossier, steps, ctx)

    dossier.gaps = compute_gaps(dossier)
    if dossier.gaps:
        extra = _llm_steps(dossier, client=client, priority=priority, origin="llm")
        calls += _execute(dossier, extra, ctx)
        dossier.gaps = compute_gaps(dossier)
    return dossier, calls


def replan(
    dossier: Dossier,
    critic_task: str,
    ctx: ToolContext,
    client: LLMClient | None = None,
    *,
    priority: float | None = None,
) -> list[ToolCall]:
    """Достройка по задаче критика: шаги с origin="critic", всего не больше max_steps. Меняет досье."""
    steps = _llm_steps(dossier, client=client, priority=priority, origin="critic", critic_task=critic_task)
    calls = _execute(dossier, steps, ctx)
    dossier.gaps = compute_gaps(dossier)
    return calls

