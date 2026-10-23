"""Прогрев демо-кеша (день 5): глубокие карточки топ-сюжетов для демо-клиентов до показа.

`uv run python scripts/warm_demo_cache.py [--stories 30] [--max-rub 40] [--client U_DEMO_BORROWER:cfo ...]`

Каждый запрос `/feed` делает не больше FEED_DEEP_PER_REQUEST глубоких разборов и кладёт карточку в кеш
(CACHE_BACKEND=redis — кеш переживает перезапуск backend). Скрипт входит под каждым клиентом и повторяет
`/feed`, пока в ленте не наберётся `--stories` глубоких карточек, лента не перестанет расти или расход
по журналу разборов (`/feed/runs`) не дойдёт до `--max-rub`. Разбор отрасли переиспользуется между
клиентами одной отрасли. Ничего не пишет сам — только вызывает API, как браузер.
"""

import argparse
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows — cp1251

DEFAULT_CLIENTS = ["U_DEMO_BORROWER:cfo", "U_DEMO_DEPOSITOR:treasurer"]


def warm(base: str, company: str, role: str, stories: int, max_rub: float, timeout: float) -> dict:
    with httpx.Client(base_url=base, timeout=timeout) as c:
        r = c.post("/session", json={"company_id": company, "role": role})
        r.raise_for_status()
        token = r.json().get("token")
        if token:
            c.headers["Authorization"] = f"Bearer {token}"
        deep, stale, spent, t0 = 0, 0, 0.0, time.monotonic()
        cards: list[dict] = []
        while deep < stories and spent < max_rub and stale < 3:
            cards = c.get("/feed").raise_for_status().json()
            now_deep = sum(1 for k in cards if k.get("stage") == "deep")
            resp = c.get("/feed/runs")
            runs = resp.json() if resp.status_code == 200 else []
            spent = sum(float(x.get("cost_rub") or 0) for x in runs)
            stale = stale + 1 if now_deep == deep else 0
            deep = now_deep
            print(f"  {company}/{role}: карточек {len(cards)}, глубоких {deep}, ₽ {spent:.2f}", flush=True)
        return {
            "company": company,
            "role": role,
            "cards": len(cards),
            "deep": deep,
            "rub": round(spent, 2),
            "seconds": round(time.monotonic() - t0),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--stories", type=int, default=30)
    ap.add_argument("--max-rub", type=float, default=40.0, help="потолок на клиента по журналу разборов")
    ap.add_argument("--client", action="append", help="COMPANY:ROLE, можно несколько")
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()
    for spec in args.client or DEFAULT_CLIENTS:
        company, role = spec.split(":")
        print(warm(args.base, company, role, args.stories, args.max_rub, args.timeout), flush=True)


if __name__ == "__main__":
    main()
