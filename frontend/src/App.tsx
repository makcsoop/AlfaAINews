import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Api } from "./api";
import { errorText, getApi } from "./api";
import { DEMO_CLIENTS } from "./clients";
import { Brief } from "./components/Brief";
import { CardView } from "./components/CardView";
import { FaqPage } from "./components/Faq";
import { Feed } from "./components/Feed";
import type { FeedFilter } from "./components/Feed";
import { Home } from "./components/Home";
import { HowItWorks } from "./components/HowItWorks";
import { SkeletonRows, whyFeatures } from "./components/bits";
import { IconAlert } from "./components/icons";
import { Layout } from "./components/Layout";
import type { Place } from "./components/Layout";
import { PasteBox } from "./components/PasteBox";
import { ProductHeader } from "./components/ProductNav";
import type { Tab } from "./components/ProductNav";
import { RunsView } from "./components/RunsView";
import { ToastProvider, useToast } from "./components/Toast";
import { Tour } from "./components/Tour";
import { TraceView } from "./components/TraceView";
import { DEMO_ROLES, ROLE_LABELS } from "./labels";
import { go } from "./nav";
import { completeStep, setToured, useOnboarding } from "./onboarding";
import type { Theme } from "./theme";
import { applyTheme, savedTheme, systemTheme } from "./theme";
import type { InsightCard, UserRole } from "./types";

// Догрузка глубоких разборов: перечитывать ленту раз в 10 с, не больше 6 раз за открытие ленты
const FEED_REFRESH_MS = 10000;
const FEED_REFRESH_TRIES = 6;
// «Не про нас» из ленты уходит в отклик не сразу: пока висит уведомление, событие можно вернуть
const HIDE_UNDO_MS = 5000;

type View =
  | { name: "home" }
  | { name: "brief" }
  | { name: "feed"; filter?: FeedFilter; channel?: string }
  | { name: "runs" }
  | { name: "how" }
  | { name: "faq" }
  | { name: "card"; id: string }
  | { name: "trace"; id: string };

function parseHash(): View {
  const h = location.hash.replace(/^#\/?/, "");
  if (h === "brief" || h === "runs" || h === "how" || h === "faq") return { name: h };
  if (h === "feed") return { name: "feed" };
  if (h.startsWith("feed/p/")) return { name: "feed", filter: h.slice(7) as FeedFilter };
  if (h.startsWith("feed/ch/")) return { name: "feed", channel: decodeURIComponent(h.slice(8)) };
  if (h.startsWith("card/")) return { name: "card", id: decodeURIComponent(h.slice(5)) };
  if (h.startsWith("trace/")) return { name: "trace", id: decodeURIComponent(h.slice(6)) };
  return { name: "home" };
}

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
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}

function Shell() {
  const [api, setApi] = useState<Api | null>(null);
  const [companyId, setCompanyId] = useState(() => readChoice().companyId);
  const [role, setRole] = useState<UserRole>(() => readChoice().role);
  const [cards, setCards] = useState<InsightCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>(parseHash);
  const seenKey = `seen:${companyId}:${role}`;
  const [seen, setSeen] = useState<Set<string>>(() => readSeen(seenKey));
  const [opened, setOpened] = useState<InsightCard | null>(null);
  // скрытые «не про нас» — до смены компании или роли
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  // входы идут строго по очереди: при быстром переключении последней ставится cookie выбранной пары
  const loginChain = useRef<Promise<unknown>>(Promise.resolve());
  // пара «компания:роль», под которой уже выполнен вход: журнал и трасса до входа получили бы 401
  const [signedIn, setSignedIn] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(() => savedTheme() ?? systemTheme());
  const [tour, setTour] = useState(false);
  const onboarding = useOnboarding();
  const toast = useToast();

  useEffect(() => {
    getApi().then(setApi);
    const onHash = () => {
      setView(parseHash());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Тема без явного выбора следует за системой
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const follow = () => {
      if (!savedTheme()) setTheme(systemTheme());
    };
    media.addEventListener("change", follow);
    return () => media.removeEventListener("change", follow);
  }, []);
  useEffect(() => applyTheme(theme, false), [theme]);
  const toggleTheme = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      applyTheme(next, true);
      return next;
    });
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
    setSignedIn(null);
    setHidden(new Set());
    setSeen(readSeen(seenKey));
    // устаревший выбор (уже переключили дальше) не входит и не читает ленту
    const run = loginChain.current.then(() =>
      alive
        ? api.login(companyId, role).then(() => {
            if (!alive) return null;
            setSignedIn(`${companyId}:${role}`);
            return api.feed();
          })
        : null,
    );
    loginChain.current = run.catch(() => undefined);
    run
      .then((c) => alive && c && setCards(c))
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
    let alive = true; // ответ опроса прежней пары после переключения не записываем
    const timer = setInterval(() => {
      tries += 1;
      api.feed().then(
        (fresh) => {
          if (!alive) return;
          setCards(fresh);
          setOpened((cur) => (cur ? (fresh.find((c) => c.card_id === cur.card_id) ?? cur) : cur));
          if (!fresh.some((c) => c.stage === "fast") || tries >= FEED_REFRESH_TRIES) clearInterval(timer);
        },
        () => clearInterval(timer),
      );
    }, FEED_REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [api, hasFast, companyId, role]);

  const visible = useMemo(() => cards && cards.filter((c) => !hidden.has(c.card_id)), [cards, hidden]);
  const unseen = useMemo(
    () => new Set((visible ?? []).map((c) => c.card_id).filter((id) => !seen.has(id))),
    [visible, seen],
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
  // «Не про нас» из ленты: событие пропадает сразу, отклик уходит, если его не вернули за HIDE_UNDO_MS
  const hide = useCallback(
    (c: InsightCard) => {
      if (!api) return;
      setHidden((h) => new Set(h).add(c.card_id));
      const timer = setTimeout(() => {
        api.feedback(c.card_id, "not_relevant").catch(() => {});
        completeStep("rate");
      }, HIDE_UNDO_MS);
      toast(
        "Скрыли событие — похожие будем показывать реже",
        {
          label: "Вернуть",
          onClick: () => {
            clearTimeout(timer);
            setHidden((h) => {
              const next = new Set(h);
              next.delete(c.card_id);
              return next;
            });
          },
        },
        HIDE_UNDO_MS,
      );
    },
    [api, toast],
  );
  const titles = useMemo(() => new Map((cards ?? []).map((c) => [c.event.story_id, c.event.title])), [cards]);

  // Знакомство показываем при первом входе в сервис
  const inProduct = view.name !== "home";
  useEffect(() => {
    if (inProduct && !onboarding.toured) setTour(true);
  }, [inProduct, onboarding.toured]);
  const closeTour = useCallback(() => {
    setTour(false);
    setToured();
  }, []);

  const client = DEMO_CLIENTS.find((c) => c.companyId === companyId);
  const tab: Tab | null =
    view.name === "brief" || view.name === "feed" || view.name === "runs" || view.name === "how" || view.name === "faq"
      ? view.name
      : null;
  const place: Place | null = view.name === "home" ? "" : view.name === "brief" || view.name === "feed" || view.name === "faq" ? view.name : null;
  const paste = api && <PasteBox api={api} onCard={onAnalyzed} />;

  return (
    <Layout
      companyId={companyId}
      role={role}
      section={view.name === "home" ? "home" : "insights"}
      place={place}
      unseen={unseen.size}
      theme={theme}
      onCompany={onCompany}
      onRole={onRole}
      onGo={(p: Place) => go(p)}
      onTheme={toggleTheme}
    >
      {error && (
        <div className="error" role="alert">
          <IconAlert size={18} />
          {error}
        </div>
      )}
      {view.name === "home" && <Home cards={visible} unseen={unseen} onOpen={open} onTour={() => setTour(true)} />}
      {tab && (
        <div className="view" key={tab}>
          <ProductHeader
            tab={tab}
            company={client?.title ?? companyId}
            role={ROLE_LABELS[role] ?? role}
            count={visible?.length ?? null}
            fresh={unseen.size}
          />
          {view.name === "brief" && <Brief cards={visible} unseen={unseen} onOpen={open} onHide={hide} paste={paste} />}
          {view.name === "feed" && (
            <Feed
              key={`${view.filter ?? ""}:${view.channel ?? ""}`}
              cards={visible}
              unseen={unseen}
              onOpen={open}
              onHide={hide}
              initial={view.filter}
              channel={view.channel}
            />
          )}
          {view.name === "runs" &&
            api &&
            (signedIn ? (
              <RunsView key={signedIn} api={api} onTrace={openTrace} titles={titles} />
            ) : (
              <div className="block">
                <SkeletonRows />
              </div>
            ))}
          {view.name === "how" && <HowItWorks onTour={() => setTour(true)} />}
          {view.name === "faq" && <FaqPage />}
        </div>
      )}
      {view.name === "trace" &&
        api &&
        (signedIn ? (
          <TraceView key={signedIn} api={api} traceId={view.id} onBack={() => history.back()} />
        ) : (
          <div className="block">
            <SkeletonRows />
          </div>
        ))}
      {view.name === "card" &&
        (opened?.card_id === view.id && api ? (
          <CardView key={opened.card_id} card={opened} api={api} onBack={() => go("feed")} onTrace={openTrace} />
        ) : (
          !error && (
            <div className="block">
              <SkeletonRows rows={4} />
            </div>
          )
        ))}
      {tour && (
        <Tour
          company={client?.title ?? "ваша компания"}
          features={visible?.[0] ? whyFeatures(visible[0]) : []}
          onClose={closeTour}
        />
      )}
    </Layout>
  );
}
