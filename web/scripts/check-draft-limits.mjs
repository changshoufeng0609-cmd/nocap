// Draft admission boundaries through the real clients and mock planner; no network.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const ts = require(resolve(root, "node_modules/typescript"));
const cache = new Map();
function load(file) {
  file = resolve(file);
  if (extname(file) === ".json") return JSON.parse(readFileSync(file, "utf8"));
  if (!extname(file)) file += ".ts";
  if (cache.has(file)) return cache.get(file).exports;
  const compiledModule = { exports: {} }; cache.set(file, compiledModule);
  const source = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
  }).outputText;
  const localRequire = (name) => name.startsWith("@/") ? load(resolve(root, name.slice(2)))
    : name.startsWith(".") ? load(resolve(dirname(file), name)) : require(name);
  new Function("require", "module", "exports", source)(localRequire, compiledModule, compiledModule.exports);
  return compiledModule.exports;
}

const realFetch = globalThis.fetch;
let passed = 0;
const check = async (name, fn) => { await fn(); passed++; console.log(`✓ ${name}`); };
try {
  const { streamCheck, streamFix } = load(resolve(root, "lib/api.ts"));
  const { runPrivateCheck, PrivateCheckError } = load(resolve(root, "lib/private.ts"));
  const { planCheck } = load(resolve(root, "lib/mock.ts"));
  for (const [name, action] of [["Check", streamCheck], ["Fix", streamFix]]) {
    await check(`${name} accepts 6,001 through 12,000 characters and rejects 12,001 before transport`, async () => {
      let calls = 0;
      globalThis.fetch = async (_url, init) => {
        calls++;
        assert.ok(JSON.parse(init.body).text.length <= 12000);
        return new Response('event: error\ndata: {"message":"Synthetic server response","retryable":false}\n\n', {
          headers: { "Content-Type": "text/event-stream" },
        });
      };
      for (const length of [6001, 12000, 12001]) {
        const errors = [];
        await action({ text: "x".repeat(length) }, { onError: (error) => errors.push(error) }, new AbortController().signal);
        assert.equal(errors.length, 1);
        if (length > 12000) {
          assert.match(errors[0].message, /12,000/);
          assert.equal(errors[0].model_calls, 0);
          assert.equal(errors[0].cost_usd, 0);
        } else assert.equal(errors[0].message, "Synthetic server response");
      }
      assert.equal(calls, 2);
    });
  }
  await check("Private client uses the same 12,000-character boundary without fallback", async () => {
    let calls = 0;
    globalThis.fetch = async (url) => {
      calls++; assert.match(url, /\/private\/check$/);
      return new Response('event: error\ndata: {"message":"Synthetic server response"}\n\n', {
        headers: { "Content-Type": "text/event-stream" },
      });
    };
    for (const length of [6001, 12000, 12001]) {
      await assert.rejects(() => runPrivateCheck({ text: "x".repeat(length) }, new AbortController().signal), (error) => {
        assert.ok(error instanceof PrivateCheckError);
        assert.match(error.message, length > 12000 ? /12,000/ : /Synthetic server response/);
        if (length > 12000) assert.equal(error.accounting.model_calls, 0);
        return true;
      });
    }
    assert.equal(calls, 2);
  });
  await check("mock planner applies the new limit before checking bundled fixtures", async () => {
    for (const length of [6001, 12000, 12001]) {
      const plan = planCheck({ text: "x".repeat(length) });
      assert.equal(plan.ok, false);
      assert.match(plan.message, length > 12000 ? /12,000/ : /bundled examples only/);
    }
  });
  console.log(`${passed} draft limit checks passed; no network or model calls.`);
} finally {
  globalThis.fetch = realFetch;
}
