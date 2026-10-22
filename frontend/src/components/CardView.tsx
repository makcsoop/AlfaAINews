// Персональная карточка: разделы по 6 вопросам ТЗ, у каждого утверждения метка и источник.
import {
  CLAIM_LABELS,
  CONFIDENCE_LABELS,
  STORY_STATUS_LABELS,
  featureLabel,
  sourceLabel,
  sourceName,
} from "../labels";
import type { Claim, InsightCard, Surprise } from "../types";
import {
  ClaimTag,
  ContributionRow,
  PolarityChip,
  ReviewChip,
  formatDate,
  safeUrl,
  topContributions,
} from "./bits";

export function CardView({ card, onBack }: { card: InsightCard; onBack: () => void }) {
  const byId = new Map(card.claims.map((c) => [c.claim_id, c]));
  const pick = (ids: string[], skip: Set<string> = new Set()) =>
    ids.filter((id) => !skip.has(id)).flatMap((id) => byId.get(id) ?? []);
  const why = pick(card.why_it_matters);
  const consequences = pick(card.consequences, new Set(card.why_it_matters));
  const contribs = topContributions(card);
  const max = Math.max(...contribs.map((c) => Math.abs(c.value)), 0);

  return (
    <article className="card">
      <button className="link back" onClick={onBack}>
        ← К ленте
      </button>
      <header className="card-head">
        <div className="feed-meta">
          <PolarityChip polarity={card.relevance.polarity} />
          <ReviewChip card={card} />
          <span className="muted small">
            {formatDate(card.event.first_seen)} · {STORY_STATUS_LABELS[card.event.status] ?? card.event.status}
          </span>
        </div>
        <h1>{card.event.title}</h1>
        <Sources card={card} />
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
                <span>{a.text}</span>
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

      {card.what_would_change.length > 0 && (
        <section className="sec">
          <h2>Что изменит вывод</h2>
          <ul className="reasons">
            {card.what_would_change.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

function Sources({ card }: { card: InsightCard }) {
  return (
    <div className="sources small">
      <span className="muted">Источники:</span>
      {card.event.sources.map((s) => {
        const href = safeUrl(s.url);
        const label = sourceName(s.source_id, s.title);
        return href ? (
          <a key={s.source_id} href={href} target="_blank" rel="noopener noreferrer">
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

function ClaimItem({ claim, byId }: { claim: Claim; byId: Map<string, Claim> }) {
  const facts = [...(claim.based_on.facts ?? []), ...(claim.based_on.bank_views ?? [])];
  const features = claim.based_on.client_features ?? [];
  return (
    <li className={`claim claim-${claim.type}`}>
      <ClaimTag type={claim.type} />
      <div className="claim-body">
        <p>{claim.text}</p>
        {claim.evidence.map((e) => {
          const href = safeUrl(e.url);
          return (
            <blockquote key={e.source_id + (e.quote ?? "")}>
              {e.quote && <span>«{e.quote}»</span>}{" "}
              <cite>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {sourceLabel(e.source_id)}
                  </a>
                ) : (
                  sourceLabel(e.source_id)
                )}
                {e.doc_date && `, ${formatDate(e.doc_date)}`}
              </cite>
            </blockquote>
          );
        })}
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
        {claim.horizon && <div className="muted small">Горизонт: {claim.horizon}</div>}
      </div>
    </li>
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
