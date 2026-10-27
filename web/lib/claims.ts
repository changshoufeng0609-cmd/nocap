import { formatValue, inClaimTermsToFull } from "./format";
import type { Claim, Fact, ReasonCode, Status, Unit, Verdict } from "./types";

export type DisplayStatus = Status | "pending";

export const STATUS_META: Record<Status, { glyph: string; label: string; legend: string }> = {
  verified: { glyph: "✓", label: "Verified", legend: "Matches the filing" },
  wrong: { glyph: "✗", label: "Wrong", legend: "Value, period, scale, basis, company, or timing is off" },
  unverifiable: { glyph: "?", label: "Unverifiable", legend: "Guidance, or no filed figure to compare" },
  approximate: { glyph: "≈", label: "Approximate", legend: "An estimate within tolerance" },
};

/** Reason codes in plain words (receipts/types.py ReasonCode). */
export const REASON_TEXT: Record<ReasonCode, string> = {
  match: "Matches the filing",
  value_mismatch: "Number doesn't match the filing",
  period_mismatch: "Figure is from a different period",
  scale_mismatch: "Scale is off (thousands, millions, billions)",
  basis_mismatch: "GAAP and non-GAAP figures mixed up",
  entity_mismatch: "Figure belongs to another company",
  look_ahead: "Not yet public at the as-of time",
  forward_looking: "Guidance: no filing can confirm it yet",
  no_evidence: "No filed figure to check against",
  ambiguous: "Period, company, or basis is ambiguous",
  approximate: "Estimate within tolerance",
};

export function reasonText(reason: string): string {
  return REASON_TEXT[reason as ReasonCode] ?? reason.replace(/_/g, " ");
}

const SEVERITY: Record<DisplayStatus, number> = { verified: 1, unverifiable: 2, approximate: 3, pending: 4, wrong: 5 };

export function displayStatus(verdict: Verdict | undefined): DisplayStatus {
  return verdict ? verdict.status : "pending";
}

export function worst(statuses: DisplayStatus[]): DisplayStatus {
  return statuses.reduce<DisplayStatus>((a, b) => (SEVERITY[b] > SEVERITY[a] ? b : a), statuses[0] ?? "verified");
}

export const SOURCE_LABEL: Record<Fact["source"], string> = {
  xbrl: "XBRL",
  press_release: "Press release",
  derived: "Derived",
};

/** Recomputed percentages use one decimal, like the verifier's explanations (19.8%). */
const round1 = (x: number) => Math.round(x * 10) / 10;

export interface FiledValue {
  value: number;
  unit: Unit;
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
  if (verdict.expected_value != null && Number.isFinite(verdict.expected_value)) {
    return { value: inClaimTermsToFull(claim, verdict.expected_value), unit: claim.unit };
  }
  const ev = verdict.evidence ?? [];
  if (!ev.length) return null;
  const direct = ev.find((f) => f.unit === claim.unit && f.metric === claim.metric) ?? ev.find((f) => f.unit === claim.unit);
  if (direct) return { value: direct.value, unit: direct.unit };
  if (claim.unit !== "%") return null;
  const usd = ev.filter((f) => f.unit === "USD");
  if (claim.metric === "gross_margin") {
    const gp = usd.find((f) => f.metric === "gross_profit");
    const rev = gp && usd.find((f) => f.metric === "revenue" && f.period_end === gp.period_end);
    if (gp && rev && rev.value) {
      return { value: round1((gp.value / rev.value) * 100), unit: "%", note: `${formatValue(gp.value, "USD")} / ${formatValue(rev.value, "USD")}` };
    }
    return null;
  }
  // Growth: the latest receipt against the one a quarter (QoQ) or a year (YoY) earlier.
  const target = claim.comparison === "YoY" ? 364 : claim.comparison === "QoQ" || claim.comparison === "sequential" ? 91 : null;
  if (target === null) return null;
  const same = usd.filter((f) => f.metric === usd[0]?.metric);
  if (same.length < 2) return null;
  const day = (s: string) => Date.parse(`${s.slice(0, 10)}T00:00:00Z`) / 86_400_000;
  const current = same.reduce((a, b) => (day(b.period_end) > day(a.period_end) ? b : a));
  const prior = same
    .filter((f) => day(f.period_end) < day(current.period_end))
    .map((f) => ({ f, off: Math.abs(day(current.period_end) - day(f.period_end) - target) }))
    .filter((x) => x.off <= 20)
    .sort((a, b) => a.off - b.off)[0]?.f;
  if (!prior?.value) return null;
  return {
    value: round1((current.value / prior.value - 1) * 100),
    unit: "%",
    note: `${formatValue(current.value, "USD")} vs ${formatValue(prior.value, "USD")}`,
  };
}

/** Short inline correction for a wrong claim: "$81.273B", or "not public yet" for look-ahead. */
export function correction(claim: Claim, verdict: Verdict): string | null {
  if (verdict.status !== "wrong") return null;
  const filed =
    verdict.expected_value != null ? formatValue(inClaimTermsToFull(claim, verdict.expected_value), claim.unit) : null;
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
