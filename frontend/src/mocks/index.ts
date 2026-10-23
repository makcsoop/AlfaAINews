// Мок API на фикстуре rate_14 — для работы без бэкенда (`npm run dev:mock`). Повторяет правила API Р3:
// вход только в демо-компании, лента — карточки компании сессии по убыванию значимости.
import bundle from "@fixtures/rate_14/bundle.json";
import type { Api } from "../api";
import { HttpError } from "../api";
import type { Claim, DeepRun, InsightCard, RunTrace, SessionInfo, TraceCost } from "../types";

// В фикстуре поля со значением по умолчанию опущены, а API (Pydantic) отдаёт их всегда — дополняем
type Raw = Partial<InsightCard> & Pick<InsightCard, "card_id" | "company_id" | "user_role" | "event">;

function withDefaults(c: Raw): InsightCard {
  return {
    actions: [],
    bank_and_market: [],
    what_would_change: [],
    confidence_reasons: [],
    review_status: "published",
    stage: "fast",
    surprise: null,
    ...c,
    claims: (c.claims ?? []).map((x) => ({
      evidence: [],
      based_on: {},
      verified: {},
      is_advertising: false,
      ...(x as Partial<Claim>),
    })),
    relevance: {
      contributions: [],
      deep_analysis: false,
      polarity: "neutral",
      ...(c.relevance as Partial<InsightCard["relevance"]>),
    },
  } as InsightCard;
}

const cards = (bundle.insight_card as unknown as Raw[]).map(withDefaults);
const demoCompanies = new Set(cards.map((c) => c.company_id));
let session: SessionInfo | null = null;

// Догрузка разбора: первые секунды карточки с глубоким разбором отдаются как быстрые
const DEEP_READY_MS = 12000;
const startedAt = Date.now();

function asOfNow(c: InsightCard): InsightCard {
  if (c.stage !== "deep" || Date.now() - startedAt > DEEP_READY_MS) return c;
  const keep = new Set([...c.what_happened, ...c.bank_and_market, ...c.why_it_matters.slice(0, 1)]);
  return {
    ...c,
    stage: "fast",
    relevance: { ...c.relevance, deep_analysis: true },
    why_it_matters: c.why_it_matters.slice(0, 1),
    consequences: [],
    actions: [],
    what_would_change: [],
    claims: c.claims.filter((x) => keep.has(x.claim_id)),
    confidence: "low",
    confidence_reasons: ["быстрая оценка по профилю, глубокого разбора ещё нет"],
  };
}

const delay = <T>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 150));

function requireSession(): SessionInfo {
  if (!session) throw new HttpError(401, "Требуется вход");
  return session;
}

export const mockApi: Api = {
  async login(companyId, role) {
    if (!demoCompanies.has(companyId)) throw new HttpError(403, "Эта компания недоступна для демо-входа");
    session = { company_id: companyId, role, user_id: `demo_${companyId}_${role}` };
    return delay(session);
  },
  async feed() {
    const s = requireSession();
    const own = cards.filter((c) => c.company_id === s.company_id);
    // как FixtureCardStore: нет карточек под роль — карточки компании, а не пустая лента
    const byRole = own.filter((c) => c.user_role === s.role);
    const list = (byRole.length ? byRole : own).map(asOfNow);
    return delay(list.sort((a, b) => b.relevance.score - a.relevance.score));
  },
  async card(cardId) {
    const s = requireSession();
    const found = cards.find((c) => c.card_id === cardId && c.company_id === s.company_id);
    if (!found) throw new HttpError(404, "Карточка не найдена");
    return delay(asOfNow(found));
  },
  async ask(question, cardId) {
    const s = requireSession();
    const card = cards.find((c) => c.card_id === cardId && c.company_id === s.company_id);
    // как у Р3: вопрос про чужую компанию — отказ, компания берётся только из сессии
    if (/(^|[\s«"])(ООО|АО|ПАО|ЗАО)[\s«"]|чуж/i.test(question)) {
      return delay({
        answer: "Могу отвечать только по данным вашей компании и этой карточке.",
        refs: [],
        tools_used: [],
        trace_id: "mock_ask",
        status: "refused" as const,
      });
    }
    if (/контрагент|поставщик|покупател/i.test(question)) {
      return delay({
        answer: "Крупнейший поставщик — 35% закупок (ОКВЭД 24), второй — 12% (ОКВЭД 49, логистика). " +
          "Зависимость от одного поставщика высокая.",
        refs: ["cp:supplier:1", "cp:supplier:2"],
        tools_used: ["get_counterparties"],
        trace_id: "mock_ask",
        status: "ok" as const,
      });
    }
    const facts = (card?.claims ?? []).filter((c) => c.type === "fact" || c.type === "bank_view");
    return delay({
      answer: facts.map((c) => c.text).join(" "),
      refs: facts.map((c) => c.claim_id),
      tools_used: [],
      trace_id: "mock_ask",
      status: card ? ("ok" as const) : ("degraded" as const),
    });
  },
  async trace(traceId) {
    requireSession();
    const t = (bundle.run_trace as unknown as RunTrace[]).find((x) => x.run_id === traceId);
    if (!t) throw new HttpError(404, "Трассировка не найдена");
    return delay({ verify_failures: [], tool_calls: [], llm_calls: [], ...(t as Partial<RunTrace>) } as RunTrace);
  },
  async traceCost(traceId) {
    const t = await this.trace(traceId);
    const by: TraceCost["cost_by_step"] = {};
    for (const c of t.llm_calls) {
      const b = (by[c.purpose] ??= { calls: 0, tokens_in: 0, tokens_out: 0, cost_rub: 0 });
      b.calls += 1;
      b.tokens_in += c.tokens_in;
      b.tokens_out += c.tokens_out;
      b.cost_rub += c.cost_rub;
    }
    return { trace_id: t.run_id, cost_rub: t.cost_rub, cost_by_step: by, budget: { daily_limit_rub: 500, spent_rub: 12.4, remaining_rub: 487.6, mode: "normal" } };
  },
  async runs() {
    const s = requireSession();
    const out: DeepRun[] = cards
      .filter((c) => c.company_id === s.company_id && c.stage === "deep")
      .map((c) => ({
        story_id: c.event.story_id, company_id: c.company_id, role: c.user_role, status: c.review_status,
        reason: null, score: c.relevance.score, trace_id: c.trace_id, cost_rub: 0.71, at: c.event.first_seen,
      }));
    return delay(out);
  },
  async feedback(cardId) {
    const s = requireSession();
    if (!cards.some((c) => c.card_id === cardId && c.company_id === s.company_id)) {
      throw new HttpError(404, "Карточка не найдена");
    }
    await delay(null);
  },
  async analyze() {
    // как сейчас на бэкенде: эндпоинта ещё нет
    throw new HttpError(404, "");
  },
};
