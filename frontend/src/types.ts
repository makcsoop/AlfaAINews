// Типы ответов API. Повторяют backend/schemas (InsightCard, RunTrace) в части, которую показывает интерфейс.

export type UserRole = "owner" | "ceo" | "cfo" | "chief_accountant" | "treasurer" | "other";
export type ClaimType = "fact" | "bank_view" | "market_view" | "model_inference" | "bank_offer";
export type Polarity = "risk" | "opportunity" | "neutral" | "mixed";
export type Confidence = "high" | "medium" | "low";

export interface Evidence {
  source_id: string;
  quote?: string | null;
  url?: string | null;
  doc_date?: string | null;
  verified?: boolean | null;
}

export interface Claim {
  claim_id: string;
  type: ClaimType;
  text: string;
  evidence: Evidence[];
  based_on: Record<string, string[]>;
  polarity?: Polarity | null;
  horizon?: string | null;
  is_advertising: boolean;
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
  stage: "fast" | "deep";
  analysis_ref?: string | null;
  event: CardEvent;
  relevance: RelevanceScore;
  what_happened: string[];
  why_it_matters: string[];
  consequences: string[];
  actions: Action[];
  bank_and_market: string[];
  what_would_change: string[];
  surprise?: Surprise | null;
  claims: Claim[];
  confidence: Confidence;
  confidence_reasons: string[];
  review_status: "published" | "on_review";
  trace_id?: string | null;
}

export interface SessionInfo {
  company_id: string;
  role: UserRole;
  user_id: string;
}

export interface ApiError {
  error: { code: string; message: string; trace_id: string };
}
