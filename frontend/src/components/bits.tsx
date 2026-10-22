// Мелкие общие элементы: метки, «почему вы это видите», дата.
import { CLAIM_LABELS, POLARITY_LABELS, channelLabel, featureLabel, formatNum } from "../labels";
import type { ClaimType, Contribution, InsightCard, Polarity } from "../types";

export function ClaimTag({ type }: { type: ClaimType }) {
  return <span className={`tag tag-${type}`}>{CLAIM_LABELS[type]}</span>;
}

export function PolarityChip({ polarity }: { polarity: Polarity }) {
  return <span className={`chip chip-${polarity}`}>{POLARITY_LABELS[polarity]}</span>;
}

export function ReviewChip({ card }: { card: InsightCard }) {
  return card.review_status === "on_review" ? <span className="chip chip-review">на проверке</span> : null;
}

// Три главных вклада скоринга → «почему вы это видите»
export function topContributions(card: InsightCard, n = 3): Contribution[] {
  return [...card.relevance.contributions].sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, n);
}

export function whyLine(card: InsightCard): string {
  const features = topContributions(card).flatMap((c) => c.client_features.map(featureLabel));
  return [...new Set(features)].join(" · ");
}

export function ContributionRow({ c, max }: { c: Contribution; max: number }) {
  const width = max > 0 ? Math.round((Math.abs(c.value) / max) * 100) : 0;
  const tone = c.sign < 0 ? "risk" : c.sign > 0 ? "opportunity" : "neutral";
  return (
    <li className="contrib">
      <div className="contrib-head">
        <span>{channelLabel(c.factor)}</span>
        <span className={`contrib-val tone-${tone}`}>
          {c.sign < 0 ? "−" : c.sign > 0 ? "+" : ""}
          {formatNum(Math.abs(c.value))}
        </span>
      </div>
      <div className="bar">
        <i className={`tone-bg-${tone}`} style={{ width: `${width}%` }} />
      </div>
      <div className="muted small">{c.client_features.map(featureLabel).join(" · ")}</div>
    </li>
  );
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

export function safeUrl(u?: string | null): string | null {
  if (!u) return null;
  try {
    const x = new URL(u);
    return x.protocol === "http:" || x.protocol === "https:" ? x.href : null;
  } catch {
    return null;
  }
}
