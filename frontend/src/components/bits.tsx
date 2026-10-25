// Мелкие общие элементы: метки, направление влияния, значимость, вклад факторов, даты, заглушки загрузки.
import { useEffect } from "react";
import { CLAIM_LABELS, POLARITY_LABELS, channelLabel, featureLabel, formatNum, sourceName } from "../labels";
import { takeScrollTarget } from "../nav";
import type { ClaimType, Contribution, InsightCard, Polarity } from "../types";

export function ClaimTag({ type }: { type: ClaimType }) {
  return <span className={`tag tag-${type}`}>{CLAIM_LABELS[type]}</span>;
}

// Что значит каждая метка — легенда в карточке, в справке и в знакомстве
export const CLAIM_HINTS: Record<ClaimType, string> = {
  fact: "Подтверждено источником: цитата и ссылка",
  bank_view: "Позиция аналитиков Альфа-Банка (ЦМИ) с датой и страницей",
  market_view: "Ожидания рынка и консенсус аналитиков",
  model_inference: "Рассуждение AI — всегда отдельно от фактов",
  bank_offer: "Продукт банка — всегда помечен как предложение",
};

export function Legend() {
  return (
    <ul className="legend">
      {(Object.keys(CLAIM_HINTS) as ClaimType[]).map((t) => (
        <li key={t}>
          <ClaimTag type={t} />
          <span>{CLAIM_HINTS[t]}</span>
        </li>
      ))}
    </ul>
  );
}

// Направление влияния на компанию: стрелка + слово, цвет дублирует смысл
const GLYPH: Record<Polarity, string> = {
  risk: "M12 5v13.5M6.5 13 12 18.5l5.5-5.5",
  opportunity: "M12 19V5.5M6.5 11 12 5.5l5.5 5.5",
  mixed: "M8 19V5.5M4.5 9 8 5.5 11.5 9M16 5v13.5M12.5 15l3.5 3.5 3.5-3.5",
  neutral: "M5 12h13.5M13 6.5l5.5 5.5-5.5 5.5",
};

export function PolarityMark({ polarity }: { polarity: Polarity }) {
  return (
    <span className={`pol pol-${polarity}`}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={GLYPH[polarity]} />
      </svg>
      {POLARITY_LABELS[polarity]}
    </span>
  );
}

export function StageFlags({ card }: { card: InsightCard }) {
  return (
    <>
      {card.stage === "fast" && (
        <span className="flag" title="Оценка по профилю компании, глубокий разбор ещё не готов">
          быстрая оценка
        </span>
      )}
      {card.review_status === "on_review" && <span className="flag flag-warn">на проверке</span>}
    </>
  );
}

// Значимость 0–100: число и пять «сигнальных» делений
export function Significance({ score, size = "m" }: { score: number; size?: "s" | "m" | "l" }) {
  const v = Math.max(0, Math.min(100, Math.round(score * 100)));
  const filled = Math.max(1, Math.ceil(v / 20));
  return (
    <span className={`sig sig-${size}`} title={`Значимость для компании: ${v} из 100`}>
      <b>{v}</b>
      <span className="sig-bars" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((i) => (
          <i key={i} className={i <= filled ? "on" : undefined} />
        ))}
      </span>
      <span className="sr-only">из 100</span>
    </span>
  );
}

// Шкала-фундамент: длина красной плашки = значимость
export function FoundationMeter({ score }: { score: number }) {
  const v = Math.max(0, Math.min(100, Math.round(score * 100)));
  return (
    <div className="fmeter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} aria-label="Значимость">
      <i style={{ width: `${v}%` }} />
    </div>
  );
}

// Три главных вклада скоринга → «почему вы это видите»
export function topContributions(card: InsightCard, n = 3): Contribution[] {
  return [...card.relevance.contributions].sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, n);
}

export function whyFeatures(card: InsightCard): string[] {
  return [...new Set(topContributions(card).flatMap((c) => c.client_features.map(featureLabel)))];
}

export function mainChannel(card: InsightCard): string | null {
  const top = topContributions(card, 1)[0];
  return top ? channelLabel(top.factor) : null;
}

// Названия изданий сюжета: официальные и агентства первыми, без повторов
export function sourceNames(card: InsightCard): string[] {
  return [...new Set([...card.event.sources].sort((a, b) => a.tier - b.tier).map((s) => sourceName(s.source_id, s.title)))];
}

// Вклад факторов значимости: «давит» — влево, «помогает» — вправо от оси
export function ContributionChart({ contribs }: { contribs: Contribution[] }) {
  const max = Math.max(...contribs.map((c) => Math.abs(c.value)), 0.0001);
  return (
    <ul className="cchart">
      {contribs.map((c) => {
        const w = (Math.abs(c.value) / max) * 50;
        const tone = c.sign < 0 ? "risk" : c.sign > 0 ? "opportunity" : "neutral";
        return (
          <li key={c.factor}>
            <div className="cchart-label">
              <strong>{channelLabel(c.factor)}</strong>
              <span>{c.client_features.map(featureLabel).join(", ")}</span>
            </div>
            <div className="cchart-track" aria-hidden="true">
              <i className={`tone-bg-${tone} ${c.sign < 0 ? "neg" : "pos"}`} style={{ width: `${w}%` }} />
            </div>
            <span className={`cchart-val tone-${tone}`}>
              {c.sign < 0 ? "−" : c.sign > 0 ? "+" : ""}
              {formatNum(Math.abs(c.value))}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// Сумма вкладов по каналам влияния во всей ленте — «карта влияния»
export interface ChannelImpact {
  factor: string;
  label: string;
  neg: number;
  pos: number;
  cards: string[];
  features: string[];
}

export function impactByChannel(cards: InsightCard[]): ChannelImpact[] {
  const map = new Map<string, ChannelImpact>();
  for (const card of cards) {
    for (const k of card.relevance.contributions) {
      const it = map.get(k.factor) ?? { factor: k.factor, label: channelLabel(k.factor), neg: 0, pos: 0, cards: [], features: [] };
      if (k.sign < 0) it.neg += Math.abs(k.value);
      if (k.sign > 0) it.pos += Math.abs(k.value);
      if (!it.cards.includes(card.card_id)) it.cards.push(card.card_id);
      for (const f of k.client_features.map(featureLabel)) if (!it.features.includes(f)) it.features.push(f);
      map.set(k.factor, it);
    }
  }
  return [...map.values()].sort((a, b) => b.neg + b.pos - (a.neg + a.pos));
}

// Заглушка на время загрузки: повторяет форму будущего списка
export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Загрузка">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="sk-row">
          <div className="skeleton" style={{ width: 140, height: 14 }} />
          <div className="skeleton" style={{ width: `${78 - i * 9}%`, height: 22 }} />
          <div className="skeleton" style={{ width: `${54 - i * 6}%`, height: 14 }} />
        </div>
      ))}
    </div>
  );
}

// Прокрутка к блоку, о котором попросили при переходе (отклик, вопрос, поле ссылки)
export function useScrollTarget(ready = true) {
  useEffect(() => {
    if (!ready) return;
    const run = () => {
      const t = takeScrollTarget();
      if (!t) return;
      requestAnimationFrame(() => {
        const el = document.getElementById(t);
        if (!el) return;
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        (el.querySelector<HTMLElement>("input, textarea") ?? el.querySelector<HTMLElement>("button"))?.focus({ preventScroll: true });
      });
    };
    run();
    window.addEventListener("app:scroll", run);
    return () => window.removeEventListener("app:scroll", run);
  }, [ready]);
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

// «сегодня», «вчера» или «11 сент.» — для ленты
export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const days = Math.round((new Date(now.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 86400000);
  if (days === 0) return "сегодня";
  if (days === 1) return "вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

export function safeUrl(u?: string | null): string | null {
  if (!u) return null;
  try {
    const x = new URL(u);
    if (x.protocol !== "http:" && x.protocol !== "https:") return null;
    // пост Telegram «t.me/<канал>/<id>» без приложения зависает на переходе в tg:// — открываем веб-версию t.me/s
    const tg = (x.hostname === "t.me" || x.hostname === "telegram.me") && x.pathname.match(/^\/([A-Za-z0-9_]{4,})\/(\d+)\/?$/);
    if (tg) return `https://t.me/s/${tg[1]}/${tg[2]}`;
    return x.href;
  } catch {
    return null;
  }
}
