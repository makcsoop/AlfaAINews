// Блок знакомства: четыре первых действия в сервисе. Шаг засчитывается, когда пользователь сделал его сам.
import { go } from "../nav";
import type { StepId } from "../onboarding";
import { setChecklistHidden, useOnboarding } from "../onboarding";
import { IconCheck, IconChevron, IconCross } from "./icons";

const STEPS: { id: StepId; title: string; text: string; cta: string }[] = [
  { id: "open", title: "Откройте главное событие", text: "Что произошло и почему это важно именно вашей компании.", cta: "Открыть" },
  { id: "rate", title: "Оцените разбор", text: "«Полезно» или «Не про нас» — лента подстроится под вас.", cta: "Оценить" },
  { id: "ask", title: "Задайте вопрос AI", text: "Например, как событие скажется на долге или контрагентах.", cta: "Спросить" },
  { id: "paste", title: "Проверьте свою новость", text: "Вставьте ссылку — покажем, что она значит для компании.", cta: "Проверить" },
];

export function Checklist({ topCardId, compact = false }: { topCardId: string | null; compact?: boolean }) {
  const { done, hidden } = useOnboarding();
  if (hidden) return null;
  const count = STEPS.filter((s) => done.includes(s.id)).length;
  const complete = count === STEPS.length;
  const run = (id: StepId) => {
    const card = topCardId ? `card/${encodeURIComponent(topCardId)}` : "feed";
    if (id === "open") go(card);
    if (id === "rate") go(card, "sec-feedback");
    if (id === "ask") go(card, "sec-ask");
    if (id === "paste") go("brief", "paste");
  };
  return (
    <section className={`onb${compact ? " onb-compact" : ""}`} aria-labelledby="onb-title">
      <div className="onb-head">
        <div>
          <span className="onb-kicker">
            Знакомство · {count} из {STEPS.length}
          </span>
          <h2 id="onb-title">{complete ? "Вы освоились" : compact ? "Начните с четырёх шагов" : "Четыре шага, чтобы освоиться"}</h2>
        </div>
        <button className="onb-close" onClick={() => setChecklistHidden(true)} aria-label="Скрыть знакомство" title="Скрыть">
          <IconCross size={18} />
        </button>
      </div>
      <div className="onb-progress" aria-hidden="true">
        {STEPS.map((s) => (
          <i key={s.id} className={done.includes(s.id) ? "on" : undefined} />
        ))}
      </div>
      {complete ? (
        <p className="onb-done">
          Все основные возможности под рукой. Ответы на частые вопросы — в разделе «Вопросы и ответы».
        </p>
      ) : (
        <ol className="onb-steps">
          {STEPS.map((s, i) => {
            const ok = done.includes(s.id);
            return (
              <li key={s.id} className={ok ? "done" : undefined}>
                <button onClick={() => run(s.id)} disabled={ok && compact}>
                  <span className="onb-n" aria-hidden="true">
                    {ok ? <IconCheck size={16} /> : i + 1}
                  </span>
                  <span className="onb-text">
                    <strong>{s.title}</strong>
                    {!compact && <span>{s.text}</span>}
                  </span>
                  {!ok && (
                    <span className="onb-cta">
                      {!compact && s.cta}
                      <IconChevron size={16} />
                    </span>
                  )}
                  {ok && <span className="sr-only">сделано</span>}
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
