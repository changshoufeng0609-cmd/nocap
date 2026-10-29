"use client";

import { BookOpenIcon, CopyIcon, DownloadIcon, Loader2Icon, MessageSquareTextIcon, XIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { StatusChip } from "@/components/status";
import { MOCK } from "@/lib/api";
import { AskClientError, exportAnswer, fetchAskOptions, fetchFilings, streamAsk,
  type AskMode, type AskOptions, type AskResult, type AskStage, type Filing, type NumberAnswer } from "@/lib/ask";
import { displayStatus } from "@/lib/claims";
import { formatErrorCost, formatLatency, formatUtc } from "@/lib/format";
import { cn } from "@/lib/utils";

const STAGES: Record<AskStage, string> = {
  planning: "Muse Spark is turning your question into a financial lookup…",
  verifying: "Checking each answer sentence against SEC filings…",
  reading_filing: "Muse Spark is reading the complete filing; code will locate exact quotations…",
};

function ResolvedQuery({ result }: { result: NumberAnswer }) {
  const q = result.resolved_query;
  return <div className="rounded-lg border border-border bg-muted/40 p-3">
    <h4 className="text-xs font-semibold tracking-wide uppercase">Interpreted question</h4>
    <p className="mt-1 text-sm">{[q.entity || "Company not resolved", q.fiscal_period || "Period not specified",
      q.fiscal_year === null ? "Year not specified" : `FY${q.fiscal_year}`, q.basis || "Basis not specified", q.segment].filter(Boolean).join(" · ")}</p>
    <p className="mt-1 text-xs text-muted-foreground">{q.metrics.length ? q.metrics.map((m) => m.replaceAll("_", " ")).join(", ") : "No supported metric selected"}. Confirm this matches what you meant.</p>
  </div>;
}

export function AskWorkspace() {
  const [mode, setMode] = useState<AskMode>("numbers");
  const [question, setQuestion] = useState("");
  const [asOf, setAsOf] = useState("");
  const [options, setOptions] = useState<AskOptions | null>(null);
  const [filings, setFilings] = useState<Filing[] | null>(null);
  const [filingId, setFilingId] = useState("");
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [filingsError, setFilingsError] = useState<string | null>(null);
  const [stage, setStage] = useState<AskStage | null>(null);
  const [result, setResult] = useState<AskResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copyNotice, setCopyNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  const copyVersion = useRef(0);
  const invalidateCopy = useCallback(() => { copyVersion.current++; }, []);
  const selectedFiling = filings?.find((f) => f.id === filingId);
  const running = stage !== null;

  useEffect(() => {
    const ac = new AbortController();
    if (!MOCK) {
      void fetchAskOptions(ac.signal).then((next) => { if (!ac.signal.aborted) setOptions(next); })
        .catch(() => { if (!ac.signal.aborted) setOptionsError("Available companies could not be loaded. Try reloading the page."); });
      void fetchFilings(ac.signal).then((next) => { if (!ac.signal.aborted) { setFilings(next); setFilingId(next[0]?.id ?? ""); } })
        .catch(() => { if (!ac.signal.aborted) setFilingsError("The filing list could not be loaded. Try reloading the page."); });
    }
    return () => { ac.abort(); controller.current?.abort(); invalidateCopy(); };
  }, [invalidateCopy]);

  function reset() {
    controller.current?.abort(); controller.current = null; invalidateCopy();
    setStage(null); setResult(null); setError(null); setCopyNotice("");
  }
  function changeMode(next: AskMode) { reset(); setMode(next); setQuestion(""); }
  async function submit() {
    reset();
    if (!question.trim()) { setError("Enter a question first."); return; }
    if (asOf.trim() && (!Number.isFinite(Date.parse(asOf)) || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(asOf.trim()))) {
      setError("Enter an ISO 8601 time with a timezone, such as 2026-08-27T00:00:00Z."); return;
    }
    const cutoff = asOf.trim() ? new Date(asOf).toISOString() : null;
    if (mode === "filing_quotes" && (!selectedFiling || (cutoff && Date.parse(selectedFiling.accepted_at) > Date.parse(cutoff)))) {
      setError("Choose a listed filing accepted on or before the selected cutoff."); return;
    }
    const ac = new AbortController(); controller.current = ac;
    setStage(mode === "numbers" ? "planning" : "reading_filing");
    try {
      const answer = await streamAsk(mode, { question: question.trim(), as_of: cutoff,
        ...(mode === "filing_quotes" ? { filing_id: filingId } : {}) },
      (next) => { if (!ac.signal.aborted) setStage(next); }, ac.signal, selectedFiling);
      if (!ac.signal.aborted) setResult(answer);
    } catch (e) {
      if (!ac.signal.aborted) {
        const accounting = e instanceof AskClientError ? formatErrorCost(e.accounting) : "Cost unavailable.";
        setError(`${e instanceof Error ? e.message : "The question could not be completed."} ${accounting}`);
      }
    } finally { if (!ac.signal.aborted) setStage(null); }
  }
  async function copy() {
    if (!result) return;
    const version = ++copyVersion.current;
    try { await navigator.clipboard.writeText(exportAnswer(result)); if (version === copyVersion.current) setCopyNotice("Copied with source references."); }
    catch { if (version === copyVersion.current) setCopyNotice("Copy unavailable. Download the text instead."); }
  }
  function download() {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([exportAnswer(result)], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url;
    link.download = result.kind === "numbers" ? "nocap-answer-with-receipts.txt" : "nocap-filing-quotes.txt";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return <main className="mx-auto w-full max-w-5xl flex-1 px-4 pt-5 pb-16 sm:px-6">
    <header className="mb-5 space-y-2">
      <h2 className="font-serif text-3xl font-medium tracking-tight">Ask with receipts</h2>
      <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">Ask for a reported financial figure, or find exact quotations in a complete SEC filing. Muse Spark proposes the lookup or quotations; code controls what reaches the answer.</p>
    </header>
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_19rem]">
      <section className="min-w-0 rounded-xl border border-border bg-card p-4 sm:p-5" aria-label="Question input">
        <fieldset className="mb-5 flex flex-wrap gap-2"><legend className="mb-2 text-xs font-medium text-muted-foreground">Answer type</legend>
          {(["numbers", "filing_quotes"] as const).map((item) => <label key={item} className={cn("inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border px-3 text-sm", mode === item ? "border-foreground/40 bg-muted font-medium" : "border-border text-muted-foreground")}>
            <input type="radio" name="ask-kind" value={item} checked={mode === item} onChange={() => changeMode(item)} className="accent-foreground" />
            {item === "numbers" ? <MessageSquareTextIcon className="size-4" aria-hidden /> : <BookOpenIcon className="size-4" aria-hidden />}{item === "numbers" ? "Numeric answer" : "Filing quotes"}
          </label>)}
        </fieldset>
        {mode === "filing_quotes" ? <div className="mb-4 space-y-2">
          <label htmlFor="ask-filing" className="block text-sm font-medium">Complete filing</label>
          <select id="ask-filing" value={filingId} onChange={(e) => { reset(); setFilingId(e.target.value); }} disabled={!filings?.length}
            className="min-h-10 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm">
            {!filings?.length ? <option value="">{filings === null ? "Loading filings…" : "No complete filings are available"}</option> : filings.map((f) => <option key={f.id} value={f.id}>{f.ticker} · {f.form} · {f.accession}</option>)}
          </select>
          {selectedFiling ? <p className="text-xs leading-relaxed text-muted-foreground">Accepted {formatUtc(selectedFiling.accepted_at)} · {selectedFiling.characters.toLocaleString()} normalized characters · <a className="underline underline-offset-4" href={selectedFiling.url} target="_blank" rel="noopener noreferrer">Read filing on EDGAR</a></p> : null}
          {filings?.length === 0 ? <p className="text-xs text-muted-foreground">The operator must add complete filing texts to the evidence snapshot before quote search is available.</p> : null}
          {filingsError ? <p role="alert" className="text-xs text-bad">{filingsError}</p> : null}
        </div> : null}
        <label htmlFor="ask-question" className="mb-2 block text-sm font-medium">Your question</label>
        <Textarea id="ask-question" rows={4} maxLength={1500} value={question} onChange={(e) => { reset(); setQuestion(e.target.value); }}
          placeholder={mode === "numbers" ? "What was NVIDIA's GAAP revenue in Q2 FY2027?" : "Which passages describe risks from customer concentration?"} />
        <p className="mt-1 text-right text-xs text-muted-foreground">{question.length.toLocaleString()} / 1,500</p>
        {mode === "numbers" && options?.examples.length ? <div className="mt-3 flex flex-col items-start gap-1.5"><p className="text-xs text-muted-foreground">Try a question</p>{options.examples.map((example) => <button key={example} type="button" className="text-left text-xs underline decoration-border underline-offset-4 hover:decoration-foreground focus-visible:outline-2 focus-visible:outline-ring" onClick={() => { reset(); setQuestion(example); }}>{example}</button>)}</div> : null}
        <label htmlFor="ask-as-of" className="mt-5 mb-2 block text-sm font-medium">As of <span className="font-normal text-muted-foreground">(optional)</span></label>
        <input id="ask-as-of" value={asOf} maxLength={80} onChange={(e) => { reset(); setAsOf(e.target.value); }} placeholder="2026-08-27T00:00:00Z"
          className="h-10 w-full rounded-md border border-input bg-transparent px-3 text-sm" aria-describedby="ask-as-of-hint" />
        <p id="ask-as-of-hint" className="mt-1 text-xs text-muted-foreground">Only evidence accepted by this time is eligible. Leave empty for all available filings.</p>
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Button onClick={() => void submit()} disabled={running || !question.trim() || MOCK || (mode === "filing_quotes" && !selectedFiling)}>{running ? <Loader2Icon className="animate-spin" /> : mode === "numbers" ? <MessageSquareTextIcon /> : <BookOpenIcon />}{mode === "numbers" ? "Ask with receipts" : "Find exact quotes"}</Button>
          {running ? <Button variant="outline" onClick={reset}><XIcon />Cancel</Button> : <Button variant="ghost" onClick={() => { reset(); setQuestion(""); setAsOf(""); }}>Clear</Button>}
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">Your question{mode === "filing_quotes" ? " and the complete public filing" : ""} goes to Meta Muse Spark. New questions incur model usage. This is separate from Private check.</p>
        {MOCK ? <p className="mt-3 text-sm text-muted-foreground">Ask requires the live API; mock mode sends no question.</p> : null}
        {stage ? <p role="status" className="mt-4 text-sm">{STAGES[stage]}</p> : null}
        {error ? <p role="alert" className="mt-4 text-sm text-bad">{error}</p> : null}
      </section>
      <aside className="space-y-4 text-sm leading-relaxed text-muted-foreground" aria-label="Question scope">
        <div className="rounded-xl border border-border p-4"><h3 className="mb-2 font-medium text-foreground">{mode === "numbers" ? "A precise answer needs context" : "Quotation is not verification"}</h3>
          <p>{mode === "numbers" ? "Name one company, a fiscal year and quarter (or full year), and up to four metrics. Specify GAAP or non-GAAP when it matters. Ambiguous questions receive a clarification, not a guessed value." : "Quotes are exact matches in one normalized filing. They can be incomplete or irrelevant to your question. They do not prove the statement is true, and quoted numbers are not checked against XBRL here."}</p>
          <p className="mt-2 text-xs">{mode === "numbers" ? "No forecasts, investment advice or multi-period comparisons." : "The entire listed filing is sent without silent truncation. Forward-looking statements remain forecasts."}</p>
        </div>
        {mode === "numbers" ? <details className="rounded-xl border border-border p-4"><summary className="cursor-pointer font-medium text-foreground">Available evidence</summary>
          {optionsError ? <p className="mt-2 text-xs">{optionsError}</p> : options ? <><ul className="mt-3 space-y-2 text-xs">{options.companies.map((c) => <li key={c.ticker}><span className="font-medium text-foreground">{c.ticker}</span> · {c.name}<span className="mt-0.5 block">{[...new Set(c.periods.map((p) => `${p.fiscal_period} FY${p.fiscal_year}`))].join(", ")}</span></li>)}</ul><p className="mt-4 text-xs">Metrics: {options.metrics.map((m) => m.replaceAll("_", " ")).join(", ")}.</p></> : <p className="mt-2 text-xs">{MOCK ? "Live evidence details are unavailable in mock mode." : "Loading available companies…"}</p>}
        </details> : null}
      </aside>
    </div>
    {result ? <section aria-label={result.kind === "numbers" ? "Answer with receipts" : "Exact filing quotations"} className="mt-6 rounded-xl border border-border bg-card p-4 sm:p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-serif text-2xl font-medium">{result.kind === "numbers" ? "Answer with receipts" : "Exact filing quotations"}</h3>
        <p className="mt-1 text-xs text-muted-foreground">As of {result.as_of ? formatUtc(result.as_of) : "all available filings"} · {formatLatency(result.latency_s)} · {formatErrorCost(result)} · {result.model_calls} model call{result.model_calls === 1 ? "" : "s"}</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => void copy()}><CopyIcon />Copy with sources</Button><Button variant="outline" size="sm" onClick={download}><DownloadIcon />Download text</Button></div>
      </div>
      {copyNotice ? <p role="status" className="mb-3 text-xs text-muted-foreground">{copyNotice}</p> : null}
      {result.kind === "numbers" ? <>
        <ResolvedQuery result={result} />
        {result.items.some((item) => !item.withheld) ? <div className="my-5 space-y-4">{result.items.filter((item) => !item.withheld).map((item) => <p key={item.claim.id} className="font-serif text-xl leading-relaxed">{item.sentence}{" "}{item.footnote_ids.map((id) => <a key={id} className="ml-0.5 font-sans text-sm underline underline-offset-4" href={`#ask-footnote-${id}`} aria-label={`Source ${id}`}>[{id}]</a>)}</p>)}</div> : <p className="my-5 text-sm">No numeric answer was accepted.</p>}
        {result.items.some((item) => item.withheld) ? <details className="my-4 rounded-lg border border-dashed border-border p-3" open><summary className="cursor-pointer text-sm font-medium">Not included in the answer</summary><ul className="mt-3 space-y-3">{result.items.filter((item) => item.withheld).map((item) => <li key={item.claim.id} className="space-y-1 text-sm"><StatusChip status={displayStatus(item.verdict)} /><p className="line-through decoration-foreground/40">{item.sentence}</p><p className="text-xs text-muted-foreground">{item.verdict.explanation}</p></li>)}</ul></details> : null}
        {result.notice ? <p className="my-3 text-sm text-muted-foreground">{result.notice}</p> : null}
        {result.footnotes.length ? <ol aria-label="Answer sources" className="mt-5 space-y-3 border-t border-border pt-4">{result.footnotes.map((note) => <li id={`ask-footnote-${note.id}`} key={note.id} className="scroll-mt-6 text-sm"><span className="mr-2 font-mono">[{note.id}]</span><a className="underline underline-offset-4" href={note.url} target="_blank" rel="noopener noreferrer">{note.form} · {note.accession}</a><p className="mt-1 text-xs text-muted-foreground">Accepted {formatUtc(note.accepted_at)}</p></li>)}</ol> : null}
      </> : <>
        <p className="mb-4 text-sm"><a className="underline underline-offset-4" href={result.filing.url} target="_blank" rel="noopener noreferrer">{result.filing.ticker} · {result.filing.form} · {result.filing.accession}</a><span className="mt-1 block text-xs text-muted-foreground">Accepted {formatUtc(result.filing.accepted_at)}</span></p>
        <p className="mb-5 rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">{result.notice}</p>
        <ol className="space-y-5">{result.quotes.map((quote, i) => <li key={`${quote.start}-${quote.end}`}><blockquote className="border-l-2 border-foreground/30 pl-4 font-serif text-lg leading-relaxed">“{quote.text}”</blockquote><p className="mt-2 pl-4 text-xs text-muted-foreground">Quote {i + 1} · normalized filing characters {quote.start.toLocaleString()}–{quote.end.toLocaleString()} (Unicode code points, end exclusive)</p></li>)}</ol>
        {!result.quotes.length ? <p className="text-sm">No exact, uniquely located quotation was accepted for this question.</p> : null}
        {result.rejected_quotes ? <p className="mt-4 text-xs text-muted-foreground">{result.rejected_quotes} proposed quote{result.rejected_quotes === 1 ? " was" : "s were"} rejected by the exact-match checks.</p> : null}
      </>}
    </section> : null}
  </main>;
}
