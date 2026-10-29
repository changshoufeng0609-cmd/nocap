import { API_BASE, costFields } from "./api";
import { readSSE } from "./sse";
import { STATUSES, type ErrorEvent, type Fact, type SlideBox, type SlideClaim, type SlideDone, type SlideMeta, type SlideRegion, type SlideRequest, type SlideStage, type Verdict } from "./types";

export interface SlideHandlers {
  onMeta: (meta: SlideMeta) => void;
  onProgress: (stage: SlideStage) => void;
  onRegion: (region: SlideRegion) => void;
  onClaim: (claim: SlideClaim) => void;
  onVerdict: (verdict: Verdict) => void;
  onDone: (done: SlideDone) => void;
  onError: (error: ErrorEvent) => void;
}

const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === "object" && !Array.isArray(x);
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const text = (x: unknown, max = 6000): x is string => typeof x === "string" && x.length <= max;
const date = (x: unknown): x is string => text(x, 40) && Number.isFinite(Date.parse(x));
const units = ["USD", "%", "USD/share", "shares", "other"];
const reasons = ["match", "value_mismatch", "period_mismatch", "scale_mismatch", "basis_mismatch", "entity_mismatch", "look_ahead", "forward_looking", "no_evidence", "ambiguous", "approximate"];

export function isSlideBox(x: unknown): x is SlideBox {
  return Array.isArray(x) && x.length === 4 && x.every((n) => finite(n) && n >= 0 && n <= 1000) && x[0] < x[2] && x[1] < x[3];
}

function isFact(x: unknown): x is Fact {
  if (!object(x) || !finite(x.cik) || !text(x.ticker, 15) || !text(x.metric, 100) || !finite(x.value) ||
      !units.includes(x.unit as string) || !["GAAP", "non-GAAP"].includes(x.basis as string) ||
      !date(x.period_end) || (x.period_start != null && !date(x.period_start)) || !date(x.accepted_at) ||
      !["xbrl", "press_release", "derived"].includes(x.source as string) || !text(x.form, 30) ||
      !text(x.accession, 100) || !text(x.url, 2000) || (x.locator != null && !text(x.locator)) ||
      (x.segment != null && !text(x.segment)) || (x.fiscal_year != null && !finite(x.fiscal_year)) ||
      (x.fiscal_period != null && !text(x.fiscal_period, 20)) ||
      (x.derived_from != null && !(Array.isArray(x.derived_from) && x.derived_from.every((s) => text(s))))) return false;
  try { const url = new URL(x.url); return url.protocol === "https:" && ["sec.gov", "www.sec.gov"].includes(url.hostname); } catch { return false; }
}

function isClaim(x: unknown): x is SlideClaim {
  return object(x) && text(x.id, 30) && /^c\d+$/.test(x.id) && text(x.region_id, 30) && isSlideBox(x.box) &&
    ["printed", "axis_estimate"].includes(x.reading as string) && finite(x.value) &&
    ["metric", "metric_text", "value_text", "period_text", "period_type", "comparison", "entity", "span_text"].every((key) => text(x[key])) &&
    units.includes(x.unit as string) && ["units", "thousands", "millions", "billions"].includes(x.scale as string) &&
    (x.basis === null || ["GAAP", "non-GAAP"].includes(x.basis as string)) && typeof x.forward_looking === "boolean" &&
    (x.segment == null || text(x.segment)) && x.span_start === 0 && Number.isInteger(x.span_end) &&
    x.span_end === Array.from(x.span_text as string).length;
}

function isVerdict(x: unknown): x is Verdict {
  return object(x) && text(x.claim_id, 30) && STATUSES.includes(x.status as Verdict["status"]) && reasons.includes(x.reason as string) &&
    text(x.explanation) && (x.expected_value == null || finite(x.expected_value)) &&
    (x.as_of == null || date(x.as_of)) && Array.isArray(x.evidence) && x.evidence.length <= 100 && x.evidence.every(isFact);
}

/** Strict terminal accounting prevents a partial stream or unsafe axis verdict from looking complete. */
export async function streamSlide(request: SlideRequest, handlers: SlideHandlers, signal: AbortSignal): Promise<void> {
  let finished = false;
  const fail = (message: string, retryable = true, accounting: Partial<ErrorEvent> = {}) => {
    if (!signal.aborted && !finished) { finished = true; handlers.onError({ message, retryable, ...accounting }); }
  };
  const invalid = () => fail("The slide response could not be verified safely. Please try again with a clearer image.");
  try {
    const response = await fetch(`${API_BASE}/slide`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ image_data_url: request.image_data_url, context: request.context, as_of: request.as_of }),
      cache: "no-store", signal,
    });
    if (signal.aborted) return;
    if (!response.ok || !response.body) {
      fail(`The slide check is unavailable (HTTP ${response.status}). Try again in a moment.`, response.status === 429 || response.status >= 500);
      return;
    }
    if (!(response.headers.get("content-type") ?? "").includes("text/event-stream")) { invalid(); return; }
    let meta: SlideMeta | null = null;
    const regions = new Map<string, SlideRegion>();
    const claims = new Map<string, SlideClaim>();
    const verdicts = new Map<string, Verdict>();
    for await (const message of readSSE(response.body, signal)) {
      if (signal.aborted) return;
      let data: unknown;
      try { data = JSON.parse(message.data); } catch { invalid(); return; }
      if (!object(data)) { invalid(); return; }
      if (message.event === "error") {
        fail(text(data.message, 1000) && data.message ? data.message : "The slide check could not be completed.", data.retryable === true, costFields(data));
        return;
      }
      if (message.event === "meta") {
        if (meta || !object(data.image) || !Number.isInteger(data.image.width) || !Number.isInteger(data.image.height) ||
          (data.image.width as number) < 1 || (data.image.height as number) < 1 || (data.image.width as number) > 2048 || (data.image.height as number) > 2048 ||
          data.cached !== false || !Array.isArray(data.models) || !data.models.length || !data.models.every((m) => text(m, 100)) ||
          (data.as_of !== null && !date(data.as_of)) ||
          (request.as_of ? Date.parse(data.as_of as string) !== Date.parse(request.as_of) : data.as_of !== null)) { invalid(); return; }
        meta = data as unknown as SlideMeta;
        handlers.onMeta(meta);
      } else if (!meta) {
        invalid(); return;
      } else if (message.event === "progress") {
        if (!["segmenting", "reading", "verifying"].includes(data.stage as string)) { invalid(); return; }
        handlers.onProgress(data.stage as SlideStage);
      } else if (message.event === "region") {
        if (!text(data.id, 30) || !text(data.label, 120) || !isSlideBox(data.box) || !["sam", "spark"].includes(data.source as string) || regions.has(data.id) || regions.size >= 16) { invalid(); return; }
        const region = data as unknown as SlideRegion;
        regions.set(region.id, region);
        handlers.onRegion(region);
      } else if (message.event === "claim") {
        if (!isClaim(data) || !regions.has(data.region_id) || claims.has(data.id) || claims.size >= 64) { invalid(); return; }
        claims.set(data.id, data);
        handlers.onClaim(data);
      } else if (message.event === "verdict") {
        if (!isVerdict(data) || !claims.has(data.claim_id) || verdicts.has(data.claim_id) ||
          (claims.get(data.claim_id)?.reading === "axis_estimate" && ["verified", "wrong"].includes(data.status))) { invalid(); return; }
        verdicts.set(data.claim_id, data);
        handlers.onVerdict(data);
      } else if (message.event === "done") {
        const actual = { verified: 0, wrong: 0, unverifiable: 0, approximate: 0 };
        for (const verdict of verdicts.values()) actual[verdict.status]++;
        if (claims.size !== verdicts.size || data.claims !== claims.size || !object(data.counts) ||
          !STATUSES.every((s) => data.counts && (data.counts as Record<string, unknown>)[s] === actual[s]) ||
          !finite(data.latency_s) || data.latency_s < 0 || !finite(data.cost_usd) || data.cost_usd < 0 ||
          !Number.isInteger(data.model_calls) || (data.model_calls as number) < 0 || (data.model_calls as number) > 3 ||
          typeof data.cost_is_estimate !== "boolean" || !Array.isArray(data.warnings) || !data.warnings.every((w) => text(w, 2000))) { invalid(); return; }
        finished = true;
        handlers.onDone(data as unknown as SlideDone);
        return;
      }
      if (signal.aborted) return;
    }
    fail("The connection ended before every slide reading had a verdict.");
  } catch {
    fail("Couldn't finish the slide check. Check the connection and try again.");
  }
}
