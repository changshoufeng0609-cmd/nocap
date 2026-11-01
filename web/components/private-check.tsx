"use client";

import { useEffect, useRef, useState } from "react";
import { HistoryIcon, ShieldCheckIcon, Loader2Icon } from "lucide-react";
import { NumberCoverage } from "@/components/number-coverage";
import { StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MOCK } from "@/lib/api";
import { displayStatus } from "@/lib/claims";
import { formatErrorCost, formatLatency, formatUtc, formatUtcMinutes } from "@/lib/format";
import { fetchPrivateStatus, PrivateCheckError, runPrivateCheck, type PrivateResult, type PrivateStatus } from "@/lib/private";
import samples from "@/lib/samples.json";
import { MAX_DRAFT_CHARS } from "@/lib/types";

// A public synthetic draft whose live run on the operator's Modal Glimmer is recorded: the server replays it at $0,
// even where Private mode isn't configured. Only this exact draft is ever accepted as a recorded result.
const SAMPLE = samples.private[0];
type PrivateDraft = { text: string; context: string | null; as_of: string | null };

export function PrivateCheck() {
  const [status, setStatus] = useState<PrivateStatus | null>(null);
  const [text, setText] = useState("");
  const [context, setContext] = useState("");
  const [asOf, setAsOf] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PrivateResult | null>(null);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    const ac = new AbortController();
    if (!MOCK) void fetchPrivateStatus(ac.signal).then((s) => { if (!ac.signal.aborted) setStatus(s); })
      .catch(() => { if (!ac.signal.aborted) setStatus({ available: false, message: "Private mode is unavailable on this server.", recorded: [] }); });
    return () => { ac.abort(); request.current?.abort(); };
  }, []);

  const recorded = status?.recorded.find((r) => r.sample_id === SAMPLE.id) ?? null;

  function reset() { request.current?.abort(); request.current = null; setRunning(false); setResult(null); setError(null); }
  async function run(draft: PrivateDraft) {
    const ac = new AbortController(); request.current = ac; setRunning(true);
    try {
      const next = await runPrivateCheck(draft, ac.signal, { recordedSampleText: SAMPLE.text });
      if (!ac.signal.aborted) setResult(next);
    } catch (e) {
      if (!ac.signal.aborted) {
        const cost = e instanceof PrivateCheckError ? formatErrorCost(e.accounting) : null;
        setError(`${e instanceof Error ? e.message : "The private check failed."}${cost ? ` ${cost}` : ""}`);
      }
    } finally { if (!ac.signal.aborted) setRunning(false); }
  }
  async function check() {
    reset();
    if (asOf.trim() && (!Number.isFinite(Date.parse(asOf)) || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(asOf.trim()))) { setError("Enter an ISO 8601 timestamp, including its timezone."); return; }
    await run({ text, context: context.trim() || null, as_of: asOf.trim() ? new Date(asOf).toISOString() : null });
  }
  /** Shows the sample in the form and sends exactly the recorded request, so the server replays it at $0. */
  function replaySample() {
    reset(); setText(SAMPLE.text); setContext(SAMPLE.context); setAsOf(SAMPLE.as_of);
    void run({ text: SAMPLE.text, context: SAMPLE.context, as_of: SAMPLE.as_of });
  }

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6"><div className="max-w-4xl">
      <h2 className="flex items-center gap-2 font-serif text-3xl font-medium"><ShieldCheckIcon className="size-6" />Private check</h2>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">Muse Glimmer extracts your draft in the operator&apos;s Modal workspace. NoCap checks the claims against its SEC snapshot. Your draft is not sent to Meta Model API, Fireworks or Baseten, and is not saved in NoCap&apos;s response caches.</p>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">This is not offline: your browser, the NoCap server and the operator&apos;s Modal service handle the draft. Hosting and Modal policies still apply. Fix and Slide use Meta and are separate modes. Modal bills GPU time; the displayed admission estimate is not its invoice.</p>
      <p role="status" className="my-4 rounded-lg border border-border p-3 text-sm">{MOCK ? "Private checks require the live API; mock mode sends no draft." : status?.message ?? "Checking private backend configuration…"}</p>
      <section aria-label="Private draft" className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6">
        <label htmlFor="private-draft" className="block text-sm font-medium">Draft · up to {MAX_DRAFT_CHARS.toLocaleString("en-US")} characters</label>
        <Textarea id="private-draft" value={text} maxLength={MAX_DRAFT_CHARS} rows={9} autoComplete="off" spellCheck={false}
          onChange={(e) => { reset(); setText(e.target.value); }} placeholder="Paste the financial draft to check in the operator's Modal workspace." />
        <label className="block text-sm">Company and period (optional)
          <input className="mt-1 block h-10 w-full rounded-md border border-input bg-transparent px-3" value={context} maxLength={300} autoComplete="off"
            onChange={(e) => { reset(); setContext(e.target.value); }} placeholder="NVIDIA, Q2 FY2027" />
        </label>
        <label className="block text-sm">As of (optional, UTC or explicit timezone)
          <input className="mt-1 block h-10 w-full rounded-md border border-input bg-transparent px-3" value={asOf} maxLength={40} autoComplete="off"
            onChange={(e) => { reset(); setAsOf(e.target.value); }} placeholder="2026-08-26T20:00:00Z" />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant={status?.available ? "default" : "outline"} disabled={running || !text.trim() || !status?.available || MOCK} onClick={() => void check()}>{running ? <Loader2Icon className="animate-spin" /> : <ShieldCheckIcon />}Check privately</Button>
          <Button variant="ghost" onClick={() => { reset(); setText(""); setContext(""); setAsOf(""); }}>Clear draft and results</Button>
        </div>
        <div className="space-y-2 border-t border-border pt-4">
          {/* Where live Private checks are off, the replay is the one working action, so it takes the primary style. */}
          <Button variant={status?.available ? "secondary" : "default"} disabled={running || MOCK || !recorded} onClick={replaySample}><HistoryIcon />Replay a recorded Private check</Button>
          <p className="text-xs leading-relaxed text-muted-foreground">{recorded
            ? <>Recorded, not live: {SAMPLE.title}. The operator checked this public synthetic draft on their own Modal Glimmer deployment{recorded.recorded_utc ? ` (${formatUtcMinutes(recorded.recorded_utc)})` : ""}; NoCap replays that run at $0, with no model call and nothing sent to Modal. Only this exact sample is replayed; your own drafts never are.</>
            : "This server has no recorded Private check to replay."}</p>
        </div>
        {running ? <p role="status" className="text-sm text-muted-foreground">{text === SAMPLE.text && recorded ? "Loading the recorded Private check…" : "Waiting for Modal Glimmer and checking its claims…"}</p> : null}
        {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
      </section>
      {result ? <section aria-label="Private check results" className="mt-6 overflow-hidden rounded-xl border border-border bg-card">
        <div className="border-b border-border p-4"><h3 className="font-medium">{result.recording ? "Recorded Private check · Muse Glimmer" : "Checked with Muse Glimmer"} · {result.claims.length} claims</h3>
          <p className="mt-1 text-xs text-muted-foreground">{result.recording
            ? <>Recorded live run on the operator&apos;s Modal ({formatUtcMinutes(result.recording.recorded_utc)}) · $0 this replay · original run: {formatLatency(result.done.latency_s)}, {result.done.model_calls ?? 0} model call{result.done.model_calls === 1 ? "" : "s"}; Modal billed its GPU time separately</>
            : <>{formatLatency(result.done.latency_s)} · NoCap response caching off · GPU charges billed separately</>}</p></div>
        <NumberCoverage text={text} coverage={result.done.coverage} />
        <ol className="divide-y divide-border">{result.claims.map((claim) => {
          const verdict = result.verdicts[claim.id];
          return <li key={claim.id} className="space-y-2 p-4"><div className="flex flex-wrap items-center gap-2"><StatusChip status={displayStatus(verdict)} /><span className="font-mono font-medium">{claim.value_text}</span><span className="text-xs text-muted-foreground">{claim.entity} · {claim.metric_text}</span></div>
            <p className="text-sm">{verdict.explanation}</p><ul className="space-y-1">{verdict.evidence.map((fact, index) => <li key={`${fact.accession}-${index}`} className="text-xs"><a className="underline underline-offset-4" href={fact.url} target="_blank" rel="noopener noreferrer">{fact.form} · {fact.accession}</a><span className="text-muted-foreground"> · accepted {formatUtc(fact.accepted_at)}</span></li>)}</ul>
          </li>;
        })}</ol>
        {!result.claims.length ? <p className="p-4 text-sm text-muted-foreground">No numeric claims were extracted. This does not confirm the draft.</p> : null}
      </section> : null}
    </div></main>
  );
}
