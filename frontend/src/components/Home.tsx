// Главный экран интернет-банка: виджет «Важно для вашего бизнеса» (до трёх событий и счётчик новых),
// знакомство с сервисом, макет счетов и быстрых действий.
import { plural } from "../labels";
import { go } from "../nav";
import type { InsightCard } from "../types";
import { SkeletonRows } from "./bits";
import { Checklist } from "./Checklist";
import { IconChevron, IconCredit, IconDoc, IconGlobe, IconHelp, IconTransfer } from "./icons";
import { StoryRow } from "./StoryRow";

// Счета — макет экрана интернет-банка, не данные кейса
const ACCOUNTS = [
  { name: "Расчётный счёт", tail: "•• 4021", amount: "128 450 210,00 ₽" },
  { name: "Валютный счёт", tail: "•• 7735", amount: "4 120 300,00 ¥" },
  { name: "Депозит до 30.12", tail: "14,2% годовых", amount: "350 000 000,00 ₽" },
];

const QUICK = [
  { name: "Платёж", icon: IconTransfer },
  { name: "Выписка", icon: IconDoc },
  { name: "Валютный контроль", icon: IconGlobe },
  { name: "Кредит", icon: IconCredit },
];

interface Props {
  cards: InsightCard[] | null;
  unseen: Set<string>;
  onOpen: (id: string) => void;
  onTour: () => void;
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

export function Home({ cards, unseen, onOpen, onTour }: Props) {
  const fresh = cards?.filter((c) => unseen.has(c.card_id)).length ?? 0;
  const today = new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
  return (
    <div className="view home">
      <header className="home-head">
        <span className="home-date">{today}</span>
        <h1>{greeting()}</h1>
      </header>
      <div className="home-grid">
        <section className="widget" aria-labelledby="widget-title">
          <div className="widget-head">
            <h2 id="widget-title">
              Важно для <span className="foundation">вашего</span> бизнеса
            </h2>
            {fresh > 0 && (
              <span className="counter" aria-label={`${fresh} ${plural(fresh, ["новое событие", "новых события", "новых событий"])}`}>
                {fresh}
              </span>
            )}
            <button className="widget-help" onClick={onTour} title="Как это работает">
              <IconHelp size={18} />
              <span>Что это?</span>
            </button>
          </div>
          <p className="widget-sub">
            {cards === null
              ? "Собираем события, которые касаются вашей компании…"
              : cards.length === 0
                ? "Сейчас нет событий, которые заметно влияют на вашу компанию в этой роли."
                : `Отобрали ${cards.length} ${plural(cards.length, ["событие", "события", "событий"])} по профилю компании — показываем самые значимые.`}
          </p>
          {cards === null && <SkeletonRows rows={3} />}
          {!!cards?.length && (
            <ul className="stories">
              {cards.slice(0, 3).map((c, i) => (
                <StoryRow key={c.card_id} card={c} isNew={unseen.has(c.card_id)} onOpen={() => onOpen(c.card_id)} rank={i + 1} compact />
              ))}
            </ul>
          )}
          <div className="widget-foot">
            <button className="btn btn-primary" onClick={() => go("brief")}>
              Открыть обзор
            </button>
            {!!cards?.length && (
              <button className="link" onClick={() => go("feed")}>
                Вся лента · {cards.length}
                <IconChevron size={16} />
              </button>
            )}
          </div>
        </section>

        <div className="home-side">
          <section className="accounts" aria-label="Счета (макет)">
            <div className="block-head">
              <h2>Счета</h2>
              <span className="flag">макет</span>
            </div>
            <ul>
              {ACCOUNTS.map((a) => (
                <li key={a.name}>
                  <span>
                    {a.name}
                    <small>{a.tail}</small>
                  </span>
                  <b>{a.amount}</b>
                </li>
              ))}
            </ul>
            <div className="quick" aria-label="Быстрые действия (макет)">
              {QUICK.map((q) => (
                <button key={q.name} disabled title="Раздел макета — в демо не подключён">
                  <q.icon size={20} />
                  {q.name}
                </button>
              ))}
            </div>
          </section>
          <Checklist topCardId={cards?.[0]?.card_id ?? null} compact />
        </div>
      </div>
    </div>
  );
}
