import { useCallback, useEffect, useMemo, useState } from "react";
import type { Api } from "./api";
import { getApi } from "./api";
import { DEMO_CLIENTS } from "./clients";
import { CardView } from "./components/CardView";
import { Feed } from "./components/Feed";
import { Home } from "./components/Home";
import { Layout } from "./components/Layout";
import { DEMO_ROLES } from "./labels";
import type { InsightCard, UserRole } from "./types";

type View = { name: "home" } | { name: "feed" } | { name: "card"; id: string };

function parseHash(): View {
  const h = location.hash.replace(/^#\/?/, "");
  if (h === "feed") return { name: "feed" };
  if (h.startsWith("card/")) return { name: "card", id: decodeURIComponent(h.slice(5)) };
  return { name: "home" };
}

const go = (path: string) => {
  location.hash = `#/${path}`;
};

// Просмотренные карточки — удобство одного браузера, хранилище может быть недоступно
function readSeen(key: string): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function writeSeen(key: string, seen: Set<string>) {
  try {
    localStorage.setItem(key, JSON.stringify([...seen]));
  } catch {
    /* без хранилища счётчик просто не запоминается */
  }
}

// Выбранные в демо компания и роль переживают перезагрузку страницы
function readChoice(): { companyId: string; role: UserRole } {
  const fallback = { companyId: DEMO_CLIENTS[0].companyId, role: DEMO_CLIENTS[0].defaultRole };
  try {
    const saved = JSON.parse(localStorage.getItem("demo:choice") ?? "null");
    const known = DEMO_CLIENTS.some((c) => c.companyId === saved?.companyId);
    return known && DEMO_ROLES.includes(saved.role) ? saved : fallback;
  } catch {
    return fallback;
  }
}

export function App() {
  const [api, setApi] = useState<Api | null>(null);
  const [companyId, setCompanyId] = useState(() => readChoice().companyId);
  const [role, setRole] = useState<UserRole>(() => readChoice().role);
  const [cards, setCards] = useState<InsightCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(parseHash);
  const seenKey = `seen:${companyId}:${role}`;
  const [seen, setSeen] = useState<Set<string>>(() => readSeen(seenKey));
  const [opened, setOpened] = useState<InsightCard | null>(null);

  useEffect(() => {
    getApi().then(setApi);
    const onHash = () => setView(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Смена компании или роли — новая сессия и новая лента
  useEffect(() => {
    if (!api) return;
    let alive = true;
    try {
      localStorage.setItem("demo:choice", JSON.stringify({ companyId, role }));
    } catch {
      /* без хранилища выбор просто не запоминается */
    }
    setCards(null);
    setError(null);
    setSeen(readSeen(seenKey));
    api
      .login(companyId, role)
      .then(() => api.feed())
      .then((c) => alive && setCards(c))
      .catch((e: Error) => alive && (setError(e.message), setCards([])));
    return () => {
      alive = false;
    };
  }, [api, companyId, role, seenKey]);

  // Карточка, которой нет в ленте (прямая ссылка), догружается отдельно
  const cardId = view.name === "card" ? view.id : null;
  useEffect(() => {
    if (!api || !cardId || cards === null) return;
    const local = cards.find((c) => c.card_id === cardId);
    if (local) {
      setOpened(local);
    } else {
      setOpened(null);
      api.card(cardId).then(setOpened, (e: Error) => setError(e.message));
    }
    setSeen((prev) => {
      const next = new Set(prev).add(cardId);
      writeSeen(seenKey, next);
      return next;
    });
  }, [api, cardId, cards, seenKey]);

  const unseen = useMemo(
    () => new Set((cards ?? []).map((c) => c.card_id).filter((id) => !seen.has(id))),
    [cards, seen],
  );

  const onCompany = useCallback((id: string) => {
    setCompanyId(id);
    setRole(DEMO_CLIENTS.find((c) => c.companyId === id)?.defaultRole ?? "cfo");
    go("");
  }, []);
  const onRole = useCallback((r: UserRole) => {
    setRole(r);
    if (parseHash().name === "card") go("feed");
  }, []);
  const open = useCallback((id: string) => go(`card/${encodeURIComponent(id)}`), []);

  return (
    <Layout companyId={companyId} role={role} onCompany={onCompany} onRole={onRole} onHome={() => go("")}>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {view.name === "home" && <Home cards={cards} unseen={unseen} onOpen={open} onFeed={() => go("feed")} />}
      {view.name === "feed" && <Feed cards={cards} unseen={unseen} onOpen={open} />}
      {view.name === "card" &&
        (opened?.card_id === view.id ? (
          <CardView card={opened} onBack={() => go("feed")} />
        ) : (
          !error && <div className="muted">Загрузка…</div>
        ))}
    </Layout>
  );
}
