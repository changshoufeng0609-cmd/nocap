import type { NumericCoverage } from "./types";

const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;

/** Invalid or legacy metadata is unavailable, never an empty/all-covered result. */
export function parseCoverage(value: unknown, text: string): NumericCoverage | undefined {
  if (!value || typeof value !== "object") return undefined;
  const c = value as NumericCoverage;
  if (c.version !== 1 || !count(c.candidate_count) || !count(c.covered_count) ||
      !count(c.excluded_count) || !Array.isArray(c.uncovered) ||
      c.covered_count > c.candidate_count || c.uncovered.length !== c.candidate_count - c.covered_count) return undefined;
  const chars = Array.from(text);
  if (c.candidate_count + c.excluded_count > chars.length) return undefined;
  let cursor = 0;
  for (const item of c.uncovered) {
    if (!item || !count(item.start) || !count(item.end) || item.start < cursor ||
        item.end <= item.start || item.end > chars.length || typeof item.text !== "string" ||
        chars.slice(item.start, item.end).join("") !== item.text) return undefined;
    cursor = item.end;
  }
  return { version: 1, candidate_count: c.candidate_count, covered_count: c.covered_count,
    excluded_count: c.excluded_count, uncovered: c.uncovered.map(({ start, end, text }) => ({ start, end, text })) };
}

/** A short location excerpt, preserving the candidate's exact text and Unicode boundaries. */
export function coverageExcerpt(text: string, item: NumericCoverage["uncovered"][number]) {
  const chars = Array.from(text);
  const before = chars.slice(Math.max(0, item.start - 45), item.start).join("");
  const after = chars.slice(item.end, item.end + 45).join("");
  return {
    line: chars.slice(0, item.start).filter((char) => char === "\n").length + 1,
    before: `${item.start > 45 ? "…" : ""}${before}`,
    value: item.text,
    after: `${after}${item.end + 45 < chars.length ? "…" : ""}`,
  };
}
