// Шапка сервиса «Важно для бизнеса»: название, для кого лента, разделы сервиса.
import { go } from "../nav";

export type Tab = "brief" | "feed" | "runs" | "how" | "faq";

const TABS: { id: Tab; name: string }[] = [
  { id: "brief", name: "Обзор" },
  { id: "feed", name: "Лента" },
  { id: "runs", name: "Журнал разборов" },
  { id: "how", name: "Как это работает" },
  { id: "faq", name: "Вопросы и ответы" },
];

interface Props {
  tab: Tab;
  company: string;
  role: string;
  count: number | null;
  fresh: number;
}

export function ProductHeader({ tab, company, role, count, fresh }: Props) {
  return (
    <header className="product-head">
      <div className="product-title">
        <h1>
          Важно для <span className="foundation">вашего</span> бизнеса
        </h1>
        <p>
          {company} · {role}
          {fresh > 0 && (
            <span className="product-fresh">
              <i className="dot" aria-hidden="true" />
              {fresh} новых
            </span>
          )}
        </p>
      </div>
      <nav className="product-tabs" aria-label="Разделы сервиса">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={t.id === tab ? "on" : undefined}
            aria-current={t.id === tab ? "page" : undefined}
            onClick={() => go(t.id)}
          >
            {t.name}
            {t.id === "feed" && count != null && <span className="tab-count">{count}</span>}
          </button>
        ))}
      </nav>
    </header>
  );
}
