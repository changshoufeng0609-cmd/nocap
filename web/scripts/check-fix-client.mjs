// Dependency-free regression checks for the real SSE client. No server, browser or model calls.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "receipts-fix-client-"));
const realFetch = globalThis.fetch;
let passed = 0;

try {
  execFileSync(process.execPath, [
    join(root, "node_modules/typescript/bin/tsc"),
    "--noEmit", "false", "--incremental", "false", "--outDir", compiled,
    "--module", "commonjs", "--target", "es2020", "--lib", "dom,dom.iterable,es2020",
    "--skipLibCheck", "lib/api.ts", "lib/types.ts", "lib/sse.ts",
  ], { cwd: root, stdio: "pipe" });
  const { streamFix } = await import(pathToFileURL(join(compiled, "api.js")).href);

  // An astral symbol before the edited number catches accidental UTF-16 offset handling.
  const original = "🧾 EPS $2.22 and revenue $9B.";
  const start = Array.from(original).indexOf("$");
  const result = {
    original_text: original,
    rewritten_text: original.replace("$2.22", "$2.46"),
    status: "corrected",
    changes: [{ claim_id: "c1", before: "$2.22", after: "$2.46", start, end: start + 5, explanation: "Matches filing", evidence: [] }],
    remaining: [], results: [],
    counts: { verified: 1, wrong: 0, unverifiable: 1, approximate: 0 },
    cost_usd: 0.01, latency_s: 1, model: "muse-wonder-1.0", extraction_model: "muse-spark-1.3",
  };
  const request = { text: original, as_of: null, context: "Company Q1", example_id: "must-not-send" };
  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  let submitted;

  async function run(body, options = {}) {
    const ac = new AbortController();
    const seen = [];
    globalThis.fetch = async (url, init) => {
      submitted = { url, body: JSON.parse(init.body) };
      if (options.networkError) throw new Error("offline");
      if (options.abortInFetch) ac.abort();
      const bytes = new TextEncoder().encode(body);
      const stream = new ReadableStream({
        start(controller) {
          // Split JSON frames and multibyte code points across network chunks.
          for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7));
          controller.close();
        },
      });
      return new Response(stream, {
        status: options.status ?? 200,
        headers: { "Content-Type": options.contentType ?? "text/event-stream" },
      });
    };
    await streamFix(request, {
      onProgress: (stage) => { seen.push(["progress", stage]); if (options.abortProgress) ac.abort(); },
      onResult: (data) => seen.push(["result", data]),
      onError: (error) => seen.push(["error", error]),
    }, ac.signal);
    return seen;
  }

  async function check(name, fn) {
    await fn();
    passed += 1;
    console.log(`✓ ${name}`);
  }

  await check("request allows only draft, cutoff and context", async () => {
    await run(frame("result", result));
    assert.match(submitted.url, /\/fix$/);
    assert.deepEqual(submitted.body, { text: original, as_of: null, context: "Company Q1" });
  });
  await check("chunked Unicode SSE preserves progress and the verified text", async () => {
    const seen = await run(frame("progress", { stage: "checking" }) + frame("progress", { stage: "verifying" }) + frame("result", result));
    assert.deepEqual(seen.map(([event]) => event), ["progress", "progress", "result"]);
    assert.equal(seen[2][1].rewritten_text, result.rewritten_text);
  });
  await check("rejects text that the supplied edits cannot reproduce", async () => {
    assert.equal((await run(frame("result", { ...result, rewritten_text: "invented text" })))[0][0], "error");
  });
  await check("rejects a before value at the wrong Unicode offset", async () => {
    assert.equal((await run(frame("result", { ...result, changes: [{ ...result.changes[0], start: start + 1 }] })))[0][0], "error");
  });
  await check("rejects overlapping edits", async () => {
    assert.equal((await run(frame("result", { ...result, changes: [...result.changes, ...result.changes] })))[0][0], "error");
  });
  await check("rejects a result for another draft", async () => {
    assert.equal((await run(frame("result", { ...result, original_text: "wrong draft" })))[0][0], "error");
  });
  await check("normalizes valid edits to document order", async () => {
    const secondStart = Array.from(original).lastIndexOf("$");
    const second = { claim_id: "c2", before: "$9B", after: "$10B", start: secondStart, end: secondStart + 3, explanation: "Matches filing", evidence: [] };
    const revised = { ...result, changes: [second, ...result.changes], rewritten_text: result.rewritten_text.replace("$9B", "$10B") };
    const seen = await run(frame("result", revised));
    assert.equal(seen[0][0], "result");
    assert.deepEqual(seen[0][1].changes.map((change) => change.claim_id), ["c1", "c2"]);
  });
  await check("EOF without a result is an error", async () => {
    assert.equal((await run(""))[0][0], "error");
  });
  await check("rejects an unexpected content type", async () => {
    assert.equal((await run("{}", { contentType: "application/json" }))[0][0], "error");
  });
  await check("surfaces HTTP failure without accepting a result", async () => {
    const seen = await run(JSON.stringify({ message: "Service unavailable" }), { status: 503, contentType: "application/json" });
    assert.equal(seen[0][0], "error");
    assert.equal(seen[0][1].retryable, true);
  });
  await check("network failure has a retryable terminal error", async () => {
    const seen = await run("", { networkError: true });
    assert.equal(seen[0][0], "error");
    assert.equal(seen[0][1].retryable, true);
  });
  await check("abort while awaiting headers suppresses stale callbacks", async () => {
    assert.equal((await run(frame("result", result), { abortInFetch: true })).length, 0);
  });
  await check("abort during streaming suppresses a queued result", async () => {
    const seen = await run(frame("progress", { stage: "checking" }) + frame("result", result), { abortProgress: true });
    assert.deepEqual(seen.map(([event]) => event), ["progress"]);
  });
  await check("server error is terminal even when a result follows", async () => {
    const seen = await run(frame("error", { message: "budget reached", retryable: false }) + frame("result", result));
    assert.deepEqual(seen, [["error", { message: "budget reached", retryable: false }]]);
  });

  console.log(`\n${passed} Fix client checks passed; no network or model calls.`);
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  console.error(error);
  process.exitCode = 1;
} finally {
  globalThis.fetch = realFetch;
  rmSync(compiled, { recursive: true, force: true });
}
