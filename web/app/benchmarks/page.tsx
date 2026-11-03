import type { Metadata } from "next";
import Link from "next/link";

import { AccuracyPanel } from "@/components/accuracy-panel";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { formatDay } from "@/lib/format";
import accuracy from "./accuracy.json";
import data from "./results.json";

export const metadata: Metadata = {
  title: "Financial Q&A benchmark · NoCap",
  description: "Inspect 50 fixed financial questions, four model backends, raw answers, verification decisions and SEC sources.",
};

type Score = { planned: number; correct: number; wrong: number; abstain: number; unassessable: number;
  format_error: number; api_error: number; not_run: number; verified_accepted: number; verified_correct: number;
  verified_precision: number | null; verified_coverage: number; unsafe_passed: number };
type Run = { target: string; label: string; hosting: string; status: string; planned: number; calls: number;
  primary_score: { raw_correct: number; raw_wrong: number; raw_abstain: number; format_errors: number;
    api_errors: number; verified_accepted: number } | null;
  sensitivity_score: Score | null; token_api_cost_usd: number | null; unknown_usage_calls: number | null;
  charged_or_reserved_usd: number | null; unknown_reserved_usd: number | null;
  latency_median_s: number | null; wall_s: number | null; max_output_tokens: number | null;
  reasoning_effort: string | null; concurrency: number | null; gpu_usage_separate: boolean };
type Response = { target: string; label: string; raw_outcome: string; value_text: string | null; entity: string | null;
  unit: string | null; period: string | null; fiscal_year: number | null; reason: string | null;
  primary_accepted: boolean | null; primary_reason: string | null; sensitivity_outcome: string | null;
  sensitivity_accepted: boolean | null; sensitivity_reason: string | null; normalizations: Record<string, string>;
  model_output_text: string | null; output_sha256: string | null };
type Case = { question: { id: string; text: string; ticker: string; metric: string; as_of: string };
  reference_value: string; sources: { url: string; accession: string; form: string; accepted_at: string; source: string }[];
  responses: Response[] };
const benchmark = data as unknown as { status: string; manifest_sha256: string; cutoff: string; seed: number;
  question_count: number; ticker_counts: Record<string, number>; runs: Run[]; cases: Case[]; sensitivity_policy: string | null;
  gpu_cost_context: { observed_shared_total_usd: number; observed_successful_session_usd: number; scope: string; source_path: string } };
const REPO = "https://github.com/changshoufeng0609-cmd/nocap/blob/main/";
const percent = (value: number | null) => value === null ? "N/A" : `${(value * 100).toFixed(1)}%`;
const human = (value: string | null) => (value ?? "pending").replaceAll("_", " ");

/** The headline, computed from the published records so it cannot drift from them. Null until every model is scored. */
function conclusion() {
  const scored = benchmark.runs.flatMap(run => run.sensitivity_score ? [{ run, score: run.sensitivity_score }] : []);
  if (benchmark.status !== "complete" || scored.length !== benchmark.runs.length || !scored.length) return null;
  const sum = (pick: (score: Score) => number) => scored.reduce((total, { score }) => total + pick(score), 0);
  const coverage = scored.map(({ run, score }) => ({ label: run.label, value: score.verified_coverage }))
    .sort((a, b) => b.value - a.value);
  const share = (value: number) => `${Math.round(value * 100)}%`;
  return { answers: benchmark.runs.reduce((total, run) => total + run.calls, 0), models: scored.length,
    strict: benchmark.runs.reduce((total, run) => total + (run.primary_score?.verified_accepted ?? 0), 0),
    passed: sum(score => score.unsafe_passed), accepted: sum(score => score.verified_accepted),
    correct: sum(score => score.verified_correct), coverage, share,
    range: `${Math.round(coverage[coverage.length - 1].value * 100)}–${share(coverage[0].value)}` };
}

const COUNT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

export default function BenchmarksPage() {
  const completedCalls = benchmark.runs.reduce((sum, run) => sum + run.calls, 0);
  const result = conclusion();
  return <>
    <SiteHeader />
    <main className="mx-auto w-full max-w-6xl flex-1 space-y-10 px-4 py-8 sm:px-6 sm:py-12">
      <header className="max-w-4xl space-y-4">
        <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">Development benchmark · {formatDay(benchmark.cutoff)} SEC snapshot</p>
        {result ? <>
          <h2 className="font-serif text-3xl font-semibold tracking-tight sm:text-5xl">{result.passed === 0 ? "No wrong number got through" : `${result.passed} wrong number${result.passed === 1 ? "" : "s"} got through`}</h2>
          <p className="text-pretty text-lg leading-relaxed text-foreground">{result.answers} closed-book answers from {COUNT_WORDS[result.models] ?? result.models} Meta models. NoCap&apos;s gate let <strong>{result.passed} wrong number{result.passed === 1 ? "" : "s"}</strong> through and accepted <strong>{result.accepted === result.correct ? `${result.correct} correct answers` : `${result.accepted} answers, ${result.correct} of them correct`}</strong>, at <strong>{result.range} coverage</strong> per model.</p>
          <p className="text-sm leading-relaxed text-muted-foreground">Coverage: {result.coverage.map(({ label, value }) => `${label} ${result.share(value)}`).join(" · ")}. These counts use the post-hoc metadata policy described below; the pre-registered exact contract accepted {result.strict} of the {result.answers} answers.</p>
        </> : <h2 className="font-serif text-3xl font-semibold tracking-tight sm:text-5xl">What reaches the answer?</h2>}
        <p className="text-pretty text-lg leading-relaxed text-muted-foreground">Four model backends answer the same 50 financial questions from their own knowledge. NoCap then checks each proposed number against SEC evidence and either accepts it or withholds it.</p>
        <p className="text-sm leading-relaxed text-muted-foreground"><strong className="text-foreground">Closed-book:</strong> models receive the question and an output schema, with no filings, retrieval, search or tools. This measures a specific workflow on development questions. It does not establish accuracy on arbitrary questions or gains for systems that retrieve evidence.</p>
        <div className="flex flex-wrap gap-2 text-xs">
          {/* The question set's companies, not the workspace's current evidence coverage. */}
          {[`${benchmark.question_count} frozen questions`, `Questions on ${Object.keys(benchmark.ticker_counts).length} companies (${formatDay(benchmark.cutoff)} snapshot)`, "8 financial metrics", `${completedCalls} recorded calls`, "Held-out split not used"].map(label =>
            <span key={label} className="rounded-full border border-border px-3 py-1.5">{label}</span>)}
        </div>
        {benchmark.status !== "complete" ? <p role="status" className="rounded-lg border border-border bg-muted/50 p-3 text-sm">The registered run is in progress. Missing model results and the offline sensitivity analysis are shown as pending, never as zero-cost or perfect accuracy.</p> : null}
        <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
          <a className="underline underline-offset-4" href="/benchmarks/records" download>Download questions and model records</a>
          <a className="underline underline-offset-4" href={`${REPO}eval/qa_methods.md`} target="_blank" rel="noopener noreferrer">Read the methods</a>
          <Link className="underline underline-offset-4" href="/">Open the workspace</Link>
        </div>
      </header>

      <AccuracyPanel data={accuracy} />

      <section aria-labelledby="answer-results" className="space-y-4">
        <div className="max-w-4xl space-y-2">
          <h2 id="answer-results" className="font-serif text-2xl font-semibold">Post-hoc metadata sensitivity · small accepted samples</h2>
          <p className="text-sm leading-relaxed text-muted-foreground">After the run, we found that exact labels such as <code className="rounded bg-muted px-1">USD millions</code> were rejected where the gate expected <code className="rounded bg-muted px-1">USD</code>. The sensitivity analysis below accepts a small, independently reviewed set of unambiguous aliases. It preserves every number and response. This parser change was made <strong className="text-foreground">after observing outputs</strong>; the original contract results are retained below.</p>
          <p className="text-sm leading-relaxed text-muted-foreground">The pre-registered exact contract accepted <strong className="text-foreground">0/50 answers for each model</strong>. The post-hoc counts below are a separate diagnostic; small accepted samples do not establish general precision.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {benchmark.runs.map(run => {
            const score = run.sensitivity_score;
            return <article key={run.target} className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-2xs">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="font-serif text-xl font-semibold">{run.label}</h3>
                  <p className="mt-1 text-xs text-muted-foreground">{run.hosting} · {run.calls}/{run.planned} calls recorded</p>
                </div>
                <span className="rounded-md border border-border bg-muted/60 px-2 py-0.5 font-mono text-[0.7rem] text-muted-foreground">
                  {run.gpu_usage_separate ? "Modal GPU · Open Weights" : "Meta Hosted API"}
                </span>
              </div>
              <p className="mt-2 text-xs font-medium text-muted-foreground">Post-hoc sensitivity · not the original contract</p>
              {score ? <>
                <dl className="mt-5 grid grid-cols-2 gap-4 border-b border-border pb-4">
                  <div><dt className="text-xs text-muted-foreground">Raw answers correct</dt><dd className="mt-1 font-mono text-2xl font-semibold tabular-nums">{score.correct}<span className="text-sm font-normal text-muted-foreground"> / {score.planned}</span></dd></div>
                  <div><dt className="text-xs text-muted-foreground">Accepted answers correct</dt><dd className="mt-1 font-mono text-2xl font-semibold tabular-nums">{score.verified_accepted ? <>{score.verified_correct}<span className="text-sm font-normal text-muted-foreground"> / {score.verified_accepted}</span></> : "N/A"}</dd><p className="text-xs text-muted-foreground">{score.verified_accepted ? `${percent(score.verified_precision)} precision in this small sample` : "0 accepted answers · precision N/A"}</p></div>
                  <div><dt className="text-xs text-muted-foreground">Verified-answer coverage</dt><dd className="mt-1 font-mono font-medium tabular-nums">{score.verified_accepted}/{score.planned} · {percent(score.verified_coverage)}</dd></div>
                  <div><dt className="text-xs text-muted-foreground">Incorrect answers passed</dt><dd className="mt-1"><span className={score.unsafe_passed === 0 ? " inline-flex rounded bg-ok-tint px-2 py-0.5 font-mono text-sm font-semibold text-ok tabular-nums" : "font-mono font-medium tabular-nums text-bad"}>{score.unsafe_passed}</span></dd></div>
                </dl>
                <div className="mt-3 flex h-2 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
                  <div className="bg-ok" style={{ width: `${(score.correct / score.planned) * 100}%` }} title={`${score.correct} correct`} />
                  <div className="bg-bad" style={{ width: `${(score.wrong / score.planned) * 100}%` }} title={`${score.wrong} wrong`} />
                  <div className="bg-approx" style={{ width: `${(score.abstain / score.planned) * 100}%` }} title={`${score.abstain} abstained`} />
                  <div className="bg-unk" style={{ width: `${((score.unassessable + score.format_error + score.api_error) / score.planned) * 100}%` }} title={`${score.unassessable + score.format_error + score.api_error} unassessable or error`} />
                </div>
                <ul className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs text-muted-foreground" aria-label={`${run.label} raw answer outcomes`}>
                  <li className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-ok" aria-hidden />{score.correct} correct</li>
                  <li className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-bad" aria-hidden />{score.wrong} wrong</li>
                  <li className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-approx" aria-hidden />{score.abstain} model abstentions</li>
                  <li className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-unk" aria-hidden />{score.unassessable} metadata unassessable</li>
                  <li>{score.format_error} format failures</li>
                  <li>{score.api_error} API failures · {score.not_run} not run</li>
                </ul>
              </> : <p className="mt-6 rounded-lg bg-muted/50 p-4 text-sm text-muted-foreground">{run.status === "not_run" ? "Model run pending." : "Raw responses retained; offline sensitivity analysis pending."}</p>}
              <p className="mt-4 text-xs leading-relaxed text-muted-foreground">{run.gpu_usage_separate ? "GPU-billed shared Modal session; token API cost does not measure compute charges." : run.token_api_cost_usd === null ? "Usage cost pending." : `Recorded token usage: $${run.token_api_cost_usd.toFixed(6)}. Not a provider invoice.`}{run.unknown_usage_calls ? ` ${run.unknown_usage_calls} calls have unknown usage; reservations are retained separately.` : ""}</p>
              {run.unknown_reserved_usd ? <p className="mt-1 text-xs text-muted-foreground">Unknown-usage reservations: ${run.unknown_reserved_usd.toFixed(6)}; usage plus reservations: ${run.charged_or_reserved_usd?.toFixed(6)}.</p> : null}
              {run.latency_median_s !== null ? <p className="mt-1 text-xs text-muted-foreground">Median request {run.latency_median_s.toFixed(2)} s · concurrency {run.concurrency} · {run.max_output_tokens?.toLocaleString()} output-token limit · {run.reasoning_effort} reasoning.</p> : null}
            </article>;
          })}
        </div>
        <p className="max-w-4xl text-xs leading-relaxed text-muted-foreground">Precision is N/A when no answer is accepted. Coverage always uses all 50 planned questions. Unknown metadata and format/API failures remain visible and in the overall denominator. Request timing excludes Modal deployment and warmup; different providers, API formats and reasoning settings prevent a controlled speed or cost ranking.</p>
        <p className="max-w-4xl text-xs leading-relaxed text-muted-foreground">Observed shared Modal GPU usage: ${benchmark.gpu_cost_context.observed_shared_total_usd.toFixed(6)} across three validation attempts; the successful session accounts for ${benchmark.gpu_cost_context.observed_successful_session_usd.toFixed(6)}. This includes warmup and Private browser validation as well as Glimmer Q&A, so it is not a per-model QA charge. Provider metering may lag. <a href={`${REPO}${benchmark.gpu_cost_context.source_path}`} className="underline underline-offset-4" target="_blank" rel="noopener noreferrer">GPU scope and evidence</a>.</p>
      </section>

      <section aria-labelledby="strict-results" className="space-y-3">
        <h2 id="strict-results" className="font-serif text-2xl font-semibold">Original exact-contract results</h2>
        <p className="max-w-4xl text-sm leading-relaxed text-muted-foreground">These are the pre-registered parser results, including strict metadata spellings. A contract rejection can be a harmless label variation; it must not be presented as proof that the financial value is wrong. The saved primary records are unchanged.</p>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground"><tr>{["Model", "Correct under contract", "Binding / numeric rejections", "Abstained", "Format / API failures", "Accepted"].map(heading => <th key={heading} className="px-4 py-3 font-medium">{heading}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">{benchmark.runs.map(run => <tr key={run.target}>
              <th className="px-4 py-3 font-medium">{run.label}</th>
              {run.primary_score ? <><td className="px-4 py-3">{run.primary_score.raw_correct}/50</td><td className="px-4 py-3">{run.primary_score.raw_wrong}/50</td><td className="px-4 py-3">{run.primary_score.raw_abstain}/50</td><td className="px-4 py-3">{run.primary_score.format_errors} / {run.primary_score.api_errors}</td><td className="px-4 py-3">{run.primary_score.verified_accepted}/50</td></> : <td colSpan={5} className="px-4 py-3 text-muted-foreground">Pending</td>}
            </tr>)}</tbody>
          </table>
        </div>
      </section>

      <section aria-labelledby="questions" className="space-y-4">
        <div className="max-w-4xl space-y-2"><h2 id="questions" className="font-serif text-2xl font-semibold">Inspect every question and receipt</h2>
          <p className="text-sm leading-relaxed text-muted-foreground">Open a question to compare the original model fields, gate decisions and source filings. Reference values were frozen before inference and are provisional labels derived from the public snapshot; independent human adjudication is still pending.</p></div>
        <div className="divide-y divide-border rounded-xl border border-border">{benchmark.cases.map((item, index) => <details key={item.question.id} className="group p-4 sm:p-5">
          <summary className="cursor-pointer text-sm font-medium leading-relaxed"><span className="mr-2 font-mono text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>{item.question.text}</summary>
          <div className="mt-4 space-y-4">
            <div className="rounded-lg bg-muted/50 p-3 text-sm"><p><span className="text-muted-foreground">Frozen reference:</span> <strong>{item.reference_value}</strong></p>
              <ul className="mt-2 space-y-1 text-xs">{item.sources.map(source => <li key={`${source.url}-${source.accession}`} className="break-words"><a href={source.url} className="underline underline-offset-4" target="_blank" rel="noopener noreferrer">{source.form} · {source.accession}</a><span className="text-muted-foreground"> · accepted {source.accepted_at} · {source.source}</span></li>)}</ul></div>
            <div className="grid gap-3 lg:grid-cols-2">{item.responses.map(response => <article key={response.target} className="min-w-0 rounded-lg border border-border p-3">
              <h3 className="text-sm font-semibold">{response.label}</h3>
              <p className="mt-2 break-words font-mono text-sm">{response.value_text ?? human(response.raw_outcome)}</p>
              {response.entity ? <p className="mt-1 break-words text-xs text-muted-foreground">Model fields: {response.entity} · {response.period} · FY{response.fiscal_year} · {response.unit}</p> : null}
              <p className="mt-3 text-xs">Original gate: <strong>{response.primary_accepted === null ? "not run" : response.primary_accepted ? "accepted" : "withheld"}</strong> · {human(response.primary_reason)}</p>
              <p className="mt-1 text-xs">Sensitivity: <strong>{human(response.sensitivity_outcome)}</strong>{response.sensitivity_accepted !== null ? ` · ${response.sensitivity_accepted ? "accepted" : "withheld"} · ${human(response.sensitivity_reason)}` : ""}</p>
              {Object.keys(response.normalizations).length ? <p className="mt-2 break-words text-xs text-muted-foreground">Metadata normalized: {Object.entries(response.normalizations).map(([key, value]) => `${human(key)} → ${value}`).join("; ")}. Numeric text unchanged.</p> : null}
              {response.model_output_text ? <details className="mt-3"><summary className="cursor-pointer text-xs underline underline-offset-4">Original response</summary><pre className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-all rounded bg-muted/50 p-3 text-[0.68rem] leading-relaxed">{response.model_output_text}</pre></details> : null}
            </article>)}</div>
          </div>
        </details>)}</div>
      </section>

      <section aria-labelledby="limits" className="max-w-4xl space-y-3 text-sm leading-relaxed text-muted-foreground">
        <h2 id="limits" className="font-serif text-2xl font-semibold text-foreground">Scope and limitations</h2>
        <p>Questions cover one company, one fiscal quarter and one GAAP metric. They ask for USD amounts to the nearest million, or EPS to the nearest cent. Selection uses seed {benchmark.seed}; eligible visible filing versions had to agree, and the existing verifier had to support the question before the set was frozen. This favors supported, unambiguous evidence.</p>
        <p>The gate receives the question and model answer, never the gold value. It checks identity, period, metric, basis, units and precision, then runs the existing deterministic verifier. It cannot correct a wrong model value. Independent scoring compares the retained answer with the frozen reference.</p>
        <p>This comparison uses the frozen gate from before the J2 review fixes. Later production verifier fixes do not rewrite these 200 records; the public source overlay reproduces this historical scoring version, not the latest runtime gate.</p>
        <p>This is a development diagnostic with one run per model and correlated source filings. It does not measure broad financial knowledge, human usefulness, retrieval quality, or general hallucination reduction. Abstention and withholding reduce coverage. This comparison did not use the held-out corpus.</p>
        <p className="text-xs">Evidence cutoff: {benchmark.cutoff}. Manifest SHA-256: <code className="break-all">{benchmark.manifest_sha256}</code>.</p>
        <p><a href={`${REPO}eval/qa_manifest.json`} className="underline underline-offset-4" target="_blank" rel="noopener noreferrer">Frozen manifest</a>{" · "}<a href={`${REPO}eval/qa_benchmark.py`} className="underline underline-offset-4" target="_blank" rel="noopener noreferrer">Runner and admission controls</a>{" · "}<a href={`${REPO}eval/qa_rescore.py`} className="underline underline-offset-4" target="_blank" rel="noopener noreferrer">Post-run normalization policy</a></p>
      </section>
    </main>
    <SiteFooter />
  </>;
}
