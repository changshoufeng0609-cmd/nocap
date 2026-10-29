import { readSSE } from "./sse";
import { parseCoverage } from "./coverage";
import type { CheckRequest, Claim, DoneEvent, ErrorEvent, Example, FixRequest, FixResult, FixStage, Health, MetaEvent, Verdict } from "./types";

/** NEXT_PUBLIC_RECEIPTS_MOCK=1 replays web/fixtures from Next.js route handlers under /mock-api. */
export const MOCK = process.env.NEXT_PUBLIC_RECEIPTS_MOCK === "1";
export const API_BASE = MOCK ? "/mock-api" : "/api";

/** Accept accounting only as a complete validated tuple. Missing usage is not zero. */
export function costFields(data: Record<string, unknown>) {
  if (typeof data.cost_usd !== "number" || !Number.isFinite(data.cost_usd) || data.cost_usd < 0 ||
      typeof data.cost_is_estimate !== "boolean" || !Number.isSafeInteger(data.model_calls) ||
      (data.model_calls as number) < 0) return {};
  return { cost_usd: data.cost_usd, cost_is_estimate: data.cost_is_estimate, model_calls: data.model_calls as number };
}

function validCost(data: { cost_usd?: unknown; cost_is_estimate?: unknown; model_calls?: unknown }): boolean {
  return typeof data.cost_usd === "number" && Number.isFinite(data.cost_usd) && data.cost_usd >= 0 &&
    (data.cost_is_estimate === undefined || typeof data.cost_is_estimate === "boolean") &&
    (data.model_calls === undefined || (Number.isSafeInteger(data.model_calls) && (data.model_calls as number) >= 0));
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly retryable: boolean,
  ) {
    super(message);
  }
}

/** The server's own message from a JSON ({message} or FastAPI's {detail}) or short text body, or "". */
async function bodyMessage(res: Response): Promise<string> {
  try {
    const body = await res.clone().json();
    const detail = body?.message ?? body?.detail ?? body?.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && typeof detail[0]?.msg === "string") return detail[0].msg;
    return "";
  } catch {
    try {
      const text = (await res.text()).trim();
      return text && text.length < 300 && !text.startsWith("<") ? text : "";
    } catch {
      return "";
    }
  }
}

// Bare reason phrases (e.g. from the dev proxy when the API is down) say nothing useful on their own.
const GENERIC = /^(internal server error|bad gateway|service unavailable|gateway time-?out|not found)\.?$/i;

async function errorFromResponse(res: Response): Promise<ErrorEvent> {
  let message = await bodyMessage(res);
  if (GENERIC.test(message)) message = "";
  if (!message && res.status >= 500) {
    return { message: "The NoCap API isn't responding.", retryable: true };
  }
  if (res.status === 429) {
    return { message: message || "Too many checks in a short time. Wait a moment, or try an example.", retryable: true };
  }
  if (!message) {
    message =
      res.status === 404
        ? "The NoCap API isn't available at this address."
        : `The NoCap API returned an error (HTTP ${res.status}).`;
  }
  return { message, retryable: res.status >= 500 };
}

function isExample(x: unknown): x is Example {
  const e = x as Example;
  return !!e && typeof e.id === "string" && typeof e.title === "string" && typeof e.text === "string";
}

export async function fetchExamples(signal?: AbortSignal): Promise<Example[]> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/examples`, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new ApiError("Couldn't reach the NoCap API to load the examples.", true);
  }
  if (!res.ok) {
    const err = await errorFromResponse(res);
    throw new ApiError(err.message, err.retryable);
  }
  const data: unknown = await res.json();
  if (!Array.isArray(data)) throw new ApiError("The examples response wasn't a list.", true);
  return data.filter(isExample).map((e) => ({
    ...e,
    description: e.description ?? "",
    as_of: e.as_of ?? null,
    context: e.context ?? null,
  }));
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal, headers: { Accept: "application/json" } });
    if (!res.ok) return null;
    const h = (await res.json()) as Health;
    return h && h.ok ? h : null;
  } catch {
    return null;
  }
}

export interface CheckHandlers {
  onMeta: (meta: MetaEvent) => void;
  onClaim: (claim: Claim) => void;
  onVerdict: (claimId: string, verdict: Verdict) => void;
  onDone: (done: DoneEvent) => void;
  onError: (error: ErrorEvent) => void;
}

/**
 * POST /api/check and dispatch the SSE events (meta, claim, verdict, done, error) as they arrive.
 * Always ends in exactly one onDone or onError call, unless the signal aborts first.
 */
export async function streamCheck(req: CheckRequest, h: CheckHandlers, signal: AbortSignal): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(req),
      cache: "no-store",
      signal,
    });
  } catch {
    if (!signal.aborted) h.onError({ message: "Couldn't reach the NoCap API.", retryable: true });
    return;
  }
  if (signal.aborted) return;
  if (!res.ok || !res.body) {
    const error = await errorFromResponse(res);
    if (!signal.aborted) h.onError(error);
    return;
  }
  const type = res.headers.get("content-type") ?? "";
  if (!type.includes("text/event-stream")) {
    if (!signal.aborted) {
      const message = await bodyMessage(res);
      if (!signal.aborted) h.onError({ message: message || "The NoCap API sent an unexpected response.", retryable: true });
    }
    return;
  }

  let finished = false;
  try {
    for await (const msg of readSSE(res.body, signal)) {
      if (signal.aborted) return;
      let data: unknown;
      try {
        data = JSON.parse(msg.data);
      } catch {
        continue; // keep-alives or anything that isn't JSON
      }
      const d = data as Record<string, unknown>;
      switch (msg.event) {
        case "meta":
          h.onMeta(d as unknown as MetaEvent);
          break;
        case "claim":
          if (d.claim) h.onClaim(d.claim as Claim);
          break;
        case "verdict": {
          const v = d.verdict as Verdict | undefined;
          if (v) h.onVerdict((d.claim_id as string) ?? v.claim_id, v);
          break;
        }
        case "done":
          finished = true;
          if (validCost(d)) h.onDone({ ...d, coverage: parseCoverage(d.coverage, req.text) } as unknown as DoneEvent);
          else h.onError({ message: "The check returned invalid cost information.", retryable: true });
          break;
        case "error":
          finished = true;
          h.onError({
            message: typeof d.message === "string" && d.message ? d.message : "The check failed.",
            retryable: d.retryable === true,
            ...costFields(d),
          });
          break;
      }
      if (finished) break;
    }
  } catch {
    if (!signal.aborted && !finished) {
      h.onError({ message: "The connection dropped before the check finished.", retryable: true });
    }
    return;
  }
  if (!finished && !signal.aborted) {
    h.onError({ message: "The check ended before every claim had a verdict.", retryable: true });
  }
}

export interface FixHandlers {
  onProgress: (stage: FixStage) => void;
  onResult: (result: FixResult) => void;
  onError: (error: ErrorEvent) => void;
}

function isFixResult(value: unknown): value is FixResult {
  const r = value as FixResult | null;
  if (!(!!r && typeof r.original_text === "string" && typeof r.rewritten_text === "string" &&
    ["corrected", "partial", "unchanged"].includes(r.status) &&
    Array.isArray(r.changes) && r.changes.every((c) => !!c && typeof c.claim_id === "string" &&
      typeof c.before === "string" && typeof c.after === "string" && typeof c.explanation === "string" &&
      Number.isInteger(c.start) && Number.isInteger(c.end) && Array.isArray(c.evidence)) &&
    Array.isArray(r.remaining) && r.remaining.every((x) => !!x && typeof x.claim_id === "string" && typeof x.explanation === "string") &&
    Array.isArray(r.results) && r.results.every((x) => !!x && !!x.claim && !!x.verdict && Array.isArray(x.verdict.evidence)) &&
    !!r.counts && ["verified", "wrong", "unverifiable", "approximate"].every((s) =>
      Number.isFinite(r.counts[s as keyof typeof r.counts])) &&
    validCost(r) && Number.isFinite(r.latency_s))) return false;

  // The comparison and citation export must reproduce the exact server-verified text.
  const chars = Array.from(r.original_text);
  let cursor = 0;
  let rebuilt = "";
  for (const change of [...r.changes].sort((a, b) => a.start - b.start)) {
    if (change.start < cursor || change.end <= change.start || change.end > chars.length ||
      chars.slice(change.start, change.end).join("") !== change.before) return false;
    rebuilt += chars.slice(cursor, change.start).join("") + change.after;
    cursor = change.end;
  }
  return rebuilt + chars.slice(cursor).join("") === r.rewritten_text;
}

/** Only the draft and its time/context are submitted; receipts and proposed changes come from the server. */
export async function streamFix(req: FixRequest, h: FixHandlers, signal: AbortSignal): Promise<void> {
  let finished = false;
  const fail = (error: ErrorEvent) => {
    if (!signal.aborted && !finished) {
      finished = true;
      h.onError(error);
    }
  };
  try {
    const res = await fetch(`${API_BASE}/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ text: req.text, as_of: req.as_of, context: req.context }),
      cache: "no-store",
      signal,
    });
    if (signal.aborted) return;
    if (!res.ok || !res.body) {
      fail(await errorFromResponse(res));
      return;
    }
    if (!(res.headers.get("content-type") ?? "").includes("text/event-stream")) {
      fail({ message: "The NoCap API sent an unexpected correction response.", retryable: true });
      return;
    }
    for await (const msg of readSSE(res.body, signal)) {
      if (signal.aborted) return;
      let data: unknown;
      try { data = JSON.parse(msg.data); } catch { continue; }
      if (!data || typeof data !== "object") continue;
      const d = data as Record<string, unknown>;
      if (msg.event === "progress" && ["checking", "rewriting", "verifying"].includes(d.stage as string)) {
        h.onProgress(d.stage as FixStage);
      } else if (msg.event === "result") {
        if (!isFixResult(data) || data.original_text !== req.text) {
          fail({ message: "The correction response did not match this draft. Please try again.", retryable: true });
        } else {
          finished = true;
          h.onResult({ ...data, changes: [...data.changes].sort((a, b) => a.start - b.start),
            original_coverage: parseCoverage(data.original_coverage, data.original_text),
            coverage: parseCoverage(data.coverage, data.rewritten_text) });
        }
      } else if (msg.event === "error") {
        fail({ message: typeof d.message === "string" && d.message ? d.message : "The correction could not be completed.", retryable: d.retryable === true, ...costFields(d) });
      }
      if (finished) return;
    }
    fail({ message: "The connection ended before the corrections were verified.", retryable: true });
  } catch {
    fail({ message: "Couldn't finish the correction request. Check the connection and try again.", retryable: true });
  }
}
