// Промежуточные результаты разбора: план, что нашли инструменты, выводы аналитиков по каналам, арбитр, критик.
// Только сводки из /trace: id и заголовки материалов, без сырых данных и платежей.
import { channelLabel } from "../labels";
import type { RunTrace, TraceAnalystReport, TraceChannel } from "../types";
import { TOOL_NAMES } from "./AskPanel";

const ARROW: Record<TraceChannel["direction"], string> = { up: "↑", down: "↓", flat: "→" };
const ANALYST: Record<TraceAnalystReport["analyst"], string> = { business: "Аналитик бизнеса", finance: "Аналитик финансов" };
const AGREEMENT: Record<string, string> = { agree: "согласны", disagree: "спор", single: "только один" };
const ORIGIN: Record<string, string> = { template: "шаблон", llm: "достройка моделью", critic: "по задаче критика" };

const fmtArgs = (args: Record<string, unknown>) =>
  Object.entries(args)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`)
    .join(" · ");

function strengthWord(s: number): string {
  return s >= 0.6 ? "сильно" : s >= 0.3 ? "средне" : "слабо";
}

export function TraceDetails({ trace }: { trace: RunTrace }) {
  const plan = trace.plan ?? [];
  const tools = trace.tool_results ?? [];
  const reports = trace.analyst_reports ?? [];
  const rounds = trace.critic_rounds ?? [];
  const arb = trace.arbiter;
  const channels = [...new Set(reports.flatMap((r) => r.channels.map((c) => c.channel)))];
  const cell = (r: TraceAnalystReport | undefined, ch: string) => {
    const c = r?.channels.find((x) => x.channel === ch);
    if (!c) return <span className="muted">—</span>;
    return (
      <span title={c.rationale ?? undefined}>
        {ARROW[c.direction]} {strengthWord(c.strength)}
        {c.horizon ? <span className="muted small"> · {c.horizon}</span> : null}
      </span>
    );
  };
  const by = (a: TraceAnalystReport["analyst"]) => reports.find((r) => r.analyst === a);

  return (
    <>
      {plan.length > 0 && (
        <section className="art-sec">
          <div className="sec-head">
            <h2>План разбора{trace.analysis_cached ? " (из кеша разбора отрасли)" : ""}</h2>
          </div>
          <ol className="plan">
            {plan.map((s, i) => (
              <li key={i}>
                <strong>{TOOL_NAMES[s.tool] ?? s.tool}</strong>
                {Object.keys(s.args).length > 0 && <span className="muted small"> · {fmtArgs(s.args)}</span>}
                {s.why && <div className="small">{s.why}</div>}
                <span className="muted small">{ORIGIN[s.origin] ?? s.origin}</span>
              </li>
            ))}
          </ol>
          {(trace.gaps ?? []).length > 0 && (
            <p className="muted small">Пробелы досье: {(trace.gaps ?? []).join("; ")}</p>
          )}
        </section>
      )}

      {tools.length > 0 && (
        <section className="art-sec">
          <div className="sec-head">
            <h2>Что нашли инструменты</h2>
          </div>
          {tools.map((t) => (
            <details key={t.tool}>
              <summary>
                {TOOL_NAMES[t.tool] ?? t.tool} — найдено: {t.found}
              </summary>
              {t.items.length > 0 ? (
                <ul className="small">
                  {t.items.map((it) => (
                    <li key={it.id}>
                      <span className="mono">{it.id}</span> · {it.title}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">Признаки компании и контрагенты — только числом, без деталей.</p>
              )}
            </details>
          ))}
        </section>
      )}

      {reports.length > 0 && (
        <section className="art-sec">
          <div className="sec-head">
            <h2>Выводы аналитиков по каналам</h2>
          </div>
          <p className="muted small">Направление фактора и сила, а не «хорошо / плохо»: знак для компании ставит скоринг.</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Канал</th>
                  <th>{ANALYST.business}</th>
                  <th>{ANALYST.finance}</th>
                  <th>Арбитр</th>
                </tr>
              </thead>
              <tbody>
                {channels.map((ch) => (
                  <tr key={ch}>
                    <td>{channelLabel(ch)}</td>
                    <td>{cell(by("business"), ch)}</td>
                    <td>{cell(by("finance"), ch)}</td>
                    <td>{arb?.agreement[ch] ? AGREEMENT[arb.agreement[ch]] : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {reports.map((r) => (
            <p key={r.analyst} className="small">
              <strong>{ANALYST[r.analyst]}</strong> <span className="muted mono">({r.model})</span>: {r.summary}
            </p>
          ))}
        </section>
      )}

      {arb && (
        <section className="art-sec">
          <div className="sec-head">
            <h2>Арбитр</h2>
          </div>
          <p>{arb.on_review ? "Итог: на проверке у эксперта — аналитики разошлись в направлении." : "Итог: спорных каналов нет, карточка публикуется."}</p>
          {arb.disputes.length > 0 && <ul className="small">{arb.disputes.map((d, i) => <li key={i}>{d}</li>)}</ul>}
          {arb.bank_check.length > 0 && (
            <>
              <p className="muted small">Сверка с позицией ЦМИ:</p>
              <ul className="small">{arb.bank_check.map((d, i) => <li key={i}>{d}</li>)}</ul>
            </>
          )}
        </section>
      )}

      {rounds.length > 0 && (
        <section className="art-sec">
          <div className="sec-head">
            <h2>Круги критика</h2>
          </div>
          <ol className="small">
            {rounds.map((r) => (
              <li key={r.round}>
                Круг {r.round}: {r.ok ? "замечаний нет" : `замечаний ${r.failures.length}`}
                {r.planner_task && <span className="muted"> · задача планировщику: {r.planner_task}</span>}
                {r.failures.length > 0 && <ul>{r.failures.map((f, i) => <li key={i}>{f}</li>)}</ul>}
              </li>
            ))}
          </ol>
          {trace.card_source && (
            <p className="muted small">
              Текст карточки: {trace.card_source === "template" ? "шаблон (модель не прошла проверку или недоступна)" : "модель, проверен кодом"}
            </p>
          )}
        </section>
      )}
    </>
  );
}
