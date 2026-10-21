"""Реестр инструментов, описание для планировщика и запуск с трассой."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from newsai.agents.tools import impl
from newsai.agents.tools.context import ToolContext, ToolError
from newsai.agents.tools.models import (
    BankPositionsOut,
    CalculatorIn,
    CalculatorOut,
    CounterpartiesOut,
    FindSimilarCasesIn,
    GetBankPositionsIn,
    GetCounterpartiesIn,
    GetMarketKnowledgeIn,
    GetProfileIn,
    MarketKnowledgeListOut,
    ProfileOut,
    SearchNewsIn,
    SearchNewsOut,
    SimilarCasesOut,
)
from newsai.schemas import ToolCall


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], BaseModel]
    description: str


def _spec(
    name: str, i: type[BaseModel], o: type[BaseModel], fn: Callable[..., BaseModel], d: str
) -> ToolSpec:
    return ToolSpec(name, i, o, fn, d)


TOOLS: dict[str, ToolSpec] = {
    s.name: s
    for s in [
        _spec(
            "get_profile", GetProfileIn, ProfileOut, impl.get_profile,
            "Признаки компании сессии: ОКВЭД, сектор, регион, продукты, ВЭД и страна, чувствительность "
            "к каналам, положение по выручке в отрасли (доля, не сумма), полнота данных.",
        ),
        _spec(
            "get_counterparties", GetCounterpartiesIn, CounterpartiesOut, impl.get_counterparties,
            "Топ контрагентов компании: доли, ОКВЭД/сектор, концентрация (HHI), высокая зависимость. "
            "Без сумм платежей.",
        ),
        _spec(
            "search_news", SearchNewsIn, SearchNewsOut, impl.search_news,
            "Поиск материалов по теме, опубликованных не позже сюжета: id, источник и уровень, заголовок, "
            "дата, короткий фрагмент.",
        ),
        _spec(
            "get_bank_positions", GetBankPositionsIn, BankPositionsOut, impl.get_bank_positions,
            "Позиции ЦМИ Альфа-Банка (тезис, прогноз, страница, цитата, срок актуальности) "
            "из документов не позже даты сюжета.",
        ),
        _spec(
            "get_market_knowledge", GetMarketKnowledgeIn, MarketKnowledgeListOut, impl.get_market_knowledge,
            "Знания аналитиков рынка (механизмы, ожидания, разборы), опубликованные до сюжета; "
            "у каждого указано происхождение (origin).",
        ),
        _spec(
            "find_similar_cases", FindSimilarCasesIn, SimilarCasesOut, impl.find_similar_cases,
            "Прошлые сюжеты того же типа события (раньше даты сюжета), по желанию в отрасли.",
        ),
        _spec(
            "calculator", CalculatorIn, CalculatorOut, impl.calculator,
            "Арифметика: числа, + - * / ** %, скобки, round/abs/min/max. Имён и атрибутов нет.",
        ),
    ]
}


def describe_tools() -> list[dict[str, Any]]:
    """JSON-схемы входа/выхода для промпта планировщика."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_model.model_json_schema(),
            "output_schema": t.output_model.model_json_schema(),
        }
        for t in TOOLS.values()
    ]


def canonical_hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _call(tool: str, args: dict[str, Any] | Any, ok: bool, result_hash: str | None = None) -> ToolCall:
    # в трассу — только хеш аргументов, не сами аргументы
    return ToolCall(
        tool=tool,
        args={"sha256": canonical_hash(args)},
        result_hash=result_hash,
        ok=ok,
        at=datetime.now(UTC),
    )


def _fail(tool: str, args: Any, code: str, message: str) -> ToolError:
    return ToolError(code, message, call=_call(tool, args, ok=False))


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None) -> tuple[BaseModel, ToolCall]:
    """Валидирует вход, выполняет, возвращает (выход, ToolCall). Ошибка — ToolError с `.call`."""
    args = args if isinstance(args, dict) else {}
    spec = TOOLS.get(name)
    if spec is None:
        raise _fail(name, args, "unknown_tool", "неизвестный инструмент")
    if "company_id" in args:
        if args["company_id"] != ctx.company_id:
            raise _fail(name, args, "forbidden_company", "доступ к другой компании запрещён")
        raise _fail(name, args, "bad_args", "company_id берётся из сессии и не принимается в аргументах")
    try:
        inp = spec.input_model.model_validate(args)
    except ValidationError:
        raise _fail(name, args, "bad_args", "аргументы не прошли проверку") from None
    try:
        out = spec.fn(ctx, inp)
        out = spec.output_model.model_validate(out.model_dump())
    except ToolError as e:
        raise _fail(name, args, e.code, e.message) from None
    except ValidationError:
        raise _fail(name, args, "bad_result", "результат не прошёл проверку") from None
    return out, _call(name, args, ok=True, result_hash=canonical_hash(out.model_dump(mode="json")))
