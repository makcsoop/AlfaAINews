// Каркас интернет-банка: боковое меню и верхняя панель с переключателями клиента и роли (для демо).
import type { ReactNode } from "react";
import { DEMO_CLIENTS } from "../clients";
import { DEMO_ROLES, ROLE_LABELS } from "../labels";
import type { UserRole } from "../types";

const NAV = ["Главная", "Счета", "Платежи", "Кредиты", "ВЭД", "Зарплатный проект", "Документы"];

interface Props {
  companyId: string;
  role: UserRole;
  onCompany: (id: string) => void;
  onRole: (r: UserRole) => void;
  onHome: () => void;
  children: ReactNode;
}

export function Layout({ companyId, role, onCompany, onRole, onHome, children }: Props) {
  const client = DEMO_CLIENTS.find((c) => c.companyId === companyId);
  return (
    <div className="app">
      <aside className="side">
        <button className="logo" onClick={onHome} aria-label="На главную">
          <span className="logo-mark">А</span>
          <span>Альфа-Бизнес</span>
        </button>
        <nav>
          {NAV.map((n, i) => (
            <a key={n} className={i === 0 ? "active" : undefined} onClick={i === 0 ? onHome : undefined}>
              {n}
            </a>
          ))}
        </nav>
      </aside>
      <div className="main">
        <header className="top">
          <div className="company">
            <label className="muted small" htmlFor="client">
              Компания
            </label>
            <select id="client" value={companyId} onChange={(e) => onCompany(e.target.value)}>
              {DEMO_CLIENTS.map((c) => (
                <option key={c.companyId} value={c.companyId}>
                  {c.title}
                </option>
              ))}
            </select>
            {client && <div className="muted small">{client.subtitle}</div>}
          </div>
          <div className="roles" role="radiogroup" aria-label="Роль пользователя">
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
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
