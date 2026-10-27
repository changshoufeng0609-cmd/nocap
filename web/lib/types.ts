// Mirrors receipts/types.py (Claim, Fact, Verdict) and the SSE payloads in docs/api-contract.md.

export type Status = "verified" | "wrong" | "unverifiable" | "approximate";

export type ReasonCode =
  | "match"
  | "value_mismatch"
  | "period_mismatch"
  | "scale_mismatch"
  | "basis_mismatch"
  | "entity_mismatch"
  | "look_ahead"
  | "forward_looking"
  | "no_evidence"
  | "ambiguous"
  | "approximate";

export type Unit = "USD" | "%" | "USD/share" | "shares" | "other";
export type Scale = "units" | "thousands" | "millions" | "billions";
export type Basis = "GAAP" | "non-GAAP";

export interface Claim {
  id: string;
  metric: string;
  metric_text: string;
  value_text: string;
  value: number;
  unit: Unit;
  scale: Scale;
  period_text: string;
  period_type: string;
  comparison: string;
  basis: Basis | null;
  forward_looking: boolean;
  entity: string;
  segment?: string | null;
  span_text: string;
  span_start: number;
  span_end: number;
}

export interface Fact {
  cik: number;
  ticker: string;
  metric: string;
  concept?: string | null;
  basis: Basis;
  segment?: string | null;
  value: number;
  unit: Unit;
  period_start?: string | null;
  period_end: string;
  fiscal_year?: number | null;
  fiscal_period?: string | null;
  source: "xbrl" | "press_release" | "derived";
  derived_from?: string[];
  form: string;
  accession: string;
  accepted_at: string;
  url: string;
  locator?: string | null;
  decimals?: number | null;
}

export interface Verdict {
  claim_id: string;
  status: Status;
  reason: ReasonCode;
  explanation: string;
  expected_value?: number | null;
  evidence: Fact[];
  as_of?: string | null;
}

export interface MetaEvent {
  draft_hash: string;
  cached: boolean;
  passages: number;
  as_of: string | null;
}

export interface DoneEvent {
  counts: Record<Status, number>;
  latency_s: number;
  cost_usd: number;
  model: string;
}

export interface ErrorEvent {
  message: string;
  retryable: boolean;
}

export interface CheckRequest {
  text: string;
  as_of: string | null;
  context: string | null;
  example_id: string | null;
}

export type FixRequest = Pick<CheckRequest, "text" | "as_of" | "context">;
export type FixStage = "checking" | "rewriting" | "verifying";

export interface FixChange {
  claim_id: string;
  before: string;
  after: string;
  /** Python character offsets in the original draft (Unicode code points, not UTF-16). */
  start: number;
  end: number;
  explanation: string;
  evidence: Fact[];
}

export interface FixResult {
  original_text: string;
  rewritten_text: string;
  /** Corrected means every original wrong claim was repaired; ? claims can remain. */
  status: "corrected" | "partial" | "unchanged";
  changes: FixChange[];
  remaining: { claim_id: string; reason: string; explanation: string }[];
  results: { claim: Claim; verdict: Verdict }[];
  counts: Record<Status, number>;
  cost_usd: number;
  latency_s: number;
  model: string;
  extraction_model: string;
}

export interface Example {
  id: string;
  title: string;
  description: string;
  text: string;
  as_of: string | null;
  context: string | null;
}

export interface Health {
  ok: boolean;
  data_snapshot: string;
  companies: string[];
  facts: number;
}

export const STATUSES: Status[] = ["verified", "wrong", "unverifiable", "approximate"];

/** Same limit as the API: longer drafts are rejected. */
export const MAX_DRAFT_CHARS = 6000;
