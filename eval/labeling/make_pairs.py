"""Пары «сюжет × клиент» для разметки и Excel-файлы разметчиков (ТЗ «разметка 60 пар»).

`uv run python eval/labeling/make_pairs.py [--labelers razmetka] [--batch oct-60] [--out data/labeling]`

Клиенты — 6 реальных компаний из CSV кейса: 5 архетипов `config/archetypes.yaml` и розница. Их профили v1
пишутся в company_profiles — по ним метрики считают значимость. Каждому клиенту 10 сюжетов за последние
`--hours` часов, отбор пулом (как в TREC): 4 лучших по системе, 3 лучших по бейзлайну «свежесть × качество»
из оставшихся и 3 случайных. Пул из двух лент нужен, чтобы в разметку попал топ-5 и системы, и бейзлайна:
иначе precision@5 одной из них считается на неразмеченных парах. Порядок сюжетов внутри клиента перемешан,
оценка системы в файл не попадает.

В пару пишется снимок сюжета и клиента (его же показывает /labeling). По умолчанию один разметчик и один файл
на все 60 пар; при нескольких (`--labelers a,b,c`) 10 пар общие у всех — для согласия, остальные поровну.
"""

import argparse
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, delete, func, select
from sqlalchemy.dialects.postgresql import insert

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.db import models as m
from backend.db.engine import get_engine
from backend.profile.archetypes import Archetype, Match, load_archetypes, resolve
from backend.profile.exposures import with_exposures
from backend.schemas import CompanyProfile, CounterpartyGraph, EventFacts, Story
from eval.labeling.sheet import PairRow, load_channels, write_sheet

ROOT = Path(__file__).resolve().parents[2]
MSK = ZoneInfo("Europe/Moscow")
PER_CLIENT = (4, 3, 3)  # лучших по системе, по бейзлайну, случайных
N_COMMON = 10

PRODUCTS = {
    "loans": "кредиты",
    "leasing": "лизинг",
    "deposits": "депозиты",
    "guarantees": "гарантии",
    "factoring": "факторинг",
    "payroll": "зарплатный проект",
    "rko": "РКО",
}
ROLES = {
    "cfo": "финдиректор",
    "treasurer": "казначей",
    "chief_accountant": "главный бухгалтер",
    "ceo": "гендиректор",
    "owner": "собственник",
}
# ОКВЭД 2 — разделы, которые встречаются среди контрагентов; остальные подписываются кодом
SECTOR_NAMES = {
    "outside": "вне базы",
    "01": "сельское хозяйство",
    "05": "добыча угля",
    "06": "добыча нефти и газа",
    "07": "добыча руд",
    "08": "добыча прочих ископаемых",
    "09": "услуги в добыче",
    "10": "пищевая промышленность",
    "19": "нефтепереработка",
    "20": "химия",
    "22": "резина и пластмассы",
    "23": "стройматериалы",
    "24": "металлургия",
    "25": "металлоизделия",
    "26": "электроника",
    "27": "электрооборудование",
    "28": "машиностроение",
    "29": "автопром",
    "33": "ремонт и монтаж оборудования",
    "35": "энергетика",
    "41": "строительство зданий",
    "42": "инфраструктурное строительство",
    "43": "специализированные строительные работы",
    "45": "торговля автомобилями",
    "46": "оптовая торговля",
    "47": "розничная торговля",
    "49": "сухопутный транспорт",
    "50": "водный транспорт",
    "51": "авиаперевозки",
    "52": "складское хозяйство и транспортные услуги",
    "53": "почта и курьеры",
    "61": "связь",
    "62": "разработка ПО",
    "63": "IT-услуги",
    "64": "финансовые услуги",
    "65": "страхование",
    "66": "вспомогательные финансовые услуги",
    "68": "операции с недвижимостью",
    "69": "право и бухгалтерия",
    "70": "консалтинг",
    "71": "инжиниринг",
    "77": "аренда и лизинг",
    "82": "административные услуги",
    "84": "госуправление",
}
RETAIL = Archetype(
    id="retail",
    title="Розничная сеть",
    default_role="ceo",
    demo="потребительский спрос, ставки и курс — цена импортных закупок",
    match=Match(okved_prefixes=["47"], prefer={"loans": True}),
)


# --- чистая логика (тесты) ---


def _top_sectors(shares: dict | None, n: int = 3, min_share: float = 0.0) -> list[str]:
    """Крупнейшие отрасли контрагентов. Сортируем по доле: JSONB хранит ключи не в порядке вставки."""
    top = sorted(((k, v) for k, v in (shares or {}).items() if v >= min_share), key=lambda kv: -kv[1])[:n]
    return [f"{SECTOR_NAMES.get(k, 'ОКВЭД ' + k)}: {v:.0%}" for k, v in top]


def pool(
    system: Mapping[str, float],
    baseline: Mapping[str, float],
    rng: random.Random,
    sizes: tuple[int, int, int] = PER_CLIENT,
) -> list[tuple[str, str]]:
    """(story_id, откуда взят): топ системы, затем топ бейзлайна без повторов, затем случайные."""
    n_sys, n_base, n_rand = sizes
    out: list[tuple[str, str]] = []
    taken: set[str] = set()
    for scores, n, why in ((system, n_sys, "system"), (baseline, n_base, "baseline")):
        for sid in sorted(scores, key=lambda s: (-scores[s], s)):
            if len([1 for _, w in out if w == why]) >= n:
                break
            if sid not in taken:
                out.append((sid, why))
                taken.add(sid)
    rest = sorted(set(system) - taken)
    out += [(sid, "random") for sid in rng.sample(rest, min(n_rand, len(rest)))]
    return out


def assign(
    groups: Mapping[str, Sequence[str]], labelers: Sequence[str], rng: random.Random, n_common: int = N_COMMON
) -> tuple[dict[str, list[str]], set[str]]:
    """Пары по разметчикам: n_common общих (поровну от каждого клиента), остальные — по кругу внутри клиента.
    Возвращает (разметчик → пары в порядке групп, общие пары)."""
    all_pairs = [p for g in groups.values() for p in g]
    if len(labelers) == 1:
        return {labelers[0]: list(all_pairs)}, set()
    shuffled = {cid: rng.sample(list(g), len(g)) for cid, g in groups.items()}
    common: set[str] = set()
    k = 0
    while len(common) < min(n_common, len(all_pairs)):
        for sh in shuffled.values():
            if k < len(sh) and len(common) < n_common:
                common.add(sh[k])
        k += 1
    out: dict[str, list[str]] = {name: [] for name in labelers}
    turn = 0
    for g in groups.values():
        for pid in g:
            if pid in common:
                for name in labelers:
                    out[name].append(pid)
            else:
                out[labelers[turn % len(labelers)]].append(pid)
                turn += 1
    return out, common


def _bn(x: float | None) -> str:
    return f"{(x or 0) / 1e9:.1f}".replace(".", ",")


def profile_text(p: CompanyProfile, graph: Mapping | None) -> str:
    g = graph or {}
    rank = (
        f" ({p.revenue_rank_in_okved}-я из {p.revenue_peers_in_okved} в отрасли)"
        if p.revenue_rank_in_okved and p.revenue_peers_in_okved
        else ""
    )
    products = [ru for key, ru in PRODUCTS.items() if p.products.get(key)]
    lines = [
        f"ОКВЭД {p.okved_code} {p.okved_name or ''}".strip(),
        f"Выручка {_bn(p.revenue)} млрд ₽{rank}",
        " · ".join(x for x in (p.region, p.workers_fns and f"сотрудников: {p.workers_fns}") if x),
        f"Продукты в банке: {', '.join(products) or 'нет данных'}",
        f"ВЭД: {'да' if p.fts_flag else 'нет'}"
        + (f" ({p.fts_country})" if p.fts_flag and p.fts_country else ""),
    ]
    for side, key, top in (
        ("Поставщики", "sector_shares_suppliers", "top_supplier_share"),
        ("Покупатели", "sector_shares_buyers", "top_buyer_share"),
    ):
        sectors = _top_sectors(g.get(key), min_share=0.01)  # «0%» разметчику ничего не говорит
        if sectors:
            share = g.get(top)
            tail = f"; на крупнейшего — {share:.0%}" if share else ""
            lines.append(f"{side}: {', '.join(sectors)}{tail}")
    if g.get("high_dependency"):
        lines.append("Высокая зависимость от одного контрагента")
    return "\n".join(x for x in lines if x)


def story_text(snap: Mapping) -> str:
    summary = (snap.get("summary") or "").strip()
    return snap["title"] if not summary or summary == snap["title"] else f"{snap['title']}\n\n{summary}"


def sources_text(items: Sequence[Mapping], names: Mapping[str, str], n: int = 3) -> str:
    seen, lines = set(), []
    for it in items:
        if not it.get("url") or it["source_id"] in seen:
            continue
        seen.add(it["source_id"])
        lines.append(f"{names.get(it['source_id'], it['source_id'])}: {it['url']}")
        if len(lines) >= n:
            break
    return "\n".join(lines)


# --- база ---


def pick_clients(directory: Path) -> list[tuple[Archetype, CompanyProfile]]:
    from backend.profile.load import load_profiles

    cfg = load_archetypes()
    cfg = cfg.model_copy(update={"archetypes": [*cfg.archetypes, RETAIL]})
    picked = resolve(load_profiles(directory), cfg)
    return [(a, p) for a in cfg.archetypes if (p := picked[a.id]) is not None]


def load_pool(conn: Connection, hours: int) -> tuple[list[Story], dict[str, EventFacts], datetime]:
    last = conn.execute(select(func.max(m.stories.c.last_seen))).scalar_one()
    rows = conn.execute(
        select(m.stories.c.data, m.event_facts.c.data)
        .join(m.event_facts, m.event_facts.c.story_id == m.stories.c.id)
        .where(
            m.stories.c.last_seen >= last - timedelta(hours=hours),
            m.stories.c.status != "unconfirmed",
            m.event_facts.c.event_type != "other",
        )
    ).all()
    stories = [Story.model_validate(s) for s, _ in rows]
    facts = {f["story_id"]: EventFacts.model_validate(f) for _, f in rows}
    # время оценки — как в backend.eval.metrics: сразу после последнего сюжета
    return stories, facts, last + timedelta(hours=1)


def story_snapshot(conn: Connection, story: Story, facts: EventFacts | None) -> dict:
    items = conn.execute(
        select(m.raw_items.c.source_id, m.raw_items.c.title, m.raw_items.c.url)
        .where(m.raw_items.c.id.in_(story.item_ids))
        .order_by(m.raw_items.c.published_at)
        .limit(8)
    ).mappings()
    return {
        "story_id": story.id,
        "title": story.title,
        "status": story.status,
        "corroboration": story.corroboration,
        "first_seen": story.first_seen.isoformat(),
        "event_type": facts.event_type if facts else story.event_type,
        "summary": facts.summary if facts else None,
        "numbers": [f"{n.value:g} {n.unit or ''}".strip() for n in (facts.numbers if facts else [])[:6]],
        "channels": [],  # разметка вслепую: каналы системы не показываем
        "items": [dict(i) for i in items],
    }


def client_snapshot(a: Archetype, p: CompanyProfile, graph: Mapping | None) -> dict:
    g = graph or {}
    return {
        "company_id": p.company_id,
        "archetype": a.title,
        "role": a.default_role,
        "okved": f"{p.okved_code} {p.okved_name or ''}".strip(),
        "revenue_bn": round((p.revenue or 0) / 1e9, 1),
        "region": p.region,
        "products": [ru for key, ru in PRODUCTS.items() if p.products.get(key)],
        "fts_countries": [p.fts_country] if p.fts_country else [],
        "suppliers_by_sector": _top_sectors(g.get("sector_shares_suppliers")),
        "buyers_by_sector": _top_sectors(g.get("sector_shares_buyers")),
        "high_dependency": g.get("high_dependency"),
        "profile_text": profile_text(p, g),
    }


def make_pairs(
    conn: Connection,
    batch: str,
    labelers: Sequence[str],
    out_dir: Path,
    *,
    hours: int,
    seed: int,
    case_dir: Path,
) -> dict[str, Path]:
    from backend.ingest.registry import load_sources
    from backend.profile.publish import save_profiles
    from backend.ranking.config import load_scoring
    from backend.ranking.score import freshness, quality, score_pair

    if conn.execute(
        select(func.count()).select_from(m.labels.join(m.label_pairs)).where(m.label_pairs.c.batch == batch)
    ).scalar():
        raise SystemExit(f"в партии {batch} уже есть метки — новую партию назовите иначе (--batch)")
    rng = random.Random(seed)
    cfg = load_scoring()
    sources = {s.id: s for s in load_sources()}
    names = {s.id: s.name for s in sources.values()}
    clients = pick_clients(case_dir)
    graphs_raw = {
        cid: data
        for cid, data in conn.execute(
            select(m.counterparty_graphs.c.company_id, m.counterparty_graphs.c.data).where(
                m.counterparty_graphs.c.company_id.in_([p.company_id for _, p in clients])
            )
        ).all()
    }
    graphs = {cid: CounterpartyGraph.model_validate(d) for cid, d in graphs_raw.items()}
    profiles = [with_exposures(p, graphs.get(p.company_id)) for _, p in clients]
    save_profiles(conn, profiles)  # метрики берут профиль клиента из company_profiles
    stories, facts, now = load_pool(conn, hours)

    rows: list[dict] = []
    by_client: dict[str, list[str]] = {}
    rows_meta: dict[str, PairRow] = {}
    n = 0
    for (a, _), p in zip(clients, profiles, strict=True):
        g = graphs.get(p.company_id)
        system = {
            s.id: score_pair(s, p, now=now, facts=facts.get(s.id), graph=g, sources=sources, cfg=cfg).score
            for s in stories
        }
        base = {s.id: quality(s, facts.get(s.id), sources, cfg) * freshness(s, now, cfg) for s in stories}
        chosen = pool(system, base, rng)
        rng.shuffle(chosen)
        client = client_snapshot(a, p, graphs_raw.get(p.company_id))
        story_by_id = {s.id: s for s in stories}
        by_client[p.company_id] = []
        for sid, why in chosen:
            n += 1
            pid = f"{batch}-{n:02d}"
            snap = story_snapshot(conn, story_by_id[sid], facts.get(sid))
            rows.append(
                {
                    "pair_id": pid,
                    "batch": batch,
                    "story_id": sid,
                    "company_id": p.company_id,
                    "data": {"story": snap, "client": client, "selection": why},
                }
            )
            by_client[p.company_id].append(pid)
            rows_meta[pid] = PairRow(
                pair_id=pid,
                story_id=sid,
                company_id=p.company_id,
                company=f"{a.title} ({p.company_id})",
                role=ROLES.get(a.default_role, a.default_role),
                profile=client["profile_text"],
                date=story_by_id[sid].first_seen.astimezone(MSK).strftime("%d.%m.%Y %H:%M"),
                story=story_text(snap),
                sources=sources_text(snap["items"], names),
            )
    per_labeler, common = assign(by_client, labelers, rng)
    for r in rows:
        r["data"]["common"] = r["pair_id"] in common
    conn.execute(delete(m.label_pairs).where(m.label_pairs.c.batch == batch))
    conn.execute(insert(m.label_pairs).values(rows))

    channels = load_channels()
    files = {}
    for name, pids in per_labeler.items():
        path = out_dir / ("razmetka.xlsx" if len(per_labeler) == 1 else f"razmetka_{name}.xlsx")
        write_sheet(path, name, batch, [rows_meta[pid] for pid in pids], channels)
        files[name] = path
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="oct-60")
    ap.add_argument(
        "--labelers", default="razmetka", help="имена через запятую, латиницей: из них имена файлов"
    )
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "labeling")
    ap.add_argument("--hours", type=int, default=72, help="окно сюжетов до последнего в базе")
    ap.add_argument("--seed", type=int, default=24)
    ap.add_argument("--case", type=Path, default=ROOT / "data" / "case")
    args = ap.parse_args()
    labelers = [x.strip() for x in args.labelers.split(",") if x.strip()]
    with get_engine().begin() as conn:
        m.metadata.create_all(conn, tables=[m.label_pairs, m.labels])
        files = make_pairs(
            conn, args.batch, labelers, args.out, hours=args.hours, seed=args.seed, case_dir=args.case
        )
    for name, path in files.items():
        print(f"{name}: {path}")
    print(f"партия {args.batch}, создано {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
