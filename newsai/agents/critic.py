"""Критик кодом: проверяет разборы аналитиков по досье и формулирует задачу планировщику.

Ничего не оценивает моделью. Проверки: ссылки существуют, числа и даты взяты из цитат ссылаемых
элементов, нет оценочных слов, позиция банка подтверждена ссылкой на ЦМИ, каналы типа события покрыты.
Провалы, которые лечатся новыми данными, дают `planner_task`; остальные идут в `notes`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from newsai.agents.analysts import EXCLUDED_KINDS, TAXONOMY_PATH, AnalystResult, find_evaluative
from newsai.agents.dossier import Dossier, DossierItem
from newsai.schemas.common import BANK_SOURCE_PREFIX

MAX_TASKS = 5

FailureCode = str
MISSING_REF = "missing_ref"
NUMBER_UNVERIFIED = "number_unverified"
EVALUATIVE = "evaluative"
BANK_VIEW_NO_REF = "bank_view_no_cmi_ref"
NO_CHANNEL_COVERAGE = "no_channel_coverage"
ALL_ANALYSTS_FAILED = "all_analysts_failed"

# data-ключи, чьи значения — идентификаторы, а не числа из цитаты
_ID_KEYS = ("id", "ids")

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}  # fmt: skip
_DATE_NUM = re.compile(r"(?<![\d.,])(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?![\d])")
_DATE_TXT = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?", re.IGNORECASE)
_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?")

# «Банк ожидает», «по мнению банка», «ЦМИ считает»: позицию банка нельзя приписывать без ссылки на ЦМИ
_BANK_VIEW = re.compile(
    r"банк\w*\s+(?:ожида|счита|прогноз|ждёт|ждет|оцени|полага)"
    r"|по\s+(?:мнению|оценк\w+|прогноз\w+)\s+(?:банка|цми|альфа)"
    r"|цми\s+(?:счита|ожида|ждёт|ждет|прогноз|оцени|полага)"
    r"|аналитик\w*\s+банка"
    r"|позици\w+\s+(?:банка|цми)",
    re.IGNORECASE,
)
_MISSING_NOTE = re.compile(r"driver\[\d+\]\s+(\S+):\s+нет в досье\s+\[(.*)\]")


class Failure(BaseModel):
    code: str
    analyst: str
    channel: str | None = None
    detail: str


class CriticVerdict(BaseModel):
    ok: bool
    failures: list[Failure] = Field(default_factory=list)
    planner_task: str | None = None
    notes: list[str] = Field(default_factory=list)
    planner_tasks: list[str] = Field(default_factory=list)


# --- числа и даты ---------------------------------------------------------------------------------


def _dates_and_numbers(text: str) -> tuple[set[tuple[int, int, int | None]], list[tuple[str, float, int]]]:
    """Даты (день, месяц, год|None) и числа (токен, значение, знаков после запятой) из текста."""
    dates: set[tuple[int, int, int | None]] = set()

    def num_date(m: re.Match[str]) -> str:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            dates.add((d, mo, y + 2000 if y < 100 else y))
        return " "

    def txt_date(m: re.Match[str]) -> str:
        dates.add((int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3)) if m.group(3) else None))
        return " "

    rest = _DATE_NUM.sub(num_date, text)
    rest = _DATE_TXT.sub(txt_date, rest)
    nums: list[tuple[str, float, int]] = []
    for m in _NUM.finditer(rest):
        tok = m.group(0)
        norm = tok.replace(",", ".")
        nums.append((tok, float(norm), len(norm.split(".")[1]) if "." in norm else 0))
    return dates, nums


def _walk_data(value: Any, out: list[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        out.append(repr(float(value)) if isinstance(value, float) else str(value))
    elif isinstance(value, str):
        out.append(value)
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if str(k).lower().endswith(_ID_KEYS):
                continue
            _walk_data(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk_data(v, out)


def _item_text(item: DossierItem) -> str:
    parts = [item.title, item.text]
    _walk_data(item.data, parts)
    if item.published_at is not None:
        parts.append(item.published_at.strftime("%d.%m.%Y"))
    return "\n".join(parts)


class _Source:
    """Числа и даты, которые встречаются в наборе элементов досье."""

    def __init__(self, items: list[DossierItem]) -> None:
        self.values: list[float] = []
        self.dates: set[tuple[int, int, int | None]] = set()
        for it in items:
            d, n = _dates_and_numbers(_item_text(it))
            self.dates |= d
            self.values += [v for _, v, _ in n]

    def has_number(self, value: float, decimals: int = 0) -> bool:
        """Точное совпадение после нормализации: «14,0» = «14» = «14%», но 14 ≠ 14,2."""
        return any(abs(v - value) < 1e-9 for v in self.values)

    def has_date(self, d: tuple[int, int, int | None]) -> bool:
        return any(s[:2] == d[:2] and (s[2] is None or d[2] is None or s[2] == d[2]) for s in self.dates)


def unsupported_numbers(text: str, items: list[DossierItem]) -> list[str]:
    """Числа и даты из текста, которых нет в цитатах/данных переданных элементов досье."""
    src = _Source(items)
    dates, nums = _dates_and_numbers(text)
    bad = [
        f"{d:02d}.{m:02d}" + (f".{y}" if y else "")
        for d, m, y in sorted(dates, key=str)
        if not src.has_date((d, m, y))
    ]
    for tok, value, decimals in nums:
        if src.has_number(value, decimals):
            continue
        parts = tok.replace(",", ".").split(".")  # «11.09» без года: возможно, дата
        is_date = len(parts) == 2 and 1 <= int(parts[0]) <= 31 and 1 <= int(parts[1]) <= 12
        if is_date and src.has_date((int(parts[0]), int(parts[1]), None)):
            continue
        bad.append(tok)
    return bad


# --- таксономия -----------------------------------------------------------------------------------


@lru_cache
def event_channels(path: str | None = None) -> dict[str, list[str]]:
    raw = yaml.safe_load(Path(path or TAXONOMY_PATH).read_text(encoding="utf-8"))
    return {et: list(v.get("channels") or []) for et, v in raw.get("event_types", {}).items()}


# --- разбор драйверов -----------------------------------------------------------------------------


class _View(BaseModel):
    channel: str
    rationale: str
    refs: list[str]


def _views(res: AnalystResult) -> list[_View]:
    if res.drivers:
        return [
            _View(channel=d.driver.channel, rationale=d.driver.rationale, refs=list(d.driver.refs))
            for d in res.drivers
        ]
    if res.report is not None:
        return [
            _View(channel=c.channel, rationale=c.rationale, refs=[e.source_id for e in c.evidence])
            for c in res.report.channels
        ]
    return []


def _task_unique(tasks: list[str], task: str) -> None:
    if task not in tasks:
        tasks.append(task)


def critique(
    dossier: Dossier, analyst_results: Mapping[str, AnalystResult] | list[AnalystResult]
) -> CriticVerdict:
    """Проверяет разборы по досье. `analyst_results` — {аналитик: результат} или список результатов."""
    results: dict[str, AnalystResult] = (
        dict(analyst_results)
        if isinstance(analyst_results, Mapping)
        else {r.analyst: r for r in analyst_results}
    )
    failures: list[Failure] = []
    notes: list[str] = []
    tasks: list[str] = []
    visible = {i.id: i for i in dossier.items if i.kind not in EXCLUDED_KINDS}

    def fail(
        code: str, analyst: str, detail: str, channel: str | None = None, task: str | None = None
    ) -> None:
        f = Failure(code=code, analyst=analyst, channel=channel, detail=detail)
        if f not in failures:
            failures.append(f)
        if task:
            _task_unique(tasks, task)
        else:
            _task_unique(notes, f"{analyst}{'/' + channel if channel else ''}: {detail}")

    if not any(r.report is not None for r in results.values()):
        fail(ALL_ANALYSTS_FAILED, "all", "оба аналитика не дали разбора")
        return CriticVerdict(ok=False, failures=failures, planner_task=None, notes=notes)

    covered: set[str] = set()
    for analyst, res in results.items():
        if res.report is None:
            notes.append(f"{analyst}: аналитик недоступен ({res.failure})")
            continue
        views = _views(res)
        covered |= {v.channel for v in views}
        has_cmi = any(r.startswith(BANK_SOURCE_PREFIX) for v in views for r in v.refs)

        for note in res.notes:  # драйверы, отброшенные ещё при разборе ответа
            m = _MISSING_NOTE.search(note)
            if m:
                covered.add(m.group(1))
                fail(
                    MISSING_REF,
                    analyst,
                    f"ссылки нет в досье: {m.group(2)}",
                    m.group(1),
                    f"найти материалы по каналу {m.group(1)}: ссылка {analyst} {m.group(2)} не в досье",
                )

        for v in views:
            missing = [r for r in v.refs if r not in visible]
            for r in missing:
                fail(
                    MISSING_REF,
                    analyst,
                    f"ссылки {r} нет в досье",
                    v.channel,
                    f"найти материалы по каналу {v.channel}: ссылка {analyst} {r} не найдена в досье",
                )
            known = [visible[r] for r in v.refs if r in visible]
            for tok in unsupported_numbers(v.rationale, known):
                fail(
                    NUMBER_UNVERIFIED,
                    analyst,
                    f"число или дата {tok} нет в цитатах ссылок",
                    v.channel,
                    f"найти материал с числом {tok} по каналу {v.channel}",
                )
            for w in find_evaluative(v.rationale):
                fail(EVALUATIVE, analyst, f"оценочное слово «{w}»", v.channel)
            if _BANK_VIEW.search(v.rationale) and not any(r.startswith(BANK_SOURCE_PREFIX) for r in v.refs):
                fail(
                    BANK_VIEW_NO_REF,
                    analyst,
                    "позиция банка в тексте без ссылки на материал ЦМИ",
                    v.channel,
                    f"найти позицию ЦМИ по каналу {v.channel}",
                )

        # Сводка пересказывает досье целиком (в т.ч. прогнозы ЦМИ, на которые драйверы не ссылаются),
        # поэтому её числа сверяются со всем видимым досье; числа драйверов — строго по их refs (выше).
        # На живом прогоне 21.10.2026 сверка сводки только по refs драйверов давала ложные круги критика.
        summary = res.report.summary
        for tok in unsupported_numbers(summary, list(visible.values())):
            fail(
                NUMBER_UNVERIFIED,
                analyst,
                f"число или дата {tok} из сводки нет в досье",
                None,
                f"найти материал с числом {tok}",
            )
        for w in {*find_evaluative(summary), *(f.split(":", 1)[1] for f in res.summary_flags)}:
            fail(EVALUATIVE, analyst, f"оценочное слово «{w}» в сводке")
        if _BANK_VIEW.search(summary) and not has_cmi:
            fail(
                BANK_VIEW_NO_REF,
                analyst,
                "позиция банка в сводке без ссылки на материал ЦМИ",
                None,
                "найти позицию ЦМИ по теме сюжета",
            )

    for ch in event_channels().get(dossier.event_type or "", []):
        if ch not in covered:
            # Новый круг планировщика нужен, только если в досье нет материала по каналу:
            # если материал есть, а аналитики канал не оценили, новые данные это не исправят.
            has_material = any((i.data or {}).get("channel") == ch for i in dossier.items)
            fail(
                NO_CHANNEL_COVERAGE,
                "all",
                f"нет оценки канала {ch}",
                ch,
                None if has_material else f"найти позицию ЦМИ или материал по каналу {ch}",
            )

    notes = list(dict.fromkeys(notes))
    task = "; ".join(tasks[:MAX_TASKS]) or None
    return CriticVerdict(
        ok=not failures, failures=failures, planner_task=task, notes=notes, planner_tasks=tasks[:MAX_TASKS]
    )


__all__ = ["CriticVerdict", "Failure", "critique", "event_channels", "unsupported_numbers"]
