// Offline regression: margin corrections retain the source unit despite cached model metadata.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "receipts-margin-units-"));
let passed = 0;
let failed = 0;
function check(name, fn) {
  try { fn(); passed++; console.log(`✓ ${name}`); }
  catch (error) { failed++; console.error(`✗ ${name}: ${error.message}`); }
}
try {
  execFileSync(process.execPath, [join(root, "node_modules/typescript/bin/tsc"), "--noEmit", "false", "--incremental", "false", "--outDir", compiled, "--module", "commonjs", "--target", "es2020", "--skipLibCheck", "lib/claims.ts"], { cwd: root, stdio: "pipe" });
  const { marginChangeUnit, formatExpectedValue, formatClaimed } = await import(pathToFileURL(join(compiled, "format.js")).href);
  const { filedValue, correction } = await import(pathToFileURL(join(compiled, "claims.js")).href);
  const claim = { metric: "other", metric_text: "net margin rose", value: 130, unit: "%", scale: "units" };
  const verdict = { status: "wrong", reason: "value_mismatch", expected_value: 120, evidence: [] };
  for (const valueText of ["130bps", "130 bps", "130bp", "130 bp", "130 basis points", "130 basis point", "130BPS", "+130bps", "−130bps", "1,130bps", ".4bps", "130\tbps"]) {
    check(`${valueText} displays 120 bps`, () => {
      const c = { ...claim, value_text: valueText };
      assert.equal(formatExpectedValue(c, 120), "120 bps");
      assert.equal(marginChangeUnit(c), "bps");
      assert.equal(correction(c, verdict), "120 bps");
      assert.deepEqual(filedValue(c, verdict), { value: 120, unit: "bps" });
      assert.equal(formatClaimed(c), valueText);
    });
  }
  for (const valueText of ["1.3pp", "1.3 pp", "1.3 pts", "1.3 percentage points", "1.3 percentage point", "1.3PP", ".4pp", "−.4pp"]) {
    check(`${valueText} displays 1.2 pp`, () => {
      for (const unit of ["%", "other", "USD"]) {
        const c = { ...claim, value_text: valueText, unit, scale: "millions" };
        const v = { ...verdict, expected_value: 1.2 };
        assert.equal(formatExpectedValue(c, 1.2), "1.2 pp");
        assert.equal(marginChangeUnit(c), "pp");
        assert.equal(correction(c, v), "1.2 pp");
        assert.deepEqual(filedValue(c, v), { value: 1.2, unit: "pp" });
        assert.equal(formatClaimed(c), valueText);
      }
    });
  }
  for (const valueText of ["bps", "1.3%", "130bpsx", "1.3pps", "130bps 140bps", "130bps and 1.3%", "rate130bps", "1.3.4pp"]) {
    check(`${valueText} is not a single margin-change amount`, () => {
      assert.equal(marginChangeUnit({ ...claim, value_text: valueText }), null);
    });
  }
  check("a non-margin claim keeps its own unit", () => {
    assert.equal(marginChangeUnit({ ...claim, metric_text: "net income", value_text: "130bps" }), null);
  });
  console.log(`\n${passed} margin unit checks passed; ${failed} failed; no network or model calls.`);
  if (failed) process.exitCode = 1;
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  console.error(error);
  process.exitCode = 1;
} finally {
  rmSync(compiled, { recursive: true, force: true });
}
