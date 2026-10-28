// Exercise the real SSE clients and cost formatting with synthetic, chunked responses.
// Fetch is replaced for every case: no server, network, provider credentials or model calls.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const compiled = mkdtempSync(join(tmpdir(), "nocap-cost-client-"));
const realFetch = globalThis.fetch;
let passed = 0;

try {
  execFileSync(process.execPath, [
    join(root, "node_modules/typescript/bin/tsc"),
    "--noEmit", "false", "--incremental", "false", "--outDir", compiled,
    "--module", "commonjs", "--target", "es2020", "--lib", "dom,dom.iterable,es2020",
    "--skipLibCheck", "lib/api.ts", "lib/slides.ts", "lib/format.ts", "lib/types.ts", "lib/sse.ts",
  ], { cwd: root, stdio: "pipe" });
  const { streamCheck, streamFix, costFields } = await import(pathToFileURL(join(compiled, "api.js")).href);
  const { streamSlide } = await import(pathToFileURL(join(compiled, "slides.js")).href);
  const { formatErrorCost } = await import(pathToFileURL(join(compiled, "format.js")).href);

  const request = { text: "A fictional cost-parser fixture.", as_of: null, context: null };
  const counts = { verified: 0, wrong: 0, unverifiable: 0, approximate: 0 };
  const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  const validCost = { cost_usd: 0.0123, cost_is_estimate: false, model_calls: 2 };
  const errorBase = { message: "The synthetic request stopped 🧾.", retryable: true };
  const clients = [
    {
      name: "Check", endpoint: "check", invoke: streamCheck, request, terminal: "done", prefix: "",
      success: { counts, latency_s: 1, model: "muse-spark-1.3", ...validCost },
    },
    {
      name: "Fix", endpoint: "fix", invoke: streamFix, request, terminal: "result", prefix: "",
      success: {
        original_text: request.text, rewritten_text: request.text, status: "unchanged", changes: [],
        remaining: [], results: [], counts, latency_s: 1, model: "muse-wonder-1.0",
        extraction_model: "muse-spark-1.3", ...validCost,
      },
    },
    {
      name: "Slide", endpoint: "slide", invoke: streamSlide,
      request: { image_data_url: "data:image/png;base64,SYNTHETIC", context: null, as_of: null },
      terminal: "done",
      prefix: frame("meta", { models: ["sam-3.1", "muse-spark-1.3"], image: { width: 32, height: 32 }, cached: false, as_of: null }),
      success: { counts, claims: 0, latency_s: 1, warnings: ["Synthetic fixture; no image was read."], ...validCost },
    },
  ];

  async function run(client, body, options = {}) {
    const seen = [];
    globalThis.fetch = async (url, init) => {
      assert.match(url, new RegExp(`/${client.endpoint}$`));
      assert.equal(init.method, "POST");
      if (options.networkError) throw new Error("Synthetic offline failure");
      const bytes = new TextEncoder().encode(body);
      const stream = new ReadableStream({
        start(controller) {
          // Split both JSON accounting fields and the Unicode error message.
          for (let i = 0; i < bytes.length; i += 5) controller.enqueue(bytes.slice(i, i + 5));
          controller.close();
        },
      });
      return new Response(stream, {
        status: options.status ?? 200,
        headers: { "Content-Type": options.contentType ?? "text/event-stream" },
      });
    };
    const ignore = () => {};
    await client.invoke(client.request, {
      onMeta: ignore, onProgress: ignore, onRegion: ignore, onClaim: ignore, onVerdict: ignore,
      onDone: (data) => seen.push(["done", data]),
      onResult: (data) => seen.push(["result", data]),
      onError: (data) => seen.push(["error", data]),
    }, new AbortController().signal);
    return seen;
  }

  function unavailable(seen) {
    assert.equal(seen.length, 1);
    assert.equal(seen[0][0], "error");
    for (const key of ["cost_usd", "cost_is_estimate", "model_calls"]) {
      assert.equal(Object.hasOwn(seen[0][1], key), false, `unavailable accounting must omit ${key}`);
    }
    assert.equal(formatErrorCost(seen[0][1]), "Model cost unavailable.");
  }

  async function check(name, fn) {
    await fn();
    passed += 1;
    console.log(`✓ ${name}`);
  }

  const malformed = [
    {}, { cost_usd: 0 }, { cost_usd: 0, cost_is_estimate: false },
    { cost_is_estimate: true, model_calls: 1 },
    ...[null, -0.01, "0.01", true, [], {}].map((cost_usd) => ({ ...validCost, cost_usd })),
    ...[null, "false", 0].map((cost_is_estimate) => ({ ...validCost, cost_is_estimate })),
    ...[null, -1, 0.5, "2", true, Number.MAX_SAFE_INTEGER + 1].map((model_calls) => ({ ...validCost, model_calls })),
  ];

  for (const client of clients) {
    for (const estimated of [false, true]) {
      await check(`${client.name} error preserves a complete ${estimated ? "estimated" : "known"} cost tuple`, async () => {
        const accounting = { ...validCost, cost_is_estimate: estimated };
        assert.deepEqual(await run(client, frame("error", { ...errorBase, ...accounting })),
          [["error", { ...errorBase, ...accounting }]]);
      });
      await check(`${client.name} successful ${client.terminal} retains estimate=${estimated}`, async () => {
        const success = { ...client.success, cost_is_estimate: estimated };
        const seen = await run(client, client.prefix + frame(client.terminal, success));
        assert.equal(seen.length, 1);
        assert.equal(seen[0][0], client.terminal);
        assert.equal(seen[0][1].cost_usd, validCost.cost_usd);
        assert.equal(seen[0][1].cost_is_estimate, estimated);
        assert.equal(seen[0][1].model_calls, 2);
      });
    }
    await check(`${client.name} explicitly reported zero remains distinguishable from missing usage`, async () => {
      const zero = { cost_usd: 0, cost_is_estimate: false, model_calls: 0 };
      const seen = await run(client, frame("error", { ...errorBase, ...zero }));
      assert.deepEqual(seen, [["error", { ...errorBase, ...zero }]]);
      assert.equal(formatErrorCost(seen[0][1]), "Model cost: $0.");
    });
    await check(`${client.name} missing, partial and malformed error costs remain unavailable`, async () => {
      for (const fields of malformed) {
        const seen = await run(client, frame("error", { ...errorBase, ...fields }));
        unavailable(seen);
        assert.equal(seen[0][1].message, errorBase.message);
        assert.equal(seen[0][1].retryable, true);
      }
    });
    await check(`${client.name} overflowing JSON numbers cannot become a cost`, async () => {
      unavailable(await run(client,
        'event: error\ndata: {"message":"overflow","retryable":false,"cost_usd":1e999,"cost_is_estimate":false,"model_calls":1}\n\n'));
    });
    await check(`${client.name} terminal failure cannot be overwritten by a later successful cost`, async () => {
      const error = { ...errorBase, ...validCost, cost_is_estimate: true };
      const seen = await run(client, frame("error", error) + client.prefix + frame(client.terminal, client.success));
      assert.deepEqual(seen, [["error", error]]);
    });
    await check(`${client.name} EOF and transport failures do not imply zero spend`, async () => {
      unavailable(await run(client, client.prefix));
      unavailable(await run(client, "", { networkError: true }));
      unavailable(await run(client, '{"message":"Synthetic unavailable service"}', {
        status: 503, contentType: "application/json",
      }));
    });
    await check(`${client.name} rejects malformed successful terminal accounting`, async () => {
      const invalid = [
        ...[-0.01, null, "0.01", true, [], {}].map((cost_usd) => ({ cost_usd })),
        ...[null, "false", 0].map((cost_is_estimate) => ({ cost_is_estimate })),
        ...[null, -1, 0.5, "2", true, Number.MAX_SAFE_INTEGER + 1].map((model_calls) => ({ model_calls })),
      ];
      for (const fields of invalid) {
        unavailable(await run(client, client.prefix + frame(client.terminal, { ...client.success, ...fields })));
      }
      const missing = { ...client.success };
      delete missing.cost_usd;
      unavailable(await run(client, client.prefix + frame(client.terminal, missing)));
      const overflow = JSON.stringify(client.success).replace('"cost_usd":0.0123', '"cost_usd":1e999');
      assert.match(overflow, /1e999/);
      unavailable(await run(client, `${client.prefix}event: ${client.terminal}\ndata: ${overflow}\n\n`));
    });
    if (client.name !== "Slide") {
      await check(`${client.name} accepts legacy successful costs without inventing optional flags`, async () => {
        const legacy = { ...client.success };
        delete legacy.cost_is_estimate;
        delete legacy.model_calls;
        const seen = await run(client, client.prefix + frame(client.terminal, legacy));
        assert.equal(seen.length, 1);
        assert.equal(seen[0][0], client.terminal);
        assert.equal(seen[0][1].cost_usd, validCost.cost_usd);
        assert.equal(Object.hasOwn(seen[0][1], "cost_is_estimate"), false);
        assert.equal(Object.hasOwn(seen[0][1], "model_calls"), false);
      });
    }
  }

  await check("cost parser rejects non-finite values without fabricating zero", () => {
    for (const cost_usd of [NaN, Infinity, -Infinity]) assert.deepEqual(costFields({ ...validCost, cost_usd }), {});
    for (const model_calls of [NaN, Infinity, -Infinity]) assert.deepEqual(costFields({ ...validCost, model_calls }), {});
  });
  await check("known error cost formatting", () => {
    assert.equal(formatErrorCost(validCost), "Model cost: $0.0123.");
    assert.equal(formatErrorCost({ ...validCost, cost_usd: 0.25 }), "Model cost: $0.25.");
  });
  await check("estimated error cost formatting", () => {
    assert.equal(formatErrorCost({ ...validCost, cost_is_estimate: true }), "Estimated model cost: $0.0123.");
  });
  await check("absent cost formatting remains unavailable even with an estimate flag", () => {
    assert.equal(formatErrorCost({}), "Model cost unavailable.");
    assert.equal(formatErrorCost({ cost_is_estimate: true, model_calls: 1 }), "Model cost unavailable.");
  });

  console.log(`\n${passed} cost client checks passed; no network or model calls.`);
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  console.error(error);
  process.exitCode = 1;
} finally {
  globalThis.fetch = realFetch;
  rmSync(compiled, { recursive: true, force: true });
}
