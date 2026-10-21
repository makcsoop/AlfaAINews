"""Проверка моделей на ключе: по одному минимальному вызову на модель, через LLMClient.

Запуск: `LLM_MODE=live uv run python -m newsai.llm.probe [--models all|id,...] [--dry-run]`.
Прямых HTTP здесь нет (инвариант 1): вызов идёт шагом `probe` с model_override и без запасной модели.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from newsai.llm.budget import BudgetExceeded, BudgetManager, RedisBudgetStore
from newsai.llm.client import LLMClient, LLMUnavailable
from newsai.llm.settings import LLMSettings

STEP = "probe"
MAX_TOKENS = 400  # модели с рассуждениями тратят 60+ токенов до ответа
MIN_REMAINING_RUB = 1.0
MESSAGES = [
    {"role": "system", "content": "Отвечай только JSON."},
    {"role": "user", "content": 'Верни {"ok": true}'},
]


class ProbeAnswer(BaseModel):
    ok: bool


def _select(client: LLMClient, models: str) -> list[str]:
    ids = list(client.config.models)
    if models.strip().lower() == "all":
        return ids
    wanted = [m.strip() for m in models.split(",") if m.strip()]
    unknown = [m for m in wanted if m not in client.config.models]
    if unknown:
        raise SystemExit(f"неизвестные модели: {', '.join(unknown)}; доступны: {', '.join(ids)}")
    return wanted


def _budget_for(settings: LLMSettings) -> BudgetManager:
    """Бюджет из Redis (общий с остальными процессами), если он доступен; иначе локальный."""
    try:
        import redis

        r = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        r.ping()
        return BudgetManager(settings=settings, store=RedisBudgetStore(settings.redis_url, client=r))
    except Exception:
        return BudgetManager(settings=settings)


def probe_one(client: LLMClient, model_id: str) -> dict:
    trace = f"probe-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    error: str | None = None
    try:
        client.call(
            step=STEP, messages=MESSAGES, schema=ProbeAnswer, trace_id=trace, dev="r3",
            max_tokens=MAX_TOKENS, temperature=0.0, model_override=model_id, allow_fallback=False,
        )
    except (LLMUnavailable, BudgetExceeded) as e:
        error = str(e)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    entries = [e for e in client.journal.entries() if e.trace_id == trace]
    last = entries[-1] if entries else None
    return {
        "model_id": model_id,
        "model": client.config.model(model_id).provider_name,
        "base": last.base if last else None,
        "latency_ms": latency_ms,
        "attempts": len(entries),
        "tokens_in": sum(e.tokens_in for e in entries),
        "tokens_out": sum(e.tokens_out for e in entries),
        "provider_cost": sum(e.provider_cost for e in entries if e.provider_cost is not None)
        if any(e.provider_cost is not None for e in entries) else None,
        "provider_cost_raw": next((e.provider_cost_raw for e in reversed(entries)
                                   if e.provider_cost_raw is not None), None),
        "cost_rub": round(sum(e.cost_rub for e in entries), 6),
        "error": error or (last.error if last and last.validation != "ok" else None),
    }


def _fmt_cost(r: dict) -> str:
    if r["provider_cost"] is not None:
        return f"{r['provider_cost']:.5f} (пров.)"
    if r["provider_cost_raw"] is not None:
        return f"raw={r['provider_cost_raw']} ~{r['cost_rub']:.5f}"
    return f"~{r['cost_rub']:.5f}"


def print_table(rows: list[dict], total: float, out=None) -> None:
    out = out or sys.stdout
    head = f"{'модель':<34}{'адрес':<10}{'мс':>7}{'in':>7}{'out':>6}  {'₽':<22}ошибка"
    print(head, file=out)
    for r in rows:
        print(
            f"{r['model']:<34}{r['base'] or '-':<10}{r['latency_ms']:>7}{r['tokens_in']:>7}"
            f"{r['tokens_out']:>6}  {_fmt_cost(r):<22}{r['error'] or ''}",
            file=out,
        )
    print(f"итого: {total:.5f} ₽", file=out)


def run(
    *, models: str = "all", dry_run: bool = False, client: LLMClient | None = None,
    settings: LLMSettings | None = None, out_dir: str | Path | None = None,
) -> int:
    settings = settings or (client.settings if client else LLMSettings())
    if settings.llm_mode == "fixture" and not dry_run:
        print(
            "probe делает настоящие вызовы и в LLM_MODE=fixture не работает: "
            "положите LLM_API_KEY в .env и запустите с LLM_MODE=live",
            file=sys.stderr,
        )
        return 2
    if client is None:
        client = LLMClient(settings=settings, budget=_budget_for(settings))
    ids = _select(client, models)

    if dry_run:
        print(f"план: {len(ids)} вызовов шага '{STEP}', max_tokens={MAX_TOKENS}, без запасных моделей")
        for i in ids:
            print(f"  {i} -> {client.config.model(i).provider_name}")
        print(f"остаток бюджета: {client.budget.remaining_rub():.2f} ₽ (вызовов нет)")
        return 0

    remaining = client.budget.remaining_rub()
    if remaining < MIN_REMAINING_RUB:
        print(f"остаток бюджета {remaining:.2f} ₽ — probe не запускаю", file=sys.stderr)
        return 3
    print(f"остаток бюджета до probe: {remaining:.2f} ₽")

    rows = [probe_one(client, i) for i in ids]
    total = round(sum(r["cost_rub"] for r in rows), 6)
    print_table(rows, total)

    now = datetime.now(UTC)
    directory = Path(out_dir) if out_dir else Path(settings.llm_journal_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"probe-{now.date().isoformat()}.json"
    report = {"at": now.isoformat(), "total_rub": total, "results": rows}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"отчёт: {path}")
    return 0 if all(r["error"] is None for r in rows) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m newsai.llm.probe", description=__doc__)
    ap.add_argument("--models", default="all", help="all или id через запятую (id из config/models.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="показать план, ничего не вызывая")
    args = ap.parse_args(argv)
    return run(models=args.models, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
