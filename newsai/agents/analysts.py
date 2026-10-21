"""Два независимых аналитика (бизнес, финансы): направление фактора, сила, горизонт по каналам.

Оба получают одно и то же досье пары «сюжет × отрасль» и один список каналов, не видят ответов друг друга.
Аналитик называет, куда сдвигается ФАКТОР (ставка ↑, спрос ↓), а не «хорошо/плохо»:
знак для клиента ставит скоринг.
Код после ответа проверяет канал, ссылки на досье и отсутствие оценочных слов; провалы уходят критику.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field

from newsai.agents.dossier import Dossier, DossierKind
from newsai.llm import BudgetExceeded, LLMClient, LLMUnavailable
from newsai.schemas import AnalystReport, ChannelImpact, Evidence

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = ROOT / "prompts"
TAXONOMY_PATH = ROOT / "config" / "taxonomy.yaml"

AnalystName = Literal["business", "finance"]
ANALYSTS: tuple[AnalystName, ...] = ("business", "finance")
STEP: dict[str, str] = {"business": "analyst_business", "finance": "analyst_finance"}

# Элементы досье, которых нет в разборе отрасли: признаки и контрагенты конкретной компании
EXCLUDED_KINDS: frozenset[str] = frozenset({"profile", "counterparties"})

STRENGTH_WEIGHT = {"low": 0.3, "medium": 0.6, "high": 0.9}
DIRECTION_SIGN = {"up": 1.0, "down": -1.0, "flat": 0.0}
HORIZON_MAP = {"days": "days", "quarter": "quarters", "year": "years"}

# Оценочные слова: аналитик не оценивает последствия для отрасли или клиента (знак ставит скоринг)
EVALUATIVE_PATTERNS: tuple[str, ...] = (
    r"хорошо",
    r"плохо",
    r"позитив",
    r"негатив",
    r"выгодн",  # выгодно, невыгодно
    r"риск\w* для",
    r"возможност\w* для",
    r"благоприятн",  # и «неблагоприятн»
)
_EVALUATIVE = re.compile("|".join(EVALUATIVE_PATTERNS), re.IGNORECASE)


_DIRECTION_WORD = {"up": "фактор растёт", "down": "фактор снижается", "flat": "без заметного сдвига"}
_STRENGTH_WORD = {"low": "слабая", "medium": "средняя", "high": "высокая"}
_HORIZON_WORD = {"days": "дни", "quarter": "кварталы", "year": "годы"}


def find_evaluative(text: str) -> list[str]:
    """Оценочные фразы, найденные в тексте (нижний регистр)."""
    return [m.group(0).lower() for m in _EVALUATIVE.finditer(text)]


# --- схема ответа модели ---


class Driver(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(description="id канала из config/taxonomy.yaml")
    direction: Literal["up", "down", "flat"]
    strength: Literal["low", "medium", "high"]
    horizon: Literal["days", "quarter", "year"]
    refs: list[str] = Field(min_length=1, description="id элементов досье")
    rationale: str = Field(max_length=300)


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drivers: list[Driver] = Field(default_factory=list)
    summary: str
    open_questions: list[str] = Field(default_factory=list)


# --- результат проверки ---


class CheckedDriver(BaseModel):
    driver: Driver
    flags: list[str] = Field(default_factory=list, description="нарушения: evaluative:<слово>")

    @property
    def clean(self) -> bool:
        return not self.flags


class AnalystResult(BaseModel):
    """Результат одного аналитика: отчёт, провалы проверок и причина отсутствия отчёта."""

    analyst: AnalystName
    report: AnalystReport | None = None
    model: str | None = None
    prompt_version: str | None = None
    drivers_total: int = 0
    drivers: list[CheckedDriver] = Field(default_factory=list, description="прошедшие каналы и ссылки")
    notes: list[str] = Field(default_factory=list, description="отброшенные драйверы и причины")
    summary_flags: list[str] = Field(default_factory=list)
    failure: str | None = None
    cost_rub: float = 0.0

    @property
    def check_failures(self) -> int:
        """Сколько проверок провалено: оценочные слова в драйверах и в summary (для критика)."""
        return sum(1 for d in self.drivers if d.flags) + (1 if self.summary_flags else 0)


# --- таксономия, промпты, сообщения ---


@lru_cache
def load_channels(path: str | None = None) -> dict[str, str]:
    """Все каналы влияния {id: название} из config/taxonomy.yaml."""
    raw = yaml.safe_load(Path(path or TAXONOMY_PATH).read_text(encoding="utf-8"))
    return {cid: str(v.get("name", cid)) for cid, v in raw["channels"].items()}


_VERSION = re.compile(r"<!--\s*version:\s*(\S+)\s*-->")


def load_prompt(step: str) -> tuple[str, str]:
    """Текст промпта шага и его версия из заголовка."""
    text = (PROMPTS_DIR / f"{step}.md").read_text(encoding="utf-8")
    m = _VERSION.search(text)
    return text, (m.group(1) if m else f"{step}@0")


def build_messages(
    analyst: AnalystName, dossier: Dossier, channels: Mapping[str, str]
) -> list[dict[str, str]]:
    """Сообщения одного аналитика. Ответ другого сюда не попадает: вход — только досье и каналы."""
    system, _ = load_prompt(STEP[analyst])
    kinds = set(get_args(DossierKind)) - EXCLUDED_KINDS
    channel_lines = "\n".join(f"- {cid}: {name}" for cid, name in channels.items())
    user = (
        f"Сюжет: {dossier.story_id} (версия {dossier.story_version}), отрасль ОКВЭД: {dossier.sector}"
        f"{', тип события: ' + dossier.event_type if dossier.event_type else ''}.\n"
        f"Дата сюжета: {dossier.story_published_at.isoformat()}.\n\n"
        f"Каналы влияния (используй только эти id):\n{channel_lines}\n\n"
        f"{dossier.render_for_prompt(kinds=kinds)}"
    )
    if dossier.gaps:
        user += "\n\nВ досье не хватает: " + "; ".join(dossier.gaps)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --- проверка кодом и конвертация ---


def validate_output(
    out: AnalystOutput, dossier: Dossier, channels: Mapping[str, str], *, visible_ids: set[str] | None = None
) -> tuple[list[CheckedDriver], list[str], list[str]]:
    """Возвращает (прошедшие драйверы с флагами, notes об отброшенных, флаги summary).

    Отбрасываются: неизвестный канал, любая несуществующая ссылка, повтор канала (остаётся сильнейший).
    Оценочные слова драйвер не отбрасывают, а помечают: это провал проверки для критика.
    """
    ids = visible_ids if visible_ids is not None else dossier.ids()
    notes: list[str] = []
    kept: dict[str, CheckedDriver] = {}
    for i, d in enumerate(out.drivers):
        if d.channel not in channels:
            notes.append(f"driver[{i}] {d.channel}: канала нет в таксономии, отброшен")
            continue
        missing = [r for r in d.refs if r not in ids]
        if missing:
            notes.append(f"driver[{i}] {d.channel}: нет в досье {missing}, отброшен")
            continue
        flags = [f"evaluative:{w}" for w in find_evaluative(d.rationale)]
        cd = CheckedDriver(driver=d, flags=flags)
        prev = kept.get(d.channel)
        if prev is None:
            kept[d.channel] = cd
        elif STRENGTH_WEIGHT[d.strength] > STRENGTH_WEIGHT[prev.driver.strength]:
            notes.append(f"driver[{i}] {d.channel}: повтор канала, заменил более слабый")
            kept[d.channel] = cd
        else:
            notes.append(f"driver[{i}] {d.channel}: повтор канала, отброшен")
    summary_flags = [f"evaluative:{w}" for w in find_evaluative(out.summary)]
    return list(kept.values()), notes, summary_flags


def neutral_summary(drivers: list[Driver]) -> str:
    """Сводка без оценок: по каждому каналу направление фактора, сила и горизонт."""
    if not drivers:
        return "Каналов влияния без оценочных формулировок не выделено."
    try:
        names = load_channels()
    except (OSError, KeyError, ValueError):
        names = {}
    parts = [
        f"{d.channel} ({names.get(d.channel, d.channel)}): {_DIRECTION_WORD[d.direction]}, "
        f"сила {_STRENGTH_WORD[d.strength]}, горизонт {_HORIZON_WORD[d.horizon]}"
        for d in drivers
    ]
    return "Каналы влияния: " + "; ".join(parts) + "."


def to_report(
    out: AnalystOutput,
    drivers: list[CheckedDriver],
    dossier: Dossier,
    analyst: AnalystName,
    model: str,
) -> AnalystReport:
    """Конвертер в AnalystReport. impact — направление ФАКТОРА × вес силы, не оценка для клиента.

    В отчёт идут только драйверы без оценочных слов: оценочный текст до карточки не доходит.
    """
    channels: list[ChannelImpact] = []
    for cd in drivers:
        if not cd.clean:
            continue
        d = cd.driver
        evidence = []
        for ref in d.refs:
            item = dossier.get(ref)
            evidence.append(Evidence(source_id=ref, quote=(item.text or None) if item else None))
        channels.append(
            ChannelImpact(
                channel=d.channel,
                impact=DIRECTION_SIGN[d.direction] * STRENGTH_WEIGHT[d.strength],
                horizon=HORIZON_MAP[d.horizon],  # type: ignore[arg-type]
                rationale=d.rationale,
                evidence=evidence,
            )
        )
    summary = out.summary
    if find_evaluative(summary):  # оценочную сводку заменяет нейтральная, собранная кодом из драйверов
        summary = neutral_summary([cd.driver for cd in drivers if cd.clean])
    return AnalystReport(
        story_id=dossier.story_id,
        sector=dossier.sector,
        analyst=analyst,
        model=model,
        channels=channels,
        summary=summary,
        open_questions=[q for q in out.open_questions if not find_evaluative(q)],
    )


# --- запуск ---


def _run_one(
    analyst: AnalystName,
    dossier: Dossier,
    channels: Mapping[str, str],
    *,
    client: LLMClient,
    trace_id: str,
    priority: float | None,
    fixture_key: str | None,
) -> AnalystResult:
    step = STEP[analyst]
    _, version = load_prompt(step)
    res = AnalystResult(analyst=analyst, prompt_version=version)
    visible = {i.id for i in dossier.items if i.kind not in EXCLUDED_KINDS}
    try:
        call = client.call(
            step=step,
            messages=build_messages(analyst, dossier, channels),
            schema=AnalystOutput,
            trace_id=trace_id,
            priority=priority,
            prompt_version=version,
            fixture_key=fixture_key,
        )
    except (LLMUnavailable, BudgetExceeded) as e:
        res.failure = f"{type(e).__name__}: {e}"
        return res
    out = call.value
    res.model = call.model
    res.cost_rub = call.cost_rub
    res.drivers_total = len(out.drivers)
    res.drivers, res.notes, res.summary_flags = validate_output(out, dossier, channels, visible_ids=visible)
    res.report = to_report(out, res.drivers, dossier, analyst, call.model)
    return res


def run_analysts_detailed(
    dossier: Dossier,
    *,
    client: LLMClient,
    trace_id: str,
    priority: float | None = None,
    fixture_keys: Mapping[str, str] | None = None,
    channels: Mapping[str, str] | None = None,
) -> dict[str, AnalystResult]:
    """Параллельный запуск двух аналитиков; сбой одного не роняет другого (failure в его результате)."""
    chans = channels if channels is not None else load_channels()
    keys = fixture_keys or {}
    with ThreadPoolExecutor(max_workers=len(ANALYSTS)) as pool:
        futures = {
            a: pool.submit(
                _run_one,
                a,
                dossier,
                chans,
                client=client,
                trace_id=trace_id,
                priority=priority,
                fixture_key=keys.get(a),
            )
            for a in ANALYSTS
        }
        return {a: f.result() for a, f in futures.items()}


def run_analysts(
    dossier: Dossier,
    *,
    client: LLMClient,
    trace_id: str,
    priority: float | None = None,
    fixture_keys: Mapping[str, str] | None = None,
) -> dict[str, AnalystReport | None]:
    """{analyst: AnalystReport | None}; None — аналитик недоступен (причина в run_analysts_detailed)."""
    detailed = run_analysts_detailed(
        dossier, client=client, trace_id=trace_id, priority=priority, fixture_keys=fixture_keys
    )
    return {a: r.report for a, r in detailed.items()}


# --- согласие аналитиков (для арбитра) ---


class ChannelAgreement(BaseModel):
    channel: str
    business: float
    finance: float
    same_direction: bool
    strength_diff: float = Field(description="|сила_бизнес − сила_финансы|, 0…1")


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def agreement(reports: Mapping[str, AnalystReport | None]) -> list[ChannelAgreement]:
    """По каждому общему каналу: совпало ли направление фактора и насколько различается сила."""
    b, f = reports.get("business"), reports.get("finance")
    if b is None or f is None:
        return []
    bi = {c.channel: c.impact for c in b.channels}
    fi = {c.channel: c.impact for c in f.channels}
    return [
        ChannelAgreement(
            channel=ch,
            business=bi[ch],
            finance=fi[ch],
            same_direction=_sign(bi[ch]) == _sign(fi[ch]),
            strength_diff=round(abs(abs(bi[ch]) - abs(fi[ch])), 3),
        )
        for ch in bi
        if ch in fi
    ]
