"""LLM-критик (DeepSeek): проверяет, следуют ли сомнительные утверждения аналитиков из их цитат.

Необязателен и дешёв: сомнительные утверждения отбирает код (не больше `MAX_CLAIMS`), нет сомнительных —
модель не вызывается. Любая ошибка модели или бюджета — пропуск, конвейер идёт дальше.
Отключение: `CRITIC_LLM_ENABLED=0`.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.analysts import AnalystResult
from backend.agents.dossier import Dossier
from backend.llm import BudgetExceeded, LLMClient, LLMUnavailable, data_block, get_client

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "critic_llm.md"
MAX_CLAIMS = 6
STEP = "critic_llm"
LLM_UNSUPPORTED = "llm_unsupported"

# причинная связка: утверждение «из-за X будет Y» без опоры на число
_CAUSAL = re.compile(
    r"поэтому|из-за|вследствие|в результате|приведёт|приведет|следовательно|что (?:снизит|повысит|усилит|"
    r"ослабит|увеличит|уменьшит)|за счёт|за счет|обусловлен|благодаря|повлечёт|повлечет",
    re.IGNORECASE,
)
_DIGIT = re.compile(r"\d")
_STRONG = {"medium", "high"}


def enabled() -> bool:
    return os.environ.get("CRITIC_LLM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    verdict: Literal["supported", "weak", "unsupported"]
    reason: str = Field(max_length=200)


class CriticLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: list[ClaimVerdict] = Field(default_factory=list)


class Claim(BaseModel):
    claim_id: str
    analyst: str
    channel: str
    direction: str
    rationale: str
    refs: list[str]
    why: str  # почему сомнительно: single_ref | disputed | causal_no_number


class CriticLLMResult(BaseModel):
    status: Literal["ok", "skipped", "no_doubtful", "error"]
    reason: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    dropped_ids: list[str] = Field(default_factory=list)

    def with_verdict(self, v: str) -> list[ClaimVerdict]:
        return [x for x in self.verdicts if x.verdict == v]


# --- отбор -----------------------------------------------------------------------------------------


def select_doubtful(results: Mapping[str, AnalystResult], max_claims: int = MAX_CLAIMS) -> list[Claim]:
    """Драйверы с силой >= medium и признаком: одна ссылка, спор по направлению, причина без числа."""
    by_channel: dict[str, set[str]] = {}
    for res in results.values():
        for cd in res.drivers:
            if cd.driver.direction != "flat":
                by_channel.setdefault(cd.driver.channel, set()).add(cd.driver.direction)
    out: list[Claim] = []
    for name in sorted(results):
        for cd in results[name].drivers:
            d = cd.driver
            if d.strength not in _STRONG:
                continue
            if len(set(d.refs)) == 1:
                why = "single_ref"
            elif len(by_channel.get(d.channel, ())) > 1:
                why = "disputed"
            elif _CAUSAL.search(d.rationale) and not _DIGIT.search(d.rationale):
                why = "causal_no_number"
            else:
                continue
            out.append(
                Claim(
                    claim_id=f"{name}:{d.channel}", analyst=name, channel=d.channel,
                    direction=d.direction, rationale=d.rationale, refs=list(d.refs), why=why,
                )
            )
    # споры — вперёд: при обрезке до лимита они важнее
    out.sort(key=lambda c: (c.why != "disputed", c.claim_id))
    return out[:max_claims]


# --- промпт ----------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_prompt() -> tuple[str, str, str]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    m = re.search(r"prompt_version:\s*(\S+)", text.splitlines()[0])
    system = re.search(r"^## SYSTEM\n(.*?)(?=^## USER\n)", text, re.S | re.M)
    user = re.search(r"^## USER\n(.*)\Z", text, re.S | re.M)
    if not (m and system and user):
        raise ValueError("в prompts/critic_llm.md нужны prompt_version и разделы ## SYSTEM и ## USER")
    return f"critic_llm-v{m.group(1)}", system.group(1).strip(), user.group(1).strip()


def build_messages(claims: list[Claim], dossier: Dossier) -> list[dict[str, str]]:
    _, system, user = load_prompt()
    claims_txt = "\n".join(
        f"- id={c.claim_id}; канал={c.channel}; направление={c.direction}; "
        f"refs={','.join(c.refs)}; обоснование: {c.rationale}"
        for c in claims
    )
    ids = list(dict.fromkeys(r for c in claims for r in c.refs))
    quotes = []
    for rid in ids:
        item = dossier.get(rid)
        quotes.append(f"[{rid}] {item.title}\n{item.text}" if item else f"[{rid}] (нет в досье)")
    values = {"claims": claims_txt, "quotes": data_block("\n\n".join(quotes))}
    user = re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), user)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --- вызов -----------------------------------------------------------------------------------------


def run_critic_llm(
    dossier: Dossier,
    results: Mapping[str, AnalystResult],
    *,
    trace_id: str,
    priority: float | None = None,
    client: LLMClient | None = None,
    fixture_key: str | None = None,
) -> CriticLLMResult:
    """Один вызов модели по сомнительным утверждениям. Не бросает исключений модели и бюджета."""
    if not enabled():
        return CriticLLMResult(status="skipped", reason="disabled")
    claims = select_doubtful(results)
    if not claims:
        return CriticLLMResult(status="no_doubtful")
    try:
        client = client or get_client()
        res = client.call(
            step=STEP, messages=build_messages(claims, dossier), schema=CriticLLMOutput,
            trace_id=trace_id, priority=priority, prompt_version=load_prompt()[0], fixture_key=fixture_key,
        )
    except (LLMUnavailable, BudgetExceeded) as e:
        log.warning("critic_llm: пропуск trace_id=%s: %s", trace_id, type(e).__name__)
        return CriticLLMResult(status="error", reason=type(e).__name__, claims=claims)
    except Exception as e:  # критик необязателен: что бы ни случилось, конвейер идёт дальше
        log.warning("critic_llm: ошибка trace_id=%s: %s", trace_id, type(e).__name__)
        return CriticLLMResult(status="error", reason=type(e).__name__, claims=claims)
    known = {c.claim_id for c in claims}
    kept: dict[str, ClaimVerdict] = {}
    dropped: list[str] = []
    for v in res.value.verdicts:
        if v.claim_id in known and v.claim_id not in kept:
            kept[v.claim_id] = v
        else:
            dropped.append(v.claim_id)
    return CriticLLMResult(status="ok", claims=claims, verdicts=list(kept.values()), dropped_ids=dropped)
