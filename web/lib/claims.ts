import { formatExpectedValue, inClaimTermsToFull, marginChangeUnit, type DisplayUnit } from "./format";
import { receiptCalculation } from "./calculations";
import { COUNT_KEYS, STATUSES, type Claim, type Fact, type ReasonCode, type Status, type Verdict } from "./types";

export type ResolvedDisplayStatus = Status | "look_ahead";
export type DisplayStatus = ResolvedDisplayStatus | "pending";
export const DISPLAY_STATUSES: ResolvedDisplayStatus[] = ["verified", "wrong", "look_ahead", "unverifiable", "approximate"];

export const STATUS_META: Record<DisplayStatus, { glyph: string; label: string; legend: string }> = {
  verified: { glyph: "✓", label: "Verified", legend: "Matches the filing" },
  wrong: { glyph: "✗", label: "Wrong", legend: "Value, period, scale, basis, or company is off" },
  look_ahead: { glyph: "◷", label: "Not public yet", legend: "Filed after the selected as-of time" },
  unverifiable: { glyph: "?", label: "Unverifiable", legend: "Guidance, or no filed figure to compare" },
  approximate: { glyph: "≈", label: "Approximate", legend: "An estimate within tolerance" },
  pending: { glyph: "…", label: "Checking", legend: "Waiting for the verifier" },
};

/** Reason codes in plain words (receipts/types.py ReasonCode). */
export const REASON_TEXT: Record<ReasonCode, string> = {
  match: "Matches the filing",
  value_mismatch: "Number doesn't match the filing",
  period_mismatch: "Figure is from a different period",
  scale_mismatch: "Scale is off (thousands, millions, billions)",
  basis_mismatch: "GAAP and non-GAAP figures mixed up",
  entity_mismatch: "Figure belongs to another company named in the draft or context",
  look_ahead: "Not yet public at the as-of time",
  forward_looking: "Guidance: no filing can confirm it yet",
  no_evidence: "No filed figure to check against",
  ambiguous: "Period, company, or basis is ambiguous",
  approximate: "Estimate within tolerance",
};

export function reasonText(reason: string): string {
  return REASON_TEXT[reason as ReasonCode] ?? reason.replace(/_/g, " ");
}

const SEVERITY: Record<DisplayStatus, number> = { verified: 1, unverifiable: 2, approximate: 3, pending: 4, look_ahead: 5, wrong: 6 };

export function displayStatus(verdict: Verdict | undefined): DisplayStatus {
  return verdict ? verdict.reason === "look_ahead" ? "look_ahead" : verdict.status : "pending";
}

/** The API's `counts` buckets, with not_public shown as look_ahead (◷). Evaluation keeps the API status. */
export function displayCounts(verdicts: Iterable<Verdict>): Record<ResolvedDisplayStatus, number> {
  const counts: Record<ResolvedDisplayStatus, number> = { verified: 0, wrong: 0, look_ahead: 0, unverifiable: 0, approximate: 0 };
  for (const verdict of verdicts) counts[verdict.reason === "look_ahead" ? "look_ahead" : verdict.status] += 1;
  return counts;
}

/** Whether a stream's reported `counts` match its verdicts, in the server's buckets: with not_public when the server
 * reports it (a look-ahead verdict is never wrong), otherwise the status buckets of older servers and recordings. */
export function countsMatch(reported: Record<string, unknown>, verdicts: Iterable<Pick<Verdict, "status" | "reason">>): boolean {
  const legacy = reported.not_public === undefined;
  const expected: Record<string, number> = { verified: 0, wrong: 0, not_public: 0, unverifiable: 0, approximate: 0 };
  for (const v of verdicts) expected[!legacy && v.reason === "look_ahead" ? "not_public" : v.status] += 1;
  return (legacy ? STATUSES : COUNT_KEYS).every((key) => reported[key] === expected[key]);
}

export function worst(statuses: DisplayStatus[]): DisplayStatus {
  return statuses.reduce<DisplayStatus>((a, b) => (SEVERITY[b] > SEVERITY[a] ? b : a), statuses[0] ?? "verified");
}

export const SOURCE_LABEL: Record<Fact["source"], string> = {
  xbrl: "XBRL",
  press_release: "Press release",
  derived: "Derived",
};

export interface FiledValue {
  value: number;
  unit: DisplayUnit;
  /** How a percentage was computed from the receipts, e.g. "$81.615B vs $68.127B". */
  note?: string;
}

/**
 * The filed figure to show next to the claimed one. expected_value (in the claim's own unit and
 * scale) wins when the verifier provides it; otherwise the receipt in the claim's unit; growth and
 * margin percentages are recomputed from their two receipts for display only.
 */
export function filedValue(claim: Claim, verdict: Verdict): FiledValue | null {
  if (verdict.status === "unverifiable") return null;
  const marginUnit = marginChangeUnit(claim);
  if (verdict.expected_value != null && Number.isFinite(verdict.expected_value)) {
    if (marginUnit) return { value: verdict.expected_value, unit: marginUnit };
    return { value: inClaimTermsToFull(claim, verdict.expected_value), unit: claim.unit };
  }
  if (marginUnit) return null;  // a filed margin level is not the period-to-period change
  const ev = verdict.evidence ?? [];
  if (!ev.length) return null;
  const direct = ev.find((f) => f.unit === claim.unit && f.metric === claim.metric) ?? ev.find((f) => f.unit === claim.unit);
  if (direct) return { value: direct.value, unit: direct.unit };
  if (claim.unit !== "%") return null;
  const calculation = receiptCalculation(claim, verdict);
  return calculation ? { value: calculation.value, unit: "%" } : null;
}

/** Short inline correction for a wrong claim: "$81.273B", or "not public yet" for look-ahead. */
export function correction(claim: Claim, verdict: Verdict): string | null {
  if (verdict.status !== "wrong") return null;
  const filed =
    verdict.expected_value != null ? formatExpectedValue(claim, verdict.expected_value) : null;
  if (verdict.reason === "look_ahead") return filed ? `${filed} · not public yet` : "not public yet";
  return filed;
}

/**
 * Whether the claimed number itself is wrong, so it gets struck through. A look-ahead claim can
 * carry the right number that simply wasn't public yet; only strike it when the value is off too.
 */
export function valueIsWrong(verdict: Verdict | undefined): boolean {
  if (!verdict || verdict.status !== "wrong") return false;
  return verdict.reason !== "look_ahead" || verdict.expected_value != null;
}

/** Earliest EDGAR acceptance among the receipts: when the figure first became public. */
export function firstPublic(verdict: Verdict): string | null {
  const times = (verdict.evidence ?? []).map((f) => f.accepted_at).filter(Boolean);
  if (!times.length) return null;
  return times.reduce((a, b) => (Date.parse(b) < Date.parse(a) ? b : a));
}

export function byDocumentOrder(a: Claim, b: Claim): number {
  return a.span_start - b.span_start || a.span_end - b.span_end || a.id.localeCompare(b.id, "en", { numeric: true });
}

// ---------------------------------------------------------------------------
// Laying out highlights: claims may overlap or share a span (GAAP and non-GAAP
// expansions), so the text is cut at every span and value boundary and each
// flat segment records which claims cover it.
// ---------------------------------------------------------------------------
export interface Segment {
  start: number;
  end: number;
  /** Claims whose span covers this segment. */
  covering: string[];
  /** Claims whose value_text covers this segment. */
  valueOf: string[];
}

export interface DraftLayout {
  segments: Segment[];
  /** Claim marks to render right after the character at this offset (after the value, or the span). */
  marks: Map<number, string[]>;
  /** For each claim, its span length, used to pick the most specific claim under the pointer. */
  spanLength: Map<string, number>;
}

export function layoutDraft(text: string, claims: Claim[]): DraftLayout {
  const n = text.length;
  const clamp = (x: number, lo: number, hi: number) => Math.min(Math.max(Number.isFinite(x) ? x : lo, lo), hi);
  const spans = [...claims].sort(byDocumentOrder).map((c) => {
    const s = clamp(c.span_start, 0, n);
    const e = clamp(c.span_end, s, n);
    let vs = -1;
    if (c.value_text) {
      const k = text.indexOf(c.value_text, s);
      if (k >= 0 && k + c.value_text.length <= e) vs = k;
    }
    const ve = vs >= 0 ? vs + c.value_text.length : -1;
    return { id: c.id, s, e, vs, ve, mark: ve >= 0 ? ve : e };
  });

  const cuts = new Set<number>([0, n]);
  for (const sp of spans) {
    cuts.add(sp.s);
    cuts.add(sp.e);
    cuts.add(sp.mark);
    if (sp.vs >= 0) {
      cuts.add(sp.vs);
      cuts.add(sp.ve);
    }
  }
  const points = [...cuts].sort((a, b) => a - b);
  const segments: Segment[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (a === b) continue;
    segments.push({
      start: a,
      end: b,
      covering: spans.filter((sp) => sp.s <= a && b <= sp.e).map((sp) => sp.id),
      valueOf: spans.filter((sp) => sp.vs >= 0 && sp.vs <= a && b <= sp.ve).map((sp) => sp.id),
    });
  }
  const marks = new Map<number, string[]>();
  for (const sp of spans) marks.set(sp.mark, [...(marks.get(sp.mark) ?? []), sp.id]);
  return { segments, marks, spanLength: new Map(spans.map((sp) => [sp.id, sp.e - sp.s])) };
}
