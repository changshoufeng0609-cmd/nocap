// Offline presentation checks: temporal flags stay distinct and displayed arithmetic is traceable.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "receipts-presentation-"));
let passed = 0;
try {
  execFileSync(process.execPath, [join(root, "node_modules/typescript/bin/tsc"), "--noEmit", "false", "--incremental", "false", "--outDir", compiled, "--module", "commonjs", "--target", "es2020", "--skipLibCheck", "lib/claims.ts", "lib/calculations.ts"], { cwd: root, stdio: "pipe" });
  const { displayCounts, displayStatus, valueIsWrong, filedValue, correction } = await import(pathToFileURL(join(compiled, "claims.js")).href);
  const { receiptCalculation } = await import(pathToFileURL(join(compiled, "calculations.js")).href);
  const { formatClaimed, formatExpectedValue, formatValue } = await import(pathToFileURL(join(compiled, "format.js")).href);
  const claim = { metric: "revenue_growth", unit: "%", comparison: "YoY", metric_text: "revenue growth", value: 20, scale: "units" };
  const current = { cik: 1, ticker: "NVDA", metric: "revenue", value: 1200000000, unit: "USD", basis: "GAAP", period_start: "2026-04-01", period_end: "2026-06-30" };
  const prior = { ...current, value: 1000000000, period_start: "2025-04-01", period_end: "2025-06-30" };
  const verdict = { status: "verified", reason: "match", evidence: [current, prior] };
  function check(name, fn) { fn(); passed++; console.log(`✓ ${name}`); }
  check("look-ahead gets its own count without changing API status", () => {
    const temporal = { ...verdict, status: "wrong", reason: "look_ahead" };
    assert.equal(displayStatus(temporal), "look_ahead");
    assert.deepEqual(displayCounts([temporal, { ...verdict, status: "wrong", reason: "value_mismatch" }, verdict]), { verified: 1, wrong: 1, look_ahead: 1, unverifiable: 0, approximate: 0 });
    assert.equal(temporal.status, "wrong");
    assert.equal(valueIsWrong(temporal), false);
    assert.equal(valueIsWrong({ ...temporal, expected_value: 19 }), true);
  });
  check("growth uses unrounded operands and exposes source indices", () => {
    const result = receiptCalculation(claim, verdict);
    assert.ok(Math.abs(result.value - 20) < 1e-9);
    assert.equal(result.equation, "($1,200,000,000 ÷ $1,000,000,000 − 1) × 100 ≈ 20%");
    assert.deepEqual(result.inputs.map((input) => input.receipt), [1, 2]);
  });
  check("quarter-over-quarter works when evidence arrives out of order", () => {
    const q1 = { ...prior, period_start: "2026-01-01", period_end: "2026-03-31" };
    assert.equal(receiptCalculation({ ...claim, comparison: "sequential" }, { ...verdict, evidence: [q1, current] }).label, "QoQ change");
  });
  check("EPS growth retains per-share units", () => {
    const evidence = [current, prior].map((f, i) => ({ ...f, metric: "eps_diluted", unit: "USD/share", value: i ? 2 : 3 }));
    const c = { ...claim, metric: "eps_diluted", metric_text: "diluted EPS growth" };
    assert.equal(receiptCalculation(c, { ...verdict, evidence }).value, 50);
    assert.equal(filedValue(c, { ...verdict, evidence }).value, 50);
  });
  for (const [name, change] of Object.entries({ "company": { cik: 2 }, "basis": { basis: "non-GAAP" }, "segment": { segment: "Data Center" }, "unit": { unit: "shares" }, "duration": { period_start: "2025-01-01" }, "zero base": { value: 0 }, "negative base": { value: -100 } })) {
    check(`does not invent growth across mismatched ${name}`, () => assert.equal(receiptCalculation(claim, { ...verdict, evidence: [current, { ...prior, ...change }] }), null));
  }
  check("conflicting versions do not produce an arbitrary formula", () => {
    assert.equal(receiptCalculation(claim, { ...verdict, evidence: [current, prior, { ...prior, value: 800000000 }] }), null);
    assert.equal(receiptCalculation(claim, { ...verdict, expected_value: 50, evidence: [current, prior, { ...prior, value: 800000000 }] }).value, 50);
  });
  check("expected percentage must match displayed arithmetic", () => assert.equal(receiptCalculation(claim, { ...verdict, expected_value: 77 }), null));
  check("gross margin supports profit and cost-of-revenue evidence", () => {
    const c = { ...claim, metric: "gross_margin", comparison: "none" };
    const gp = { ...current, metric: "gross_profit", value: 900000000 };
    const cost = { ...current, metric: "cost_of_revenue", value: 300000000 };
    assert.equal(receiptCalculation(c, { ...verdict, evidence: [gp, current] }).value, 75);
    assert.equal(receiptCalculation(c, { ...verdict, evidence: [current, cost] }).value, 75);
    assert.equal(receiptCalculation(c, { ...verdict, evidence: [{ ...gp, period_end: "2026-03-31" }, current] }), null);
  });
  check("operating margin keeps its numerator distinct", () => {
    const c = { ...claim, metric: "other", metric_text: "operating margin", comparison: "none" };
    assert.equal(receiptCalculation(c, { ...verdict, evidence: [{ ...current, metric: "operating_income", value: 600000000 }, current] }).value, 50);
  });
  for (const [metricText, valueText, expected, unit, label] of [
    ["gross margin expanded", "120 basis points", 125, "bps", "125 bps"],
    ["operating margin rose", "2.1 percentage points", 2.3, "pp", "2.3 pp"],
    ["net profit margin fell", "50 bps", -60, "bps", "−60 bps"],
    ["net margin rose", "2.1 pts", 2.15, "pp", "2.15 pp"],
  ]) {
    check(`${valueText} corrections keep the source unit despite percent or scaled model metadata`, () => {
      for (const modelUnit of ["%", "other", "USD"]) {
        const c = { ...claim, metric: "other", metric_text: metricText, value_text: valueText, unit: modelUnit, scale: "millions", value: 999 };
        const wrong = { ...verdict, status: "wrong", expected_value: expected };
        const filed = filedValue(c, wrong);
        assert.deepEqual(filed, { value: expected, unit });
        assert.equal(formatValue(filed.value, filed.unit), label);  // ReceiptDetail's Filed value
        assert.equal(correction(c, wrong), label);  // inline annotation
        assert.equal(formatExpectedValue(c, expected), label);  // ClaimsList's correction
        assert.equal(formatClaimed(c), valueText);
      }
    });
  }
  check("margin changes never display a coincidentally matching level or growth calculation", () => {
    const operating = { ...claim, metric: "other", metric_text: "operating margin rose", value_text: "50 bps" };
    const levelFacts = [{ ...current, metric: "operating_income", value: 600000000 }, current];
    assert.equal(receiptCalculation(operating, { ...verdict, expected_value: 50, evidence: levelFacts }), null);
    const net = { ...claim, metric: "other", metric_text: "net margin rose", value_text: "20 bps" };
    const growthFacts = [current, prior].map((f) => ({ ...f, metric: "net_income" }));
    assert.equal(receiptCalculation(net, { ...verdict, expected_value: 20, evidence: growthFacts }), null);
    const gross = { ...claim, metric: "gross_margin", metric_text: "gross margin expanded", value_text: "120 bps" };
    const filedLevel = { ...current, metric: "gross_margin", unit: "%", value: 75 };
    assert.equal(filedValue(gross, { ...verdict, evidence: [filedLevel] }), null);
    assert.equal(filedValue(gross, { ...verdict, status: "unverifiable", expected_value: 120 }), null);
  });
  check("source point units do not change unrelated percentage or money presentation", () => {
    assert.equal(formatExpectedValue({ ...claim, value_text: "20%" }, 21), "21%");
    const money = { ...claim, metric_text: "net income", value_text: "$2 million", unit: "USD", scale: "millions" };
    assert.equal(formatExpectedValue(money, 3), "$3M");
  });
  check("margin does not combine GAAP and non-GAAP operands", () => {
    for (const [metric, metricText, numeratorMetric] of [["gross_margin", "gross margin", "gross_profit"], ["other", "operating margin", "operating_income"]]) {
      const c = { ...claim, metric, metric_text: metricText, comparison: "none" };
      const numerator = { ...current, metric: numeratorMetric, basis: "non-GAAP", value: 900000000 };
      assert.equal(receiptCalculation(c, { ...verdict, expected_value: 75, evidence: [numerator, current] }), null);
      assert.equal(receiptCalculation(c, { ...verdict, expected_value: 75, evidence: [numerator, { ...current, basis: "non-GAAP" }] }).value, 75);
    }
  });
  check("a direct filed margin does not get a fabricated derivation", () => {
    assert.equal(receiptCalculation({ ...claim, metric: "gross_margin" }, { ...verdict, evidence: [{ ...current, metric: "gross_margin", unit: "%", value: 75 }, current] }), null);
  });
  check("unverifiable evidence cannot become a displayed calculation", () => assert.equal(receiptCalculation(claim, { ...verdict, status: "unverifiable" }), null));
  console.log(`\n${passed} presentation checks passed; no network or model calls.`);
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  console.error(error);
  process.exitCode = 1;
} finally {
  rmSync(compiled, { recursive: true, force: true });
}
