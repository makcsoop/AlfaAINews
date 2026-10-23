"""Адаптер модулей Р1 «Банк и рынок» и «Статистик» к досье планировщика (только чтение, TOOL_SOURCE=db).

Отчёты Р1 превращаются в `DossierItem` с теми же id, что у инструментов: позиции ЦМИ `cmi_…`,
консенсус рынка `mk_consensus_…`, похожие случаи — по id сюжета. Уже имеющиеся в досье элементы не
заменяются. Любая ошибка (нет БД, нет индекса, нет таблиц) — молчаливый пропуск с флагом для трассы.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from backend.agents.dossier import Dossier, DossierItem
from backend.agents.planner import compute_gaps

log = logging.getLogger(__name__)

TEXT_LEN = 600


def _cut(text: str | None, n: int = TEXT_LEN) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)
    except ValueError:
        return None


def bank_market_items(report: Any, dossier: Dossier) -> list[DossierItem]:
    """Позиции ЦМИ до события и консенсус рынка из отчёта «Банк и рынок»."""
    have = dossier.ids()
    items: list[DossierItem] = []
    for v in report.bank:
        if v.position_id in have or not v.before_event:  # опубликованные не раньше сюжета не берём
            continue
        items.append(
            DossierItem(
                id=v.position_id, kind="bank_position", title=v.doc_title, text=_cut(v.quote),
                data={
                    "thesis": v.thesis, "channel": v.channel, "direction": v.direction,
                    "forecast": v.forecast, "page": v.page, "doc_date": v.doc_date.isoformat(),
                    "expired": v.stale, "superseded_by": None,
                },
                source_tool="bank_market",
            )
        )
    m = report.market
    if m is not None:
        mid = f"mk_consensus_{m.meeting.isoformat()}"
        if mid not in have:
            items.append(
                DossierItem(
                    id=mid, kind="market_knowledge", title=f"expectation: key_rate, заседание {m.meeting}",
                    text=_cut(m.statement),
                    data={
                        "kind": "expectation", "channel": "key_rate", "direction": None,
                        "expected_value": m.expected_value, "expected_unit": "pct",
                        "origin": "market_consensus", "one_sided": False,
                    },
                    source_tool="bank_market",
                )
            )
    return items


def statistician_items(report: Any, dossier: Dossier) -> list[DossierItem]:
    """Похожие прошлые случаи с заметками аналитиков, опубликованными раньше сюжета."""
    have = dossier.ids()
    items: list[DossierItem] = []
    for c in report.similar:
        if c.story_id in have or c.story_id == dossier.story_id:
            continue
        first_seen = _dt(c.first_seen)
        if first_seen is not None and first_seen >= dossier.story_published_at:
            continue
        notes = " ".join(n.excerpt for n in c.analyst_notes[:2])
        items.append(
            DossierItem(
                id=c.story_id, kind="case", title=c.title, text=_cut(notes or c.title),
                data={"event_type": c.event_type, "similarity": c.similarity, "sectors_okved": []},
                source_tool="statistician", published_at=first_seen,
            )
        )
    return items


def enrich_dossier(dossier: Dossier, engine: Engine) -> list[str]:
    """Дополняет досье отчётами Р1; возвращает флаги для трассы (`r1:bank_market:ok|skipped:…`)."""
    flags: list[str] = []
    for name in ("bank_market", "statistician"):
        try:
            with engine.connect() as conn:
                if name == "bank_market":
                    from backend.agents import bank_market

                    new = bank_market_items(bank_market.report(conn, dossier.story_id), dossier)
                else:
                    from backend.agents import statistician

                    rep = statistician.report(conn, dossier.story_id, dossier.sector)
                    new = statistician_items(rep, dossier)
        except Exception as e:  # модуль Р1 или БД недоступны: разбор идёт без них
            log.info("r1 %s пропущен: %r", name, e)
            flags.append(f"r1:{name}:skipped:{type(e).__name__}")
            continue
        for item in new:
            dossier.add(item)
        flags.append(f"r1:{name}:ok:{len(new)}")
    dossier.gaps = compute_gaps(dossier)
    return flags
