// Переходы по hash-маршрутам и «куда прокрутить» после перехода (блок отклика, поле вопроса, поле ссылки).
let pending: string | null = null;

export function go(path: string, scrollTo?: string) {
  pending = scrollTo ?? null;
  const hash = `#/${path}`;
  if (location.hash === hash || (path === "" && (location.hash === "" || location.hash === "#/"))) {
    // маршрут тот же — hashchange не будет, прокручиваем сразу
    window.dispatchEvent(new Event("app:scroll"));
  } else {
    location.hash = hash;
  }
}

export function takeScrollTarget(): string | null {
  const t = pending;
  pending = null;
  return t;
}
