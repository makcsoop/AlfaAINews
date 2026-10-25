// Типы ответов API. Повторяют backend/schemas (InsightCard, RunTrace) в части, которую показывает интерфейс.

export type UserRole = "owner" | "ceo" | "cfo" | "chief_accountant" | "treasurer" | "other";
export type ClaimType = "fact" | "bank_view" | "market_view" | "model_inference" | "bank_offer";
export type Polarity = "risk" | "opportunity" | "neutral" | "mixed";
export type Confidence = "high" | "medium" | "low";

export interface Evidence {
  source_id: string;
  quote?: string | null;
  span?: [number, number] | null;
  url?: string | null;
  doc_date?: string | null;
  verified?: boolean | null; // цитата найдена в тексте источника
  feed_id?: string | null; // лента материала из config/sources.yaml: «prime», «cbr_press»
}

// Итог проверки утверждения кодом: refs, label, numbers, dates — true, если прошло
export type ClaimChecks = Record<string, boolean | string>;

export interface Claim {
  claim_id: string;
  type: ClaimType;
  text: string;
  evidence: Evidence[];
  based_on: Record<string, string[]>;
  polarity?: Polarity | null;
  horizon?: string | null;
  verified: ClaimChecks;
  is_advertising: boolean;
  trigger?: string | null;
}

export interface Contribution {
  factor: string;
  value: number;
  client_features: string[];
  sign: -1 | 0 | 1;
}

export interface RelevanceScore {
  story_id: string;
  company_id: string;
  role?: UserRole | null;
  score: number;
  quality: number;
  freshness?: number | null;
  impact?: number | null; // сила влияния для показа: без свежести, 1 — сильный сдвиг значимого фактора
  contributions: Contribution[];
  polarity: Polarity;
  deep_analysis: boolean;
}

export interface StorySource {
  source_id: string;
  tier: number;
  url?: string | null;
  title?: string | null;
}

export interface CardEvent {
  story_id: string;
  event_type: string | null;
  status: string;
  title: string;
  first_seen: string;
  sources: StorySource[];
  corroboration: number;
}

export interface Action {
  type: "analysis_question" | "scenario" | "bank_product";
  text: string;
  is_advertising: boolean;
  kind?: ClaimType | null; // схемы 0.2.1: метка и ссылки (инвариант 4)
  refs?: string[];
}

// «Что изменит вывод» с меткой и ссылками (схемы 0.2.1); what_would_change — те же тексты строками
export interface Condition {
  text: string;
  kind?: ClaimType | null;
  refs?: string[];
}

export interface Surprise {
  metric: string;
  unit?: string | null;
  actual: number;
  market_expected?: number | null;
  market_ref?: string | null;
  bank_expected?: number | null;
  bank_ref?: string | null;
  direction?: "above" | "below" | "in_line" | null;
}

export interface InsightCard {
  card_id: string;
  company_id: string;
  user_role: UserRole;
  version?: number;
  updated_at?: string | null;
  stage: "fast" | "deep";
  analysis_ref?: string | null;
  headline?: string | null; // заголовок модели; нет — event.title
  event: CardEvent;
  relevance: RelevanceScore;
  what_happened: string[];
  why_it_matters: string[];
  consequences: string[];
  actions: Action[];
  bank_and_market: string[];
  what_would_change: string[];
  conditions?: Condition[];
  surprise?: Surprise | null;
  claims: Claim[];
  confidence: Confidence;
  confidence_reasons: string[];
  review_status: "published" | "on_review";
  trace_id?: string | null;
}

// Компания витрины демо-входа (GET /demo/companies): только витринные поля
export interface ShowcaseCompany {
  company_id: string;
  kind: "case" | "demo";
  title: string;
  description: string;
  archetype: string | null;
  default_role: UserRole;
  okved_code: string;
  okved_name: string;
  region: string;
  fea_countries: string[];
  products: { loans: boolean; leasing: boolean; deposits: boolean; guarantees: boolean; factoring: boolean; fea: boolean };
}

export interface SessionInfo {
  company_id: string;
  role: UserRole;
  user_id: string;
}

export interface ApiError {
  error: { code: string; message: string; trace_id: string };
}

export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  result_hash?: string | null;
  ok: boolean;
  at: string;
}

export interface LlmCall {
  model: string;
  purpose: string;
  tokens_in: number;
  tokens_out: number;
  cost_rub: number;
  at: string;
  cached: boolean;
  original_cost_rub?: number | null;
}

// Промежуточные результаты прогона (схема 0.2.3): план, что нашли инструменты, аналитики, арбитр, критик
export interface TracePlanStep {
  tool: string;
  args: Record<string, unknown>;
  why: string;
  origin: string;
}

export interface TraceToolResult {
  tool: string;
  found: number;
  items: { id: string; kind: string; title: string }[];
}

export interface TraceChannel {
  channel: string;
  direction: "up" | "down" | "flat";
  strength: number;
  horizon?: string | null;
  rationale?: string | null;
}

export interface TraceAnalystReport {
  analyst: "business" | "finance";
  model: string;
  channels: TraceChannel[];
  summary: string;
  open_questions: string[];
}

export interface TraceArbiter {
  agreement: Record<string, "agree" | "disagree" | "single">;
  disputes: string[];
  bank_check: string[];
  on_review: boolean;
}

export interface TraceCriticRound {
  round: number;
  ok: boolean;
  failures: string[];
  planner_task?: string | null;
}

export interface RunTrace {
  run_id: string;
  story_id: string;
  company_id?: string | null;
  steps: string[];
  tool_calls: ToolCall[];
  llm_calls: LlmCall[];
  verify_rounds: number;
  verify_failures: string[];
  cost_rub: number;
  started_at: string;
  finished_at?: string | null;
  plan?: TracePlanStep[];
  gaps?: string[];
  tool_results?: TraceToolResult[];
  analyst_reports?: TraceAnalystReport[];
  arbiter?: TraceArbiter | null;
  critic_rounds?: TraceCriticRound[];
  card_source?: "llm" | "template" | "fixture" | null;
  analysis_cached?: boolean;
  saved_rub?: number;
}

export interface StepCost {
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_rub: number;
}

export interface TraceCost {
  trace_id: string;
  cost_rub: number;
  cost_by_step: Record<string, StepCost>;
  budget: { daily_limit_rub?: number; spent_rub?: number; remaining_rub?: number; mode?: string };
}

export interface DeepRun {
  story_id: string;
  company_id: string;
  role: string;
  status: "published" | "on_review" | "dropped" | "failed";
  reason?: string | null;
  score?: number | null;
  trace_id?: string | null;
  cost_rub: number;
  at: string;
}

export interface AskResponse {
  answer: string;
  refs: string[];
  tools_used: string[];
  trace_id: string;
  status: "ok" | "refused" | "degraded" | "unclear";
  cost_rub: number; // ₽ на вызовы моделей для ответа; из кеша — 0
  cached: boolean; // все вызовы взяты из кеша

}

export type FeedbackKind = "useful" | "not_relevant" | "wrong" | "click_source";
