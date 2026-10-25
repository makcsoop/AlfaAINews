// Лента событий: фильтр по направлению влияния, новым и каналу, сортировка по значимости или времени.
import { useState } from "react";
import { channelLabel } from "../labels";
import { go } from "../nav";
import type { InsightCard, Polarity } from "../types";
import { SkeletonRows, strengthOf } from "./bits";
import { IconCross } from "./icons";
import { StoryRow } from "./StoryRow";

export type FeedFilter = "all" | "new" | Polarity;

const FILTERS: { id: FeedFilter; name: string }[] = [
  { id: "all", name: "Все" },
  { id: "new", name: "Новые" },
  { id: "risk", name: "Риски" },
  { id: "opportunity", name: "Возможности" },
  { id: "mixed", name: "Риск и возможность" },
  { id: "neutral", name: "К сведению" },
];

interface Props {
  cards: InsightCard[] | null;
  unseen: Set<string>;
  onOpen: (id: string) => void;
  onHide: (c: InsightCard) => void;
  initial?: FeedFilter;
  channel?: string | null;
}

export function Feed({ cards, unseen, onOpen, onHide, initial = "all", channel = null }: Props) {
  const [filter, setFilter] = useState<FeedFilter>(initial);
  const [sort, setSort] = useState<"score" | "time">("score");
  if (cards === null)
    return (
      <div className="block">
        <SkeletonRows rows={4} />
      </div>
    );
  if (cards.length === 0)
    return (
      <div className="empty">
        <strong>Пока ничего важного</strong>
        <p>Для этой компании и роли пока нет событий. Попробуйте другую роль или проверьте свою новость на «Обзоре».</p>
      </div>
    );
  const byChannel = channel ? cards.filter((c) => c.relevance.contributions.some((k) => k.factor === channel)) : cards;
  const match = (c: InsightCard, f: FeedFilter) =>
    f === "all" || (f === "new" ? unseen.has(c.card_id) : f === "risk" ? c.relevance.polarity === "risk" : c.relevance.polarity === f);
  const count = (f: FeedFilter) => byChannel.filter((c) => match(c, f)).length;
  const active = count(filter) > 0 ? filter : "all";
  const shown = byChannel
    .filter((c) => match(c, active))
    .sort((a, b) => (sort === "score" ? strengthOf(b.relevance) - strengthOf(a.relevance) : b.event.first_seen.localeCompare(a.event.first_seen)));
  return (
    <div className="feed">
      <div className="toolbar">
        <div className="chips" role="radiogroup" aria-label="Показать">
          {FILTERS.filter((f) => f.id === "all" || count(f.id) > 0).map((f) => (
            <button
              key={f.id}
              role="radio"
              aria-checked={active === f.id}
              className={active === f.id ? "chip-f on" : "chip-f"}
              onClick={() => setFilter(f.id)}
            >
              {f.name}
              <span>{count(f.id)}</span>
            </button>
          ))}
        </div>
        <div className="sort" role="radiogroup" aria-label="Порядок">
          <span className="muted small">Сначала</span>
          <button role="radio" aria-checked={sort === "score"} className={sort === "score" ? "on" : undefined} onClick={() => setSort("score")}>
            важные
          </button>
          <button role="radio" aria-checked={sort === "time"} className={sort === "time" ? "on" : undefined} onClick={() => setSort("time")}>
            свежие
          </button>
        </div>
      </div>
      {channel && (
        <div className="channel-filter">
          Только события через фактор <b>{channelLabel(channel)}</b>
          <button onClick={() => go("feed")} aria-label="Сбросить фильтр по фактору">
            <IconCross size={16} />
          </button>
        </div>
      )}
      <ul className="stories stories-feed">
        {shown.map((c) => (
          <StoryRow key={c.card_id} card={c} isNew={unseen.has(c.card_id)} onOpen={() => onOpen(c.card_id)} onHide={() => onHide(c)} />
        ))}
      </ul>
      {shown.length === 0 && <p className="muted">По этому фильтру событий нет.</p>}
    </div>
  );
}
