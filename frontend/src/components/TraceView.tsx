// «Как это получено»: шаги автомата разбора, инструменты, вызовы моделей, что вернул критик, ₽ по шагам.
import { useEffect, useState } from "react";
import type { Api } from "../api";
import { errorText } from "../api";
import { rub } from "../format";
import type { RunTrace, TraceCost } from "../types";
import { TOOL_NAMES } from "./AskPanel";
import { SkeletonRows } from "./bits";
import { IconAlert, IconBack, IconCheck, IconCross, IconShield } from "./icons";

// Состояния автомата Р3 (backend/agents/pipeline.py) в порядке конвейера
const STATES: Record<string, string> = {
  NEW: "Сюжет принят",
  FACTS_READY: "Факты извлечены и сверены с цитатами",
  SCORED: "Скоринг: значимость для компании",
  QUICK_CARD: "Быстрая карточка без LLM",
  QUEUED_DEEP: "Отобран на глубокий разбор",
  PLANNED: "План разбора",
  ANALYZED: "Два независимых аналитика",
  CRITIQUED: "Критик: проверка чисел, ссылок, меток",
  ARBITRATED: "Арбитр: согласны ли аналитики",
  CARD_DRAFTED: "Текст карточки",
  CARD_VERIFIED: "Финальная проверка карточки кодом",
  PUBLISHED: "Опубликовано",
  ON_REVIEW: "На проверке у эксперта",
  DEGRADED: "Упрощённый режим (бюджет или сбой модели)",
  FAILED: "Разбор не удался",
};

export const PURPOSES: Record<string, string> = {
  planner: "Планировщик",
  analyst_business: "Аналитик бизнеса",
  analyst_finance: "Аналитик финансов",
  critic_llm: "Критик (модель)",
  card: "Текст карточки",
  qa: "Вопрос-ответ",
  extractor: "Экстрактор фактов",
};

interface Phase {
  state: string;
  ok: boolean;
}

// «A->B» — переход, «handler:B:ok» — итог шага; всё остальное (dossier:r1:*, круги критика) — пометки
export function phases(steps: string[]): { phases: Phase[]; notes: string[] } {
  const out: Phase[] = [];
  const notes: string[] = [];
  for (const s of steps) {
    const move = s.match(/^([A-Z_]+)->([A-Z_]+)$/);
    const handler = s.match(/^handler:([A-Z_]+):(\w+)$/);
    if (move) {
      if (!out.length) out.push({ state: move[1], ok: true });
      out.push({ state: move[2], ok: true });
    } else if (handler) {
      const p = [...out].reverse().find((x) => x.state === handler[1]);
      if (p) p.ok = handler[2] === "ok";
    } else {
      notes.push(s);
    }
  }
  return { phases: out, notes };
}

const time = (iso: string) => new Date(iso).toLocaleTimeString("ru-RU");

export function TraceView({ api, traceId, onBack }: { api: Api; traceId: string; onBack: () => void }) {
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [cost, setCost] = useState<TraceCost | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTrace(null);
    setError(null);
    api.trace(traceId).then(setTrace, (e) => setError(errorText(e)));
    api.traceCost(traceId).then(setCost, () => setCost(null));
  }, [api, traceId]);

  const back = (
    <div className="crumbs">
      <button className="btn btn-ghost" onClick={onBack}>
        <IconBack size={18} />
        Назад
      </button>
    </div>
  );
  if (error) {
    return (
      <div className="view">
        {back}
        <div className="error" role="alert">
          <IconAlert size={18} />
          {error}
        </div>
      </div>
    );
  }
  if (!trace)
    return (
      <div className="view">
        {back}
        <div className="block">
          <SkeletonRows />
        </div>
      </div>
    );
  const { phases: ph, notes } = phases(trace.steps);
  const seconds = trace.finished_at
    ? Math.round((Date.parse(trace.finished_at) - Date.parse(trace.started_at)) / 1000)
    : null;
  const hasCost = !!cost && Object.keys(cost.cost_by_step).length > 0;
  // все вызовы взяты из кеша — «0 ₽» объясняем пометкой, а не выдаём за бесплатный разбор
  const allCached = trace.llm_calls.length > 0 && trace.llm_calls.every((c) => c.cached);
  const costLabel = `${rub(cost?.cost_rub ?? trace.cost_rub)}${allCached ? " — из кеша" : ""}`;

  return (
    <div className="view">
      {back}
      <header className="trace-head">
        <div>
          <span className="eyebrow">
            <b>Прозрачность</b> / как работали агенты
          </span>
          <h1>Как получена карточка</h1>
          <div className="stats">
            <Stat label="Стоимость разбора" value={costLabel} />
            <Stat label="Кругов проверки" value={String(trace.verify_rounds)} />
            <Stat label="Вызовов моделей" value={String(trace.llm_calls.length)} />
            {seconds !== null && <Stat label="Время" value={`${seconds} с`} />}
          </div>
        </div>
      </header>

      <div className="trace-body">
        <section className="art-sec">
          <div className="sec-head">
            <h2>Шаги</h2>
          </div>
          {ph.length === 0 && <p className="muted">Переходы автомата в трассе не записаны — ниже служебные пометки.</p>}
          <ol className="timeline">
            {ph.map((p, i) => (
              <li key={`${p.state}-${i}`} className={p.ok ? "" : "step-bad"}>
                <span className="step-dot">{p.ok ? <IconCheck /> : <IconCross />}</span>
                <span className="step-name">
                  {STATES[p.state] ?? p.state}
                  {!p.ok && <span className="check check-no"> · не прошёл</span>}
                </span>
              </li>
            ))}
          </ol>
          {notes.length > 0 && (
            <details>
              <summary className="muted small">Служебные пометки ({notes.length})</summary>
              <ul className="mono small">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </details>
          )}
        </section>

        <section className="art-sec">
          <div className="sec-head">
            <h2>Что вернул критик</h2>
          </div>
          {trace.verify_failures.length === 0 ? (
            <div className="callout">
              <IconShield size={20} />
              <span>
                {trace.verify_rounds > 1
                  ? `Замечания исправлены за ${trace.verify_rounds} круга, итоговая проверка пройдена.`
                  : "Проверка пройдена с первого раза: числа, даты, ссылки и метки совпали с источниками."}
              </span>
            </div>
          ) : (
            <ul className="reasons">
              {trace.verify_failures.map((f, i) => (
                <li key={i} className="tone-neutral">
                  <IconAlert size={18} />
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {hasCost && cost && (
          <section className="art-sec">
            <div className="sec-head">
              <h2>Модели и расходы по шагам</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Шаг</th><th className="num">Вызовов</th><th className="num">Токены (вход / выход)</th><th className="num">₽</th></tr></thead>
                <tbody>
                  {Object.entries(cost.cost_by_step).map(([step, c]) => (
                    <tr key={step}>
                      <td>{PURPOSES[step] ?? step}</td>
                      <td className="num">{c.calls}</td>
                      <td className="num">{c.tokens_in.toLocaleString("ru-RU")} / {c.tokens_out.toLocaleString("ru-RU")}</td>
                      <td className="num">{rub(c.cost_rub)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {cost.budget?.daily_limit_rub != null && (
              <p className="muted small">
                Бюджет дня: потрачено {rub(cost.budget.spent_rub ?? 0)} из {rub(cost.budget.daily_limit_rub)}
                {cost.budget.mode && cost.budget.mode !== "normal" ? ` · режим: ${cost.budget.mode}` : ""}
              </p>
            )}
          </section>
        )}

        {trace.llm_calls.length > 0 && (
          <section className="art-sec">
            <div className="sec-head">
              <h2>Вызовы моделей</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Время</th><th>Шаг</th><th>Модель</th><th className="num">Токены</th><th className="num">₽</th></tr></thead>
                <tbody>
                  {trace.llm_calls.map((c, i) => (
                    <tr key={i}>
                      <td className="muted nowrap">{time(c.at)}</td>
                      <td>{PURPOSES[c.purpose] ?? c.purpose}{c.cached ? " (кеш)" : ""}</td>
                      <td className="mono">{c.model}</td>
                      <td className="num">{(c.tokens_in + c.tokens_out).toLocaleString("ru-RU")}</td>
                      <td className="num">{rub(c.cost_rub)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {trace.tool_calls.length > 0 && (
          <section className="art-sec">
            <div className="sec-head">
              <h2>Инструменты (только чтение)</h2>
            </div>
            <ul className="tools">
              {trace.tool_calls.map((t, i) => (
                <li key={i}>
                  <span className={t.ok ? "check check-ok" : "check check-no"}>{t.ok ? <IconCheck /> : <IconCross />}</span>
                  {TOOL_NAMES[t.tool] ?? t.tool} <span className="muted small">· {time(t.at)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="muted small">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
