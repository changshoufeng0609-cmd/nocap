import type { Claim, CostReport, Fact, Scale, Unit } from "./types";

export function formatErrorCost(report: CostReport): string {
  if (report.cost_usd === undefined) return "Model cost unavailable.";
  return `${report.cost_is_estimate ? "Estimated model cost" : "Model cost"}: ${formatCost(report.cost_usd)}.`;
}

const SCALE_FACTOR: Record<Scale, number> = { units: 1, thousands: 1e3, millions: 1e6, billions: 1e9 };

const MINUS = "−";

export type DisplayUnit = Unit | "bps" | "pp";

// Keep the number and point-unit grammar aligned with receipts.verify._MARGIN_CHANGE_VALUE.
const SOURCE_NUMBER = String.raw`(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)`;
// No lookbehind (Safari before 16.4 rejects it): the character before the number is matched instead.
const MARGIN_CHANGE_VALUE = new RegExp(String.raw`(?:^|[^\w.])[+−-]?${SOURCE_NUMBER}\s*(basis\s+points?|bps?|percentage\s+points?|pp|pts)(?!\w)`, "gi");

/** Margin differences keep the draft's point unit, even when cached extraction says % or millions. */
export function marginChangeUnit(claim: Claim): "bps" | "pp" | null {
  if (!/\b(?:(?:gross|net)\s+(?:profit\s+)?|operating\s+)margins?\b/i.test(claim.metric_text)) return null;
  const text = claim.value_text ?? "";
  const units = [...text.matchAll(MARGIN_CHANGE_VALUE)];
  if (units.length !== 1 || [...text.matchAll(new RegExp(SOURCE_NUMBER, "g"))].length !== 1) return null;
  return /^(?:basis|bp)/i.test(units[0][1]) ? "bps" : "pp";
}

function trimmed(x: number, maxDecimals: number): string {
  return x
    .toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: maxDecimals })
    .replace(/^-/, "");
}

/** $96.221B, $592M, $2.46, 19.8%: a value in full units, compact and exact to three decimals. */
export function formatValue(value: number, unit: DisplayUnit): string {
  const sign = value < 0 ? MINUS : "";
  const a = Math.abs(value);
  if (unit === "bps" || unit === "pp") return `${sign}${trimmed(a, 6)} ${unit}`;
  if (unit === "%") return `${sign}${trimmed(a, 2)}%`;
  if (unit === "USD/share") return `${sign}$${a.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
  const prefix = unit === "USD" ? "$" : "";
  const suffix = unit === "shares" ? " shares" : "";
  for (const [factor, letter] of [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ] as const) {
    if (a >= factor) return `${sign}${prefix}${trimmed(a / factor, 3)}${letter}${suffix}`;
  }
  return `${sign}${prefix}${trimmed(a, 2)}${suffix}`;
}

/** The claim's value in full units (scale applied), e.g. 96.2 billions -> 96,200,000,000. */
export function claimFullValue(claim: Claim): number {
  const scaled = claim.unit === "USD" || claim.unit === "shares" || claim.unit === "other";
  return scaled ? claim.value * (SCALE_FACTOR[claim.scale] ?? 1) : claim.value;
}

/** A number in the claim's own unit and scale (like Verdict.expected_value) to full units. */
export function inClaimTermsToFull(claim: Claim, x: number): number {
  const scaled = claim.unit === "USD" || claim.unit === "shares" || claim.unit === "other";
  return scaled ? x * (SCALE_FACTOR[claim.scale] ?? 1) : x;
}

export function formatClaimed(claim: Claim): string {
  if (marginChangeUnit(claim)) return claim.value_text;
  return formatValue(claimFullValue(claim), claim.unit);
}

/** Server margin-change expectations are already in source bps/pp; model scale is irrelevant. */
export function formatExpectedValue(claim: Claim, value: number): string {
  const marginUnit = marginChangeUnit(claim);
  return marginUnit ? formatValue(value, marginUnit) : formatValue(inClaimTermsToFull(claim, value), claim.unit);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** 2026-08-26 20:21:19 UTC, the same format the verifier uses in its explanations. */
export function formatUtc(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(
    d.getUTCMinutes(),
  )}:${pad(d.getUTCSeconds())} UTC`;
}

/** 2026-08-26 20:00 UTC (minutes only, for the as-of summary). */
export function formatUtcMinutes(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(
    d.getUTCMinutes(),
  )} UTC`;
}

/** Value for <input type="datetime-local"> read as UTC: 2026-08-26T20:00. */
export function toUtcInputValue(d: Date): string {
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(
    d.getUTCMinutes(),
  )}`;
}

/** The inverse: 2026-08-26T20:00 (UTC wall time) -> 2026-08-26T20:00:00Z, or null when incomplete. */
export function fromUtcInputValue(v: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(v);
  if (!m) return null;
  return `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6] ?? "00"}Z`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parseDay(s: string): { y: number; m: number; d: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  return m ? { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) } : null;
}

/** Jul 26, 2026 */
export function formatDay(s: string): string {
  const p = parseDay(s);
  return p ? `${MONTHS[p.m - 1]} ${p.d}, ${p.y}` : s;
}

/** Apr 27 – Jul 26, 2026, or a single day for instants. */
export function formatPeriodRange(fact: Fact): string {
  const end = parseDay(fact.period_end);
  const start = fact.period_start ? parseDay(fact.period_start) : null;
  if (!end) return fact.period_end;
  if (!start) return formatDay(fact.period_end);
  const startText = start.y === end.y ? `${MONTHS[start.m - 1]} ${start.d}` : formatDay(fact.period_start!);
  return `${startText} – ${formatDay(fact.period_end)}`;
}

/**
 * Q2 FY2027 for an offset fiscal year, Q3 2026 when the fiscal year is the calendar year
 * (the verifier's own convention). Heuristic: the fiscal quarter matches the calendar quarter
 * its period ends in, within the same year.
 */
export function formatFiscalPeriod(fact: Fact): string | null {
  const fp = fact.fiscal_period;
  const fy = fact.fiscal_year;
  if (!fp || !fy) return null;
  const end = parseDay(fact.period_end);
  if (fp === "FY") {
    const calendar = end ? end.y === fy && (end.m === 12 || (end.m === 1 && end.d <= 7)) : false;
    return calendar ? `Full year ${fy}` : `Fiscal ${fy}`;
  }
  const q = /^Q([1-4])$/.exec(fp);
  const calendar = end && q ? end.y === fy && Math.ceil(end.m / 3) === Number(q[1]) : false;
  return calendar ? `${fp} ${fy}` : `${fp} FY${fy}`;
}

export function formatLatency(s: number): string {
  if (s < 10) return `${s.toFixed(1)} s`;
  return `${Math.round(s)} s`;
}

export function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  if (usd < 0.1) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/** 21 min, 3 h 25 min, 56 days */
export function formatDuration(ms: number): string {
  const min = Math.round(Math.abs(ms) / 60000);
  if (min < 1) return "under a minute";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 48) return min % 60 ? `${h} h ${min % 60} min` : `${h} h`;
  return `${Math.round(h / 24)} days`;
}
