// Mock mode (NEXT_PUBLIC_RECEIPTS_MOCK=1): replays web/fixtures, which web/fixtures/generate.py
// builds from the real `python -m receipts check --json`. Server-only: imported by app/mock-api.
import { createHash } from "node:crypto";

import draft6 from "@/fixtures/checks/draft-6claims.json";
import nvdaQ1 from "@/fixtures/checks/nvda-q1-fy2027.json";
import examples from "@/fixtures/examples.json";
import health from "@/fixtures/health.json";
import { MAX_DRAFT_CHARS } from "./types";

export interface FixtureEvent {
  event: "meta" | "claim" | "verdict" | "done" | "error";
  data: unknown;
}

interface FixtureRun {
  valid_from: string | null;
  events: FixtureEvent[];
}

interface CheckFixture {
  id: string;
  text: string;
  context: string | null;
  source: string;
  latest: FixtureRun;
  timeline: FixtureRun[];
}

const FIXTURES = [draft6, nvdaQ1] as unknown as CheckFixture[];

export const mockExamples = examples;
export const mockHealth = health;

export function mockEnabled(): boolean {
  return process.env.NEXT_PUBLIC_RECEIPTS_MOCK === "1";
}

const normalize = (s: string) => s.replace(/\r\n?/g, "\n").trimEnd();

/** as_of from the request: null for latest filings; a bare date or zone-less time is read as UTC. */
export function parseAsOf(v: unknown): Date | null | "invalid" {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v !== "string") return "invalid";
  let s = v.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) s += "T00:00:00Z";
  else if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(s)) s += "Z";
  const t = Date.parse(s);
  return Number.isNaN(t) ? "invalid" : new Date(t);
}

const iso = (d: Date) => d.toISOString().replace(/\.\d{3}Z$/, "Z");
const verifierTime = (d: Date) => iso(d).replace("T", " ").replace("Z", " UTC");

export type MockPlan = { ok: true; events: FixtureEvent[] } | { ok: false; message: string; retryable: boolean };

/** The event sequence the real API would send for this request, or why mock mode can't answer it. */
export function planCheck(body: Record<string, unknown>): MockPlan {
  const text = typeof body.text === "string" ? body.text : "";
  if (!text.trim()) return { ok: false, message: "The draft is empty.", retryable: false };
  if (text.length > MAX_DRAFT_CHARS) {
    return { ok: false, message: `Drafts are limited to ${MAX_DRAFT_CHARS.toLocaleString("en-US")} characters.`, retryable: false };
  }
  const asOf = parseAsOf(body.as_of);
  if (asOf === "invalid") return { ok: false, message: "The as-of time isn't a valid date and time.", retryable: false };
  const fixture = FIXTURES.find((f) => normalize(f.text) === normalize(text));
  if (!fixture) {
    return {
      ok: false,
      message: "Mock mode replays the bundled examples only. Pick an example, or run against the live API to check any draft.",
      retryable: false,
    };
  }
  let run = fixture.latest;
  if (asOf) {
    run = fixture.timeline[0];
    for (const seg of fixture.timeline) {
      if (seg.valid_from && Date.parse(seg.valid_from) <= asOf.getTime()) run = seg;
    }
  }
  const asOfIso = asOf ? iso(asOf) : "";
  const context = typeof body.context === "string" ? body.context : "";
  const hash = createHash("sha256").update(text + asOfIso + context).digest("hex");
  const fill = (s: string) =>
    s
      .replaceAll("{{DRAFT_HASH}}", hash)
      .replaceAll("{{AS_OF_ISO}}", asOfIso)
      .replaceAll("{{AS_OF_TEXT}}", asOf ? verifierTime(asOf) : "");
  return { ok: true, events: run.events.map((e) => ({ event: e.event, data: JSON.parse(fill(JSON.stringify(e.data))) })) };
}

const between = (lo: number, hi: number) => lo + Math.random() * (hi - lo);

/** Pause before each event: extraction "thinking", claims 100-300 ms apart, then the verdicts. */
export function delayBefore(events: FixtureEvent[], i: number): number {
  const e = events[i].event;
  const prev = i > 0 ? events[i - 1].event : null;
  if (e === "meta") return 120;
  if (e === "claim") return prev === "meta" ? between(600, 900) : between(100, 300);
  if (e === "verdict") return prev === "claim" ? between(350, 550) : between(110, 240);
  return 300;
}

export function sseFrame(e: FixtureEvent): string {
  return `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`;
}

export const SSE_HEADERS = {
  "Content-Type": "text/event-stream; charset=utf-8",
  "Cache-Control": "no-cache, no-transform",
  Connection: "keep-alive",
  "X-Accel-Buffering": "no",
};
