import { readSSE } from "./sse";
import type { CheckRequest, Claim, DoneEvent, ErrorEvent, Example, Health, MetaEvent, Verdict } from "./types";

/** NEXT_PUBLIC_RECEIPTS_MOCK=1 replays web/fixtures from Next.js route handlers under /mock-api. */
export const MOCK = process.env.NEXT_PUBLIC_RECEIPTS_MOCK === "1";
export const API_BASE = MOCK ? "/mock-api" : "/api";

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
    return { message: "The Receipts API isn't responding.", retryable: true };
  }
  if (res.status === 429) {
    return { message: message || "Too many checks in a short time. Wait a moment, or try an example.", retryable: true };
  }
  if (!message) {
    message =
      res.status === 404
        ? "The Receipts API isn't available at this address."
        : `The Receipts API returned an error (HTTP ${res.status}).`;
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
    throw new ApiError("Couldn't reach the Receipts API to load the examples.", true);
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
    if (!signal.aborted) h.onError({ message: "Couldn't reach the Receipts API.", retryable: true });
    return;
  }
  if (!res.ok || !res.body) {
    if (!signal.aborted) h.onError(await errorFromResponse(res));
    return;
  }
  const type = res.headers.get("content-type") ?? "";
  if (!type.includes("text/event-stream")) {
    if (!signal.aborted) {
      const message = await bodyMessage(res);
      h.onError({ message: message || "The Receipts API sent an unexpected response.", retryable: true });
    }
    return;
  }

  let finished = false;
  try {
    for await (const msg of readSSE(res.body, signal)) {
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
          h.onDone(d as unknown as DoneEvent);
          break;
        case "error":
          finished = true;
          h.onError({
            message: typeof d.message === "string" && d.message ? d.message : "The check failed.",
            retryable: d.retryable === true,
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
