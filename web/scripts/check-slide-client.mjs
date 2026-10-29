// Offline checks for image preflight and the actual slide SSE client. No model or server calls.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "receipts-slide-client-"));
const realFetch = globalThis.fetch;
let passed = 0;
try {
  execFileSync(process.execPath, [join(root, "node_modules/typescript/bin/tsc"), "--noEmit", "false", "--incremental", "false", "--outDir", compiled, "--module", "commonjs", "--target", "es2020", "--lib", "dom,dom.iterable,es2020", "--skipLibCheck", "lib/slides.ts", "lib/slide-image.ts"], { cwd: root, stdio: "pipe" });
  const { streamSlide } = await import(pathToFileURL(join(compiled, "slides.js")).href);
  const { validateSlideImage, slideDataUrl } = await import(pathToFileURL(join(compiled, "slide-image.js")).href);
  const meta = { models: ["sam-3.1", "muse-spark-1.3"], image: { width: 1000, height: 600 }, cached: false, as_of: null };
  const region = { id: "r1", label: "Revenue", box: [0, 0, 1000, 1000], source: "sam" };
  const span = "🧾 NVIDIA revenue $96.2 billion.";
  const claim = { id: "c1", metric: "revenue", metric_text: "Revenue", value_text: "$96.2 billion", value: 96.2, unit: "USD", scale: "billions", period_text: "Q2 FY2027", period_type: "quarter", comparison: "none", basis: "GAAP", forward_looking: false, entity: "NVIDIA", span_text: span, span_start: 0, span_end: Array.from(span).length, region_id: "r1", box: [100, 100, 200, 200], reading: "printed" };
  const fact = { cik: 1045810, ticker: "NVDA", metric: "revenue", value: 96221000000, unit: "USD", basis: "GAAP", period_start: "2026-04-27", period_end: "2026-07-26", source: "xbrl", form: "10-Q", accession: "0001045810-26-000000", accepted_at: "2026-08-26T20:21:19Z", url: "https://www.sec.gov/Archives/edgar/data/1045810/example.htm" };
  const verdict = { claim_id: "c1", status: "verified", reason: "match", explanation: "Matches the filing.", evidence: [fact], as_of: null };
  const done = { counts: { verified: 1, wrong: 0, unverifiable: 0, approximate: 0 }, claims: 1, model_calls: 3, latency_s: 1, cost_usd: 0.01, cost_is_estimate: false, warnings: ["Confirm the reading against the image."] };
  const request = { image_data_url: "data:image/png;base64,AAAA", context: "NVIDIA Q2 FY2027", as_of: null, verdict: "must-not-send" };
  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const initial = frame("meta", meta) + frame("region", region);
  const full = initial + frame("claim", claim) + frame("verdict", verdict) + frame("done", done);
  let submitted;
  async function run(body, options = {}) {
    const ac = new AbortController();
    const seen = [];
    globalThis.fetch = async (url, init) => {
      submitted = { url, body: JSON.parse(init.body) };
      if (options.networkError) throw new Error("offline");
      if (options.abortInFetch) ac.abort();
      const bytes = new TextEncoder().encode(body);
      const stream = new ReadableStream({ start(controller) {
        for (let i = 0; i < bytes.length; i += 5) controller.enqueue(bytes.slice(i, i + 5));
        controller.close();
      } });
      return new Response(stream, { status: options.status ?? 200, headers: { "Content-Type": options.contentType ?? "text/event-stream" } });
    };
    const receive = (event) => (data) => { seen.push([event, data]); if (options.abortAt === event) ac.abort(); };
    await streamSlide({ ...request, ...options.request }, { onMeta: receive("meta"), onProgress: receive("progress"), onRegion: receive("region"), onClaim: receive("claim"), onVerdict: receive("verdict"), onDone: receive("done"), onError: receive("error") }, ac.signal);
    return seen;
  }
  async function check(name, fn) { await fn(); passed++; console.log(`✓ ${name}`); }
  const rejects = async (body) => {
    const seen = await run(body);
    assert.equal(seen.at(-1)[0], "error");
    assert.equal(seen.filter(([event]) => event === "done").length, 0);
    return seen;
  };
  await check("submits only the image, context and time cutoff", async () => {
    await run(full);
    assert.match(submitted.url, /\/slide$/);
    assert.deepEqual(submitted.body, { image_data_url: request.image_data_url, context: request.context, as_of: null });
  });
  await check("chunked Unicode and keep-alives preserve the completed reading", async () => {
    const seen = await run(": keep-alive\n\n" + full);
    assert.deepEqual(seen.map(([event]) => event), ["meta", "region", "claim", "verdict", "done"]);
    assert.equal(seen[2][1].span_text, span);
  });
  await check("progress and an empty slide complete honestly", async () => {
    const seen = await run(frame("meta", meta) + frame("progress", { stage: "reading" }) + frame("done", { ...done, claims: 0, counts: { verified: 0, wrong: 0, unverifiable: 0, approximate: 0 } }));
    assert.deepEqual(seen.map(([event]) => event), ["meta", "progress", "done"]);
  });
  await check("accepts an approximate axis reading", async () => {
    const seen = await run(initial + frame("claim", { ...claim, reading: "axis_estimate" }) + frame("verdict", { ...verdict, status: "approximate", reason: "approximate" }) + frame("done", { ...done, counts: { verified: 0, wrong: 0, unverifiable: 0, approximate: 1 } }));
    assert.equal(seen.at(-1)[0], "done");
  });
  for (const status of ["verified", "wrong"]) await check(`an axis estimate cannot earn ${status}`, () => rejects(initial + frame("claim", { ...claim, reading: "axis_estimate" }) + frame("verdict", { ...verdict, status })));
  await check("out-of-range and reversed boxes cannot become overlays", async () => {
    for (const box of [[-1, 0, 50, 50], [0, 0, 1001, 50], [100, 100, 50, 50], [0, 0, 0, 10]]) await rejects(initial + frame("claim", { ...claim, box }));
  });
  await check("rejects orphan claims and verdicts", async () => {
    await rejects(initial + frame("claim", { ...claim, region_id: "missing" }));
    await rejects(initial + frame("verdict", verdict));
  });
  await check("rejects duplicate region, claim and verdict identifiers", async () => {
    await rejects(initial + frame("region", region));
    await rejects(initial + frame("claim", claim) + frame("claim", claim));
    await rejects(initial + frame("claim", claim) + frame("verdict", verdict) + frame("verdict", verdict));
  });
  await check("does not expose an unsafe filing link as evidence", () => rejects(initial + frame("claim", claim) + frame("verdict", { ...verdict, evidence: [{ ...fact, url: "javascript:alert(1)" }] })));
  await check("a missing verdict cannot be marked complete", () => rejects(initial + frame("claim", claim) + frame("done", done)));
  await check("a mismatched summary cannot be marked complete", () => rejects(initial + frame("claim", claim) + frame("verdict", verdict) + frame("done", { ...done, counts: { ...done.counts, verified: 2 } })));
  await check("a response for a different cutoff is rejected", async () => assert.equal((await run(full, { request: { as_of: "2026-01-01T00:00:00Z" } })).at(-1)[0], "error"));
  await check("EOF and malformed JSON report an incomplete check", async () => { await rejects(initial); await rejects(initial + "event: claim\ndata: {bad}\n\n"); });
  await check("error is terminal even if later events look successful", async () => {
    const seen = await run(frame("error", { message: "budget reached", retryable: false }) + full);
    assert.deepEqual(seen, [["error", { message: "budget reached", retryable: false }]]);
  });
  await check("abort during fetch suppresses stale events", async () => assert.deepEqual(await run(full, { abortInFetch: true }), []));
  await check("abort after a region suppresses queued claims", async () => assert.deepEqual((await run(full, { abortAt: "region" })).map(([event]) => event), ["meta", "region"]));
  await check("HTTP, content-type and network failures produce one terminal error", async () => {
    for (const options of [{ status: 503 }, { contentType: "application/json" }, { networkError: true }]) assert.deepEqual((await run(full, options)).map(([event]) => event), ["error"]);
  });
  // Headers are synthetic: these checks validate preflight metadata, not full pixel decoding.
  function png(width, height, animated = false) {
    const bytes = new Uint8Array(animated ? 45 : 33);
    bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
    const view = new DataView(bytes.buffer);
    view.setUint32(8, 13); view.setUint32(12, 0x49484452); view.setUint32(16, width); view.setUint32(20, height);
    if (animated) { view.setUint32(33, 0); view.setUint32(37, 0x6163544c); }
    return bytes;
  }
  await check("PNG and JPEG dimensions are read before pixel decoding", () => {
    assert.deepEqual(validateSlideImage(png(1000, 600), "image/png"), { width: 1000, height: 600 });
    assert.deepEqual(validateSlideImage(new Uint8Array([0xff, 0xd8, 0xff, 0xc2, 0, 8, 8, 2, 88, 3, 232, 0]), "image/jpeg"), { width: 1000, height: 600 });
  });
  await check("oversized dimensions, pixels, bytes and tiny crops are rejected", () => {
    for (const [width, height] of [[4097, 100], [4096, 2048], [31, 600]]) assert.throws(() => validateSlideImage(png(width, height), "image/png"));
    assert.throws(() => validateSlideImage(new Uint8Array(2 * 1024 * 1024 + 1), "image/png"));
  });
  await check("animated PNG, MIME mismatch and unknown formats are rejected", () => {
    assert.throws(() => validateSlideImage(png(1000, 600, true), "image/png"));
    assert.throws(() => validateSlideImage(png(1000, 600), "image/jpeg"));
    assert.throws(() => validateSlideImage(new TextEncoder().encode("<svg></svg>"), "image/svg+xml"));
  });
  await check("data URL conversion preserves every image byte", () => {
    const bytes = new Uint8Array(20_001).map((_, i) => i % 256);
    assert.deepEqual(new Uint8Array(Buffer.from(slideDataUrl(bytes, "image/png").split(",")[1], "base64")), bytes);
  });
  console.log(`\n${passed} slide client checks passed; no network or model calls.`);
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  console.error(error);
  process.exitCode = 1;
} finally {
  globalThis.fetch = realFetch;
  rmSync(compiled, { recursive: true, force: true });
}
