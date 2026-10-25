// Светлая или тёмная тема: выбор пользователя в браузере, без выбора — как в системе.
// Атрибут ставит ещё скрипт в index.html, чтобы страница не мигала при загрузке.
export type Theme = "light" | "dark";

const KEY = "ui:theme";

export function savedTheme(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme, remember: boolean) {
  document.documentElement.dataset.theme = theme;
  if (!remember) return;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* без хранилища тема держится до перезагрузки */
  }
}
