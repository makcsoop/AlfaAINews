// Журнал глубоких разборов ленты компании: что разбирали, чем кончилось и сколько стоило.
import { useEffect, useState } from "react";
import type { Api } from "../api";
import { errorText } from "../api";
import type { DeepRun } from "../types";

const STATUS: Record<DeepRun["status"], string> = {
  published: "опубликован",
  on_review: "на проверке",
  dropped: "отброшен по значимости",
  failed: "не удался",
};

export function RunsView({ api, onTrace, titles }: { api: Api; onTrace: (id: string) => void; titles: Map<string, string> }) {
  const [runs, setRuns] = useState<DeepRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.runs().then(setRuns, (e) => setError(errorText(e)));
  }, [api]);

  if (error) return <div className="error">{error}</div>;
  if (runs === null) return <div className="muted">Загрузка журнала…</div>;
  if (runs.length === 0)
    return <div className="empty">Глубоких разборов для вашей компании пока не было: лента из быстрых оценок.</div>;
  const total = runs.reduce((s, r) => s + r.cost_rub, 0);
  return (
    <section className="card">
      <h1>Журнал разборов</h1>
      <p className="muted small">
        Разборов: {runs.length} · потрачено {total.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽
      </p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Когда</th><th>Сюжет</th><th>Итог</th><th>Значимость</th><th>₽</th><th /></tr></thead>
          <tbody>
            {[...runs].sort((a, b) => b.at.localeCompare(a.at)).map((r) => (
              <tr key={`${r.story_id}-${r.at}`}>
                <td>{new Date(r.at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</td>
                <td>{titles.get(r.story_id) ?? r.story_id}</td>
                <td>
                  <span className={`run run-${r.status}`}>{STATUS[r.status]}</span>
                  {r.reason && <div className="muted small">{r.reason}</div>}
                </td>
                <td className="num">{r.score == null ? "—" : Math.round(r.score * 100)}</td>
                <td className="num">{r.cost_rub.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}</td>
                <td>{r.trace_id && <button className="link" onClick={() => onTrace(r.trace_id!)}>журнал →</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
