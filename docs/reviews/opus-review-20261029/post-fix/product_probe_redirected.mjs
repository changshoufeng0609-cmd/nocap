// Read-only product review probe: actual TypeScript client, in-memory compilation,
// synthetic fetch response, no server/network/provider/SEC calls or credentials.
// Run from any directory: node /absolute/path/to/product_probe.mjs
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidenceDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(evidenceDir, "../../../..");
const require = createRequire(import.meta.url);
const ts = require(path.join(root, "web/node_modules/typescript"));
const sourceFiles = ["web/lib/api.ts", "web/lib/sse.ts", "web/hooks/use-check.ts"];
const sourceHashes = Object.fromEntries(sourceFiles.map((file) => [file,
  createHash("sha256").update(fs.readFileSync(path.join(root, file))).digest("hex")
]));
const moduleCache = new Map();

function loadTypescript(file) {
  file = path.resolve(file);
  if (moduleCache.has(file)) return moduleCache.get(file);
  const module = { exports: {} };
  moduleCache.set(file, module.exports);
  const code = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const localRequire = (name) => name.startsWith(".")
    ? loadTypescript(path.resolve(path.dirname(file), `${name}.ts`))
    : require(name);
  new Function("require", "module", "exports", code)(localRequire, module, module.exports);
  return module.exports;
}

const originalFetch = globalThis.fetch;
const events = [];
let fetchCalls = 0;
let bodyController;
const controller = new AbortController();
try {
  const { streamCheck } = loadTypescript(path.join(root, "web/lib/api.ts"));
  // The response has arrived, while its error body is still being read. An actual
  // fetch abort rejects the body read; this fixture models that rejection directly.
  globalThis.fetch = async (url, init) => {
    fetchCalls += 1;
    assert.match(url, /^\/(?:api|mock-api)\/check$/);
    assert.equal(init.method, "POST");
    assert.equal(init.signal, controller.signal);
    return new Response(new ReadableStream({ start(stream) { bodyController = stream; } }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  };
  const ignore = () => {};
  const pending = streamCheck({ text: "Synthetic offline cancellation fixture." }, {
    onMeta: ignore, onClaim: ignore, onVerdict: ignore,
    onDone: (data) => events.push({ type: "done", signal_aborted: controller.signal.aborted, data }),
    onError: (data) => events.push({ type: "error", signal_aborted: controller.signal.aborted, data }),
  }, controller.signal);
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(bodyController, "Synthetic fetch must have returned the delayed body");
  controller.abort();
  bodyController.error(new DOMException("The operation was aborted.", "AbortError"));
  await pending;

  const result = {
    probe: "Check error-body cancellation",
    recorded_at: new Date().toISOString(),
    source_sha256: sourceHashes,
    scope: {
      actual_typescript_client: "web/lib/api.ts::streamCheck",
      in_memory_compilation: true,
      synthetic_fetch_calls: fetchCalls,
      network_calls: 0,
      model_calls: 0,
      sec_calls: 0,
      new_provider_spend_usd: 0,
      browser_used: false,
      heldout_used: false,
      credential_files_read: false,
    },
    steps: [
      "Return synthetic HTTP 503 with an unresolved JSON response body.",
      "Allow streamCheck to start awaiting error-body parsing.",
      "Abort the request and reject its body with AbortError.",
      "Wait for streamCheck completion and record callbacks.",
    ],
    expected_callbacks_after_abort: 0,
    observed_callbacks_after_abort: events.filter((event) => event.signal_aborted).length,
    events,
    finding_reproduced: events.some((event) => event.type === "error" && event.signal_aborted),
    evidence_limit: "This reproduces the client callback race offline. React UI consequences follow from static hook/component inspection; no browser acceptance was run.",
  };
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  fs.writeFileSync(path.join(evidenceDir, "product_results.json"), serialized);
  process.stdout.write(serialized);
} finally {
  globalThis.fetch = originalFetch;
}
