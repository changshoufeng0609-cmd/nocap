import { API_BASE, costFields } from "./api";
import { parseCoverage } from "./coverage";
import { readSSE } from "./sse";
import { STATUSES, type CheckRequest, type Claim, type DoneEvent, type ErrorEvent, type Verdict } from "./types";

export interface PrivateStatus { available: boolean; message: string }
export interface PrivateResult { claims: Claim[]; verdicts: Record<string, Verdict>; done: DoneEvent }
export class PrivateCheckError extends Error {
  constructor(message: string, readonly accounting: Partial<ErrorEvent> = {}) { super(message); }
}
const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const text = (x: unknown): x is string => typeof x === "string";
const safeReceipt = (value: unknown) => {
  if (!object(value) || !text(value.url) || !text(value.accession) || !text(value.form) || !text(value.accepted_at)) return false;
  try { const url = new URL(value.url); return url.protocol === "https:" && ["www.sec.gov", "sec.gov"].includes(url.hostname) && !url.username && !url.password; }
  catch { return false; }
};

export async function fetchPrivateStatus(signal: AbortSignal): Promise<PrivateStatus> {
  const response = await fetch(`${API_BASE}/private/status`, { signal, cache: "no-store" });
  const data: unknown = await response.json();
  if (!response.ok || !object(data) || data.backend !== "modal" || data.model !== "muse-glimmer" ||
      data.offline !== false || data.response_cache !== false || typeof data.available !== "boolean" || !text(data.message)) {
    throw new Error("Private mode is unavailable on this server.");
  }
  return { available: data.available, message: data.message };
}

/** Only the dedicated private endpoint receives the draft. There is no retry to /check. */
export async function runPrivateCheck(request: Pick<CheckRequest, "text" | "context" | "as_of">, signal: AbortSignal): Promise<PrivateResult> {
  const response = await fetch(`${API_BASE}/private/check`, {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ text: request.text, context: request.context, as_of: request.as_of }),
    cache: "no-store", signal,
  });
  if (!response.ok || !response.body || !(response.headers.get("content-type") ?? "").includes("text/event-stream")) {
    throw new PrivateCheckError("The private check is unavailable. Your draft was not retried with another backend.");
  }
  const chars = Array.from(request.text);
  const claims = new Map<string, Claim>();
  const verdicts: Record<string, Verdict> = {};
  let meta = false;
  const invalid = () => new PrivateCheckError("The private response could not be verified safely. No fallback was used.");
  for await (const message of readSSE(response.body, signal)) {
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    let d: unknown;
    try { d = JSON.parse(message.data); } catch { throw invalid(); }
    if (!object(d)) throw invalid();
    if (message.event === "error") {
      throw new PrivateCheckError(text(d.message) ? d.message : "The private check failed.", costFields(d));
    }
    if (message.event === "meta") {
      if (meta || d.private !== true || d.backend !== "modal" || d.model !== "muse-glimmer" || d.cached !== false ||
          (request.as_of ? Date.parse(d.as_of as string) !== Date.parse(request.as_of) : d.as_of !== null)) throw invalid();
      meta = true;
    } else if (!meta) throw invalid();
    else if (message.event === "claim") {
      const c = d.claim;
      if (!object(c) || !text(c.id) || !/^c\d+$/.test(c.id) || claims.has(c.id) || claims.size >= 500 ||
          !finite(c.value) || !text(c.span_text) || !text(c.value_text) || !text(c.metric_text) || !text(c.entity) ||
          !Number.isSafeInteger(c.span_start) || !Number.isSafeInteger(c.span_end) || (c.span_start as number) < 0 ||
          (c.span_end as number) <= (c.span_start as number) || (c.span_end as number) > chars.length ||
          chars.slice(c.span_start as number, c.span_end as number).join("") !== c.span_text) throw invalid();
      claims.set(c.id, c as unknown as Claim);
    } else if (message.event === "verdict") {
      const v = d.verdict;
      if (!object(v) || !text(v.claim_id) || !claims.has(v.claim_id) || verdicts[v.claim_id] || d.claim_id !== v.claim_id ||
          !STATUSES.includes(v.status as Verdict["status"]) || !text(v.explanation) ||
          !Array.isArray(v.evidence) || !v.evidence.every(safeReceipt)) throw invalid();
      verdicts[v.claim_id] = v as unknown as Verdict;
    } else if (message.event === "done") {
      const counts = { verified: 0, wrong: 0, unverifiable: 0, approximate: 0 };
      Object.values(verdicts).forEach((v) => { counts[v.status]++; });
      if (d.private !== true || d.model !== "muse-glimmer" || !object(d.counts) ||
          claims.size !== Object.keys(verdicts).length || !STATUSES.every((s) => (d.counts as Record<string, unknown>)[s] === counts[s]) ||
          !finite(d.latency_s) || d.latency_s < 0 || Object.keys(costFields(d)).length !== 3) throw invalid();
      return { claims: [...claims.values()].sort((a, b) => a.span_start - b.span_start), verdicts,
        done: { ...d, coverage: parseCoverage(d.coverage, request.text) } as unknown as DoneEvent };
    } else throw invalid();
  }
  throw new PrivateCheckError("The connection ended before the private check finished.");
}
