// Разбор события как аналитическая записка: шапка с механизмом влияния, разделы по 6 вопросам ТЗ,
// утверждения абзацами — метка на полях, сноски на цитаты и итог проверки кодом; справа — значимость и оглавление.
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { Api } from "../api";
import {
  CLAIM_LABELS,
  CONFIDENCE_LABELS,
  STORY_STATUS_LABELS,
  evidenceDoc,
  evidenceLabel,
  featureLabel,
  sourceLabel,
  sourceName,
} from "../labels";
import { completeStep } from "../onboarding";
import type { Claim, ClaimChecks, Condition, Confidence, Evidence, FeedbackKind, InsightCard, Surprise } from "../types";
import { AskPanel } from "./AskPanel";
import {
  ClaimTag,
  ContributionChart,
  FoundationMeter,
  Legend,
  PolarityMark,
  StageFlags,
  formatDate,
  mainChannel,
  safeUrl,
  topContributions,
  useScrollTarget,
} from "./bits";
import { FeedbackBar } from "./FeedbackBar";
import { AlfaMark, IconAlert, IconBack, IconCheck, IconClock, IconExternal, IconInfo, IconLayers } from "./icons";
import { Mechanism } from "./StoryRow";

const SOURCES_SHOWN = 5;
const CONFIDENCE_LEVEL: Record<Confidence, number> = { low: 1, medium: 2, high: 3 };

// Отметки из глубины карточки и открытая сноска — без проброса через все уровни
interface Ctx {
  mark: (kind: FeedbackKind, claimId?: string) => void;
  noteNo: Map<Evidence, number>;
  open: number | null;
  setOpen: (n: number | null) => void;
}
const CardCtx = createContext<Ctx>({ mark: () => {}, noteNo: new Map(), open: null, setOpen: () => {} });

interface Props {
  card: InsightCard;
  api: Api;
  onBack: () => void;
  onTrace: (traceId: string) => void;
}

interface Part {
  id: string;
  title: string;
  body: ReactNode;
}

export function CardView({ card, api, onBack, onTrace }: Props) {
  const [open, setOpen] = useState<number | null>(null);
  const byId = new Map(card.claims.map((c) => [c.claim_id, c]));
  const pick = (ids: string[], skip: Set<string> = new Set()) =>
    ids.filter((id) => !skip.has(id)).flatMap((id) => byId.get(id) ?? []);
  const what = pick(card.what_happened);
  const why = pick(card.why_it_matters);
  const consequences = pick(card.consequences, new Set(card.why_it_matters));
  const bankMarket = pick(card.bank_and_market);
  const conditions: Condition[] = card.conditions?.length ? card.conditions : card.what_would_change.map((text) => ({ text }));
  const contribs = topContributions(card);
  // отметка пишется в журнал; ошибка отметки не мешает читать карточку
  const mark = (kind: FeedbackKind, claimId?: string) => {
    api.feedback(card.card_id, kind, claimId).catch(() => {});
  };

  // Сноски: один номер на документ в порядке появления в тексте; цитаты одного документа — в одной сноске
  const notes: Evidence[][] = [];
  const noteNo = new Map<Evidence, number>();
  const docNo = new Map<string, number>();
  for (const c of [...what, ...why, ...consequences, ...bankMarket]) {
    for (const e of c.evidence) {
      if (noteNo.has(e)) continue;
      const doc = evidenceDoc(e);
      let n = docNo.get(doc);
      if (n === undefined) {
        notes.push([]);
        n = notes.length;
        docNo.set(doc, n);
      }
      const group = notes[n - 1];
      if (!group.some((x) => x.quote === e.quote)) group.push(e);
      noteNo.set(e, n);
    }
  }

  // Разделы по вопросам ТЗ; пустые не показываем
  const parts: Part[] = [];
  if (what.length > 0) parts.push({ id: "what", title: "Что произошло", body: <Notes claims={what} byId={byId} /> });
  parts.push({
    id: "why",
    title: "Почему важно вам",
    body: (
      <>
        <Notes claims={why} byId={byId} />
        {contribs.length > 0 && (
          <div className="why-see">
            <div className="why-see-head">
              <h3>Почему вы это видите</h3>
              <span>вклад факторов в значимость {Math.round(card.relevance.score * 100)} из 100</span>
            </div>
            <ContributionChart contribs={contribs} />
          </div>
        )}
      </>
    ),
  });
  if (consequences.length > 0)
    parts.push({ id: "consequences", title: "Последствия", body: <Notes claims={consequences} byId={byId} /> });
  if (card.actions.length > 0) parts.push({ id: "actions", title: "Что сделать", body: <Actions card={card} byId={byId} /> });
  if (bankMarket.length > 0 || card.surprise)
    parts.push({
      id: "market",
      title: "Как оценивают банк и рынок",
      body: (
        <>
          {card.surprise && <Expectations s={card.surprise} />}
          <Notes claims={bankMarket} byId={byId} />
        </>
      ),
    });
  parts.push({
    id: "basis",
    title: "На чём основан вывод",
    body: (
      <>
        <div className="confidence">
          <ConfidenceMeter level={card.confidence} />
          <strong>{CONFIDENCE_LABELS[card.confidence]}</strong>
        </div>
        <ul className="reasons">
          {card.confidence_reasons.map((r) => (
            <li key={r}>
              <IconCheck size={18} />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      </>
    ),
  });
  if (conditions.length > 0)
    parts.push({
      id: "change",
      title: "Что изменит вывод",
      body: (
        <ul className="conditions">
          {conditions.map((c) => (
            <li key={c.text}>
              {c.kind && <ClaimTag type={c.kind} />}
              <p>{c.text}</p>
              <Refs refs={c.refs} byId={byId} />
            </li>
          ))}
        </ul>
      ),
    });
  if (notes.length > 0) parts.push({ id: "sources", title: "Источники и цитаты", body: <SourceList notes={notes} /> });

  const activeId = useActiveSection(parts.map((p) => p.id));
  const scrollTo = (id: string) => document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  useScrollTarget(true);
  useEffect(() => completeStep("open"), []); // шаг знакомства «откройте событие»

  // открытая сноска закрывается кликом мимо и Esc
  useEffect(() => {
    if (open === null) return;
    const close = () => setOpen(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const channel = mainChannel(card);
  return (
    <CardCtx.Provider value={{ mark, noteNo, open, setOpen }}>
      <ReadingProgress />
      <div className="view article">
        <div className="crumbs">
          <button className="btn btn-ghost" onClick={onBack}>
            <IconBack size={18} />
            Лента
          </button>
          {card.trace_id && (
            <button className="btn" onClick={() => onTrace(card.trace_id!)}>
              <IconLayers size={18} />
              Как это получено
            </button>
          )}
        </div>

        <header className="article-head">
          <div className="kicker">
            <PolarityMark polarity={card.relevance.polarity} />
            {channel && <span className="kicker-item">{channel}</span>}
            <span className="kicker-item">{formatDate(card.event.first_seen)}</span>
            <span className="kicker-item">{STORY_STATUS_LABELS[card.event.status] ?? card.event.status}</span>
            <StageFlags card={card} />
          </div>
          <h1>{card.headline || card.event.title}</h1>
          {card.headline && card.headline !== card.event.title && <p className="article-dek">{card.event.title}</p>}
          <Sources card={card} />
          {card.stage === "fast" && (
            <div className="callout" role="status">
              <IconClock size={20} />
              <span>
                Быстрая оценка по профилю компании. Если событие окажется среди самых значимых, глубокий разбор догрузится
                в эту карточку сам.
              </span>
            </div>
          )}
          {card.review_status === "on_review" && (
            <div className="callout callout-review" role="status">
              <IconInfo size={20} />
              <span>Аналитики разошлись в оценке — вывод на проверке у эксперта банка. Факты и источники проверены.</span>
            </div>
          )}
          <Mechanism card={card} />
        </header>

        <div className="article-grid">
          <div className="article-body">
            {parts.map((p) => (
              <section key={p.id} id={`sec-${p.id}`} className="art-sec" aria-labelledby={`h-${p.id}`}>
                <h2 id={`h-${p.id}`}>{p.title}</h2>
                {p.body}
              </section>
            ))}
            <FeedbackBar key={card.card_id} onMark={mark} />
            <AskPanel key={`ask-${card.card_id}`} api={api} card={card} />
          </div>

          <aside className="article-rail" aria-label="Значимость и содержание">
            <div className="rail-score">
              <span className="rail-k">Значимость для компании</span>
              <div className="rail-num">
                <b>{Math.round(card.relevance.score * 100)}</b>
                <span>из 100</span>
              </div>
              <FoundationMeter score={card.relevance.score} />
              <div className="confidence">
                <ConfidenceMeter level={card.confidence} />
                <span>{CONFIDENCE_LABELS[card.confidence]}</span>
              </div>
            </div>
            <nav className="toc" aria-label="Разделы разбора">
              {parts.map((p) => (
                <button key={p.id} className={activeId === p.id ? "on" : undefined} onClick={() => scrollTo(p.id)}>
                  {p.title}
                </button>
              ))}
            </nav>
            <details className="rail-legend">
              <summary>Как читать метки</summary>
              <Legend />
            </details>
          </aside>
        </div>
      </div>
    </CardCtx.Provider>
  );
}

// Тонкая красная полоса вверху: сколько разбора прочитано
function ReadingProgress() {
  const [p, setP] = useState(0);
  useEffect(() => {
    const on = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      setP(max > 0 ? Math.min(1, h.scrollTop / max) : 0);
    };
    on();
    window.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("scroll", on);
      window.removeEventListener("resize", on);
    };
  }, []);
  return <div className="read-progress" style={{ transform: `scaleX(${p})` }} aria-hidden="true" />;
}

// Раздел, который сейчас в верхней части экрана, — подсветка в оглавлении
function useActiveSection(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(ids[0] ?? null);
  const key = ids.join("|");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const els = key.split("|").flatMap((id) => document.getElementById(`sec-${id}`) ?? []);
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id.slice(4));
      },
      { rootMargin: "-90px 0px -55% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [key]);
  return active;
}

function ConfidenceMeter({ level }: { level: Confidence }) {
  const n = CONFIDENCE_LEVEL[level];
  return (
    <span className="conf-meter" aria-hidden="true">
      {[1, 2, 3].map((i) => (
        <i key={i} className={i <= n ? "on" : undefined} />
      ))}
    </span>
  );
}

// Ссылки действия или условия: утверждение карточки — его метка и начало текста, иначе источник досье
function Refs({ refs, byId }: { refs?: string[]; byId: Map<string, Claim> }) {
  if (!refs || refs.length === 0) return null;
  const label = (id: string) => {
    const c = byId.get(id);
    return c ? `${CLAIM_LABELS[c.type].toLowerCase()} «${c.text.slice(0, 48)}…»` : sourceLabel(id);
  };
  return <p className="basis">На основе: {[...new Set(refs.map(label))].join(" · ")}</p>;
}

function Sources({ card }: { card: InsightCard }) {
  const { mark } = useContext(CardCtx);
  const [all, setAll] = useState(false);
  // официальные и агентства — первыми; в сюжете решения ЦБ больше 20 источников, на телефоне это экран
  const seen = new Set<string>();
  const sorted = [...card.event.sources]
    .sort((a, b) => a.tier - b.tier)
    .filter((s) => {
      const name = sourceName(s.source_id, s.title);
      if (seen.has(name)) return false; // «Банк России»: пресс-релиз, события и канал — одно имя
      seen.add(name);
      return true;
    });
  const shown = all ? sorted : sorted.slice(0, SOURCES_SHOWN);
  const hidden = sorted.length - shown.length;
  return (
    <p className="article-sources">
      <span className="muted">Источники: </span>
      {shown.map((s, i) => {
        const href = safeUrl(s.url);
        const label = sourceName(s.source_id, s.title);
        return (
          <span key={s.source_id}>
            {i > 0 && ", "}
            {href ? (
              <a href={href} title={s.title ?? undefined} target="_blank" rel="noopener noreferrer" onClick={() => mark("click_source")}>
                {label}
              </a>
            ) : (
              <span title={s.title ?? undefined}>{label}</span>
            )}
          </span>
        );
      })}
      {hidden > 0 && (
        <>
          {" "}
          <button type="button" className="link" onClick={() => setAll(true)}>
            и ещё {hidden}
          </button>
        </>
      )}
      <span className="muted"> · независимых: {card.event.corroboration}</span>
    </p>
  );
}

function Notes({ claims, byId }: { claims: Claim[]; byId: Map<string, Claim> }) {
  if (claims.length === 0) return null;
  return (
    <ul className="notes">
      {claims.map((c) => (
        <Note key={c.claim_id} claim={c} byId={byId} />
      ))}
    </ul>
  );
}

const CHECK_NAMES: Record<string, string> = {
  refs: "ссылки",
  label: "метка",
  numbers: "числа",
  dates: "даты",
  entailment: "вывод из цитаты",
};

// Итог проверки кодом: все проверки пройдены — «проверено», иначе — что именно не прошло
export function CheckBadge({ checks }: { checks: ClaimChecks }) {
  const entries = Object.entries(checks ?? {});
  if (entries.length === 0)
    return (
      <span className="check check-no" title="Код не проверял это утверждение">
        не проверено
      </span>
    );
  const failed = entries.filter(([, v]) => !(v === true || v === "yes")).map(([k]) => CHECK_NAMES[k] ?? k);
  return failed.length === 0 ? (
    <span className="check check-ok" title={`Проверено кодом: ${entries.map(([k]) => CHECK_NAMES[k] ?? k).join(", ")}`}>
      <IconCheck />
      проверено
    </span>
  ) : (
    <span className="check check-no">
      <IconAlert />
      не проверено: {failed.join(", ")}
    </span>
  );
}

function Note({ claim, byId }: { claim: Claim; byId: Map<string, Claim> }) {
  const { mark, noteNo } = useContext(CardCtx);
  const [wrong, setWrong] = useState(false);
  const facts = [...(claim.based_on.facts ?? []), ...(claim.based_on.bank_views ?? [])];
  const features = claim.based_on.client_features ?? [];
  return (
    <li className={`note note-${claim.type}`}>
      <div className="note-label">
        <ClaimTag type={claim.type} />
        <CheckBadge checks={claim.verified} />
      </div>
      <div className="note-body">
        <p className="note-text">
          {claim.text}
          {claim.evidence
            .filter((e, i, all) => all.findIndex((x) => noteNo.get(x) === noteNo.get(e)) === i)
            .map((e, i) => (
              <FootRef key={`${e.source_id}-${i}`} e={e} />
            ))}
        </p>
        {(facts.length > 0 || features.length > 0) && (
          <p className="basis">
            На основе:{" "}
            {facts.map((id, i) => {
              const c = byId.get(id);
              const nums = [...new Set(c?.evidence.map((e) => noteNo.get(e)).filter(Boolean) ?? [])];
              return (
                <span key={id}>
                  {i > 0 && " · "}
                  {c ? CLAIM_LABELS[c.type].toLowerCase() : id}
                  {nums.length > 0 ? <sup>{nums.join(",")}</sup> : c && ` «${c.text.slice(0, 40)}…»`}
                </span>
              );
            })}
            {facts.length > 0 && features.length > 0 && " · "}
            {features.map(featureLabel).join(" · ")}
          </p>
        )}
        <div className="note-foot">
          {claim.horizon && <span>Горизонт: {claim.horizon}</span>}
          {wrong ? (
            <span>Отмечено как неверное — спасибо</span>
          ) : (
            <button
              className="note-flag"
              onClick={() => {
                setWrong(true);
                mark("wrong", claim.claim_id);
              }}
            >
              Неверно?
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

// Сноска: номер в тексте, по нажатию — цитата, источник и итог сверки
function FootRef({ e }: { e: Evidence }) {
  const { mark, noteNo, open, setOpen } = useContext(CardCtx);
  const n = noteNo.get(e) ?? 0;
  const href = safeUrl(e.url);
  const isOpen = open === n;
  return (
    <span className="fn">
      <button
        className={isOpen ? "fn-btn on" : "fn-btn"}
        aria-expanded={isOpen}
        aria-label={`Источник ${n}: ${evidenceLabel(e)}`}
        onClick={(ev) => {
          ev.stopPropagation();
          setOpen(isOpen ? null : n);
        }}
      >
        {n}
      </button>
      {isOpen && (
        <span className="fn-pop" role="dialog" aria-label={`Источник ${n}`} onClick={(ev) => ev.stopPropagation()}>
          {e.quote && <span className="fn-q">«{e.quote}»</span>}
          <span className="fn-src">
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" onClick={() => mark("click_source")}>
                {evidenceLabel(e)}
                <IconExternal size={14} />
              </a>
            ) : (
              <b>{evidenceLabel(e)}</b>
            )}
            {e.doc_date && <span>{formatDate(e.doc_date)}</span>}
          </span>
          <EvidenceCheck e={e} />
        </span>
      )}
    </span>
  );
}

function EvidenceCheck({ e }: { e: Evidence }) {
  if (e.verified === true)
    return (
      <span className="check check-ok">
        <IconCheck />
        цитата найдена в источнике
      </span>
    );
  if (e.verified === false) return <span className="check check-no">цитата не найдена в источнике</span>;
  return null;
}

function SourceList({ notes }: { notes: Evidence[][] }) {
  const { mark } = useContext(CardCtx);
  return (
    <ol className="refs">
      {notes.map((group, i) => {
        const e = group[0];
        const href = safeUrl(group.find((x) => x.url)?.url);
        return (
          <li key={`${evidenceDoc(e)}-${i}`}>
            <span className="refs-n">{i + 1}</span>
            <div>
              {group.map((x, k) => x.quote && <p key={k}>«{x.quote}»</p>)}
              <span className="refs-src">
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" onClick={() => mark("click_source")}>
                    {evidenceLabel(e)}
                    <IconExternal size={14} />
                  </a>
                ) : (
                  <b>{evidenceLabel(e)}</b>
                )}
                {e.doc_date && <span>{formatDate(e.doc_date)}</span>}
                <EvidenceCheck e={e} />
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// «Что сделать»: чек-лист шагов (отметки — только в этом браузере) и отдельно — предложение банка
function readDone(key: string): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

function Actions({ card, byId }: { card: InsightCard; byId: Map<string, Claim> }) {
  const key = `todo:${card.card_id}`;
  const [done, setDone] = useState<Set<string>>(() => readDone(key));
  const toggle = (text: string) =>
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(text)) next.delete(text);
      else next.add(text);
      try {
        localStorage.setItem(key, JSON.stringify([...next]));
      } catch {
        /* без хранилища отметки держатся до перезагрузки */
      }
      return next;
    });
  const plain = card.actions.filter((a) => !a.is_advertising);
  const offers = card.actions.filter((a) => a.is_advertising);
  return (
    <>
      {plain.length > 0 && (
        <ul className="todo">
          {plain.map((a) => (
            <li key={a.text} className={done.has(a.text) ? "done" : undefined}>
              <label>
                <input type="checkbox" checked={done.has(a.text)} onChange={() => toggle(a.text)} />
                <span className="todo-box" aria-hidden="true">
                  <IconCheck size={14} />
                </span>
                <span className="todo-text">{a.text}</span>
              </label>
              <Refs refs={a.refs} byId={byId} />
            </li>
          ))}
        </ul>
      )}
      {plain.length > 0 && <p className="todo-note">Отмечайте сделанное — отметки видны только вам.</p>}
      {offers.map((a) => (
        <div key={a.text} className="offer">
          <span className="offer-mark" aria-hidden="true">
            <AlfaMark />
          </span>
          <div>
            <ClaimTag type="bank_offer" />
            <p>{a.text}</p>
          </div>
        </div>
      ))}
    </>
  );
}

// «Ждали — вышло»: точки ожиданий и итога на одной шкале
function Expectations({ s }: { s: Surprise }) {
  const fmt = (v: number) => `${v.toLocaleString("ru-RU")}${s.unit ?? ""}`;
  const points = [
    { k: "Рынок ждал", v: s.market_expected, actual: false },
    { k: "Банк ждал", v: s.bank_expected, actual: false },
    { k: "Вышло", v: s.actual, actual: true },
  ];
  const known = points.filter((p): p is { k: string; v: number; actual: boolean } => p.v != null);
  const groups = new Map<number, { v: number; labels: string[]; actual: boolean }>();
  for (const p of known) {
    const g = groups.get(p.v) ?? { v: p.v, labels: [], actual: false };
    g.labels.push(p.k);
    g.actual ||= p.actual;
    groups.set(p.v, g);
  }
  let lo = Math.min(...known.map((p) => p.v));
  let hi = Math.max(...known.map((p) => p.v));
  if (hi - lo < 1e-9) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.3;
  lo -= pad;
  hi += pad;
  const verdict =
    s.bank_expected != null && s.bank_expected === s.actual
      ? "Итог совпал с прогнозом аналитиков Альфа-Банка"
      : s.direction === "in_line"
        ? "Итог в рамках ожиданий"
        : s.direction === "above"
          ? "Итог выше ожиданий"
          : s.direction === "below"
            ? "Итог ниже ожиданий"
            : null;
  return (
    <div className="expect">
      {known.length > 1 && (
        <div className="expect-scale" aria-hidden="true">
          <span className="expect-line" />
          {[...groups.values()].map((g) => (
            <span key={g.v} className={g.actual ? "expect-pt actual" : "expect-pt"} style={{ left: `${((g.v - lo) / (hi - lo)) * 100}%` }}>
              <b>{fmt(g.v)}</b>
              <i />
              <span>{g.labels.join(" · ")}</span>
            </span>
          ))}
        </div>
      )}
      <dl className="expect-list">
        {points.map((p) => (
          <div key={p.k} className={p.actual ? "actual" : undefined}>
            <dt>{p.k}</dt>
            <dd>{p.v == null ? "нет данных" : fmt(p.v)}</dd>
          </div>
        ))}
      </dl>
      {verdict && <p className="expect-verdict">{verdict}</p>}
    </div>
  );
}
