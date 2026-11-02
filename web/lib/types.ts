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
  | "approximate"
  /** Verified by the company's own filed 8-K earnings release text, for a figure no filing fact covers. */
  | "release_statement";

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
  /** release_text: a sentence of the filed 8-K earnings release, quoted in `locator`. */
  source: "xbrl" | "press_release" | "derived" | "release_text";
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

export interface CostReport {
  cost_usd?: number;
  cost_is_estimate?: boolean;
  model_calls?: number;
}

/** Digit-based candidates linked to extracted claims, independent of their verdicts. */
export interface NumericCoverage {
  version: 1;
  candidate_count: number;
  covered_count: number;
  excluded_count: number;
  /** Python Unicode code-point offsets in the exact checked draft. */
  uncovered: { start: number; end: number; text: string }[];
}

/** done/result `counts` buckets (receipts.types.count_key): a ◷ look-ahead verdict is not_public, never wrong.
 * Servers and recordings from before 2026-10-31 omit not_public and count a look-ahead verdict under its status. */
export type Counts = Record<Status, number> & { not_public?: number };
export const COUNT_KEYS = ["verified", "wrong", "not_public", "unverifiable", "approximate"] as const;

/** Admission refusals (docs/api-contract.md#errors): no model call started, and the $0 samples still work. */
export type RefusalReason = "live_paused" | "budget_unavailable" | "daily_budget" | "rate_limited" | "filing_question_limit";
export const REFUSAL_REASONS: readonly RefusalReason[] = ["live_paused", "budget_unavailable", "daily_budget", "rate_limited", "filing_question_limit"];

export interface DoneEvent extends CostReport {
  coverage?: NumericCoverage;
  counts: Counts;
  latency_s: number;
  cost_usd: number;
  model: string;
}

export interface ErrorEvent extends CostReport {
  message: string;
  retryable: boolean;
  /** Present only on an admission refusal. */
  reason?: RefusalReason;
  /** How long Meta's API asked NoCap to wait, when the message says so. */
  retry_after_s?: number;
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

export interface FixResult extends CostReport {
  original_coverage?: NumericCoverage;
  coverage?: NumericCoverage;
  original_text: string;
  rewritten_text: string;
  /** Corrected means every original wrong claim was repaired; ? claims can remain. */
  status: "corrected" | "partial" | "unchanged";
  changes: FixChange[];
  remaining: { claim_id: string; reason: string; explanation: string }[];
  results: { claim: Claim; verdict: Verdict }[];
  counts: Counts;
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
  live_requests_paused?: boolean;
  /** Scheduled end (UTC ISO) of an open live window, or null when none is set or live work is paused. */
  live_until?: string | null;
  draft_cache_policy?: "snapshot_only" | "persistent" | "disabled";
  ok: boolean;
  data_snapshot: string;
  companies: string[];
  facts: number;
}

export type SlideBox = [number, number, number, number];
export type SlideStage = "segmenting" | "reading" | "verifying";
export interface SlideRequest {
  image_data_url: string;
  context: string | null;
  as_of: string | null;
}
export interface SlideMeta {
  models: string[];
  image: { width: number; height: number };
  /** true for a built-in sample replaying its recorded live run at $0 */
  cached: boolean;
  recorded_utc?: string | null;
  as_of: string | null;
}
export interface SlideRegion {
  id: string;
  label: string;
  box: SlideBox;
  source: "sam" | "spark";
}
export interface SlideClaim extends Claim {
  region_id: string;
  box: SlideBox;
  reading: "printed" | "axis_estimate";
}
export interface SlideDone {
  counts: Counts;
  claims: number;
  model_calls: number;
  latency_s: number;
  cost_usd: number;
  cost_is_estimate: boolean;
  warnings: string[];
}

export const STATUSES: Status[] = ["verified", "wrong", "unverifiable", "approximate"];

/** Same limit as the API: longer drafts are rejected. */
export const MAX_DRAFT_CHARS = 12000;
