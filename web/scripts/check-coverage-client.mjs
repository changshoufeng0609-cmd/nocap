// Exercise coverage validation and real chunked SSE clients without network/model calls.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "nocap-coverage-client-"));
const realFetch = globalThis.fetch;
let passed = 0;

try {
  execFileSync(process.execPath, [join(root, "node_modules/typescript/bin/tsc"),
    "--noEmit", "false", "--incremental", "false", "--outDir", compiled,
    "--module", "commonjs", "--target", "es2020", "--lib", "dom,dom.iterable,es2020",
    "--skipLibCheck", "lib/api.ts", "lib/coverage.ts", "lib/types.ts", "lib/sse.ts",
  ], { cwd: root, stdio: "pipe" });
  const { parseCoverage, coverageExcerpt } = await import(pathToFileURL(join(compiled, "coverage.js")).href);
  const { streamCheck, streamFix } = await import(pathToFileURL(join(compiled, "api.js")).href);
  const text = "🧾 Revenue $9B.\nEPS $2.22, margin 70%.";
  const chars = Array.from(text);
  const start = chars.join("").split("🧾").join("x").indexOf("70%");
  const coverage = { version: 1, candidate_count: 3, covered_count: 2, excluded_count: 0,
    uncovered: [{ start, end: start + 3, text: "70%" }] };
  const counts = { verified: 2, wrong: 0, unverifiable: 0, approximate: 0 };
  const done = { counts, latency_s: 1, cost_usd: 0, model: "muse-spark-1.3", coverage };
  const request = { text, as_of: null, context: null, example_id: null };
  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const check = async (name, fn) => { await fn(); passed++; console.log(`ok ${passed} - ${name}`); };
  async function run(invoke, body, req = request) {
    const seen = [];
    globalThis.fetch = async () => {
      const bytes = new TextEncoder().encode(body);
      return new Response(new ReadableStream({ start(controller) {
        for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7));
        controller.close();
      } }), { headers: { "Content-Type": "text/event-stream" } });
    };
    const ignore = () => {};
    await invoke(req, { onMeta: ignore, onClaim: ignore, onVerdict: ignore, onProgress: ignore,
      onDone: (data) => seen.push(["done", data]), onResult: (data) => seen.push(["result", data]),
      onError: (data) => seen.push(["error", data]) }, new AbortController().signal);
    return seen;
  }

  await check("Unicode code-point positions survive and locate the correct line", () => {
    assert.deepEqual(parseCoverage(coverage, text), coverage);
    const excerpt = coverageExcerpt(text, coverage.uncovered[0]);
    assert.equal(excerpt.line, 2);
    assert.equal(excerpt.value, "70%");
    assert.equal(excerpt.before + excerpt.value + excerpt.after, text);
  });
  await check("legacy and absent coverage remain unavailable", () => {
    for (const value of [undefined, null, false, 0, "", [], {}]) assert.equal(parseCoverage(value, text), undefined);
  });
  for (const [name, fields] of Object.entries({
    "unknown version": { version: 2 }, "negative count": { covered_count: -1 },
    "fractional count": { candidate_count: 3.5 }, "boolean count": { excluded_count: false },
    "unsafe count": { candidate_count: Number.MAX_SAFE_INTEGER + 1 }, "infinite count": { candidate_count: Infinity },
    "count mismatch": { candidate_count: 4 }, "too many covered": { covered_count: 4 },
    "missing list": { uncovered: undefined }, "non-list": { uncovered: {} },
    "excessive tokens": { excluded_count: text.length },
  })) await check(`rejects ${name}`, () => assert.equal(parseCoverage({ ...coverage, ...fields }, text), undefined));
  for (const [name, item] of Object.entries({
    "wrong text": { start, end: start + 3, text: "80%" },
    "UTF-16 offset": { start: start + 1, end: start + 4, text: "70%" },
    "negative range": { start: -1, end: 2, text: "🧾 " },
    "fractional range": { start: start + 0.1, end: start + 3, text: "70%" },
    "empty range": { start, end: start, text: "" },
    "out of bounds": { start, end: chars.length + 1, text: "70%." },
    "missing item": null,
  })) await check(`rejects ${name}`, () => assert.equal(parseCoverage({ ...coverage, uncovered: [item] }, text), undefined));
  await check("duplicate or overlapping candidates cannot inflate omissions", () => {
    assert.equal(parseCoverage({ ...coverage, candidate_count: 4, uncovered: [coverage.uncovered[0], coverage.uncovered[0]] }, text), undefined);
  });
  await check("unsorted candidates cannot point at the wrong excerpts", () => {
    const early = { start: 10, end: 13, text: "$9B" };
    assert.equal(parseCoverage({ ...coverage, candidate_count: 4, uncovered: [coverage.uncovered[0], early] }, text), undefined);
  });
  await check("zero candidates is distinct from absent metadata", () => {
    const empty = { version: 1, candidate_count: 0, covered_count: 0, excluded_count: 0, uncovered: [] };
    assert.deepEqual(parseCoverage(empty, "No figures."), empty);
  });
  await check("all-linked candidates do not become an extra verified verdict", async () => {
    const seen = await run(streamCheck, frame("done", { ...done, coverage: { ...coverage, covered_count: 3, uncovered: [] } }));
    assert.deepEqual(seen[0][1].counts, counts);
    assert.equal(seen[0][1].coverage.covered_count, 3);
  });
  await check("Check preserves validated omissions with unchanged verdict counts", async () => {
    const seen = await run(streamCheck, frame("done", done));
    assert.equal(seen[0][0], "done");
    assert.deepEqual(seen[0][1].coverage, coverage);
    assert.deepEqual(seen[0][1].counts, counts);
  });
  await check("malformed coverage is unavailable while existing verdicts remain usable", async () => {
    const seen = await run(streamCheck, frame("done", { ...done, coverage: { ...coverage, covered_count: 3 } }));
    assert.equal(seen[0][0], "done");
    assert.equal(seen[0][1].coverage, undefined);
    assert.deepEqual(seen[0][1].counts, counts);
  });
  await check("failed partial checks cannot be relabeled complete by a later coverage frame", async () => {
    const seen = await run(streamCheck, frame("error", { message: "Partial extraction", retryable: true }) + frame("done", done));
    assert.equal(seen.length, 1);
    assert.equal(seen[0][0], "error");
  });
  const valueStart = chars.indexOf("$", 14);
  const rewritten = text.replace("$2.22", "$12.22");
  const revisedCoverage = { ...coverage, uncovered: [{ start: start + 1, end: start + 4, text: "70%" }] };
  const fix = { original_text: text, rewritten_text: rewritten, status: "corrected",
    changes: [{ claim_id: "c2", before: "$2.22", after: "$12.22", start: valueStart, end: valueStart + 5, explanation: "Fixture only", evidence: [] }],
    remaining: [], results: [], counts, latency_s: 1, cost_usd: 0, model: "muse-wonder-1.0", extraction_model: "muse-spark-1.3",
    original_coverage: coverage, coverage: revisedCoverage };
  await check("Fix validates original and revised offsets against their own drafts", async () => {
    const seen = await run(streamFix, frame("result", fix));
    assert.equal(seen[0][0], "result");
    assert.deepEqual(seen[0][1].original_coverage, coverage);
    assert.deepEqual(seen[0][1].coverage, revisedCoverage);
  });
  await check("Fix never displays stale original positions as revised coverage", async () => {
    const seen = await run(streamFix, frame("result", { ...fix, coverage }));
    assert.equal(seen[0][0], "result");
    assert.equal(seen[0][1].coverage, undefined);
    assert.deepEqual(seen[0][1].original_coverage, coverage);
  });
  console.log(`\n${passed} coverage checks passed; no network or model calls.`);
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  console.error(error);
  process.exitCode = 1;
} finally {
  globalThis.fetch = realFetch;
  rmSync(compiled, { recursive: true, force: true });
}
