import type { Metadata } from "next";
import Link from "next/link";

import { DemoPlayer } from "@/components/demo-player";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Demo · NoCap",
  description: "A 2:58 pitch demo and a vertical X/Threads cut: check an AI-drafted earnings story against SEC filings, fix it with receipts, apply a historical cutoff, read a real SEC slide with SAM 3.1, ask a whole 10-K and replay a real Private check.",
};

export default function DemoPage() {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-8 px-4 py-8 sm:px-6 sm:py-12">
        <div className="max-w-3xl space-y-3">
          <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">Team NoCap · Meta Global AI Developer Hackathon</p>
          <h2 className="font-serif text-3xl font-semibold tracking-tight sm:text-4xl">Every financial number needs a receipt.</h2>
          <p className="text-pretty text-muted-foreground">Follow an AI-drafted earnings story to SEC receipts and verified corrections, then a real SEC slide and a whole 10-K, in 2 minutes 58 seconds — or switch to the 9:16 vertical cut built for X and Threads.</p>
        </div>
        <DemoPlayer />
        <section aria-labelledby="workflow-title" className="space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="workflow-title" className="font-serif text-2xl font-semibold">From draft to evidence</h2>
            <span className="font-mono text-xs text-muted-foreground">4 Meta models · deterministic SEC verification</span>
          </div>
          <ol className="grid gap-4 sm:grid-cols-2">
            {[
              ["Check the draft", "Muse Spark 1.3", "Muse Spark extracts financial claims. Deterministic code compares values, periods and accounting bases with SEC evidence."],
              ["Inspect the receipt", "SEC EDGAR Gate", "Open the filing behind each verdict. Ambiguous or unsupported claims remain unverifiable."],
              ["Review a correction", "Muse Wonder 1.0", "Muse Wonder proposes corrected digits. Code re-checks each edit and the whole draft; review the changed numbers and keep the supporting receipts."],
              ["Move the cutoff", "Point-in-Time Gate", "See which evidence was accepted by EDGAR at a chosen time. Filing availability is distinct from numerical correctness."],
              ["Check a slide", "SAM 3.1 + Muse Spark", "SAM 3.1 finds the charts, Muse Spark reads the printed labels, and code checks each reading against filings."],
              ["Ask a whole filing", "Muse Spark · 101k Tokens", "Muse Spark reads an entire 10-K in one request. Code keeps only quotes it finds word for word in the filing."],
            ].map(([title, badge, description], index) => (
              <li key={title} className="flex flex-col justify-between rounded-xl border border-border bg-card p-4 shadow-2xs">
                <div>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="font-medium">
                      <span className="mr-2 rounded border border-border bg-muted/60 px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
                        0{index + 1}
                      </span>
                      {title}
                    </h3>
                    <span className="rounded-md border border-border/80 bg-background px-2 py-0.5 font-mono text-[0.7rem] text-muted-foreground">
                      {badge}
                    </span>
                  </div>
                  <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{description}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
        <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">The walkthrough uses saved examples and recorded live runs: Fix, two real SEC documents and a Private check. Ambiguous and forward-looking claims remain unverifiable, and image readings need review. See the <Link href="/benchmarks" className="underline underline-offset-4">benchmark results and methods</Link> for measured accuracy, coverage and limitations.</p>
        <Link href="/" className="inline-flex rounded-md bg-foreground px-5 py-3 text-sm font-medium text-background focus-visible:outline-2 focus-visible:outline-offset-4">Open the workspace</Link>
      </main>
      <SiteFooter />
    </>
  );
}
