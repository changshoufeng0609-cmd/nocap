"use client";

import { ArrowLeftIcon, ChevronLeftIcon, ChevronRightIcon, ClockAlertIcon, ExternalLinkIcon, XIcon } from "lucide-react";
import type { Ref } from "react";

import { PendingDots, StatusChip } from "@/components/status";
import { ReceiptCalculation } from "@/components/receipt-calculation";
import { Button } from "@/components/ui/button";
import { SOURCE_LABEL, displayStatus, filedValue, firstPublic, reasonText, valueIsWrong } from "@/lib/claims";
import {
  formatClaimed,
  formatDuration,
  formatFiscalPeriod,
  formatPeriodRange,
  formatUtc,
  formatValue,
} from "@/lib/format";
import type { Claim, Fact, Verdict } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  claim: Claim;
  verdict: Verdict | undefined;
  /** The as-of time of the check, ISO, or null for latest filings. */
  asOf: string | null;
  index: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  onBack?: () => void;
  onClose?: () => void;
  titleRef?: Ref<HTMLHeadingElement>;
  titleId: string;
}

export function ReceiptDetail({ claim, verdict, asOf, index, total, onPrev, onNext, onBack, onClose, titleRef, titleId }: Props) {
  const status = displayStatus(verdict);
  const filed = verdict ? filedValue(claim, verdict) : null;
  const lookAhead = verdict?.reason === "look_ahead";
  const struck = valueIsWrong(verdict);
  const publicAt = verdict && lookAhead ? firstPublic(verdict) : null;
  const effectiveAsOf = verdict?.as_of ?? asOf;
  const gap = publicAt && effectiveAsOf ? Date.parse(publicAt) - Date.parse(effectiveAsOf) : null;

  return (
    <article aria-labelledby={titleId} className="rise flex flex-col gap-5">
      <div className="flex items-center justify-between gap-2">
        {onBack ? (
          <Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" onClick={onBack}>
            <ArrowLeftIcon />
            All claims
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">Receipt</span>
        )}
        <div className="flex items-center gap-1">
          <span className="mr-1 font-mono text-xs text-muted-foreground tabular-nums">
            {index + 1} / {total}
          </span>
          <Button variant="ghost" size="icon-sm" onClick={onPrev} aria-label="Previous claim" disabled={total < 2}>
            <ChevronLeftIcon />
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={onNext} aria-label="Next claim" disabled={total < 2}>
            <ChevronRightIcon />
          </Button>
          {onClose ? (
            <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="Close receipt">
              <XIcon />
            </Button>
          ) : null}
        </div>
      </div>

      <header className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={status} />
          <span className="text-sm text-muted-foreground">
            {verdict ? reasonText(verdict.reason) : "Checking against filings"}
          </span>
        </div>
        <h2
          id={titleId}
          ref={titleRef}
          tabIndex={-1}
          className="font-serif text-[1.75rem] leading-tight font-medium tracking-tight outline-none"
        >
          <span className={cn(struck && "line-through decoration-bad-line decoration-2")}>
            {claim.value_text}
          </span>
        </h2>
        <p className="text-sm text-pretty text-muted-foreground">
          {[claim.entity, claim.metric_text, claim.period_text].filter(Boolean).join(" · ")}
        </p>
      </header>

      {lookAhead && publicAt ? (
        <div className="flex gap-3 rounded-lg border border-time-line/40 bg-time-tint px-3.5 py-3 text-sm">
          <ClockAlertIcon className="mt-0.5 size-4 shrink-0 text-time" aria-hidden />
          <div className="flex flex-col gap-0.5">
            <p className="font-semibold text-time">Not public until {formatUtc(publicAt)}</p>
            {effectiveAsOf ? (
              <p className="text-foreground/80">
                Checked as of {formatUtc(effectiveAsOf)}
                {gap && gap > 0 ? `, ${formatDuration(gap)} before it was filed` : ""}.
              </p>
            ) : null}
          </div>
        </div>
      ) : null}

      <dl className="grid grid-cols-2 overflow-hidden rounded-lg border border-border bg-card">
        <div className="flex flex-col gap-1 border-r border-border px-4 py-3">
          <dt className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">Claimed</dt>
          <dd
            className={cn(
              "font-mono text-xl font-medium tabular-nums",
              struck && "text-bad line-through decoration-2",
            )}
          >
            {formatClaimed(claim)}
          </dd>
        </div>
        <div className="flex flex-col gap-1 px-4 py-3">
          <dt className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">
            {lookAhead ? "Filed later" : "Filed"}
          </dt>
          <dd className={cn("font-mono text-xl font-medium tabular-nums", status === "verified" && "text-ok")}>
            {!verdict ? (
              <PendingDots className="text-muted-foreground" />
            ) : filed ? (
              formatValue(filed.value, filed.unit)
            ) : (
              <span className="text-base text-muted-foreground">{verdict.evidence.length && verdict.status !== "unverifiable" ? "See cited figures" : "No filed figure"}</span>
            )}
          </dd>
          {filed?.note ? <dd className="font-mono text-[0.7rem] text-muted-foreground">{filed.note}</dd> : null}
        </div>
      </dl>

      {verdict ? <ReceiptCalculation claim={claim} verdict={verdict} /> : null}

      {verdict ? (
        <figure className="flex flex-col gap-1.5">
          <figcaption className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">
            Why
          </figcaption>
          <blockquote className="border-l-2 border-border pl-3.5 font-serif text-[1.05rem] leading-relaxed text-pretty">
            {verdict.explanation}
          </blockquote>
        </figure>
      ) : (
        <div className="shimmer h-16 rounded-lg" aria-hidden />
      )}

      {verdict ? (
        <section aria-label="Receipts" className="flex flex-col gap-2.5">
          <h3 className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">
            {verdict.evidence.length
              ? `Receipts · ${verdict.evidence.length} ${verdict.evidence.length === 1 ? "filing" : "filings"}`
              : "Receipts"}
          </h3>
          {verdict.evidence.length ? (
            <ul className="flex flex-col gap-3">
              {verdict.evidence.map((f, i) => (
                <FactReceipt key={`${f.accession}-${f.metric}-${f.period_end}-${i}`} fact={f} asOf={effectiveAsOf} index={i + 1} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No filing holds this figure, so there is nothing to cite. Receipts marks it ? rather than guessing.
            </p>
          )}
        </section>
      ) : null}

      <section aria-label="Extracted claim" className="flex flex-col gap-2">
        <h3 className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">
          Extracted by Muse Spark
        </h3>
        <dl className="flex flex-wrap gap-1.5 font-mono text-[0.7rem]">
          {[
            ["metric", claim.metric],
            ["value", `${claim.value} ${claim.scale === "units" ? "" : claim.scale}`.trim()],
            ["unit", claim.unit],
            ["period", claim.period_type],
            ["comparison", claim.comparison],
            ["basis", claim.basis ?? "not stated"],
            ...(claim.segment ? [["segment", claim.segment]] : []),
            ...(claim.forward_looking ? [["forward-looking", "yes"]] : []),
          ].map(([k, v]) => (
            <div key={k} className="flex gap-1 rounded-md bg-muted px-1.5 py-1">
              <dt className="text-muted-foreground">{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>
    </article>
  );
}

function Row({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 py-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0", className)}>{children}</dd>
    </div>
  );
}

function FactReceipt({ fact, asOf, index }: { fact: Fact; asOf: string | null | undefined; index: number }) {
  const afterAsOf = !!asOf && Date.parse(fact.accepted_at) > Date.parse(asOf);
  const period = formatFiscalPeriod(fact);
  return (
    <li className="receipt-slip rounded-b-lg px-4 pt-5 pb-3.5 text-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-md bg-foreground px-1.5 py-0.5 font-mono text-[0.7rem] font-semibold text-background">
            {fact.form}
          </span>
          <span className="font-medium">{SOURCE_LABEL[fact.source] ?? fact.source} · {index}</span>
          {fact.basis === "non-GAAP" ? (
            <span className="rounded-md border border-border px-1.5 py-0.5 text-[0.7rem] text-muted-foreground">
              non-GAAP
            </span>
          ) : null}
        </div>
        {period ? <span className="shrink-0 font-mono text-xs text-muted-foreground">{period}</span> : null}
      </div>
      <p className="mt-2 font-mono text-lg font-medium tabular-nums">
        {formatValue(fact.value, fact.unit)}
        {fact.segment ? <span className="ml-2 font-sans text-xs text-muted-foreground">{fact.segment}</span> : null}
      </p>
      <dl className="mt-2 border-t border-dashed border-border pt-2 text-[0.8rem]">
        <Row label="Accession" className="font-mono">
          {fact.accession}
        </Row>
        <Row label="Accepted" className="font-mono">
          {formatUtc(fact.accepted_at)}
          {afterAsOf ? (
            <span className="ml-2 rounded bg-time-tint px-1 py-px font-sans text-[0.68rem] font-semibold text-time">
              after as-of
            </span>
          ) : null}
        </Row>
        <Row label="Period">{formatPeriodRange(fact)}</Row>
        {fact.source === "xbrl" && fact.concept ? (
          <Row label="Concept" className="font-mono break-all">
            {fact.concept}
          </Row>
        ) : null}
        {fact.source === "press_release" && fact.locator ? (
          <Row label="Cell text" className="font-mono">
            <q className="before:content-['“'] after:content-['”']">{fact.locator}</q>
          </Row>
        ) : null}
        {fact.source === "derived" ? (
          <Row label="Derived" className="text-pretty">
            {fact.locator ?? (fact.derived_from?.length ? `From ${fact.derived_from.join(", ")}` : "Computed from filed figures")}
          </Row>
        ) : null}
      </dl>
      <a
        href={fact.url}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-2.5 inline-flex items-center gap-1.5 text-[0.8rem] font-medium underline decoration-border underline-offset-4 hover:decoration-foreground focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        View on EDGAR
        <ExternalLinkIcon className="size-3.5" aria-hidden />
        <span className="sr-only">(opens in a new tab)</span>
      </a>
    </li>
  );
}
