"use client";

import { ArrowRightIcon, CopyIcon, ExternalLinkIcon, Loader2Icon, WandSparklesIcon } from "lucide-react";
import { Fragment, useId, type ReactNode } from "react";
import { toast } from "sonner";

import { StatusChip, StatusGlyph } from "@/components/status";
import { ReceiptCalculation } from "@/components/receipt-calculation";
import { Button } from "@/components/ui/button";
import { useFix } from "@/hooks/use-fix";
import { byDocumentOrder, DISPLAY_STATUSES, STATUS_META, displayCounts, displayStatus } from "@/lib/claims";
import { formatCost, formatFiscalPeriod, formatLatency, formatPeriodRange, formatUtc, formatUtcMinutes, formatValue } from "@/lib/format";
import { type Fact, type FixRequest, type FixResult, type FixStage } from "@/lib/types";

const STAGES: Record<FixStage, string> = {
  checking: "Checking the original against the filings…",
  rewriting: "Muse Wonder is proposing corrections…",
  verifying: "Re-checking the revised draft…",
};

function sourceUrl(raw: string): string | undefined {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" ? url.href : undefined;
  } catch { return undefined; }
}

function FilingReceipt({ fact, index }: { fact: Fact; index?: number }) {
  const href = sourceUrl(fact.url);
  return (
    <li className="min-w-0 rounded-lg border border-border bg-background p-3 text-xs leading-relaxed">
      <p className="font-medium text-foreground">
        {index ? `Receipt ${index} · ` : ""}{fact.ticker} · {fact.form} · {formatFiscalPeriod(fact) ?? formatPeriodRange(fact)} · {fact.basis}
      </p>
      <p className="mt-1">{formatValue(fact.value, fact.unit)} · {fact.metric.replace(/_/g, " ")}</p>
      {fact.locator ? <blockquote className="mt-2 border-l-2 border-border pl-2 break-words text-muted-foreground">{fact.locator}</blockquote> : null}
      <p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">{fact.accession}</p>
      <p className="text-muted-foreground">Accepted {formatUtc(fact.accepted_at)}</p>
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 font-medium underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
          View on EDGAR <ExternalLinkIcon className="size-3" aria-hidden />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      ) : null}
    </li>
  );
}

/** Offsets are Python Unicode code points. Preserve every untouched character, including emoji. */
function diffNodes(result: FixResult, revised: boolean, prefix: string): ReactNode[] {
  const text = Array.from(result.original_text);
  const nodes: ReactNode[] = [];
  let cursor = 0;
  result.changes.forEach((change, index) => {
    nodes.push(<Fragment key={`text-${index}`}>{text.slice(cursor, change.start).join("")}</Fragment>);
    nodes.push(revised ? (
      <Fragment key={`change-${index}`}>
        <mark className="rounded-sm bg-ok-tint px-0.5 text-foreground underline decoration-ok-line decoration-2 underline-offset-4">{change.after}</mark>
        <a href={`#${prefix}-change-${index}`} aria-label={`Receipt ${index + 1} for ${change.after}`} className="ml-0.5 align-super font-sans text-xs font-semibold text-ok underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">[{index + 1}]</a>
      </Fragment>
    ) : <del key={`change-${index}`} className="rounded-sm bg-bad-tint px-0.5 decoration-bad/70">{change.before}</del>);
    cursor = change.end;
  });
  nodes.push(<Fragment key="tail">{text.slice(cursor).join("")}</Fragment>);
  return nodes;
}

function withReceipts(result: FixResult): string {
  const text = Array.from(result.original_text);
  let cited = "";
  let cursor = 0;
  result.changes.forEach((change, index) => {
    cited += text.slice(cursor, change.start).join("") + change.after + ` [${index + 1}]`;
    cursor = change.end;
  });
  cited += text.slice(cursor).join("");
  const receipts = result.changes.map((change, index) => {
    const sources = change.evidence.map((f) => `${f.ticker} ${f.form}; accession ${f.accession}; accepted ${formatUtc(f.accepted_at)}; ${f.url}`).join("\n    ");
    return `[${index + 1}] ${change.before} → ${change.after}. ${change.explanation}\n    ${sources}`;
  });
  return `${cited}\n\nReceipts for corrections\n${receipts.join("\n\n")}`;
}

function Revision({ result, request, prefix }: { result: FixResult; request: FixRequest; prefix: string }) {
  const claims = [...result.results].sort((a, b) => byDocumentOrder(a.claim, b.claim));
  const changed = result.changes.length;
  const counts = displayCounts(result.results.map(({ verdict }) => verdict));
  const summary = result.status === "unchanged"
    ? "No safe corrections were applied."
    : result.status === "partial"
      ? `${changed} ${changed === 1 ? "correction" : "corrections"} verified; some red flags still need review.`
      : `${changed} ${changed === 1 ? "correction" : "corrections"} verified. Original red flags resolved.`;

  async function copy(citations: boolean) {
    try {
      await navigator.clipboard.writeText(citations ? withReceipts(result) : result.rewritten_text);
      toast.success(citations ? "Copied draft with correction receipts." : "Copied revised draft without citations.");
    } catch {
      toast.error("Couldn't copy. Select the revised text and copy it manually.");
    }
  }

  return (
    <div className="border-t border-border" data-testid="fix-result">
      <div className="space-y-3 px-4 py-4 sm:px-6">
        <p className="font-medium" role="status">{summary}</p>
        <ul className="flex flex-wrap gap-x-5 gap-y-2" aria-label="Re-check verdict counts">
          {DISPLAY_STATUSES.filter((status) => status !== "look_ahead" || counts[status] > 0).map((status) => <li key={status} className="flex items-center gap-1.5 text-sm"><StatusGlyph status={status} size="sm" /><span className="font-semibold tabular-nums">{counts[status]}</span><span className="text-muted-foreground">{STATUS_META[status].label.toLowerCase()}</span></li>)}
        </ul>
        <p className="text-xs text-muted-foreground">
          As of {request.as_of ? formatUtcMinutes(request.as_of) : "the latest filings"}. Unverifiable claims remain unconfirmed.
          {" "}{formatLatency(result.latency_s)} · {formatCost(result.cost_usd)}
        </p>
      </div>

      {changed ? (
        <>
          <div className="grid border-y border-border lg:grid-cols-2">
            <section aria-label="Original draft before corrections" className="min-w-0 border-b border-border px-4 py-5 sm:px-6 lg:border-r lg:border-b-0">
              <h3 className="mb-3 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Original draft</h3>
              <div className="proof whitespace-pre-wrap break-words">{diffNodes(result, false, prefix)}</div>
            </section>
            <section aria-label="Revised draft" className="min-w-0 bg-ok-tint/15 px-4 py-5 sm:px-6">
              <h3 className="mb-3 text-xs font-semibold tracking-wide text-muted-foreground uppercase">Revised draft · re-checked</h3>
              <div className="proof whitespace-pre-wrap break-words" data-testid="revised-draft">{diffNodes(result, true, prefix)}</div>
            </section>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6">
            <p className="text-xs text-muted-foreground">Review the changes before using them. Your original draft is unchanged.</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => void copy(false)}><CopyIcon />Copy draft</Button>
              <Button size="sm" variant="outline" onClick={() => void copy(true)}><CopyIcon />Copy with receipts</Button>
            </div>
          </div>
          <section aria-label="Correction receipts" className="px-4 py-5 sm:px-6">
            <h3 className="mb-3 font-serif text-xl font-medium">Receipts for each correction</h3>
            <ol className="space-y-3">
              {result.changes.map((change, index) => (
                <li key={`${change.claim_id}-${index}`} id={`${prefix}-change-${index}`} className="scroll-mt-6 rounded-lg border border-border p-4">
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-medium">
                    <span className="text-sm text-muted-foreground">[{index + 1}]</span><del className="text-bad">{change.before}</del><ArrowRightIcon className="size-4 text-muted-foreground" aria-hidden /><span className="text-ok">{change.after}</span>
                  </p>
                  <p className="mt-2 text-sm leading-relaxed">{change.explanation}</p>
                  <ul className="mt-3 grid gap-2 sm:grid-cols-2" aria-label={`Sources for correction ${index + 1}`}>
                    {change.evidence.map((fact, i) => <FilingReceipt key={`${fact.accession}-${i}`} fact={fact} />)}
                  </ul>
                </li>
              ))}
            </ol>
          </section>
        </>
      ) : null}

      {result.remaining.length ? (
        <section aria-label="Unresolved corrections" className="border-t border-border bg-bad-tint/30 px-4 py-4 sm:px-6">
          <h3 className="font-medium">Still needs your review</h3>
          <ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-relaxed">
            {result.remaining.map((item) => <li key={item.claim_id}>{item.explanation}</li>)}
          </ul>
        </section>
      ) : null}

      <section aria-label="Every claim after correction" className="border-t border-border px-4 py-5 sm:px-6">
        <h3 className="font-serif text-xl font-medium">{changed ? "Every claim, checked again" : "Original check results"}</h3>
        <p className="mt-1 mb-3 text-xs text-muted-foreground">Expand a claim to inspect its verdict and filing evidence.</p>
        <div className="divide-y divide-border">
          {claims.map(({ claim, verdict }) => (
            <details key={claim.id} className="py-3" open={verdict.status === "wrong" || verdict.status === "unverifiable"}>
              <summary className="cursor-pointer rounded-sm text-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
                <span className="ml-1 inline-flex max-w-[95%] flex-wrap items-center gap-2 align-middle"><StatusChip status={displayStatus(verdict)} /><span className="font-medium">{claim.value_text}</span><span className="text-muted-foreground">{claim.metric_text}{claim.basis ? ` · ${claim.basis}` : ""}</span></span>
              </summary>
              <p className="mt-3 text-sm leading-relaxed">{verdict.explanation}</p>
              <div className="mt-3"><ReceiptCalculation claim={claim} verdict={verdict} /></div>
              {verdict.evidence.length ? <ul className="mt-3 grid gap-2 sm:grid-cols-2">{verdict.evidence.map((fact, i) => <FilingReceipt key={`${fact.accession}-${i}`} fact={fact} index={i + 1} />)}</ul> : null}
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}

/** Parent keys this section to one finished check and unmounts it as soon as the draft or cutoff changes. */
export function FixWithReceipts({ request }: { request: FixRequest }) {
  const { state, start } = useFix();
  const prefix = useId();
  const running = state.phase === "running";
  return (
    <section aria-label="Fix with receipts" aria-busy={running} className="mt-8 overflow-hidden rounded-xl border border-border bg-card shadow-[0_1px_3px_rgb(0_0_0/0.04)]">
      <div className="flex flex-col items-start gap-4 px-4 py-5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div>
          <h2 className="font-serif text-2xl font-medium tracking-tight">Fix with receipts</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">Muse Wonder proposes corrections to wrong numbers. Every change must pass the verifier, then the revised draft is checked again.</p>
        </div>
        {state.phase === "idle" || running ? (
          <Button onClick={() => start(request)} disabled={running} className="shrink-0">
            {running ? <Loader2Icon className="animate-spin" aria-hidden /> : <WandSparklesIcon aria-hidden />}
            {running ? "Verifying corrections…" : "Fix with receipts"}
          </Button>
        ) : null}
      </div>
      {running ? <p className="border-t border-border px-4 py-4 text-sm text-muted-foreground sm:px-6" role="status">{STAGES[state.stage]}</p> : null}
      {state.phase === "error" ? (
        <div className="border-t border-border bg-bad-tint/30 px-4 py-4 sm:px-6" role="alert">
          <p className="text-sm font-medium">{state.error.message}</p>
          <p className="mt-1 text-xs text-muted-foreground">Your original draft and check results are still available above.</p>
          {state.error.retryable ? <Button size="sm" variant="outline" className="mt-3" onClick={() => start(request)}>Retry corrections</Button> : null}
        </div>
      ) : null}
      {state.phase === "done" ? <Revision result={state.result} request={request} prefix={prefix} /> : null}
    </section>
  );
}
