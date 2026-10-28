import { StatusGlyph } from "@/components/status";
import { DISPLAY_STATUSES, STATUS_META } from "@/lib/claims";

const STEPS = [
  {
    title: "Extract",
    body: "Muse Spark reads the draft and turns every figure into a typed claim: metric, value, unit, period, company.",
  },
  {
    title: "Verify",
    body: "Deterministic code checks each claim against SEC EDGAR: the value, its scale and period, GAAP or non-GAAP, the company, and whether it was public yet.",
  },
  {
    title: "Cite",
    body: "Every verdict comes with its receipt: the filing, accession number, and the time EDGAR accepted it.",
  },
];

export function HowItWorks() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="font-serif text-xl font-medium tracking-tight">How a draft gets its receipts</h2>
        <ol className="mt-4 flex flex-col gap-4">
          {STEPS.map((s, i) => (
            <li key={s.title} className="grid grid-cols-[1.75rem_minmax(0,1fr)] gap-x-2">
              <span className="font-mono text-xs leading-6 text-muted-foreground tabular-nums">0{i + 1}</span>
              <div>
                <p className="text-sm leading-6 font-medium">{s.title}</p>
                <p className="text-sm leading-relaxed text-pretty text-muted-foreground">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <div>
        <h3 className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">Verdicts</h3>
        <ul className="mt-3 flex flex-col gap-2.5">
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
      <p className="rounded-lg bg-muted px-3.5 py-3 text-sm leading-relaxed text-pretty text-muted-foreground">
        <span className="font-medium text-foreground">Time travel.</span> Set “Check as of” to a past moment, and any
        figure filed after it is flagged as not yet public.
      </p>
    </div>
  );
}
