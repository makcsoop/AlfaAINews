// Лента карточек по убыванию значимости.
import { CONFIDENCE_LABELS } from "../labels";
import type { InsightCard } from "../types";
import { PolarityChip, ReviewChip, formatDate, whyLine } from "./bits";

interface Props {
  cards: InsightCard[] | null;
  unseen: Set<string>;
  onOpen: (id: string) => void;
}

export function Feed({ cards, unseen, onOpen }: Props) {
  if (cards === null) return <div className="muted">Загрузка…</div>;
  if (cards.length === 0)
    return (
      <div className="empty">
        Новостей для вас пока нет. Как только появится событие, важное для вашей компании, оно будет здесь.
      </div>
    );
  return (
    <>
      <h1>Важно для вашего бизнеса</h1>
      <ul className="feed">
        {cards.map((c) => {
          const lead = c.claims.find((x) => x.claim_id === c.why_it_matters[0]);
          return (
            <li key={c.card_id}>
              <button className="feed-item" onClick={() => onOpen(c.card_id)}>
                <div className="feed-meta">
                  {unseen.has(c.card_id) && <i className="dot" aria-label="новое" />}
                  <PolarityChip polarity={c.relevance.polarity} />
                  <ReviewChip card={c} />
                  <span className="muted small">
                    {formatDate(c.event.first_seen)} · {CONFIDENCE_LABELS[c.confidence].toLowerCase()}
                  </span>
                  <span className="score" title="Значимость для компании">
                    {Math.round(c.relevance.score * 100)}
                  </span>
                </div>
                <h3>{c.event.title}</h3>
                {lead && <p>{lead.text}</p>}
                <div className="muted small">Почему вы это видите: {whyLine(c)}</div>
              </button>
            </li>
          );
        })}
      </ul>
    </>
  );
}
