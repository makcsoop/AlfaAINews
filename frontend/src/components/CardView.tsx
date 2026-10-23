// Персональная карточка: разделы по 6 вопросам ТЗ, у каждого утверждения метка, источник и итог проверки кодом.
import { createContext, useContext, useState } from "react";
import type { Api } from "../api";
import {
  CLAIM_LABELS,
  CONFIDENCE_LABELS,
  STORY_STATUS_LABELS,
  featureLabel,
  sourceLabel,
  sourceName,
} from "../labels";
import type { Claim, ClaimChecks, Condition, Evidence, FeedbackKind, InsightCard, Surprise } from "../types";
import { AskPanel } from "./AskPanel";
import {
  ClaimTag,
  ContributionRow,
  PolarityChip,
  ReviewChip,
  formatDate,
  safeUrl,
  topContributions,
} from "./bits";
import { FeedbackBar } from "./FeedbackBar";

// Отметки из глубины карточки (клик по источнику, «неверно» у утверждения) — без проброса через все уровни
const Mark = createContext<(kind: FeedbackKind, claimId?: string) => void>(() => {});

interface Props {
  card: InsightCard;
  api: Api;
  onBack: () => void;
  onTrace: (traceId: string) => void;
}

export function CardView({ card, api, onBack, onTrace }: Props) {
  const byId = new Map(card.claims.map((c) => [c.claim_id, c]));
  const pick = (ids: string[], skip: Set<string> = new Set()) =>
    ids.filter((id) => !skip.has(id)).flatMap((id) => byId.get(id) ?? []);
  const why = pick(card.why_it_matters);
  const consequences = pick(card.consequences, new Set(card.why_it_matters));
  const conditions: Condition[] = card.conditions?.length ? card.conditions : card.what_would_change.map((text) => ({ text }));
  const contribs = topContributions(card);
  const max = Math.max(...contribs.map((c) => Math.abs(c.value)), 0);
  // отметка пишется в журнал; ошибка отметки не мешает читать карточку
  const mark = (kind: FeedbackKind, claimId?: string) => {
    api.feedback(card.card_id, kind, claimId).catch(() => {});
  };

  return (
    <Mark.Provider value={mark}>
      <article className="card">
        <div className="card-top">
          <button className="link back" onClick={onBack}>
            ← К ленте
          </button>
          {card.trace_id && (
            <button className="link" onClick={() => onTrace(card.trace_id!)}>
              Как это получено →
            </button>
          )}
        </div>
        <header className="card-head">
          <div className="feed-meta">
            <PolarityChip polarity={card.relevance.polarity} />
            <ReviewChip card={card} />
            <span className="muted small">
              {formatDate(card.event.first_seen)} · {STORY_STATUS_LABELS[card.event.status] ?? card.event.status}
            </span>
          </div>
          <h1>{card.headline || card.event.title}</h1>
          <Sources card={card} />
          {card.stage === "fast" && (
            <div className="banner" role="status">
              Быстрая оценка по профилю компании. Если событие окажется среди самых значимых, глубокий разбор
              догрузится в эту карточку сам.
            </div>
          )}
          {card.review_status === "on_review" && (
            <div className="banner banner-review" role="status">
              Аналитики разошлись в оценке — вывод на проверке у эксперта банка. Факты и источники проверены.
            </div>
          )}
        </header>

        <Section title="Что произошло" claims={pick(card.what_happened)} byId={byId} />

        <section className="sec">
          <h2>Почему важно вам</h2>
          <ClaimList claims={why} byId={byId} />
          {contribs.length > 0 && (
            <div className="why-box">
              <div className="muted small">
                Почему вы это видите · значимость {Math.round(card.relevance.score * 100)} из 100
              </div>
              <ul className="contribs">
                {contribs.map((c) => (
                  <ContributionRow key={c.factor} c={c} max={max} />
                ))}
              </ul>
            </div>
          )}
        </section>

        {consequences.length > 0 && <Section title="Последствия" claims={consequences} byId={byId} />}

        {card.actions.length > 0 && (
          <section className="sec">
            <h2>Что сделать</h2>
            <ul className="actions">
              {card.actions.map((a) => (
                <li key={a.text} className={a.is_advertising ? "claim offer" : "claim"}>
                  {a.is_advertising && <span className="tag tag-bank_offer">{CLAIM_LABELS.bank_offer}</span>}
                  <div className="claim-body">
                    <span>{a.text}</span>
                    {!a.is_advertising && <Refs refs={a.refs} byId={byId} />}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {(card.bank_and_market.length > 0 || card.surprise) && (
          <section className="sec">
            <h2>Как оценивают банк и рынок</h2>
            {card.surprise && <SurpriseLine s={card.surprise} />}
            <ClaimList claims={pick(card.bank_and_market)} byId={byId} />
          </section>
        )}

        <section className="sec">
          <h2>На чём основан вывод</h2>
          <p>
            <strong>{CONFIDENCE_LABELS[card.confidence]}</strong>
          </p>
          <ul className="reasons">
            {card.confidence_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>

        {conditions.length > 0 && (
          <section className="sec">
            <h2>Что изменит вывод</h2>
            <ul className="reasons">
              {conditions.map((c) => (
                <li key={c.text}>
                  {c.kind && <ClaimTag type={c.kind} />} {c.text}
                  <Refs refs={c.refs} byId={byId} />
                </li>
              ))}
            </ul>
          </section>
        )}

        <FeedbackBar key={card.card_id} onMark={mark} />
        <AskPanel key={`ask-${card.card_id}`} api={api} card={card} />
      </article>
    </Mark.Provider>
  );
}

// Ссылки действия или условия: утверждение карточки — его метка и начало текста, иначе источник досье
function Refs({ refs, byId }: { refs?: string[]; byId: Map<string, Claim> }) {
  if (!refs || refs.length === 0) return null;
  const label = (id: string) => {
    const c = byId.get(id);
    return c ? `${CLAIM_LABELS[c.type].toLowerCase()} «${c.text.slice(0, 48)}…»` : sourceLabel(id);
  };
  return <div className="muted small">На основе: {[...new Set(refs.map(label))].join(" · ")}</div>;
}

function Sources({ card }: { card: InsightCard }) {
  const mark = useContext(Mark);
  return (
    <div className="sources small">
      <span className="muted">Источники:</span>
      {card.event.sources.map((s) => {
        const href = safeUrl(s.url);
        const label = sourceName(s.source_id, s.title);
        return href ? (
          <a key={s.source_id} href={href} target="_blank" rel="noopener noreferrer" onClick={() => mark("click_source")}>
            {label}
          </a>
        ) : (
          <span key={s.source_id}>{label}</span>
        );
      })}
      <span className="muted">· независимых: {card.event.corroboration}</span>
    </div>
  );
}

function Section({ title, claims, byId }: { title: string; claims: Claim[]; byId: Map<string, Claim> }) {
  if (claims.length === 0) return null;
  return (
    <section className="sec">
      <h2>{title}</h2>
      <ClaimList claims={claims} byId={byId} />
    </section>
  );
}

function ClaimList({ claims, byId }: { claims: Claim[]; byId: Map<string, Claim> }) {
  return (
    <ul className="claims">
      {claims.map((c) => (
        <ClaimItem key={c.claim_id} claim={c} byId={byId} />
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
  if (entries.length === 0) return <span className="check check-no">не проверено</span>;
  const failed = entries.filter(([, v]) => !(v === true || v === "yes")).map(([k]) => CHECK_NAMES[k] ?? k);
  return failed.length === 0 ? (
    <span className="check check-ok" title={entries.map(([k]) => CHECK_NAMES[k] ?? k).join(", ")}>
      ✓ проверено кодом
    </span>
  ) : (
    <span className="check check-no">не проверено: {failed.join(", ")}</span>
  );
}

function ClaimItem({ claim, byId }: { claim: Claim; byId: Map<string, Claim> }) {
  const mark = useContext(Mark);
  const [wrong, setWrong] = useState(false);
  const facts = [...(claim.based_on.facts ?? []), ...(claim.based_on.bank_views ?? [])];
  const features = claim.based_on.client_features ?? [];
  return (
    <li className={`claim claim-${claim.type}`}>
      <ClaimTag type={claim.type} />
      <div className="claim-body">
        <p>{claim.text}</p>
        {claim.evidence.map((e, i) => (
          <Quote key={`${e.source_id}-${i}`} e={e} />
        ))}
        {(facts.length > 0 || features.length > 0) && (
          <div className="muted small">
            На основе:{" "}
            {[
              ...facts.map((id) => {
                const c = byId.get(id);
                return c ? `${CLAIM_LABELS[c.type].toLowerCase()} «${c.text.slice(0, 48)}…»` : id;
              }),
              ...features.map(featureLabel),
            ].join(" · ")}
          </div>
        )}
        <div className="claim-foot small">
          {claim.horizon && <span className="muted">Горизонт: {claim.horizon}</span>}
          <CheckBadge checks={claim.verified} />
          {wrong ? (
            <span className="muted">отмечено как неверное</span>
          ) : (
            <button
              className="link link-quiet"
              onClick={() => {
                setWrong(true);
                mark("wrong", claim.claim_id);
              }}
            >
              неверно?
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

function Quote({ e }: { e: Evidence }) {
  const mark = useContext(Mark);
  const href = safeUrl(e.url);
  return (
    <blockquote>
      {e.quote && <span>«{e.quote}»</span>}{" "}
      <cite>
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" onClick={() => mark("click_source")}>
            {sourceLabel(e.source_id)} ↗
          </a>
        ) : (
          sourceLabel(e.source_id)
        )}
        {e.doc_date && `, ${formatDate(e.doc_date)}`}
      </cite>
      {e.verified === true && <span className="check check-ok"> · цитата найдена в источнике</span>}
      {e.verified === false && <span className="check check-no"> · цитата не найдена</span>}
    </blockquote>
  );
}

function SurpriseLine({ s }: { s: Surprise }) {
  const fmt = (v?: number | null) => (v == null ? "нет данных" : `${v.toLocaleString("ru-RU")}${s.unit ?? ""}`);
  return (
    <div className="surprise">
      <div>
        <div className="muted small">Рынок ждал</div>
        <strong>{fmt(s.market_expected)}</strong>
      </div>
      <div>
        <div className="muted small">Банк ждал</div>
        <strong>{fmt(s.bank_expected)}</strong>
      </div>
      <div>
        <div className="muted small">Вышло</div>
        <strong>{fmt(s.actual)}</strong>
      </div>
    </div>
  );
}
