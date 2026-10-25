// Страница метрик: отбор (nDCG@5, precision@5 против бейзлайна — из reports/metrics.md) и живые показатели
// по ленте компании: точность чисел, доля утверждений со ссылкой, ₽ на карточку, время до глубокой карточки.
import { useEffect, useState } from "react";
import type { Api } from "../api";
import { errorText } from "../api";
import { selectionMetrics } from "../metricsReport";
import type { DeepRun, InsightCard, RunTrace, TraceCost } from "../types";

const TRACES_MAX = 10; // журналов для времени разбора: хватит на среднее, не грузим API

interface Live {
  cards: InsightCard[];
  runs: DeepRun[];
  traces: RunTrace[];
  cost: TraceCost | null;
}

const pct = (part: number, total: number) => (total ? `${Math.round((part / total) * 100)}%` : "—");
const rub = (v: number) => `${v.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
const fmt = (v: number) => v.toFixed(2).replace(".", ",");

export function liveStats({ cards, runs, traces }: Omit<Live, "cost">) {
  const deep = cards.filter((c) => c.stage === "deep");
  const claims = deep.flatMap((c) => c.claims);
  const withNumbers = claims.filter((c) => c.verified && "numbers" in c.verified);
  const numbersOk = withNumbers.filter((c) => c.verified.numbers === true || c.verified.numbers === "yes");
  const linked = claims.filter((c) => c.evidence.length > 0 || Object.values(c.based_on ?? {}).some((v) => v.length));
  const checked = claims.filter((c) => {
    const v = Object.values(c.verified ?? {});
    return v.length > 0 && v.every((x) => x === true || x === "yes");
  });
  const done = runs.filter((r) => r.status === "published" || r.status === "on_review");
  const spent = runs.reduce((s, r) => s + r.cost_rub, 0);
  const secs = traces
    .filter((t) => t.finished_at)
    .map((t) => (Date.parse(t.finished_at!) - Date.parse(t.started_at)) / 1000);
  return {
    cards: cards.length,
    deep: deep.length,
    claims: claims.length,
    numbers: [numbersOk.length, withNumbers.length] as const,
    linked: [linked.length, claims.length] as const,
    checked: [checked.length, claims.length] as const,
    runs: runs.length,
    perCard: done.length ? spent / done.length : null,
    spent,
    avgSeconds: secs.length ? secs.reduce((a, b) => a + b, 0) / secs.length : null,
  };
}

export function MetricsView({ api }: { api: Api }) {
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [cards, runs] = await Promise.all([api.feed(), api.runs()]);
      const ids = runs.map((r) => r.trace_id).filter((x): x is string => !!x).slice(0, TRACES_MAX);
      const traces = (await Promise.all(ids.map((id) => api.trace(id).catch(() => null)))).filter(
        (t): t is RunTrace => t !== null,
      );
      const cost = ids.length ? await api.traceCost(ids[0]).catch(() => null) : null;
      if (alive) setLive({ cards, runs, traces, cost });
    })().catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [api]);

  const sel = selectionMetrics;
  const s = live ? liveStats(live) : null;
  const budget = live?.cost?.budget;

  return (
    <article className="card">
      <header className="card-head">
        <h1>Метрики</h1>
        <p className="muted small">Отбор — по размеченным парам «сюжет × клиент»; остальное — по ленте и разборам вашей компании.</p>
      </header>

      <section className="sec">
        <h2>Отбор: что система ставит наверх</h2>
        {sel && sel.ndcg ? (
          <>
            {sel.pairs != null && (
              <p className="muted small">
                Пар: {sel.pairs}, клиентов: {sel.clients}. Бейзлайн — «свежесть + вес источника» без учёта клиента.
              </p>
            )}
            <div className="table-wrap">
              <table>
                <thead><tr><th>Метрика</th><th>Скоринг</th><th>Бейзлайн</th></tr></thead>
                <tbody>
                  <Row name="nDCG@5" v={sel.ndcg} />
                  {sel.precision && <Row name="precision@5" v={sel.precision} />}
                  {sel.coverage.map((c) => (
                    <Row key={c.name} name={`nDCG@5, ${c.name} отрасли (клиентов: ${c.clients})`} v={c.ndcg} />
                  ))}
                </tbody>
              </table>
            </div>
            {sel.misses.length > 0 && (
              <p className="muted small">
                Пропуски важного: {sel.misses.map((m) => `${m.name} — ${m.value}`).join("; ")}
              </p>
            )}
            {sel.interpretation.length > 0 && (
              <ul className="reasons">{sel.interpretation.map((x) => <li key={x}>{x}</li>)}</ul>
            )}
          </>
        ) : (
          <div className="empty">
            Метрики отбора появятся после разметки 60 пар: импорт разметки пишет <code>reports/metrics.md</code>,
            страница покажет его после пересборки интерфейса.
          </div>
        )}
      </section>

      <section className="sec">
        <h2>Карточки и разборы вашей ленты</h2>
        {error && <div className="error">{error}</div>}
        {!s && !error && <div className="muted">Загрузка…</div>}
        {s && (
          <div className="stats">
            <Stat label="Карточек в ленте" value={`${s.cards}`} hint={`глубоких — ${s.deep}`} />
            <Stat label="Точность чисел и дат" value={pct(...s.numbers)} hint={`${s.numbers[0]} из ${s.numbers[1]} утверждений с числами`} />
            <Stat label="Утверждений со ссылкой" value={pct(...s.linked)} hint={`из ${s.linked[1]} в глубоких карточках`} />
            <Stat label="Прошли все проверки кода" value={pct(...s.checked)} hint="ссылки, метка, числа, даты" />
            <Stat label="₽ на глубокую карточку" value={s.perCard == null ? "—" : rub(s.perCard)} hint={`разборов: ${s.runs}, всего ${rub(s.spent)}`} />
            <Stat label="Время до глубокой карточки" value={s.avgSeconds == null ? "—" : `${Math.round(s.avgSeconds)} с`} hint="в среднем по журналам разборов" />
            {budget?.daily_limit_rub != null && (
              <Stat label="Бюджет дня" value={`${rub(budget.spent_rub ?? 0)} из ${rub(budget.daily_limit_rub)}`} hint={budget.mode && budget.mode !== "normal" ? `режим: ${budget.mode}` : "обычный режим"} />
            )}
          </div>
        )}
      </section>
    </article>
  );
}

function Row({ name, v }: { name: string; v: [number, number] }) {
  const better = v[0] > v[1] + 0.02 ? "check-ok" : v[0] < v[1] - 0.02 ? "check-no" : "";
  return (
    <tr>
      <td>{name}</td>
      <td className={`num ${better}`}>{fmt(v[0])}</td>
      <td className="num">{fmt(v[1])}</td>
    </tr>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat">
      <div className="muted small">{label}</div>
      <strong>{value}</strong>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}
