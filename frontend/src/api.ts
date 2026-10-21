// Клиент API. Компания и роль задаются только входом (POST /session): дальше бэкенд берёт их из cookie.
import type { ApiError, InsightCard, SessionInfo, UserRole } from "./types";

export interface Api {
  login(companyId: string, role: UserRole): Promise<SessionInfo>;
  feed(): Promise<InsightCard[]>;
  card(cardId: string): Promise<InsightCard>;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new HttpError(0, "Сервис недоступен — проверьте, что бэкенд запущен");
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as ApiError | null;
    throw new HttpError(res.status, body?.error.message ?? `Ошибка ${res.status}`, body?.error.trace_id);
  }
  return res.json() as Promise<T>;
}

const httpApi: Api = {
  login: (companyId, role) =>
    request("/session", { method: "POST", body: JSON.stringify({ company_id: companyId, role }) }),
  feed: () => request("/feed"),
  card: (cardId) => request(`/card/${encodeURIComponent(cardId)}`),
};

// Моки подключаются только в `npm run dev:mock`; в обычной сборке ветка вырезается
export async function getApi(): Promise<Api> {
  if (import.meta.env.VITE_USE_MOCKS === "1") {
    const { mockApi } = await import("./mocks");
    return mockApi;
  }
  return httpApi;
}
