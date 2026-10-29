"use client";

import { useId } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatUtcMinutes, fromUtcInputValue } from "@/lib/format";
import type { Example } from "@/lib/types";
import { cn } from "@/lib/utils";

const CONTROL_LABEL = "text-xs font-medium text-muted-foreground";

export function ExamplePicker({
  examples,
  error,
  activeId,
  onPick,
}: {
  examples: Example[] | null;
  error: string | null;
  activeId: string | null;
  onPick: (id: string) => void;
}) {
  const placeholder =
    examples === null ? "Loading examples…" : examples.length ? "Load an example draft" : error ? "Examples unavailable" : "No examples";
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <Label htmlFor="example-picker" className={CONTROL_LABEL}>
        Example
      </Label>
      <Select value={activeId ?? ""} onValueChange={onPick} disabled={!examples?.length}>
        <SelectTrigger id="example-picker" className="w-full min-w-0 data-[size=default]:h-9">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent position="popper" align="start" className="w-(--radix-select-trigger-width) min-w-72 max-w-[calc(100vw-2rem)]">
          {(examples ?? []).map((e) => (
            <SelectItem key={e.id} value={e.id} description={e.description || undefined}>
              {e.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export type AsOfState = { mode: "now" } | { mode: "custom"; value: string };

export function AsOfPicker({
  asOf,
  nowValue,
  onChange,
}: {
  asOf: AsOfState;
  /** The current UTC time as an input value; filled in after mount. */
  nowValue: string;
  onChange: (next: AsOfState) => void;
}) {
  const isNow = asOf.mode === "now";
  const iso = asOf.mode === "custom" ? fromUtcInputValue(asOf.value) : null;
  // Draft stays mounted beside Slide, so each picker needs its own label and hint ids.
  const inputId = `${useId()}as-of`;
  const hintId = `${inputId}-hint`;
  return (
    <div className="flex min-w-0 flex-col gap-1.5 sm:w-[19rem] sm:flex-none">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={inputId} className={CONTROL_LABEL}>
          Check as of — time travel
        </Label>
        <span className="font-mono text-[0.68rem] text-muted-foreground">UTC</span>
      </div>
      <div className="flex gap-1.5">
        <input
          id={inputId}
          type="datetime-local"
          step={60}
          value={isNow ? nowValue : asOf.value}
          onChange={(e) => onChange({ mode: "custom", value: e.target.value })}
          aria-describedby={hintId}
          className={cn(
            "h-9 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 font-mono text-[0.8rem] tabular-nums transition-colors outline-none",
            "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30",
            isNow && "text-muted-foreground",
          )}
        />
        <Button
          type="button"
          variant={isNow ? "secondary" : "outline"}
          size="lg"
          aria-pressed={isNow}
          onClick={() => onChange({ mode: "now" })}
        >
          Now
        </Button>
      </div>
      <p id={hintId} className={cn("text-xs", isNow ? "text-muted-foreground" : "text-foreground/80")}>
        {isNow
          ? "Now: checks against the latest filings"
          : iso
            ? `Anything filed after ${formatUtcMinutes(iso).replace(" UTC", "")} counts as not yet public`
            : "Enter a full date and time"}
      </p>
    </div>
  );
}
