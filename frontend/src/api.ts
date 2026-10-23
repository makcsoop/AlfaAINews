// Клиент API. Компания и роль задаются только входом (POST /session): дальше бэкенд берёт их из cookie.
// company_id в тело запросов не передаётся никогда — API ответит 403.
import type {
  ApiError,
  AskResponse,
  DeepRun,
  FeedbackKind,
  InsightCard,
  RunTrace,
  SessionInfo,
  TraceCost,
  UserRole,
} from "./types";

export interface Api {
  login(companyId: string, role: UserRole): Promise<SessionInfo>;
  feed(): Promise<InsightCard[]>;
  card(cardId: string): Promise<InsightCard>;
  ask(question: string, cardId?: string): Promise<AskResponse>;
  trace(traceId: string): Promise<RunTrace>;
  traceCost(traceId: string): Promise<TraceCost>;
  runs(): Promise<DeepRun[]>;
  feedback(cardId: string, kind: FeedbackKind, claimId?: string): Promise<void>;
  analyze(input: { url?: string; text?: string; title?: string }): Promise<InsightCard>;
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public traceId?: string,
  ) {
    super(message);
  }
}

// Понятный текст по коду ответа; сообщение сервера — если оно есть и не служебное
const STATUS_TEXT: Record<number, string> = {
  0: "Сервис недоступен — проверьте подключение",
  401: "Сессия истекла — входим заново",
  403: "Нет доступа к данным другой компании",
  404: "Не найдено: карточка или разбор не относятся к вашей компании",
  429: "Слишком часто — попробуйте через минуту",
};

export function errorText(e: unknown): string {
  if (e instanceof HttpError) return e.message || STATUS_TEXT[e.status] || `Ошибка ${e.status}`;
  return e instanceof Error ? e.message : String(e);
}

let lastLogin: { companyId: string; role: UserRole } | null = null;

async function raw<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new HttpError(0, STATUS_TEXT[0]);
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as ApiError | null;
    const server = body?.error?.message;
    const generic = !server || server === "Not Found" || res.status === 401 || res.status === 429;
    throw new HttpError(res.status, generic ? (STATUS_TEXT[res.status] ?? `Ошибка ${res.status}`) : server, body?.error?.trace_id);
  }
  return res.json() as Promise<T>;
}

// 401 — сессия истекла: один раз входим заново той же компанией и ролью и повторяем запрос
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await raw<T>(path, init);
  } catch (e) {
    if (e instanceof HttpError && e.status === 401 && lastLogin) {
      await raw("/session", { method: "POST", body: JSON.stringify({ company_id: lastLogin.companyId, role: lastLogin.role }) });
      return raw<T>(path, init);
    }
    throw e;
  }
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

const httpApi: Api = {
  async login(companyId, role) {
    lastLogin = { companyId, role };
    return raw("/session", post({ company_id: companyId, role }));
  },
  feed: () => request("/feed"),
  card: (cardId) => request(`/card/${encodeURIComponent(cardId)}`),
  ask: (question, cardId) => request("/ask", post({ question, card_id: cardId })),
  trace: (traceId) => request(`/trace/${encodeURIComponent(traceId)}`),
  traceCost: (traceId) => request(`/trace/${encodeURIComponent(traceId)}/cost`),
  runs: () => request("/feed/runs"),
  async feedback(cardId, kind, claimId) {
    await request("/feedback", post({ card_id: cardId, kind, claim_id: claimId }));
  },
  analyze: (input) => request("/analyze", post(input)),
};

// Моки подключаются только в `npm run dev:mock`; в обычной сборке ветка вырезается
export async function getApi(): Promise<Api> {
  if (import.meta.env.VITE_USE_MOCKS === "1") {
    const { mockApi } = await import("./mocks");
    return mockApi;
  }
  return httpApi;
}
