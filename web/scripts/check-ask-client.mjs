// Validate the actual Ask/whole-filing client using local SSE fixtures only.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const compiled = mkdtempSync(join(tmpdir(), "nocap-ask-client-"));
const realFetch = globalThis.fetch;
let passed = 0;
try {
  execFileSync(process.execPath, [join(root, "node_modules/typescript/bin/tsc"), "--noEmit", "false", "--incremental", "false", "--outDir", compiled, "--module", "commonjs", "--target", "es2022", "--lib", "dom,dom.iterable,es2022", "--skipLibCheck", "lib/ask.ts"], { cwd: root, stdio: "pipe" });
  const { streamAsk, exportAnswer, fetchFilings, fetchFilingIndex, fetchAskOptions, latestCoveredQuarter, AskClientError } = await import(pathToFileURL(join(compiled, "ask.js")).href);
  const question = "What was Example Corp's GAAP revenue in Q2 FY2026?";
  const sentence = "EXCO GAAP revenue for Q2 FY2026 was $12,400,000,000.";
  const note = { id: 1, accession: "0000000001-26-000001", form: "10-Q", accepted_at: "2026-08-01T00:00:00Z", url: "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/example.htm" };
  const filing = { id: `EXCO-${note.accession}`, cik: 1, ticker: "EXCO", form: note.form, accession: note.accession, accepted_at: note.accepted_at, url: note.url, sha256: "a".repeat(64), characters: 500 };
  const claim = { id: "a1", metric: "revenue", metric_text: "Revenue", value: 12400000000, value_text: "$12,400,000,000", span_text: sentence, span_start: 0, span_end: Array.from(sentence).length };
  const verdict = { claim_id: "a1", status: "verified", reason: "match", explanation: "Matches evidence.", evidence: [{ ...note }], as_of: null };
  const item = { sentence, claim, verdict, withheld: false, footnote_ids: [1] };
  const result = { kind: "numbers", question, as_of: null, model: "muse-spark-1.3", latency_s: 1.2, cost_usd: .003, cost_is_estimate: false, model_calls: 1, notice: null,
    resolved_query: { entity: "EXCO", metrics: ["revenue"], fiscal_year: 2026, fiscal_period: "Q2", basis: "GAAP", segment: null, unsupported: false },
    items: [item], answer: `${sentence} [1]`, footnotes: [note] };
  const quote = "🧾 We depend on a limited number of customers for a substantial part of revenue.";
  const quotes = { ...result, kind: "filing_quotes", filing, quotes: [{ text: quote, start: 10, end: 10 + Array.from(quote).length }], rejected_quotes: 0, offset_unit: "unicode_code_points", whole_document: true, notice: "Exact matching is provenance, not financial verification." };
  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  let submitted = [];
  function transport(body, opts = {}) {
    submitted = [];
    globalThis.fetch = async (url, init) => {
      submitted.push({ url, init });
      if (opts.abort) opts.abort.abort();
      const bytes = new TextEncoder().encode(body);
      const readable = new ReadableStream({ start(controller) {
        for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7));
        controller.close();
      } });
      return new Response(readable, { status: opts.status ?? 200, headers: { "Content-Type": opts.contentType ?? "text/event-stream" } });
    };
  }
  const run = (body = result, request = {}, opts = {}) => {
    transport(frame("result", body), opts);
    return streamAsk("numbers", { question, as_of: null, ...request }, () => {}, new AbortController().signal);
  };
  const runQuotes = (body = quotes, selected = filing, request = {}) => {
    transport(frame("result", body));
    return streamAsk("filing_quotes", { question, as_of: null, filing_id: filing.id, ...request }, () => {}, new AbortController().signal, selected);
  };
  const check = async (name, fn) => { await fn(); passed++; console.log(`✓ ${name}`); };
  const reject = (body) => assert.rejects(() => run(body), AskClientError);
  const options = JSON.parse(readFileSync(join(root, "fixtures/ask-options.json"), "utf8"));
  await check("coverage options validate the fixture and retain the health ticker list", async () => {
    transport(JSON.stringify(options), { contentType: "application/json" });
    const available = await fetchAskOptions(new AbortController().signal);
    const health = JSON.parse(readFileSync(join(root, "fixtures/health.json"), "utf8"));
    assert.deepEqual(available.companies.map((c) => c.ticker), health.companies);
    assert.equal(latestCoveredQuarter(available.companies.find((c) => c.ticker === "JPM").periods), "Q2 2026");
    assert.equal(latestCoveredQuarter(available.companies.find((c) => c.ticker === "NVDA").periods), "Q2 2027");
    assert.match(submitted[0].url, /\/ask\/options$/);
    assert.equal(submitted[0].init.cache, "no-store");
  });
  await check("latest covered quarter handles unordered periods and a fiscal-year rollover", () => {
    const periods = [{ fiscal_year: 2026, fiscal_period: "Q3" }, { fiscal_year: 2027, fiscal_period: "Q1" },
      { fiscal_year: 2026, fiscal_period: "Q4" }, { fiscal_year: 2027, fiscal_period: "Q2" },
      { fiscal_year: 2027, fiscal_period: "FY" }];
    const original = structuredClone(periods);
    assert.equal(latestCoveredQuarter(periods), "Q2 2027");
    assert.deepEqual(periods, original);
  });
  await check("annual-only and missing periods never imply quarter coverage", async () => {
    assert.equal(latestCoveredQuarter([{ fiscal_year: 2026, fiscal_period: "FY" }]), null);
    assert.equal(latestCoveredQuarter([]), null);
    transport(JSON.stringify({ ...options, companies: [{ ticker: "EXCO", name: "Example", periods: [] }] }), { contentType: "application/json" });
    assert.deepEqual((await fetchAskOptions(new AbortController().signal)).companies[0].periods, []);
  });
  await check("coverage options reject malformed fiscal labels and years", async () => {
    for (const period of [{ fiscal_year: 2026, fiscal_period: "Q5" }, { fiscal_year: 2026, fiscal_period: "Q2?" },
      { fiscal_year: 0, fiscal_period: "Q2" }, { fiscal_year: 10000, fiscal_period: "Q2" },
      { fiscal_year: 2026.5, fiscal_period: "Q2" }, { fiscal_year: "2026", fiscal_period: "Q2" }]) {
      transport(JSON.stringify({ ...options, companies: [{ ticker: "EXCO", name: "Example", periods: [period] }] }), { contentType: "application/json" });
      await assert.rejects(() => fetchAskOptions(new AbortController().signal), AskClientError);
    }
  });
  await check("coverage disclosure renders compact quarter labels and stays outside the draft description", () => {
    const ts = require("typescript"), React = require("react");
    const { renderToStaticMarkup } = require("react-dom/server");
    const source = readFileSync(join(root, "components/receipts-app.tsx"), "utf8");
    const ast = ts.createSourceFile("receipts-app.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let disclosure;
    function visit(node) {
      if (ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === "details" && node.getText(ast).includes("Covered tickers")) disclosure = node.getText(ast);
      ts.forEachChild(node, visit);
    }
    visit(ast); assert.ok(disclosure);
    const code = ts.transpileModule(`export function Coverage({companies, coveredQuarters}) { return (${disclosure}); }`, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const loaded = { exports: {} };
    new Function("require", "module", "exports", code)(require, loaded, loaded.exports);
    const companies = options.companies.map((c) => c.ticker);
    const coveredQuarters = Object.fromEntries(options.companies.map((c) => [c.ticker, latestCoveredQuarter(c.periods)]));
    const html = renderToStaticMarkup(React.createElement(loaded.exports.Coverage, { companies, coveredQuarters }));
    assert.equal((html.match(/<li>/g) ?? []).length, companies.length);
    assert.match(html, /JPM · <span[^>]*>Q2 2026<\/span>/);
    assert.match(html, /FDXF · <span[^>]*>quarter unavailable<\/span>/);
    assert.doesNotMatch(html.match(/<details[^>]*>/)[0], /\bopen=/);
    assert.match(html, /role="group" aria-label="Covered tickers and latest fiscal quarters" tabindex="0"/);
    assert.match(html, /max-h-32 overflow-y-auto/);
    assert.match(html, /grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-3/);
    assert.match(source, /aria-describedby="draft-coverage"/);
    assert.match(source, /<\/p>\s*\{companies\?\.length \? \(\s*<details/);
    const unavailable = renderToStaticMarkup(React.createElement(loaded.exports.Coverage, { companies, coveredQuarters: {} }));
    assert.equal((unavailable.match(/quarter unavailable/g) ?? []).length, companies.length);
  });
  await check("mock coverage route returns the fixture only when mock mode is enabled", async () => {
    const ts = require("typescript");
    const code = ts.transpileModule(readFileSync(join(root, "app/mock-api/ask/options/route.ts"), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText;
    const route = { exports: {} }; let enabled = false;
    new Function("require", "module", "exports", code)((name) => {
      assert.equal(name, "@/lib/mock");
      return { mockEnabled: () => enabled, mockAskOptions: options };
    }, route, route.exports);
    assert.equal((await route.exports.GET()).status, 404);
    enabled = true;
    assert.deepEqual(await (await route.exports.GET()).json(), options);
  });
  await check("numeric request sends only question and cutoff", async () => {
    const next = await run(result, { question: ` ${question} `, model: "attacker", url: "https://attacker.test" });
    assert.equal(next.answer, `${sentence} [1]`); assert.match(submitted[0].url, /\/ask$/);
    assert.equal(submitted[0].init.cache, "no-store");
    assert.deepEqual(JSON.parse(submitted[0].init.body), { question, as_of: null });
  });
  await check("chunked Unicode SSE and valid progress produce one result", async () => {
    const stages = []; transport(": keep-alive\n\n" + frame("progress", { stage: "planning" }) + frame("progress", { stage: "verifying" }) + frame("result", result));
    const next = await streamAsk("numbers", { question, as_of: null }, (s) => stages.push(s), new AbortController().signal);
    assert.equal(next.kind, "numbers"); assert.deepEqual(stages, ["planning", "verifying"]);
  });
  await check("answer must reconstruct exactly from accepted sentences and citations", async () => {
    await reject({ ...result, answer: result.answer + " Unsupported conclusion." });
    await reject({ ...result, answer: `${sentence} [2]` });
  });
  await check("wrong or evidence-free statements cannot enter the accepted answer", async () => {
    await reject({ ...result, items: [{ ...item, verdict: { ...verdict, status: "wrong" } }] });
    await reject({ ...result, items: [{ ...item, verdict: { ...verdict, evidence: [] } }] });
  });
  await check("withheld sentences stay visible but do not enter export", async () => {
    const next = await run({ ...result, items: [{ ...item, withheld: true, footnote_ids: [], verdict: { ...verdict, status: "unverifiable", evidence: [] } }], footnotes: [], answer: "", notice: "No sentence was accepted." });
    const exported = exportAnswer(next); assert.match(exported, /No answer was accepted/); assert.ok(!exported.includes(sentence));
  });
  await check("mismatched question and historical cutoff are rejected", async () => {
    await reject({ ...result, question: "Different question" });
    await reject({ ...result, as_of: "2026-01-01T00:00:00Z" });
    await reject({ ...result, items: [{ ...item, verdict: { ...verdict, as_of: "2026-01-01T00:00:00Z" } }] });
  });
  await check("citations must match actual evidence and safe SEC URLs", async () => {
    await reject({ ...result, footnotes: [{ ...note, url: "https://attacker.test/" }] });
    await reject({ ...result, footnotes: [{ ...note, accession: "0000000001-26-000002" }] });
    await reject({ ...result, footnotes: [note, { ...note, id: 2 }] });
  });
  await check("accepted evidence cannot come from after cutoff", async () => {
    const cutoff = "2026-07-01T00:00:00Z";
    await assert.rejects(() => run({ ...result, as_of: cutoff, items: [{ ...item, verdict: { ...verdict, as_of: cutoff } }] }, { as_of: cutoff }), AskClientError);
  });
  await check("numeric export preserves exact answer, resolved query and accession", async () => {
    const text = exportAnswer(await run());
    assert.ok(text.includes(result.answer)); assert.ok(text.includes(note.url)); assert.ok(text.includes(note.accession));
    assert.match(text, /Resolved lookup: EXCO · Q2 · FY2026 · GAAP; revenue/);
  });
  await check("filing request submits a server-listed ID and exact Unicode quote", async () => {
    const next = await runQuotes(); assert.equal(next.quotes[0].text, quote);
    assert.match(submitted[0].url, /\/grounding$/);
    assert.deepEqual(JSON.parse(submitted[0].init.body), { question, as_of: null, filing_id: filing.id });
  });
  await check("changed filing identity, hash or source cannot pass", async () => {
    for (const changed of [{ sha256: "b".repeat(64) }, { characters: 499 }, { accession: "0000000001-26-000002" }, { url: "https://attacker.test" }]) {
      await assert.rejects(() => runQuotes({ ...quotes, filing: { ...filing, ...changed } }), AskClientError);
    }
  });
  await check("UTF-16 offsets, repeated quotes and truncated-document claims are rejected", async () => {
    await assert.rejects(() => runQuotes({ ...quotes, quotes: [{ text: quote, start: 10, end: 10 + quote.length }] }), AskClientError);
    await assert.rejects(() => runQuotes({ ...quotes, quotes: [quotes.quotes[0], quotes.quotes[0]] }), AskClientError);
    await assert.rejects(() => runQuotes({ ...quotes, whole_document: false }), AskClientError);
  });
  await check("quote export retains text and does not imply financial verification", async () => {
    const text = exportAnswer(await runQuotes({ ...quotes, notice: "The selected filing includes forward-looking statements." })); assert.ok(text.includes(quote)); assert.ok(text.includes(note.accession));
    assert.match(text, /not factual verification/); assert.match(text, /Unicode code points/);
    assert.match(text, /Note: The selected filing includes forward-looking statements\./);
  });
  await check("error preserves usage rather than showing free failure", async () => {
    transport(frame("error", { message: "Provider unavailable", cost_usd: .02, cost_is_estimate: true, model_calls: 1 }));
    await assert.rejects(() => streamAsk("numbers", { question, as_of: null }, () => {}, new AbortController().signal), (e) => e.accounting.cost_usd === .02 && e.accounting.model_calls === 1);
  });
  await check("an admission refusal keeps its reason, so the recorded sample can be offered", async () => {
    const refused = { message: "Today's live budget for new questions is used up.", retryable: false, cost_usd: 0, cost_is_estimate: false, model_calls: 0 };
    for (const reason of ["daily_budget", "live_paused", "filing_question_limit"]) {
      transport(frame("error", { ...refused, reason }));
      await assert.rejects(() => streamAsk("numbers", { question, as_of: null }, () => {}, new AbortController().signal),
        (e) => e instanceof AskClientError && e.reason === reason && e.accounting.model_calls === 0);
    }
    transport(frame("error", { ...refused, reason: "anything_else" }));
    await assert.rejects(() => streamAsk("numbers", { question, as_of: null }, () => {}, new AbortController().signal),
      (e) => e instanceof AskClientError && e.reason === undefined);
  });
  await check("incomplete stream and malformed cost tuple cannot appear complete", async () => {
    transport(frame("progress", { stage: "planning" }));
    await assert.rejects(() => streamAsk("numbers", { question, as_of: null }, () => {}, new AbortController().signal), AskClientError);
    await reject({ ...result, cost_is_estimate: undefined });
  });
  await check("cancelled responses are not delivered as results", async () => {
    const ac = new AbortController(); transport(frame("result", result), { abort: ac });
    await assert.rejects(() => streamAsk("numbers", { question, as_of: null }, () => {}, ac.signal));
  });
  await check("filing list validates SEC accession-to-URL linkage", async () => {
    transport(JSON.stringify({ filings: [filing] }), { contentType: "application/json" });
    assert.equal((await fetchFilings(new AbortController().signal)).length, 1);
    transport(JSON.stringify({ filings: [{ ...filing, cik: 999 }] }), { contentType: "application/json" });
    await assert.rejects(() => fetchFilings(new AbortController().signal), AskClientError);
  });
  await check("recorded quote replays and provider token usage are accepted only when well-formed", async () => {
    const replay = await runQuotes({ ...quotes, cached: true, recorded_utc: "2026-10-30T07:23:11Z", usage: { input_tokens: 101135, output_tokens: 509, cached_tokens: 0 } });
    assert.equal(replay.cached, true); assert.equal(replay.usage.input_tokens, 101135);
    assert.equal((await runQuotes({ ...quotes, usage: null })).usage, null);
    for (const changed of [{ usage: { input_tokens: -1, output_tokens: 1 } }, { usage: { input_tokens: 1.5, output_tokens: 1 } }, { cached: "yes" }, { recorded_utc: "yesterday" }]) {
      await assert.rejects(() => runQuotes({ ...quotes, ...changed }), AskClientError);
    }
  });
  await check("recorded sample questions must name a listed filing", async () => {
    const sample = { filing_id: filing.id, question: "What does the filing say about customers?", as_of: null, recorded_utc: "2026-10-30T07:23:11Z" };
    transport(JSON.stringify({ filings: [filing], samples: [sample, { ...sample, filing_id: "OTHER-1" }, { ...sample, question: "  " }, { ...sample, recorded_utc: "soon" }] }), { contentType: "application/json" });
    assert.deepEqual((await fetchFilingIndex(new AbortController().signal)).samples, [sample]);
    transport(JSON.stringify({ filings: [filing] }), { contentType: "application/json" });
    assert.deepEqual((await fetchFilingIndex(new AbortController().signal)).samples, []);
  });
  console.log(`${passed} Ask client checks passed.`);
} finally { globalThis.fetch = realFetch; rmSync(compiled, { recursive: true, force: true }); }
