"use client";

import { DatabaseZapIcon } from "lucide-react";

import { StatusGlyph } from "@/components/status";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CheckState } from "@/hooks/use-check";
import { DISPLAY_STATUSES, STATUS_META, displayCounts, type ResolvedDisplayStatus } from "@/lib/claims";
import { formatCost, formatLatency, formatUtcMinutes } from "@/lib/format";
import { cn } from "@/lib/utils";

// A count tile takes its verdict's tint only when it has something to report.
const TILE: Record<ResolvedDisplayStatus, string> = {
  verified: "border-ok-line/35 bg-ok-tint/60",
  wrong: "border-bad-line/40 bg-bad-tint/70",
  look_ahead: "border-time-line/40 bg-time-tint/60",
  unverifiable: "border-unk-line/40 bg-unk-tint/60",
  approximate: "border-approx-line/40 bg-approx-tint/60",
};

/** ✓ ✗ ? ≈ counts, then latency, cost, model and the cached badge once the check is done. */
export function SummaryStrip({ state }: { state: CheckState }) {
  const { phase, claims, verdicts, done, meta, request } = state;
  const resolved = claims.filter((c) => verdicts[c.id]).length;
  const counts = displayCounts(Object.values(verdicts));
  const asOf = meta?.as_of ?? request?.as_of ?? null;
  const streaming = phase === "streaming";
  const progress = claims.length ? resolved / claims.length : 0;
  // A saved example rebuilt from stored model responses reports a $0, near-instant "original run": that is the
  // rebuild, not the model's work, so only a run that recorded a charge shows its time and cost.
  const recordedRun = (done?.cost_usd ?? 0) > 0;

  let status: string;
  if (streaming && !claims.length) status = "Reading the draft with Muse Spark…";
  else if (streaming) status = `Checking ${claims.length} ${claims.length === 1 ? "claim" : "claims"} against SEC filings… ${resolved} done`;
  else if (phase === "error") status = claims.length ? "Stopped before every claim was checked" : "The check didn't run";
  else if (phase === "idle" && request)
    status = claims.length
      ? "Check interrupted. Run Check again to finish; claims checked so far are shown."
      : "Check interrupted before any claim was checked. Run Check again.";
  else if (done && !claims.length) status = "No claims extracted";
  else status = `${claims.length} ${claims.length === 1 ? "claim" : "claims"} checked`;

  return (
    <section aria-label="Check summary" className="relative border-b border-border px-4 py-4 sm:px-6">
      <ul className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap" aria-label="Verdict counts">
        {/* Four tiles fit one row: ◷ appears only when used, and then takes the slot of an empty ≈. */}
        {DISPLAY_STATUSES.filter((s) => s === "look_ahead" ? counts.look_ahead > 0 : s === "approximate" ? counts.approximate > 0 || counts.look_ahead === 0 : true).map((s) => {
          const quiet = counts[s] === 0 && !streaming;
          return (
            <li
              key={s}
              className={cn(
                "flex min-w-0 items-center gap-2.5 rounded-lg border px-3 py-2 transition-colors sm:min-w-[8.5rem] sm:flex-1",
                quiet ? "border-border/70" : TILE[s],
              )}
            >
              <StatusGlyph status={s} className={cn("transition-opacity", quiet && "opacity-45")} />
              <span className="flex min-w-0 flex-col leading-tight">
                <span className={cn("text-lg font-semibold tabular-nums", quiet && "font-medium text-muted-foreground")}>
                  {counts[s]}
                </span>
                <span className="truncate text-xs text-muted-foreground">{STATUS_META[s].label}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
        <p className="flex flex-wrap items-center gap-x-2" aria-live="polite">
          <span className={cn(streaming && "text-foreground")}>{status}</span>
          <span aria-hidden>·</span>
          <span>As of {asOf ? formatUtcMinutes(asOf) : "the snapshot's latest filings"}</span>
        </p>
        {done ? (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {meta?.cached ? (
              <span className="tabular-nums">
                $0 this replay ·{" "}
                {recordedRun
                  ? `original run: ${formatLatency(done.latency_s)} / ${done.cost_is_estimate ? "estimated " : ""}${formatCost(done.cost_usd)}`
                  : "recorded extraction"}
              </span>
            ) : (
              <span className="tabular-nums">{formatLatency(done.latency_s)} · {done.cost_is_estimate ? "estimated " : ""}{formatCost(done.cost_usd)}</span>
            )}
            <span aria-hidden>·</span>
            <span className="font-mono">{done.model}</span>
            {meta?.cached ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    tabIndex={0}
                    className="ml-1 inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 font-medium text-foreground/80 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <DatabaseZapIcon className="size-3" aria-hidden />
                    Cached
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-60 text-pretty">
                  {recordedRun
                    ? "Served from the result cache with no new model charge. The original run's time and cost are shown for reference, not this replay's latency."
                    : "Served from the result cache with no new model charge. Its claims come from a recorded extraction by the model shown here."}
                </TooltipContent>
              </Tooltip>
            ) : null}
          </div>
        ) : null}
      </div>
      {streaming ? (
        <div
          className="absolute inset-x-0 bottom-[-1px] h-0.5 overflow-hidden"
          role="progressbar"
          aria-label="Claims checked"
          aria-valuemin={0}
          aria-valuemax={claims.length || 1}
          aria-valuenow={resolved}
        >
          <div
            className={cn("h-full bg-foreground/70 transition-[width] duration-300", !claims.length && "shimmer w-full")}
            style={claims.length ? { width: `${Math.max(6, progress * 100)}%` } : undefined}
          />
        </div>
      ) : null}
    </section>
  );
}
