import type { ReactNode } from "react";

import { formatDay } from "@/lib/format";

// Every number here comes from app/benchmarks/accuracy.json, which scripts/build_accuracy_panel.py generates from the
// committed spot500 run records with eval/spot500_report.py. Percentages are the report's own rounding.
type RateKey = "caught" | "target_verified" | "true_wrong" | "true_verified" | "verified_precision" | "wrong_precision";
type Rate = { label: string; k: number; n: number; rate: number; ci95: number[]; pct: string; ci95_pct: string[] };
type Measured = { items: number; companies: number; rates: Record<RateKey, Rate> };
type Run = Measured & { set: string; record: string; run_date: string; model: string; model_calls: number;
  cost_usd: number; cost_is_estimate: boolean; commit: string; methods: string };
export type AccuracyData = { generated_by: string; report: string; github: string; interval: string;
  current: Run & { without_company_in_development: Measured & { slice: string } }; previous: Run };

const MARK_WORD = { "✓": "verified", "✗": "wrong", "?": "unverifiable" } as const;

/** A verdict mark as the site draws it; screen readers hear the word instead. */
function Mark({ glyph }: { glyph: keyof typeof MARK_WORD }) {
  return <><span aria-hidden="true">{glyph}</span><span className="sr-only">{MARK_WORD[glyph]}</span></>;
}

const count = (value: number) => value.toLocaleString("en-US");
const fraction = (rate: Rate) => `${count(rate.k)}/${count(rate.n)}`;

const TILES: { key: RateKey; label: ReactNode }[] = [
  { key: "caught", label: "Planted errors caught" },
  { key: "target_verified", label: <>Planted errors marked&nbsp;<Mark glyph="✓" /></> },
  { key: "true_wrong", label: <>True figures flagged&nbsp;<Mark glyph="✗" /></> },
  { key: "verified_precision", label: <>When NoCap says&nbsp;<Mark glyph="✓" />, it&apos;s right</> },
];

function External({ href, children }: { href: string; children: ReactNode }) {
  return <a className="underline underline-offset-4" href={href} target="_blank" rel="noopener noreferrer">
    {children}<span className="sr-only"> (opens in a new tab)</span>
  </a>;
}

/** The headline result of the comprehensive accuracy test, for readers who never open the README. */
export function AccuracyPanel({ data }: { data: AccuracyData }) {
  const { current, previous } = data;
  const fresh = current.without_company_in_development;
  const before = previous.rates;
  const url = (path: string) => `${data.github}${path}`;
  return <section aria-labelledby="accuracy-test" className="space-y-5 rounded-xl border border-border bg-card p-5 sm:p-6">
    <div className="max-w-4xl space-y-2">
      <h2 id="accuracy-test" className="font-serif text-2xl font-semibold">Comprehensive accuracy test (current verifier)</h2>
      <p className="text-pretty leading-relaxed">{count(current.items)} frozen items from {count(current.companies)} {"S&P 500"} companies, run once on {formatDay(current.run_date)} against the deployed verifier.</p>
      <p className="text-sm leading-relaxed text-muted-foreground">Each item is a real sentence from a company&apos;s earnings release, or a copy of one with a single planted error such as a changed value, period, scale or company.</p>
    </div>
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {TILES.map(({ key, label }) => {
        const rate = current.rates[key];
        return <div key={key} className="flex min-w-0 flex-col rounded-lg border border-border bg-background p-3 sm:p-4">
          <dt className="text-xs leading-snug text-muted-foreground">{label}</dt>
          <dd className="mt-auto flex flex-col pt-2 sm:flex-row sm:flex-wrap sm:items-baseline sm:gap-x-2">
            <span className="whitespace-nowrap text-2xl font-semibold tabular-nums">{fraction(rate)}</span>
            <span className="text-sm font-medium tabular-nums text-muted-foreground">{rate.pct}</span>
          </dd>
          <dd className="mt-1 text-xs tabular-nums text-muted-foreground">95% interval <span className="whitespace-nowrap">{rate.ci95_pct[0]}–{rate.ci95_pct[1]}</span></dd>
        </div>;
      })}
    </dl>
    <div className="max-w-4xl space-y-2 text-sm leading-relaxed text-muted-foreground">
      <p><strong className="font-medium text-foreground">On the {count(fresh.companies)} companies not used in development or named in fixes</strong> ({count(fresh.items)} items): {fraction(fresh.rates.caught)} planted errors caught ({fresh.rates.caught.pct}) and {fraction(fresh.rates.true_wrong)} true figures flagged&nbsp;<Mark glyph="✗" /> ({fresh.rates.true_wrong.pct}).</p>
      <p>NoCap verified {fraction(current.rates.true_verified)} true figures ({current.rates.true_verified.pct}). A figure it cannot settle from the filings stays&nbsp;<Mark glyph="?" />.</p>
      <p><strong className="font-medium text-foreground">Previous version</strong> (first comprehensive test, code <code className="rounded bg-muted px-1">{previous.commit}</code>, {count(previous.items)} items from {count(previous.companies)} companies): {fraction(before.caught)} caught, {fraction(before.target_verified)} marked&nbsp;<Mark glyph="✓" />, {fraction(before.true_wrong)} flagged&nbsp;<Mark glyph="✗" />; its&nbsp;<Mark glyph="✓" /> marks were right {count(before.verified_precision.k)} of {count(before.verified_precision.n)} times. The two tests use different sentences, so this is not a controlled before/after comparison.</p>
      <p className="text-xs">Run {formatDay(current.run_date)} on code <code className="rounded bg-muted px-1">{current.commit}</code> · {count(current.model_calls)} calls to <span className="whitespace-nowrap font-mono">{current.model}</span> · ${current.cost_usd.toFixed(2)} {current.cost_is_estimate ? "estimated" : "measured"} usage cost · 95% intervals: {data.interval}.</p>
    </div>
    <p className="max-w-4xl rounded-lg border border-border bg-muted/50 p-3 text-sm leading-relaxed text-muted-foreground"><strong className="font-medium text-foreground">Limits.</strong> Labels are machine-made, with no independent human review. Later changes, such as release-statement matching and wording fixes, came after this test and are not in its numbers. The full method and every failure are in the <External href={url(current.methods.split("#")[0])}>methods document</External>.</p>
    <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
      <External href={url(current.methods)}>This run&apos;s results and failures</External>
      <External href={url(previous.methods)}>Previous version&apos;s results</External>
      <External href={url(data.report)}>Report script that computes every number</External>
    </div>
  </section>;
}
