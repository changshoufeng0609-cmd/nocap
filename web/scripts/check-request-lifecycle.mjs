// Real client/hook regressions with in-memory transports and a deterministic hook lifecycle.
// Browser acceptance separately covers DOM, focus, tab visibility and Private clearing.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const ts = require(resolve(root, "node_modules/typescript"));
const realFetch = globalThis.fetch;
let passed = 0;
const check = async (name, fn) => { await fn(); passed++; console.log(`✓ ${name}`); };
const tick = () => new Promise((done) => setImmediate(done));
const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const request = { text: "Revenue $12 billion.", as_of: "2026-08-27T00:00:00Z", context: null };
const done = { cost_usd: 0, counts: { verified: 1, wrong: 0, unverifiable: 0, approximate: 0 } };
const claim = { id: "c1", span_start: 0, span_end: request.text.length };
const verdict = { claim_id: "c1", status: "verified", evidence: [] };

function loader(overrides = {}) {
  const cache = new Map();
  function load(file) {
    file = resolve(file);
    if (cache.has(file)) return cache.get(file).exports;
    const compiledModule = { exports: {} }; cache.set(file, compiledModule);
    const code = ts.transpileModule(readFileSync(file, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText;
    const localRequire = (name) => Object.hasOwn(overrides, name) ? overrides[name]
      : name.startsWith("@/") ? load(resolve(root, `${name.slice(2)}.ts`))
        : name.startsWith(".") ? load(resolve(dirname(file), `${name}.ts`)) : require(name);
    new Function("require", "module", "exports", code)(localRequire, compiledModule, compiledModule.exports);
    return compiledModule.exports;
  }
  return (file) => load(resolve(root, file));
}

// Only hook state/effect scheduling is simulated; all event guards and reducers are real code.
function hookHarness(file, exportName, dependencies) {
  const slots = [];
  let cursor = 0, effects = [];
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, i) => !Object.is(value, b[i]));
  const react = {
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useReducer(reducer, initial) {
      const i = cursor++;
      if (!slots[i]) {
        const slot = { value: initial, dispatch(action) { slot.value = reducer(slot.value, action); } };
        slots[i] = slot;
      }
      return [slots[i].value, slots[i].dispatch];
    },
    useState(initial) {
      return react.useReducer((value, next) => typeof next === "function" ? next(value) : next,
        typeof initial === "function" ? initial() : initial);
    },
    useCallback(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { fn, deps };
      return slots[i].fn;
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) effects.push(() => {
        slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: fn() };
      });
    },
  };
  const hook = loader({ react, ...dependencies })(file)[exportName];
  return {
    render(...args) {
      cursor = 0; effects = [];
      const value = hook(...args);
      effects.forEach((effect) => effect());
      return value;
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

try {
  globalThis.fetch = () => { throw new Error("Unexpected transport: tests must supply an in-memory response."); };
  const load = loader();
  const { streamCheck, streamFix } = load("lib/api.ts");
  const { readSSE } = load("lib/sse.ts");
  const handlers = (events, ac, abortOn) => Object.fromEntries(
    ["Meta", "Claim", "Verdict", "Done", "Error", "Progress", "Result"].map((kind) => [`on${kind}`, (...args) => {
      events.push([kind, ...args]); if (kind === abortOn) ac.abort();
    }]),
  );

  for (const [label, status, contentType, action] of [
    ["Check HTTP error body", 503, "application/json", streamCheck],
    ["Check unexpected content body", 200, "application/json", streamCheck],
    ["Fix HTTP error body", 503, "application/json", streamFix],
  ]) {
    for (const rejectBody of [false, true]) {
      await check(`${label}: abort while parsing ${rejectBody ? "rejected" : "resolved"} body suppresses callbacks`, async () => {
        const ac = new AbortController(), events = [];
        let body;
        globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) { body = controller; } }), {
          status, headers: { "Content-Type": contentType },
        });
        const pending = action(request, handlers(events, ac), ac.signal);
        await tick();
        ac.abort();
        if (rejectBody) body.error(new DOMException("Cancelled", "AbortError"));
        else { body.enqueue(new TextEncoder().encode('{"message":"Synthetic delayed response"}')); body.close(); }
        await pending;
        assert.deepEqual(events, []);
      });
    }
  }
  await check("Check drops every queued event after a callback aborts the same network chunk", async () => {
    const ac = new AbortController(), events = [];
    globalThis.fetch = async () => new Response(frame("meta", {}) + frame("claim", { claim }) + frame("verdict", { verdict }) + frame("done", done), {
      headers: { "Content-Type": "text/event-stream" },
    });
    await streamCheck(request, handlers(events, ac, "Meta"), ac.signal);
    assert.deepEqual(events.map(([event]) => event), ["Meta"]);
  });
  await check("Check rejects late headers after abort without any callback", async () => {
    const ac = new AbortController(), events = [];
    globalThis.fetch = async () => { ac.abort(); return new Response("offline", { status: 503 }); };
    await streamCheck(request, handlers(events, ac), ac.signal);
    assert.deepEqual(events, []);
  });
  await check("shared SSE reader stops its queued messages on abort", async () => {
    const ac = new AbortController(), events = [];
    const response = new Response(frame("one", {}) + frame("two", {}));
    for await (const event of readSSE(response.body, ac.signal)) { events.push(event.event); ac.abort(); }
    assert.deepEqual(events, ["one"]);
  });
  await check("shared SSE reader cancels a body when its signal was already aborted", async () => {
    const ac = new AbortController(); ac.abort(); let cancelled = false;
    const body = new ReadableStream({ cancel() { cancelled = true; } });
    const events = [];
    for await (const event of readSSE(body, ac.signal)) events.push(event);
    assert.deepEqual(events, []); assert.equal(cancelled, true);
  });
  await check("ordinary Check success and duplicate claim updates count distinct claims", async () => {
    const calls = [], notices = [];
    const hook = hookHarness("hooks/use-check.ts", "useCheck", { "@/lib/api": {
      streamCheck(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    const callbacks = { onDone: (...args) => notices.push(args) };
    hook.render(callbacks).start(request);
    calls[0].h.onClaim(claim); calls[0].h.onClaim(claim); calls[0].h.onVerdict("c1", verdict);
    calls[0].h.onDone(done);
    assert.equal(hook.render(callbacks).state.phase, "done");
    assert.equal(notices[0][1].claims, 1); assert.deepEqual(notices[0][1].verdicts, [verdict]);
    calls[0].h.onError({ message: "late" }); calls[0].h.onDone(done);
    assert.equal(notices.length, 1); assert.equal(hook.render(callbacks).state.phase, "done");
    hook.unmount();
  });
  for (const operation of ["cancel", "supersede", "hide", "unmount"]) {
    await check(`Check ${operation} ignores all stale callbacks and notifications`, async () => {
      const calls = [], notices = [];
      const hook = hookHarness("hooks/use-check.ts", "useCheck", { "@/lib/api": {
        streamCheck(req, h, signal) { calls.push({ req, h, signal }); },
      } });
      const callbacks = { onDone: () => notices.push("done"), onError: () => notices.push("error") };
      const api = hook.render(callbacks); api.start(request);
      if (operation === "cancel") api.cancel();
      if (operation === "supersede") api.start({ ...request, text: "Another draft" });
      if (operation === "hide") hook.render(callbacks, false);
      if (operation === "unmount") hook.unmount();
      assert.equal(calls[0].signal.aborted, true);
      const h = calls[0].h;
      h.onMeta({ as_of: "stale" }); h.onClaim(claim); h.onVerdict("c1", verdict); h.onDone(done); h.onError({ message: "stale" });
      assert.deepEqual(notices, []);
      if (operation !== "unmount") {
        const state = hook.render(callbacks, operation !== "hide").state;
        assert.equal(state.meta, null); assert.deepEqual(state.claims, []); assert.deepEqual(state.verdicts, {});
        assert.equal(state.done, null); assert.equal(state.error, null);
        assert.equal(state.phase, operation === "supersede" ? "streaming" : "idle");
      }
      hook.unmount();
    });
  }
  await check("an interrupted Check keeps only claims that already have verdicts", async () => {
    const calls = [];
    const hook = hookHarness("hooks/use-check.ts", "useCheck", { "@/lib/api": {
      streamCheck(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    const api = hook.render(); api.start(request);
    const second = { ...claim, id: "c2", span_start: claim.span_start + 1 };
    calls[0].h.onClaim(claim); calls[0].h.onVerdict("c1", verdict); calls[0].h.onClaim(second);
    api.cancel();
    const state = hook.render().state;
    assert.equal(state.phase, "idle"); assert.deepEqual(state.claims.map((c) => c.id), ["c1"]);
    assert.deepEqual(Object.keys(state.verdicts), ["c1"]);
    hook.unmount();
  });
  await check("hidden Draft cannot dispatch an old Retry action and does not auto-run on return", async () => {
    const calls = [];
    const hook = hookHarness("hooks/use-check.ts", "useCheck", { "@/lib/api": {
      streamCheck(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    const retry = hook.render().start;
    retry(request); calls[0].h.onError({ message: "Retry me", retryable: true });
    hook.render({}, false); retry(request); assert.equal(calls.length, 1);
    hook.render({}, true); assert.equal(calls.length, 1);
    retry(request); assert.equal(calls.length, 2);
    hook.unmount();
  });
  await check("completed Draft results and cutoff survive hide and return with no new request", async () => {
    const calls = [];
    const hook = hookHarness("hooks/use-check.ts", "useCheck", { "@/lib/api": {
      streamCheck(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    hook.render().start(request); calls[0].h.onClaim(claim); calls[0].h.onVerdict("c1", verdict); calls[0].h.onDone(done);
    const completed = hook.render().state;
    hook.render({}, false); hook.render({}, true);
    assert.deepEqual(hook.render().state, completed);
    assert.equal(hook.render().state.request.as_of, request.as_of); assert.equal(calls.length, 1);
    hook.unmount();
  });
  await check("Fix cancels on hide, suppresses late events and permits an explicit restart", async () => {
    const calls = [];
    const hook = hookHarness("hooks/use-fix.ts", "useFix", { "@/lib/api": {
      streamFix(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    const restart = hook.render().start; restart(request); hook.render(false);
    assert.equal(calls[0].signal.aborted, true);
    calls[0].h.onProgress("verifying"); calls[0].h.onResult({ rewritten_text: "stale" }); calls[0].h.onError({ message: "stale" });
    assert.equal(hook.render(false).state.phase, "idle"); restart(request); assert.equal(calls.length, 1);
    hook.render(true); assert.equal(calls.length, 1); restart(request); assert.equal(calls.length, 2);
    hook.unmount(); assert.equal(calls[1].signal.aborted, true);
  });
  await check("completed Fix stays available across mode switches and paused availability", async () => {
    const calls = [];
    const hook = hookHarness("hooks/use-fix.ts", "useFix", { "@/lib/api": {
      streamFix(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    hook.render().start(request); const result = { rewritten_text: "Corrected text" }; calls[0].h.onResult(result);
    hook.render(false); assert.deepEqual(hook.render(false).state, { phase: "done", result });
    hook.render(true); assert.deepEqual(hook.render().state, { phase: "done", result }); assert.equal(calls.length, 1);
    hook.unmount();
  });
  await check("Fix cannot dispatch while paused, including through a retained start handler", async () => {
    const calls = [];
    const hook = hookHarness("hooks/use-fix.ts", "useFix", { "@/lib/api": {
      streamFix(req, h, signal) { calls.push({ req, h, signal }); },
    } });
    const start = hook.render(false).start; start(request); assert.equal(calls.length, 0);
    hook.render(true); start(request); assert.equal(calls.length, 1);
    hook.render(false); start(request); assert.equal(calls.length, 1);
    hook.unmount();
  });
  console.log(`\n${passed} request lifecycle checks passed; no network or model calls.`);
} finally {
  globalThis.fetch = realFetch;
}
