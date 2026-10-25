// Журнал глубоких разборов ленты компании: что разбирали, чем кончилось и сколько стоило.
import { useEffect, useState } from "react";
import type { Api } from "../api";
import { errorText } from "../api";
import { rub, rubNum } from "../format";
import type { DeepRun } from "../types";
import { SkeletonRows } from "./bits";
import { IconAlert, IconChevron } from "./icons";

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

  if (error)
    return (
      <div className="error" role="alert">
        <IconAlert size={18} />
        {error}
      </div>
    );
  if (runs === null)
    return (
      <div className="block">
        <SkeletonRows />
      </div>
    );
  if (runs.length === 0)
    return (
      <div className="empty">
        <strong>Разборов пока не было</strong>
        Глубоких разборов для вашей компании ещё не делали: лента из быстрых оценок.
      </div>
    );
  const total = runs.reduce((s, r) => s + r.cost_rub, 0);
  const published = runs.filter((r) => r.status === "published").length;
  return (
    <section className="block" aria-label="Журнал разборов">
      <div className="stats">
        <Stat label="Разборов" value={String(runs.length)} />
        <Stat label="Опубликовано" value={String(published)} />
        <Stat label="Потрачено" value={rub(total)} />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Когда</th>
              <th>Сюжет</th>
              <th>Итог</th>
              <th className="num">Значимость</th>
              <th className="num">₽</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {[...runs]
              .sort((a, b) => b.at.localeCompare(a.at))
              .map((r) => (
                <tr key={`${r.story_id}-${r.at}`}>
                  <td className="muted nowrap">
                    {new Date(r.at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                  </td>
                  <td>{titles.get(r.story_id) ?? r.story_id}</td>
                  <td>
                    <span className={`run run-${r.status}`}>{STATUS[r.status]}</span>
                    {r.reason && <div className="muted small">{r.reason}</div>}
                  </td>
                  <td className="num">{r.score == null ? "—" : Math.round(r.score * 100)}</td>
                  <td className="num">{rubNum(r.cost_rub)}</td>
                  <td>
                    {r.trace_id && (
                      <button className="link" onClick={() => onTrace(r.trace_id!)}>
                        журнал
                        <IconChevron size={16} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </section>
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
