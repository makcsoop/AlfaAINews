// Событие в списке: как строка делового издания — время, направление и канал влияния, заголовок, лид,
// «касается вас через», значимость. Вся строка — ссылка на разбор; «не про нас» скрывает событие.
import { CONFIDENCE_LABELS, POLARITY_LABELS, channelLabel, featureLabel } from "../labels";
import type { InsightCard } from "../types";
import {
  PolarityMark,
  Significance,
  StageFlags,
  strengthOf,
  dayLabel,
  mainChannel,
  sourceNames,
  timeLabel,
  topContributions,
  whyFeatures,
} from "./bits";
import { IconEyeOff } from "./icons";

interface Props {
  card: InsightCard;
  isNew: boolean;
  onOpen: () => void;
  onHide?: () => void;
  rank?: number;
  compact?: boolean;
}

export function StoryRow({ card, isNew, onOpen, onHide, rank, compact = false }: Props) {
  const lead = card.claims.find((x) => x.claim_id === card.why_it_matters[0]);
  const channel = mainChannel(card);
  const features = whyFeatures(card);
  const sources = sourceNames(card);
  return (
    <li className={`story${isNew ? " is-new" : ""}${compact ? " story-compact" : ""}`}>
      <div className="story-when">
        {rank != null ? (
          <span className="story-rank">{rank}</span>
        ) : (
          <>
            <span>{dayLabel(card.event.first_seen)}</span>
            <span>{timeLabel(card.event.first_seen)}</span>
          </>
        )}
      </div>
      <div className="story-main">
        <div className="kicker">
          <PolarityMark polarity={card.relevance.polarity} />
          {channel && <span className="kicker-item">{channel}</span>}
          {!compact && sources.length > 0 && (
            <span className="kicker-item">
              {sources.slice(0, 2).join(", ")}
              {sources.length > 2 && ` и ещё ${sources.length - 2}`}
            </span>
          )}
          <StageFlags card={card} />
        </div>
        <h3 className="story-title">
          <button className="story-link" onClick={onOpen}>
            {isNew && <i className="dot" role="img" aria-label="новое" />}
            {card.event.title}
          </button>
        </h3>
        {!compact && lead && <p className="story-lede">{lead.text}</p>}
        {features.length > 0 && (
          <p className="story-why">
            Касается вас через <b>{features.join(", ")}</b>
            {!compact && <span> · {CONFIDENCE_LABELS[card.confidence].toLowerCase()}</span>}
          </p>
        )}
      </div>
      <div className="story-side">
        <Significance score={strengthOf(card.relevance)} size={compact ? "s" : "m"} />
        {onHide && (
          <button className="story-hide" onClick={onHide} title="Не про нас — скрыть" aria-label={`Не про нас: скрыть «${card.event.title}»`}>
            <IconEyeOff size={18} />
          </button>
        )}
      </div>
    </li>
  );
}

// Механизм влияния: событие → канал → что задевает у компании → итог для неё
export function Mechanism({ card }: { card: InsightCard }) {
  const top = topContributions(card, 1)[0];
  if (!top) return null;
  const features = top.client_features.map(featureLabel);
  return (
    <ol className="mech" aria-label="Как событие влияет на компанию">
      <li>
        <span className="mech-k">Событие</span>
        <span className="mech-v">{card.event.title}</span>
      </li>
      <li>
        <span className="mech-k">Через</span>
        <span className="mech-v">{channelLabel(top.factor)}</span>
      </li>
      {features.length > 0 && (
        <li>
          <span className="mech-k">Задевает у вас</span>
          <span className="mech-v">{features.join(", ")}</span>
        </li>
      )}
      <li className={`mech-out mech-${card.relevance.polarity}`}>
        <span className="mech-k">Итог для компании</span>
        <span className="mech-v">{POLARITY_LABELS[card.relevance.polarity]}</span>
      </li>
    </ol>
  );
}
