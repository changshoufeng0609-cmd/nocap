"use client";

import { ChevronRightIcon } from "lucide-react";

import { markLabel } from "@/components/highlighted-draft";
import { StatusGlyph } from "@/components/status";
import { displayStatus, reasonText, valueIsWrong } from "@/lib/claims";
import { formatExpectedValue } from "@/lib/format";
import type { Claim, Verdict } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  claims: Claim[];
  verdicts: Record<string, Verdict>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** Extraction still running: show a placeholder row for claims yet to arrive. */
  streaming: boolean;
  panelId: string;
}

/** The claim's basis, unless its metric text already names that one basis ("GAAP net income"). Two rows that share
 *  "GAAP and non-GAAP gross margins" then read "GAAP" and "non-GAAP" instead of looking identical. */
function basisLabel(c: Claim): string | null {
  if (!c.basis) return null;
  const text = c.metric_text.toLowerCase();
  const nonGaap = text.includes("non-gaap");
  const gaap = text.replaceAll("non-gaap", "").includes("gaap");
  const named = nonGaap && !gaap ? "non-GAAP" : gaap && !nonGaap ? "GAAP" : null;
  return named === c.basis ? null : c.basis;
}

export function ClaimsList({ claims, verdicts, selectedId, onSelect, streaming, panelId }: Props) {
  return (
    <ol className="flex flex-col">
      {claims.map((c) => {
        const v = verdicts[c.id];
        const status = displayStatus(v);
        const struck = valueIsWrong(v);
        const filed =
          struck && v?.expected_value != null ? formatExpectedValue(c, v.expected_value) : null;
        return (
          <li key={c.id} className="rise border-b border-border/70 last:border-b-0">
            <button
              type="button"
              data-list-id={c.id}
              onClick={() => onSelect(c.id)}
              aria-label={`${markLabel(c, v)} Open its receipt.`}
              aria-expanded={selectedId === c.id}
              aria-controls={panelId}
              className={cn(
                "group flex w-full items-start gap-3 rounded-md px-2.5 py-3 text-left transition-colors hover:bg-muted/70 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring",
                selectedId === c.id && "bg-muted shadow-2xs ring-1 ring-border",
              )}
            >
              <StatusGlyph status={status} className="mt-0.5" />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-baseline justify-between gap-3">
                  <span
                    className={cn(
                      "font-mono text-sm font-medium tabular-nums",
                      struck && "text-bad line-through decoration-1",
                    )}
                  >
                    {c.value_text}
                  </span>
                  {filed ? (
                    <span className="shrink-0 rounded bg-bad-tint px-1.5 py-0.5 font-mono text-xs font-medium text-bad tabular-nums">
                      filed {filed}
                    </span>
                  ) : null}
                </span>
                <span className="truncate text-xs text-muted-foreground">
                  {[c.entity, basisLabel(c), c.metric_text, c.period_text].filter(Boolean).join(" · ")}
                </span>
                <span className={cn("text-xs", status === "pending" ? "text-muted-foreground" : "text-foreground/80")}>
                  {v ? reasonText(v.reason) : "Checking against filings…"}
                </span>
              </span>
              <ChevronRightIcon
                className="mt-1 size-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5"
                aria-hidden
              />
            </button>
          </li>
        );
      })}
      {streaming ? (
        <li aria-hidden className="flex items-center gap-3 px-2 py-3">
          <span className="shimmer size-6 rounded-full" />
          <span className="flex flex-1 flex-col gap-1.5">
            <span className="shimmer h-3 w-24 rounded" />
            <span className="shimmer h-2.5 w-40 rounded" />
          </span>
        </li>
      ) : null}
    </ol>
  );
}
