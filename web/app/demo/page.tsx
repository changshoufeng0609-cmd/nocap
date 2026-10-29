import type { Metadata } from "next";
import Link from "next/link";

import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Demo · NoCap",
  description: "A 2:45 product walkthrough: paste a financial draft, inspect SEC receipts, review corrections, apply a historical cutoff and check a slide.",
};

export default function DemoPage() {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-8 px-4 py-8 sm:px-6 sm:py-12">
        <div className="max-w-3xl space-y-3">
          <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">Team NoCap · Meta Global AI Developer Hackathon</p>
          <h2 className="font-serif text-3xl font-semibold tracking-tight sm:text-4xl">Every financial number needs a receipt.</h2>
          <p className="text-pretty text-muted-foreground">Follow a draft from pasted text to corrected numbers and SEC receipts in 2 minutes 45 seconds.</p>
        </div>
        <figure className="space-y-3">
          <video controls playsInline preload="metadata" aria-label="NoCap product demonstration, 2 minutes 45 seconds" className="aspect-video w-full rounded-lg border border-border bg-black">
            <source src="/demo/receipts-demo.mp4" type="video/mp4" />
            <track kind="captions" src="/demo/receipts-demo.en.vtt" srcLang="en" label="English" />
            Your browser does not support embedded video. <a href="/demo/receipts-demo.mp4">Download the MP4</a>.
          </video>
          <figcaption className="text-sm leading-relaxed text-muted-foreground">
            Continuous recording of actual browser actions, with local Kokoro-82M synthetic narration and English captions.
            Draft checks use cached extraction. Fix, Slide and Ask replay real October 29 responses for the same inputs;
            the recording labels these replays and shortens their original waiting time. It is a seeded demonstration,
            not a live provider latency measurement. The recorded slide used Spark regions after SAM found none.
          </figcaption>
          <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <a href="/demo/receipts-demo.mp4" download className="underline underline-offset-4">Download video</a>
            <a href="/demo/receipts-demo.en.vtt" download className="underline underline-offset-4">Download English captions</a>
          </div>
        </figure>
        <section aria-labelledby="workflow-title" className="space-y-4">
          <h2 id="workflow-title" className="font-serif text-2xl font-semibold">From draft to evidence</h2>
          <ol className="grid gap-4 sm:grid-cols-2">
            {[
              ["Check the draft", "Muse extracts financial claims. Deterministic code compares values, periods and accounting bases with SEC evidence."],
              ["Inspect the receipt", "Open the filing behind each verdict. Ambiguous or unsupported claims remain unverifiable."],
              ["Review a correction", "Proposed edits are checked again. Review the changed numbers and keep the supporting receipts."],
              ["Move the cutoff", "See which evidence was accepted by EDGAR at a chosen time. Filing availability is distinct from numerical correctness."],
            ].map(([title, description], index) => (
              <li key={title} className="rounded-lg border border-border p-4">
                <h3 className="font-medium"><span className="mr-2 text-muted-foreground">{index + 1}.</span>{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
              </li>
            ))}
          </ol>
        </section>
        <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">The demonstration shows specific seeded examples. Ambiguous and forward-looking claims remain unverifiable, and image readings need review. See the <Link href="/benchmarks" className="underline underline-offset-4">benchmark results and methods</Link> for measured accuracy, coverage and limitations.</p>
        <Link href="/" className="inline-flex rounded-md bg-foreground px-5 py-3 text-sm font-medium text-background focus-visible:outline-2 focus-visible:outline-offset-4">Open the workspace</Link>
      </main>
      <SiteFooter />
    </>
  );
}
