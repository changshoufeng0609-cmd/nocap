"use client";

import { DatabaseZapIcon } from "lucide-react";

import { StatusGlyph } from "@/components/status";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { CheckState } from "@/hooks/use-check";
import { STATUS_META } from "@/lib/claims";
import { formatCost, formatLatency, formatUtcMinutes } from "@/lib/format";
import { STATUSES, type Status } from "@/lib/types";
import { cn } from "@/lib/utils";

/** ✓ ✗ ? ≈ counts, then latency, cost, model and the cached badge once the check is done. */
export function SummaryStrip({ state }: { state: CheckState }) {
  const { phase, claims, verdicts, done, meta, request } = state;
  const resolved = claims.filter((c) => verdicts[c.id]).length;
  const counts: Record<Status, number> =
    done?.counts ??
    STATUSES.reduce(
      (acc, s) => ({ ...acc, [s]: claims.filter((c) => verdicts[c.id]?.status === s).length }),
      {} as Record<Status, number>,
    );
  const asOf = meta?.as_of ?? request?.as_of ?? null;
  const streaming = phase === "streaming";
  const progress = claims.length ? resolved / claims.length : 0;

  let status: string;
  if (streaming && !claims.length) status = "Reading the draft with Muse Spark…";
  else if (streaming) status = `Checking ${claims.length} ${claims.length === 1 ? "claim" : "claims"} against SEC filings… ${resolved} done`;
  else if (phase === "error") status = claims.length ? "Stopped before every claim was checked" : "The check didn't run";
  else if (done && !claims.length) status = "No financial figures found";
  else status = `${claims.length} ${claims.length === 1 ? "claim" : "claims"} checked`;

  return (
    <section aria-label="Check summary" className="relative border-b border-border px-4 py-3.5 sm:px-6">
      <ul className="flex flex-wrap items-center gap-x-5 gap-y-1.5" aria-label="Verdict counts">
        {STATUSES.map((s) => (
          <li key={s} className="flex items-center gap-1.5 text-sm">
            <StatusGlyph
              status={s}
              size="sm"
              className={cn("transition-opacity", counts[s] === 0 && !streaming && "opacity-45")}
            />
            <span
              className={cn("font-semibold tabular-nums", counts[s] === 0 && !streaming && "font-normal text-muted-foreground")}
            >
              {counts[s]}
            </span>
            <span className="text-muted-foreground">{STATUS_META[s].label.toLowerCase()}</span>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
        <p className="flex flex-wrap items-center gap-x-2" aria-live="polite">
          <span className={cn(streaming && "text-foreground")}>{status}</span>
          <span aria-hidden>·</span>
          <span>As of {asOf ? formatUtcMinutes(asOf) : "the latest filings"}</span>
        </p>
        {done ? (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="tabular-nums">{formatLatency(done.latency_s)}</span>
            <span aria-hidden>·</span>
            <span className="tabular-nums">{formatCost(done.cost_usd)}</span>
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
                  Served from the result cache: the model wasn&apos;t called again for this draft.
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
