import { useCallback, useEffect, useMemo, useState } from "react";
import type { Api } from "./api";
import { errorText, getApi } from "./api";
import { DEMO_CLIENTS } from "./clients";
import { CardView } from "./components/CardView";
import { Feed } from "./components/Feed";
import { Home } from "./components/Home";
import { Layout } from "./components/Layout";
import { MetricsView } from "./components/MetricsView";
import { PasteBox } from "./components/PasteBox";
import { RunsView } from "./components/RunsView";
import { TraceView } from "./components/TraceView";
import { DEMO_ROLES } from "./labels";
import type { InsightCard, UserRole } from "./types";

// Догрузка глубоких разборов: перечитывать ленту раз в 10 с, не больше 6 раз за открытие ленты
const FEED_REFRESH_MS = 10000;
const FEED_REFRESH_TRIES = 6;

type View =
  | { name: "home" }
  | { name: "feed" }
  | { name: "runs" }
  | { name: "metrics" }
  | { name: "card"; id: string }
  | { name: "trace"; id: string };

function parseHash(): View {
  const h = location.hash.replace(/^#\/?/, "");
  if (h === "feed") return { name: "feed" };
  if (h === "runs") return { name: "runs" };
  if (h === "metrics") return { name: "metrics" };
  if (h.startsWith("card/")) return { name: "card", id: decodeURIComponent(h.slice(5)) };
  if (h.startsWith("trace/")) return { name: "trace", id: decodeURIComponent(h.slice(6)) };
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
      .catch((e) => alive && (setError(errorText(e)), setCards([])));
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
      api.card(cardId).then(setOpened, (e) => setError(errorText(e)));
    }
    setSeen((prev) => {
      const next = new Set(prev).add(cardId);
      writeSeen(seenKey, next);
      return next;
    });
  }, [api, cardId, cards, seenKey]);

  // Глубокий разбор API делает в фоне, не больше FEED_DEEP_PER_REQUEST в очереди: пока в ленте есть быстрые
  // карточки, ленту перечитываем — разборы догружаются, открытая карточка обновляется вместе с лентой.
  // Попыток немного: на живых моделях каждый запрос ленты — это ₽.
  const hasFast = !!cards?.some((c) => c.stage === "fast");
  useEffect(() => {
    if (!api || !hasFast) return;
    let tries = 0;
    const timer = setInterval(() => {
      tries += 1;
      api.feed().then(
        (fresh) => {
          setCards(fresh);
          setOpened((cur) => (cur ? (fresh.find((c) => c.card_id === cur.card_id) ?? cur) : cur));
          if (!fresh.some((c) => c.stage === "fast") || tries >= FEED_REFRESH_TRIES) clearInterval(timer);
        },
        () => clearInterval(timer),
      );
    }, FEED_REFRESH_MS);
    return () => clearInterval(timer);
  }, [api, hasFast, companyId, role]);

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
  const openTrace = useCallback((id: string) => go(`trace/${encodeURIComponent(id)}`), []);
  // вставленная новость: быстрая карточка сразу в начало ленты и открывается, разбор догрузится
  const onAnalyzed = useCallback(
    (c: InsightCard) => {
      setCards((list) => [c, ...(list ?? []).filter((x) => x.card_id !== c.card_id)]);
      open(c.card_id);
    },
    [open],
  );
  const titles = useMemo(() => new Map((cards ?? []).map((c) => [c.event.story_id, c.event.title])), [cards]);

  return (
    <Layout companyId={companyId} role={role} onCompany={onCompany} onRole={onRole} onHome={() => go("")}>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {view.name === "home" && api && <PasteBox api={api} scope={`${companyId}:${role}`} onCard={onAnalyzed} />}
      {view.name === "home" && <Home cards={cards} unseen={unseen} onOpen={open} onFeed={() => go("feed")} />}
      {(view.name === "feed" || view.name === "runs" || view.name === "metrics") && (
        <nav className="tabs" aria-label="Разделы">
          <button className={view.name === "feed" ? "tab on" : "tab"} onClick={() => go("feed")}>
            Лента
          </button>
          <button className={view.name === "runs" ? "tab on" : "tab"} onClick={() => go("runs")}>
            Журнал разборов
          </button>
          <button className={view.name === "metrics" ? "tab on" : "tab"} onClick={() => go("metrics")}>
            Метрики
          </button>
        </nav>
      )}
      {view.name === "feed" && api && <PasteBox api={api} scope={`${companyId}:${role}`} onCard={onAnalyzed} />}
      {view.name === "feed" && <Feed cards={cards} unseen={unseen} onOpen={open} />}
      {view.name === "metrics" && api && <MetricsView api={api} />}
      {view.name === "runs" && api && <RunsView api={api} onTrace={openTrace} titles={titles} />}
      {view.name === "trace" && api && <TraceView api={api} traceId={view.id} onBack={() => history.back()} />}
      {view.name === "card" &&
        (opened?.card_id === view.id && api ? (
          <CardView card={opened} api={api} onBack={() => go("feed")} onTrace={openTrace} />
        ) : (
          !error && <div className="muted">Загрузка…</div>
        ))}
    </Layout>
  );
}
