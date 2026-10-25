// «Обзор» сервиса: знакомство, сводка, главное событие с механизмом влияния, карта влияния по каналам,
// остальные события, разбор своей новости и частые вопросы.
import type { ReactNode } from "react";
import { CONFIDENCE_LABELS, plural } from "../labels";
import { go } from "../nav";
import type { InsightCard } from "../types";
import { PolarityMark, Significance, SkeletonRows, StageFlags, dayLabel, impactByChannel, mainChannel, useScrollTarget } from "./bits";
import { Checklist } from "./Checklist";
import { FaqTeaser } from "./Faq";
import { IconChevron } from "./icons";
import { Mechanism, StoryRow } from "./StoryRow";

interface Props {
  cards: InsightCard[] | null;
  unseen: Set<string>;
  onOpen: (id: string) => void;
  onHide: (c: InsightCard) => void;
  paste: ReactNode;
}

export function Brief({ cards, unseen, onOpen, onHide, paste }: Props) {
  const top = cards?.[0] ?? null;
  useScrollTarget(true);
  return (
    <div className="brief">
      <Checklist topCardId={top?.card_id ?? null} />
      {cards === null ? (
        <div className="block">
          <SkeletonRows rows={3} />
        </div>
      ) : cards.length === 0 ? (
        <div className="empty">
          <strong>Пока тихо</strong>
          <p>Сейчас нет событий, которые заметно влияют на вашу компанию в этой роли. Проверьте свою новость ниже или выберите другую роль.</p>
        </div>
      ) : (
        <>
          <Kpis cards={cards} unseen={unseen} />
          <div className="brief-grid">
            {top && <LeadStory card={top} onOpen={() => onOpen(top.card_id)} />}
            <ImpactMap cards={cards} />
          </div>
        </>
      )}
      <div className="brief-grid">
        <section className="block" aria-labelledby="more-title">
          <div className="block-head">
            <h2 id="more-title">Ещё в ленте</h2>
            {cards && cards.length > 1 && (
              <button className="link" onClick={() => go("feed")}>
                Вся лента · {cards.length}
                <IconChevron size={16} />
              </button>
            )}
          </div>
          {cards === null && <SkeletonRows rows={2} />}
          {cards && cards.length <= 1 && <p className="muted">Других событий для вашей компании сейчас нет.</p>}
          {cards && cards.length > 1 && (
            <ul className="stories">
              {cards.slice(1, 5).map((c) => (
                <StoryRow key={c.card_id} card={c} isNew={unseen.has(c.card_id)} onOpen={() => onOpen(c.card_id)} onHide={() => onHide(c)} compact />
              ))}
            </ul>
          )}
        </section>
        <div className="brief-side">
          {paste}
          <FaqTeaser />
        </div>
      </div>
    </div>
  );
}

function Kpis({ cards, unseen }: { cards: InsightCard[]; unseen: Set<string> }) {
  const n = (p: string) => cards.filter((c) => c.relevance.polarity === p).length;
  const items = [
    { k: "Событий в ленте", v: cards.length, to: "feed" },
    { k: "Риски", v: n("risk"), to: "feed/p/risk", tone: "risk" },
    { k: "Возможности", v: n("opportunity"), to: "feed/p/opportunity", tone: "opportunity" },
    { k: "Новые", v: cards.filter((c) => unseen.has(c.card_id)).length, to: "feed/p/new" },
  ];
  return (
    <section className="kpis" aria-label="Сводка">
      {items.map((it) => (
        <button key={it.k} className="kpi" onClick={() => go(it.to)}>
          <b className={it.tone ? `tone-${it.tone}` : undefined}>{it.v}</b>
          <span>
            {it.k}
            <IconChevron size={14} />
          </span>
        </button>
      ))}
    </section>
  );
}

function LeadStory({ card, onOpen }: { card: InsightCard; onOpen: () => void }) {
  const lead = card.claims.find((x) => x.claim_id === card.why_it_matters[0]);
  const channel = mainChannel(card);
  return (
    <article className="lead-story">
      <span className="lead-label">Главное сейчас</span>
      <div className="kicker">
        <PolarityMark polarity={card.relevance.polarity} />
        {channel && <span className="kicker-item">{channel}</span>}
        <span className="kicker-item">{dayLabel(card.event.first_seen)}</span>
        <StageFlags card={card} />
      </div>
      <h2 className="lead-title">
        <button className="story-link" onClick={onOpen}>
          {card.headline || card.event.title}
        </button>
      </h2>
      {lead && <p className="lead-dek">{lead.text}</p>}
      <Mechanism card={card} />
      <div className="lead-foot">
        <Significance score={card.relevance.score} />
        <span className="muted small">{CONFIDENCE_LABELS[card.confidence]}</span>
        <span className="lead-cta">
          Читать разбор
          <IconChevron size={18} />
        </span>
      </div>
    </article>
  );
}

// Что давит на бизнес и что помогает: сумма вкладов по каналам влияния во всей ленте
function ImpactMap({ cards }: { cards: InsightCard[] }) {
  const rows = impactByChannel(cards).slice(0, 6);
  const max = Math.max(...rows.map((r) => Math.max(r.neg, r.pos)), 0.0001);
  return (
    <section className="block impact" aria-labelledby="impact-title">
      <div className="block-head">
        <div>
          <h2 id="impact-title">Карта влияния</h2>
          <p className="muted small">Что сейчас давит на бизнес и что помогает — по всем событиям ленты</p>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="muted">Факторы влияния появятся, когда в ленте будут оценённые события.</p>
      ) : (
        <>
          <div className="impact-legend" aria-hidden="true">
            <span className="neg">давит</span>
            <span className="pos">помогает</span>
          </div>
          <ul className="impact-rows">
            {rows.map((r) => (
              <li key={r.factor}>
                <button onClick={() => go(`feed/ch/${encodeURIComponent(r.factor)}`)} title="Показать события в ленте">
                  <span className="impact-name">
                    <strong>{r.label}</strong>
                    <span>
                      {r.cards.length} {plural(r.cards.length, ["событие", "события", "событий"])}
                      {r.features.length > 0 && ` · ${r.features.slice(0, 2).join(", ")}`}
                    </span>
                  </span>
                  <span className="impact-bars" aria-label={`давит ${r.neg.toFixed(2)}, помогает ${r.pos.toFixed(2)}`}>
                    <span className="half neg">
                      <i style={{ width: `${(r.neg / max) * 100}%` }} />
                    </span>
                    <span className="half pos">
                      <i style={{ width: `${(r.pos / max) * 100}%` }} />
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
