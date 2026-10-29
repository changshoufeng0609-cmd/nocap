import { STATUS_META, type DisplayStatus } from "@/lib/claims";
import { cn } from "@/lib/utils";

const GLYPH: Record<DisplayStatus, string> = {
  verified: "bg-ok-tint text-ok border border-ok-line/50",
  wrong: "bg-bad-solid text-white border border-transparent",
  look_ahead: "bg-time-tint text-time border border-time-line/60",
  unverifiable: "bg-card text-unk border border-dashed border-unk-line",
  approximate: "bg-approx-tint text-approx border border-approx-line/60",
  pending: "bg-pending-tint text-pending border border-transparent",
};

const CHIP: Record<DisplayStatus, string> = {
  verified: "bg-ok-tint text-ok",
  wrong: "bg-bad-tint text-bad",
  look_ahead: "bg-time-tint text-time",
  unverifiable: "bg-unk-tint text-unk",
  approximate: "bg-approx-tint text-approx",
  pending: "bg-pending-tint text-pending",
};

export function PendingDots({ className }: { className?: string }) {
  return (
    <span className={cn("pending-dots", className)} aria-hidden>
      <span />
      <span />
      <span />
    </span>
  );
}

/** The ✓ ✗ ? ≈ glyph in its colored disc. The glyph is always visible, so color is never the only cue. */
export function StatusGlyph({
  status,
  size = "md",
  className,
}: {
  status: DisplayStatus;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full leading-none font-semibold",
        size === "sm" ? "size-[1.15rem] text-[0.7rem]" : "size-6 text-[0.8rem]",
        GLYPH[status],
        className,
      )}
    >
      {status === "pending" ? <PendingDots className="text-[0.55rem]" /> : STATUS_META[status].glyph}
    </span>
  );
}

export function statusLabel(status: DisplayStatus): string {
  return status === "pending" ? "Checking" : STATUS_META[status].label;
}

export function StatusChip({ status, className }: { status: DisplayStatus; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full py-0.5 pr-2.5 pl-0.5 text-xs font-semibold",
        CHIP[status],
        className,
      )}
    >
      <StatusGlyph status={status} size="sm" />
      {statusLabel(status)}
    </span>
  );
}
