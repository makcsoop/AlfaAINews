// Каркас интернет-банка: чёрное боковое меню со знаком банка, верхняя панель с выбором компании и роли (для демо),
// на телефоне — нижняя навигация.
import type { ReactNode } from "react";
import type { DemoClient } from "../clients";
import { DEMO_ROLES, ROLE_LABELS } from "../labels";
import type { Theme } from "../theme";
import type { UserRole } from "../types";
import {
  AlfaAppIcon,
  IconChevronDown,
  IconCredit,
  IconDoc,
  IconGlobe,
  IconHelp,
  IconHome,
  IconInsights,
  IconJournal,
  IconMoon,
  IconPeople,
  IconSun,
  IconTransfer,
  IconWallet,
} from "./icons";

export type Section = "home" | "insights";
export type Place = "" | "brief" | "feed" | "faq";

// Разделы макета интернет-банка — не подключены, показаны для контекста
const MOCK_NAV = [
  { name: "Счета", icon: IconWallet },
  { name: "Платежи", icon: IconTransfer },
  { name: "Кредиты", icon: IconCredit },
  { name: "ВЭД", icon: IconGlobe },
  { name: "Зарплатный проект", icon: IconPeople },
  { name: "Документы", icon: IconDoc },
];

interface Props {
  clients: DemoClient[];
  companyId: string;
  role: UserRole;
  section: Section;
  place: Place | null;
  unseen: number;
  theme: Theme;
  onCompany: (id: string) => void;
  onRole: (r: UserRole) => void;
  onGo: (place: Place) => void;
  onTheme: () => void;
  children: ReactNode;
}

function initials(title: string): string {
  const words = title.split(/\s+/).filter((w) => w.length > 2);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase();
}

export function Layout({ clients, companyId, role, section, place, unseen, theme, onCompany, onRole, onGo, onTheme, children }: Props) {
  const client = clients.find((c) => c.companyId === companyId);
  const items: { place: Place; section: Section; name: string; icon: typeof IconHome; count?: number }[] = [
    { place: "", section: "home", name: "Главная", icon: IconHome },
    { place: "brief", section: "insights", name: "Важно для бизнеса", icon: IconInsights, count: unseen },
  ];
  // на телефоне — нижнее меню из главного экрана банка и разделов сервиса
  const tabs: { place: Place; name: string; icon: typeof IconHome; count?: number }[] = [
    { place: "", name: "Главная", icon: IconHome },
    { place: "brief", name: "Обзор", icon: IconInsights, count: unseen },
    { place: "feed", name: "Лента", icon: IconJournal },
    { place: "faq", name: "Помощь", icon: IconHelp },
  ];
  return (
    <div className="app">
      <aside className="side" aria-label="Меню интернет-банка">
        <button className="brand" onClick={() => onGo("")} aria-label="Альфа-Бизнес, на главную">
          <AlfaAppIcon size={40} />
          <span className="brand-name">
            <strong>Альфа-Бизнес</strong>
            <span>интернет-банк</span>
          </span>
        </button>
        <nav className="nav" aria-label="Разделы">
          {items.map((it) => (
            <button
              key={it.place}
              className={section === it.section ? "nav-item on" : "nav-item"}
              aria-current={section === it.section ? "page" : undefined}
              onClick={() => onGo(it.place)}
            >
              <it.icon />
              {it.name}
              {!!it.count && <span className="count" aria-label={`${it.count} новых`}>{it.count}</span>}
            </button>
          ))}
          <div className="nav-label">Банк · макет</div>
          {MOCK_NAV.map((n) => (
            <button key={n.name} className="nav-item" disabled title="Раздел макета — в демо не подключён">
              <n.icon />
              {n.name}
            </button>
          ))}
        </nav>
        <div className="side-slogan" aria-label="Слоган: банк класса А">
          банк класса <span className="foundation">А</span>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="topbar-brand" onClick={() => onGo("")} aria-label="На главную">
            <AlfaAppIcon size={38} />
          </button>
          <div className="org">
            <span className="org-avatar" aria-hidden="true">
              {client ? initials(client.title) : "А"}
            </span>
            <span className="org-text">
              <strong>{client?.title ?? companyId}</strong>
              {client && <span title={client.okvedName}>{client.subtitle}</span>}
            </span>
            <span className="org-chevron">
              <IconChevronDown size={18} />
            </span>
            <label className="sr-only" htmlFor="client">
              Компания
            </label>
            <select id="client" value={companyId} onChange={(e) => onCompany(e.target.value)}>
              {clients.map((c) => (
                <option key={c.companyId} value={c.companyId} title={c.okvedName}>
                  {c.title}
                </option>
              ))}
            </select>
          </div>
          <div className="topbar-right">
            <span className="role-label" id="role-label">
              Смотреть как
            </span>
            <div className="segmented" role="radiogroup" aria-labelledby="role-label">
              {DEMO_ROLES.map((r) => (
                <button
                  key={r}
                  role="radio"
                  aria-checked={r === role}
                  className={r === role ? "seg on" : "seg"}
                  onClick={() => onRole(r)}
                >
                  {ROLE_LABELS[r]}
                </button>
              ))}
            </div>
            <button
              className="icon-btn"
              onClick={onTheme}
              aria-label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
              title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
            >
              {theme === "dark" ? <IconSun /> : <IconMoon />}
            </button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>

      <nav className="tabbar" aria-label="Разделы">
        {tabs.map((it) => (
          <button
            key={it.place}
            className={place === it.place ? "on" : undefined}
            aria-current={place === it.place ? "page" : undefined}
            onClick={() => onGo(it.place)}
          >
            <it.icon size={22} />
            {it.name}
            {!!it.count && <span className="count">{it.count}</span>}
          </button>
        ))}
      </nav>
    </div>
  );
}
