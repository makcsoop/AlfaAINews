// Вопрос-ответ по карточке: отвечает Р3 только по досье и инструментам своей компании (компания — из сессии).
import { useState } from "react";
import type { Api } from "../api";
import { errorText } from "../api";
import { CLAIM_LABELS, sourceLabel } from "../labels";
import type { AskResponse, InsightCard } from "../types";

const SUGGESTED = ["Что по нашим контрагентам-логистам?", "Как это скажется на нашем долге?", "Что делать казначейству?"];

// Инструменты агентов Р3 (backend/agents/tools/registry.py)
export const TOOL_NAMES: Record<string, string> = {
  get_counterparties: "контрагенты",
  get_profile: "профиль компании",
  get_bank_positions: "позиции ЦМИ",
  get_market_knowledge: "мнения аналитиков рынка",
  find_similar_cases: "похожие случаи",
  search_news: "поиск новостей",
  calculator: "калькулятор",
};

interface Exchange {
  question: string;
  reply?: AskResponse;
  error?: string;
}

export function AskPanel({ api, card }: { api: Api; card: InsightCard }) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [busy, setBusy] = useState(false);
  const claims = new Map(card.claims.map((c) => [c.claim_id, c]));

  const ask = async (q: string) => {
    const text = q.trim();
    if (!text || busy) return;
    setBusy(true);
    setQuestion("");
    setHistory((h) => [...h, { question: text }]);
    try {
      const reply = await api.ask(text, card.card_id);
      setHistory((h) => h.map((x, i) => (i === h.length - 1 ? { ...x, reply } : x)));
    } catch (e) {
      setHistory((h) => h.map((x, i) => (i === h.length - 1 ? { ...x, error: errorText(e) } : x)));
    } finally {
      setBusy(false);
    }
  };

  // ссылка ответа: утверждение карточки — его метка и начало текста, иначе материал досье
  const refLabel = (ref: string) => {
    const c = claims.get(ref);
    if (c) return `${CLAIM_LABELS[c.type]}: «${c.text.slice(0, 40)}…»`;
    if (ref.startsWith("cp:")) return "контрагенты компании";
    return sourceLabel(ref);
  };

  return (
    <section className="ask" aria-label="Вопрос по карточке">
      <h2>Спросить о событии</h2>
      {history.map((x, i) => (
        <div key={i} className="qa">
          <div className="qa-q">{x.question}</div>
          {!x.reply && !x.error && <div className="muted small">Ищем ответ в материалах разбора…</div>}
          {x.error && <div className="error">{x.error}</div>}
          {x.reply && (
            <div className={`qa-a qa-${x.reply.status}`}>
              {x.reply.status === "refused" && <div className="qa-note">Отказ: отвечаем только по данным вашей компании.</div>}
              {x.reply.status === "degraded" && <div className="qa-note">Ответ неполный: часть данных сейчас недоступна.</div>}
              <p>{x.reply.answer}</p>
              {x.reply.refs.length > 0 && (
                <div className="muted small">Основано на: {[...new Set(x.reply.refs.map(refLabel))].join(" · ")}</div>
              )}
              {x.reply.tools_used.length > 0 && (
                <div className="muted small">
                  Смотрели: {x.reply.tools_used.map((t) => TOOL_NAMES[t] ?? t).join(", ")}
                </div>
              )}
            </div>
          )}
        </div>
      ))}
      <div className="suggest">
        {SUGGESTED.map((s) => (
          <button key={s} className="chip chip-btn" onClick={() => ask(s)} disabled={busy}>
            {s}
          </button>
        ))}
      </div>
      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          ask(question);
        }}
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Например: что это значит для наших поставщиков?"
          maxLength={2000}
          aria-label="Вопрос"
        />
        <button className="btn btn-primary" disabled={busy || !question.trim()}>
          Спросить
        </button>
      </form>
    </section>
  );
}
