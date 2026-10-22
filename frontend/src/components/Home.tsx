// Главный экран: макет счетов и виджет «Важно для вашего бизнеса».
import type { InsightCard } from "../types";
import { PolarityChip, ReviewChip, whyLine } from "./bits";

// Счета — макет экрана интернет-банка, не данные кейса
const ACCOUNTS = [
  { name: "Расчётный счёт ₽", amount: "128 450 210,00 ₽" },
  { name: "Валютный счёт ¥", amount: "4 120 300,00 ¥" },
  { name: "Депозиты", amount: "350 000 000,00 ₽" },
];

interface Props {
  cards: InsightCard[] | null;
  unseen: Set<string>;
  onOpen: (id: string) => void;
  onFeed: () => void;
}

export function Home({ cards, unseen, onOpen, onFeed }: Props) {
  return (
    <>
      <section className="accounts" aria-label="Счета (макет)">
        {ACCOUNTS.map((a) => (
          <div key={a.name} className="account">
            <div className="muted small">{a.name}</div>
            <div className="amount">{a.amount}</div>
          </div>
        ))}
      </section>
      <ImportantWidget cards={cards} unseen={unseen} onOpen={onOpen} onFeed={onFeed} />
      <section className="quick">
        {["Новый платёж", "Выписка", "Валютный контроль", "Заявка на кредит"].map((q) => (
          <div key={q} className="quick-item">
            {q}
          </div>
        ))}
      </section>
    </>
  );
}

function ImportantWidget({ cards, unseen, onOpen, onFeed }: Props) {
  const fresh = cards?.filter((c) => unseen.has(c.card_id)).length ?? 0;
  return (
    <section className="widget">
      <div className="widget-head">
        <h2>
          Важно для вашего бизнеса
          {fresh > 0 && <span className="badge" aria-label={`${fresh} новых`}>{fresh}</span>}
        </h2>
        {cards && cards.length > 0 && (
          <button className="link" onClick={onFeed}>
            Вся лента · {cards.length}
          </button>
        )}
      </div>
      {cards === null && <div className="muted">Загрузка…</div>}
      {cards?.length === 0 && (
        <div className="muted">Пока нет событий, важных для вашей компании в этой роли.</div>
      )}
      <ul className="widget-list">
        {cards?.slice(0, 3).map((c) => (
          <li key={c.card_id}>
            <button className="widget-item" onClick={() => onOpen(c.card_id)}>
              <span className="widget-row">
                {unseen.has(c.card_id) && <i className="dot" aria-label="новое" />}
                <strong>{c.event.title}</strong>
              </span>
              <span className="widget-row">
                <PolarityChip polarity={c.relevance.polarity} />
                <ReviewChip card={c} />
                <span className="muted small">Почему вы это видите: {whyLine(c)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
