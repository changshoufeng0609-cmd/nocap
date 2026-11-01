// Private routing and complete-result checks; only in-memory fetch responses.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "nocap-private-client-"));
const realFetch = globalThis.fetch;
let passed = 0;
try {
  execFileSync(process.execPath, [join(root, "node_modules/typescript/bin/tsc"), "--noEmit", "false", "--incremental", "false", "--outDir", compiled, "--module", "commonjs", "--target", "es2020", "--lib", "dom,dom.iterable,es2020", "--skipLibCheck", "lib/private.ts"], { cwd: root, stdio: "pipe" });
  const { runPrivateCheck, fetchPrivateStatus, PrivateCheckError } = await import(pathToFileURL(join(compiled, "private.js")).href);
  const span = "🧾 NVIDIA revenue $96.2 billion.";
  const meta = { private: true, model: "muse-glimmer", backend: "modal", cached: false, as_of: null };
  const claim = { id: "c1", metric: "revenue", metric_text: "Revenue", value: 96.2, value_text: "$96.2 billion", entity: "NVIDIA", span_text: span, span_start: 0, span_end: Array.from(span).length };
  const verdict = { claim_id: "c1", status: "verified", reason: "match", explanation: "Matches SEC evidence.", evidence: [{ url: "https://www.sec.gov/Archives/edgar/data/1/example.htm", form: "10-Q", accession: "0000000001-26-000001", accepted_at: "2026-01-01T00:00:00Z" }] };
  const done = { private: true, model: "muse-glimmer", counts: { verified: 1, wrong: 0, unverifiable: 0, approximate: 0 }, latency_s: 1, cost_usd: .02, cost_is_estimate: true, model_calls: 1 };
  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const frames = (m = meta, c = claim, v = verdict, d = done) => frame("meta", m) + frame("claim", { claim: c }) + frame("verdict", { claim_id: v.claim_id, verdict: v }) + frame("done", d);
  let calls = [];
  function transport(body, options = {}) {
    calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push({ url, init });
      if (options.error) throw new Error("network unavailable");
      return new Response(body, { status: options.status ?? 200, headers: { "Content-Type": options.contentType ?? "text/event-stream" } });
    };
  }
  const req = { text: span, context: null, as_of: null, backend: "meta", base_url: "https://attacker.test", verdicts: [] };
  const run = () => runPrivateCheck(req, new AbortController().signal);
  const check = async (name, fn) => { await fn(); passed++; console.log(`✓ ${name}`); };
  await check("draft goes only to private endpoint with approved fields", async () => {
    transport(frames()); const result = await run();
    assert.equal(result.claims.length, 1); assert.equal(result.done.model, "muse-glimmer");
    assert.equal(calls.length, 1); assert.match(calls[0].url, /\/private\/check$/);
    assert.equal(calls[0].init.cache, "no-store");
    assert.deepEqual(JSON.parse(calls[0].init.body), { text: span, context: null, as_of: null });
  });
  await check("public backend metadata cannot be presented as private", async () => {
    for (const changed of [{ private: false }, { backend: "meta" }, { model: "muse-spark-1.3" }, { cached: true },
      { cached: true, recorded_utc: "2026-10-30T18:17:48Z" }]) {
      transport(frames({ ...meta, ...changed })); await assert.rejects(run, PrivateCheckError);
    }
  });
  await check("a recorded replay is accepted only for the bundled sample and keeps its recording time", async () => {
    const recordedMeta = { ...meta, cached: true, recorded_utc: "2026-10-30T18:17:48Z" };
    const replay = (sampleText) => runPrivateCheck(req, new AbortController().signal, { recordedSampleText: sampleText });
    transport(frames(recordedMeta)); const result = await replay(span);
    assert.deepEqual(result.recording, { recorded_utc: "2026-10-30T18:17:48Z" }); assert.equal(result.claims.length, 1);
    transport(frames()); assert.equal((await replay(span)).recording, null);  // a live run of the same text is not "recorded"
    transport(frames(recordedMeta)); await assert.rejects(() => replay(`${span} `), PrivateCheckError);  // any other draft
    for (const bad of [undefined, null, "yesterday"]) {
      transport(frames({ ...meta, cached: true, recorded_utc: bad })); await assert.rejects(() => replay(span), PrivateCheckError);
    }
  });
  await check("failed request never falls back to public check", async () => {
    transport("", { error: true }); await assert.rejects(run); assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/private\/check$/);
  });
  await check("partial streams and wrong terminal counts are rejected", async () => {
    transport(frame("meta", meta) + frame("claim", { claim })); await assert.rejects(run, PrivateCheckError);
    transport(frames(meta, claim, verdict, { ...done, counts: { ...done.counts, verified: 2 } })); await assert.rejects(run, PrivateCheckError);
  });
  await check("a look-ahead result counts as not_public, never wrong; older counts keep status buckets", async () => {
    const ahead = { ...verdict, status: "wrong", reason: "look_ahead", explanation: "Filed after the cutoff." };
    const current = { verified: 0, wrong: 0, not_public: 1, unverifiable: 0, approximate: 0 };
    transport(frames(meta, claim, ahead, { ...done, counts: current })); assert.equal((await run()).done.counts.not_public, 1);
    transport(frames(meta, claim, ahead, { ...done, counts: { verified: 0, wrong: 1, unverifiable: 0, approximate: 0 } })); await run();
    transport(frames(meta, claim, ahead, { ...done, counts: { ...current, wrong: 1, not_public: 0 } })); await assert.rejects(run, PrivateCheckError);
  });
  await check("fabricated spans and unsafe filing links are rejected", async () => {
    transport(frames(meta, { ...claim, span_text: "invented" })); await assert.rejects(run, PrivateCheckError);
    transport(frames(meta, claim, { ...verdict, evidence: [{ ...verdict.evidence[0], url: "javascript:alert(1)" }] })); await assert.rejects(run, PrivateCheckError);
  });
  await check("an error retains the complete cost tuple", async () => {
    transport(frame("error", { message: "Private check failed", cost_usd: .02, cost_is_estimate: true, model_calls: 1 }));
    await assert.rejects(run, (e) => e.accounting.cost_usd === .02 && e.accounting.model_calls === 1);
  });
  await check("status cannot claim offline or allow response caching", async () => {
    const status = { available: true, message: "Configured", backend: "modal", model: "muse-glimmer", offline: false, response_cache: false };
    transport(JSON.stringify(status), { contentType: "application/json" });
    const parsed = await fetchPrivateStatus(new AbortController().signal);
    assert.equal(parsed.available, true); assert.deepEqual(parsed.recorded, []);  // older servers list no recordings
    transport(JSON.stringify({ ...status, offline: true }), { contentType: "application/json" });
    await assert.rejects(() => fetchPrivateStatus(new AbortController().signal));
  });
  await check("status lists the recorded samples it can replay and drops malformed entries", async () => {
    const good = { sample_id: "nvda-q2-fy2027-private", recorded_utc: "2026-10-30T18:17:48Z" };
    const status = { available: false, message: "Unavailable", backend: "modal", model: "muse-glimmer", offline: false, response_cache: false,
      recorded_samples: [good, { sample_id: 7, recorded_utc: null }, { sample_id: "x", recorded_utc: "not a time" }, "nvda", null] };
    transport(JSON.stringify(status), { contentType: "application/json" });
    assert.deepEqual((await fetchPrivateStatus(new AbortController().signal)).recorded, [good]);
  });
  await check("a mismatched historical cutoff is rejected", async () => {
    transport(frames({ ...meta, as_of: "2026-01-01T00:00:00Z" })); await assert.rejects(run, PrivateCheckError);
  });
  console.log(`${passed} private client checks passed.`);
} finally {
  globalThis.fetch = realFetch;
  rmSync(compiled, { recursive: true, force: true });
}
