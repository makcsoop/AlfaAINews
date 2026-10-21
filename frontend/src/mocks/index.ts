// Мок API на фикстуре rate_14 — для работы без бэкенда (`npm run dev:mock`). Повторяет правила API Р3:
// вход только в демо-компании, лента — карточки компании сессии по убыванию значимости.
import bundle from "@fixtures/rate_14/bundle.json";
import type { Api } from "../api";
import { HttpError } from "../api";
import type { Claim, InsightCard, SessionInfo } from "../types";

// В фикстуре поля со значением по умолчанию опущены, а API (Pydantic) отдаёт их всегда — дополняем
type Raw = Partial<InsightCard> & Pick<InsightCard, "card_id" | "company_id" | "user_role" | "event">;

function withDefaults(c: Raw): InsightCard {
  return {
    actions: [],
    bank_and_market: [],
    what_would_change: [],
    confidence_reasons: [],
    review_status: "published",
    surprise: null,
    ...c,
    claims: (c.claims ?? []).map((x) => ({
      evidence: [],
      based_on: {},
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
    return delay([...(byRole.length ? byRole : own)].sort((a, b) => b.relevance.score - a.relevance.score));
  },
  async card(cardId) {
    const s = requireSession();
    const found = cards.find((c) => c.card_id === cardId && c.company_id === s.company_id);
    if (!found) throw new HttpError(404, "Карточка не найдена");
    return delay(found);
  },
};
