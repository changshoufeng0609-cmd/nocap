import { API_BASE, costFields } from "./api";
import { readSSE } from "./sse";
import { STATUSES, type Claim, type ErrorEvent, type Verdict } from "./types";

export type AskMode = "numbers" | "filing_quotes";
export type AskStage = "planning" | "verifying" | "reading_filing";
export interface AskRequest { question: string; as_of: string | null; filing_id?: string }
export interface Filing {
  id: string; ticker: string; cik: number; form: "10-Q" | "10-K"; accession: string;
  accepted_at: string; url: string; sha256: string; characters: number;
}
export interface AskOptions {
  companies: { ticker: string; name: string; periods: { fiscal_year: number; fiscal_period: string }[] }[];
  metrics: string[]; examples: string[];
}
export interface Footnote { id: number; accession: string; url: string; form: string; accepted_at: string }
export interface AskItem { sentence: string; claim: Claim; verdict: Verdict; withheld: boolean; footnote_ids: number[] }
interface ResultBase {
  question: string; as_of: string | null; model: string; latency_s: number;
  cost_usd: number; cost_is_estimate: boolean; model_calls: number; notice: string | null;
}
export interface NumberAnswer extends ResultBase {
  kind: "numbers"; answer: string; items: AskItem[]; footnotes: Footnote[];
  resolved_query: { entity: string; metrics: string[]; fiscal_year: number | null;
    fiscal_period: string | null; basis: string | null; segment: string | null; unsupported: boolean };
}
export interface FilingAnswer extends ResultBase {
  kind: "filing_quotes"; filing: Filing; quotes: { text: string; start: number; end: number }[];
  rejected_quotes: number; offset_unit: "unicode_code_points"; whole_document: true;
}
export type AskResult = NumberAnswer | FilingAnswer;
export class AskClientError extends Error {
  constructor(message: string, readonly accounting: Partial<ErrorEvent> = {}) { super(message); }
}

const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const text = (x: unknown, max = 6000): x is string => typeof x === "string" && x.length <= max;
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const integer = (x: unknown): x is number => Number.isSafeInteger(x) && (x as number) >= 0;
const timestamp = (x: unknown): x is string => text(x, 80) && Number.isFinite(Date.parse(x));
const sameTime = (a: unknown, b: string | null) => b === null ? a === null : timestamp(a) && Date.parse(a) === Date.parse(b);
const eligible = (date: string, cutoff: string | null) => cutoff === null || Date.parse(date) <= Date.parse(cutoff);
const strings = (x: unknown): x is string[] => Array.isArray(x) && x.every((s) => text(s, 200));

function secUrl(value: unknown): value is string {
  if (!text(value, 2000)) return false;
  try { const u = new URL(value); return u.protocol === "https:" && u.hostname === "www.sec.gov" &&
    u.pathname.startsWith("/Archives/edgar/data/") && !u.username && !u.password && !u.search && !u.hash; }
  catch { return false; }
}
function isFootnote(x: unknown): x is Footnote {
  return object(x) && integer(x.id) && x.id > 0 && text(x.accession, 30) && /^\d{10}-\d{2}-\d{6}$/.test(x.accession) &&
    secUrl(x.url) && text(x.form, 20) && timestamp(x.accepted_at);
}
export function isFiling(x: unknown): x is Filing {
  if (!object(x) || !text(x.id, 64) || !text(x.ticker, 12) || !integer(x.cik) || x.cik === 0 ||
      !["10-Q", "10-K"].includes(x.form as string) || !text(x.accession, 30) || !/^\d{10}-\d{2}-\d{6}$/.test(x.accession) ||
      !timestamp(x.accepted_at) || !secUrl(x.url) || !text(x.sha256) || !/^[a-f0-9]{64}$/.test(x.sha256) ||
      !integer(x.characters) || x.characters < 1 || x.characters > 1000000) return false;
  return x.id === `${x.ticker}-${x.accession}` && new URL(x.url).pathname.startsWith(`/Archives/edgar/data/${x.cik}/${x.accession.replaceAll("-", "")}/`);
}

export async function fetchAskOptions(signal: AbortSignal): Promise<AskOptions> {
  const response = await fetch(`${API_BASE}/ask/options`, { signal, cache: "no-store" });
  const d: unknown = await response.json();
  if (!response.ok || !object(d) || !Array.isArray(d.companies) || !strings(d.metrics) || !strings(d.examples) ||
      !d.companies.every((c) => object(c) && text(c.ticker, 12) && text(c.name, 200) && Array.isArray(c.periods) &&
        c.periods.every((p) => object(p) && integer(p.fiscal_year) && text(p.fiscal_period, 5)))) throw new AskClientError("The available companies could not be loaded.");
  return d as unknown as AskOptions;
}
export async function fetchFilings(signal: AbortSignal): Promise<Filing[]> {
  const response = await fetch(`${API_BASE}/grounding/filings`, { signal, cache: "no-store" });
  const d: unknown = await response.json();
  if (!response.ok || !object(d) || !Array.isArray(d.filings) || !d.filings.every(isFiling)) throw new AskClientError("The filing list could not be loaded.");
  return d.filings;
}

function isNumberAnswer(d: Record<string, unknown>, req: AskRequest): d is Record<string, unknown> & NumberAnswer {
  const q = d.resolved_query;
  if (!object(q) || !text(q.entity, 120) || !strings(q.metrics) || q.metrics.length > 4 ||
      !(q.fiscal_year === null || integer(q.fiscal_year)) || !(q.fiscal_period === null || ["Q1", "Q2", "Q3", "Q4", "FY"].includes(q.fiscal_period as string)) ||
      !(q.basis === null || ["GAAP", "non-GAAP"].includes(q.basis as string)) || !(q.segment === null || text(q.segment, 120)) ||
      typeof q.unsupported !== "boolean" || !text(d.answer, 15000) || !Array.isArray(d.items) || d.items.length > 4 ||
      !Array.isArray(d.footnotes) || d.footnotes.length > 100 || !d.footnotes.every(isFootnote)) return false;
  const notes = new Map(d.footnotes.map((n) => [n.id, n]));
  if (notes.size !== d.footnotes.length || d.footnotes.some((n) => !eligible(n.accepted_at, req.as_of))) return false;
  const ids = new Set();
  const used = new Set();
  const accepted = [];
  for (const row of d.items) {
    if (!object(row) || !text(row.sentence, 3000) || !object(row.claim) || !object(row.verdict) || typeof row.withheld !== "boolean" ||
        !Array.isArray(row.footnote_ids) || !row.footnote_ids.every((n) => integer(n) && notes.has(n)) ||
        new Set(row.footnote_ids).size !== row.footnote_ids.length) return false;
    const c = row.claim, v = row.verdict;
    const footnoteIds = row.footnote_ids as number[];
    if (!text(c.id, 30) || !/^a\d+$/.test(c.id) || ids.has(c.id) || !finite(c.value) || !text(c.value_text, 300) ||
        c.span_text !== row.sentence || c.span_start !== 0 || c.span_end !== Array.from(row.sentence).length ||
        v.claim_id !== c.id || !STATUSES.includes(v.status as Verdict["status"]) || !text(v.explanation) ||
        !sameTime(v.as_of, req.as_of) || !Array.isArray(v.evidence) || !v.evidence.every((f) => object(f) && secUrl(f.url) &&
          text(f.accession, 30) && timestamp(f.accepted_at))) return false;
    ids.add(c.id);
    if (row.withheld) { if (row.footnote_ids.length) return false; }
    else {
      if (v.status !== "verified" || !v.evidence.length || !row.footnote_ids.length) return false;
      if (v.evidence.some((f) => !eligible(f.accepted_at, req.as_of))) return false;
      for (const n of row.footnote_ids) {
        const note = notes.get(n)!;
        if (!v.evidence.some((f) => f.accession === note.accession && f.url === note.url && Date.parse(f.accepted_at) === Date.parse(note.accepted_at))) return false;
        used.add(n);
      }
      if (v.evidence.some((f) => !footnoteIds.some((n) => notes.get(n)!.accession === f.accession && notes.get(n)!.url === f.url))) return false;
      accepted.push(`${row.sentence} ${row.footnote_ids.map((n) => `[${n}]`).join("")}`);
    }
  }
  return d.answer === accepted.join("\n") && used.size === notes.size && !(q.unsupported && accepted.length);
}

function isFilingAnswer(d: Record<string, unknown>, req: AskRequest, selected?: Filing): d is Record<string, unknown> & FilingAnswer {
  if (!isFiling(d.filing) || d.filing.id !== req.filing_id || !selected ||
      ["id", "sha256", "characters", "url", "accession", "accepted_at"].some((k) => d.filing && (d.filing as unknown as Record<string, unknown>)[k] !== (selected as unknown as Record<string, unknown>)[k]) ||
      !eligible(d.filing.accepted_at, req.as_of) || d.whole_document !== true || d.offset_unit !== "unicode_code_points" ||
      !integer(d.rejected_quotes) || d.rejected_quotes > 5 || !Array.isArray(d.quotes) || d.quotes.length > 5 ||
      d.quotes.length + d.rejected_quotes > 5) return false;
  const quotes = new Set();
  for (const q of d.quotes) {
    if (!object(q) || !text(q.text, 3200) || !integer(q.start) || !integer(q.end) || q.end > d.filing.characters ||
        q.end - q.start !== Array.from(q.text).length || q.end - q.start < 24 || q.end - q.start > 1600 || quotes.has(q.text)) return false;
    quotes.add(q.text);
  }
  return true;
}

export async function streamAsk(mode: AskMode, req: AskRequest, onProgress: (stage: AskStage) => void,
                                signal: AbortSignal, selectedFiling?: Filing): Promise<AskResult> {
  const response = await fetch(`${API_BASE}/${mode === "numbers" ? "ask" : "grounding"}`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question: req.question.trim(), as_of: req.as_of,
      ...(mode === "filing_quotes" ? { filing_id: req.filing_id } : {}) }), cache: "no-store", signal,
  });
  if (!response.ok || !response.body || !(response.headers.get("content-type") ?? "").includes("text/event-stream")) {
    throw new AskClientError("The question service is unavailable. Please try again later.");
  }
  const invalid = () => new AskClientError("The answer did not pass the response checks. No answer was accepted.");
  for await (const message of readSSE(response.body, signal)) {
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    let d: unknown;
    try { d = JSON.parse(message.data); } catch { throw invalid(); }
    if (!object(d)) throw invalid();
    if (message.event === "error") throw new AskClientError(text(d.message, 2000) ? d.message : "The question failed.", costFields(d));
    if (message.event === "progress") {
      if (!(mode === "numbers" ? ["planning", "verifying"] : ["reading_filing"]).includes(d.stage as string)) throw invalid();
      onProgress(d.stage as AskStage);
    } else if (message.event === "result") {
      if (d.kind !== mode || d.question !== req.question.trim() || !sameTime(d.as_of, req.as_of) || d.model !== "muse-spark-1.3" ||
          !finite(d.latency_s) || d.latency_s < 0 || Object.keys(costFields(d)).length !== 3 ||
          (d.model_calls as number) > 1 || !(d.notice === null || text(d.notice))) throw invalid();
      if (mode === "numbers" ? !isNumberAnswer(d, req) : !isFilingAnswer(d, req, selectedFiling)) throw invalid();
      return d as unknown as AskResult;
    } else throw invalid();
  }
  throw new AskClientError("The connection ended before the question finished.");
}

/** Export the accepted content exactly; withheld text never enters the numeric answer. */
export function exportAnswer(result: AskResult): string {
  const header = `NoCap · ${result.kind === "numbers" ? "Verified numeric answer" : "Filing quotations"}\nQuestion: ${result.question}\nAs of: ${result.as_of ?? "latest eligible filings"}\n`;
  if (result.kind === "numbers") {
    const q = result.resolved_query;
    const resolved = [q.entity, q.fiscal_period, q.fiscal_year === null ? "" : `FY${q.fiscal_year}`, q.basis, q.segment].filter(Boolean).join(" · ");
    return `${header}Resolved lookup: ${resolved}; ${q.metrics.join(", ")}\n\n${result.answer || "No answer was accepted."}\n\n${result.footnotes.map((n) => `[${n.id}] ${n.form} · ${n.accession} · accepted ${n.accepted_at}\n${n.url}`).join("\n")}\n${result.notice ? `\nNote: ${result.notice}\n` : ""}`;
  }
  return `${header}Source: ${result.filing.ticker} ${result.filing.form} · ${result.filing.accession}\nAccepted: ${result.filing.accepted_at}\n${result.filing.url}\n\n${result.quotes.map((q, i) => `[${i + 1}] “${q.text}”\nNormalized filing characters ${q.start}–${q.end} (Unicode code points, end exclusive).`).join("\n\n")}\n\nExact quotation matches establish provenance, not factual verification or relevance. Quoted numbers and forecasts are not independently verified here.\n${result.notice ? `\nNote: ${result.notice}\n` : ""}`;
}
