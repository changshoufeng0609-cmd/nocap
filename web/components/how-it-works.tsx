import { StatusGlyph } from "@/components/status";
import { DISPLAY_STATUSES, STATUS_META } from "@/lib/claims";

const STEPS = [
  {
    title: "Extract",
    body: "Muse Spark extracts numeric claims from the draft: metric, value, unit, period and company. Review the highlights for omissions or misreadings.",
  },
  {
    title: "Verify",
    body: "Deterministic code checks each claim against SEC EDGAR: the value, its scale and period, GAAP or non-GAAP, the company, and whether it was public yet.",
  },
  {
    title: "Cite",
    body: "Inspect the filing, accession number and EDGAR acceptance time behind supported verdicts. A claim without usable evidence remains unverifiable.",
  },
];

const MODEL_ROLES = [
  { mode: "Draft & Ask", model: "muse-spark-1.3", role: "Typed claim extraction, lookup planning & 10-K quotes" },
  { mode: "Fix", model: "muse-wonder-1.0", role: "Numeric correction proposals, re-checked by code" },
  { mode: "Slide", model: "sam-3.1 + Spark", role: "Chart/table segmentation & visual label reading" },
  { mode: "Private", model: "Muse Glimmer", role: "Open-weights extraction on the operator's Modal GPU" },
];

export function HowItWorks() {
  return (
    <div className="flex flex-col gap-5 rounded-xl border border-border bg-card/60 p-4 shadow-2xs sm:p-5">
      <div>
        <h2 className="font-serif text-xl font-medium tracking-tight">How a draft gets its receipts</h2>
        <ol className="mt-4 flex flex-col gap-3.5">
          {STEPS.map((s, i) => (
            <li key={s.title} className="grid grid-cols-[1.85rem_minmax(0,1fr)] gap-x-2.5">
              <span className="mt-0.5 inline-flex size-6 items-center justify-center rounded-md border border-border bg-muted/70 font-mono text-[11px] font-semibold text-foreground/80 tabular-nums">
                0{i + 1}
              </span>
              <div>
                <p className="text-sm leading-6 font-semibold">{s.title}</p>
                <p className="text-sm leading-relaxed text-pretty text-muted-foreground">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <div className="border-t border-border/80 pt-4">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">Models propose · Code decides</h3>
          <span className="font-mono text-[10px] text-muted-foreground">SEC EDGAR gate</span>
        </div>
        <dl className="mt-2.5 grid gap-1.5">
          {MODEL_ROLES.map((item) => (
            <div key={item.mode} className="rounded-lg border border-border/70 bg-background/70 px-2.5 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <dt className="font-medium text-foreground">{item.mode}</dt>
                <dd className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-foreground/85">{item.model}</dd>
              </div>
              <dd className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{item.role}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="border-t border-border/80 pt-4">
        <h3 className="text-[0.7rem] font-semibold tracking-wide text-muted-foreground uppercase">Verdicts</h3>
        <ul className="mt-2.5 flex flex-col gap-2">
          {DISPLAY_STATUSES.map((s) => (
            <li key={s} className="flex items-start gap-2.5 text-sm">
              <StatusGlyph status={s} size="sm" className="mt-px" />
              <span>
                <span className="font-medium">{STATUS_META[s].label}</span>
                <span className="text-muted-foreground"> · {STATUS_META[s].legend}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
      <p className="rounded-lg border border-time-line/30 bg-time-tint/40 px-3.5 py-3 text-sm leading-relaxed text-pretty text-muted-foreground">
        <span className="font-semibold text-time">Time travel.</span> Set “Check as of” to a past moment, and any
        figure filed after it is flagged as not yet public.
      </p>
    </div>
  );
}
