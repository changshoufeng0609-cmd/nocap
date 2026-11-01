// Exercise the real Draft component and check hook with deferred, in-memory responses.
// Browser acceptance separately verifies layout, focus order and keyboard activation.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const ts = require("typescript");
const examples = JSON.parse(readFileSync(resolve(root, "fixtures/examples.json"), "utf8"));
const tick = () => new Promise((done) => setImmediate(done));
const nodes = (tree) => !tree || typeof tree !== "object" ? []
  : Array.isArray(tree) ? tree.flatMap(nodes) : [tree, ...nodes(tree.props?.children)];
const find = (tree, type) => nodes(tree).find((node) => node.type === type);
const text = (tree) => tree == null || typeof tree === "boolean" ? ""
  : typeof tree !== "object" ? String(tree)
    : Array.isArray(tree) ? tree.map(text).join("") : text(tree.props?.children);
const group = (tree) => nodes(tree).find((node) => node.props?.role === "group"
  && node.props["aria-labelledby"] === "saved-examples-label");
const exampleButtons = (tree) => nodes(group(tree)).filter((node) => node.type === "Button");

function harness() {
  const slots = [], requests = [], keyHandlers = new Set(), notices = [];
  let cursor = 0, effects = [], resolveExamples, rejectExamples;
  const pendingExamples = new Promise((resolve, reject) => { resolveExamples = resolve; rejectExamples = reject; });
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((value, i) => !Object.is(value, b[i]));
  const react = {
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useReducer(reducer, initial) {
      const i = cursor++;
      slots[i] ??= { value: initial, dispatch(next) { slots[i].value = reducer(slots[i].value, next); } };
      return [slots[i].value, slots[i].dispatch];
    },
    useState(initial) { return react.useReducer((value, next) => typeof next === "function" ? next(value) : next, initial); },
    useMemo(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) slots[i] = { value: fn(), deps };
      return slots[i].value;
    },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || changed(slots[i].deps, deps)) effects.push(() => {
        slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: fn() };
      });
    },
    useSyncExternalStore(_subscribe, _snapshot, serverSnapshot) { return serverSnapshot(); },
  };
  const toast = Object.assign((message) => notices.push(message), {
    error: (message) => notices.push(message), dismiss() {},
  });
  const overrides = {
    react,
    sonner: { toast },
    "@/hooks/use-media-query": { useMediaQuery: () => false },
    "@/lib/api": {
      fetchExamples: () => pendingExamples,
      fetchHealth: async () => null,
      streamCheck: (request, handlers) => requests.push({ request, handlers }),
    },
    "@/lib/ask": { fetchAskOptions: async () => ({ companies: [] }) },
  };
  const cache = new Map();
  function load(file) {
    if (!extname(file)) file += ".ts";
    if (cache.has(file)) return cache.get(file).exports;
    const loaded = { exports: {} }; cache.set(file, loaded);
    const code = ts.transpileModule(readFileSync(file, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const localRequire = (name) => Object.hasOwn(overrides, name) ? overrides[name]
      : name.startsWith("@/components/") || name === "lucide-react" ? new Proxy({}, { get: (_target, key) => key })
        : name.startsWith("@/") ? load(resolve(root, name.slice(2)))
          : name.startsWith(".") ? load(resolve(dirname(file), name)) : require(name);
    const window = {
      addEventListener: (name, fn) => { if (name === "keydown") keyHandlers.add(fn); },
      removeEventListener: (name, fn) => { if (name === "keydown") keyHandlers.delete(fn); },
    };
    new Function("require", "module", "exports", "window", "document", "requestAnimationFrame", code)(
      localRequire, loaded, loaded.exports, window, { getElementById: () => null }, (fn) => fn(),
    );
    return loaded.exports;
  }
  const { ReceiptsApp } = load(resolve(root, "components/receipts-app.tsx"));
  return {
    requests, notices,
    render(props = {}) {
      cursor = 0; effects = [];
      const tree = ReceiptsApp(props);
      effects.forEach((effect) => effect());
      return tree;
    },
    async resolve(list = examples) { resolveExamples(list); await tick(); },
    async reject() { rejectExamples(new Error("Examples unavailable")); await tick(); },
    shortcut() {
      const event = { key: "Enter", ctrlKey: true, preventDefault() {}, stopPropagation() {} };
      keyHandlers.forEach((fn) => fn(event));
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

const realFetch = globalThis.fetch;
let passed = 0;
const check = async (name, fn) => {
  const app = harness();
  try { await fn(app); passed++; console.log(`✓ ${name}`); }
  finally { app.unmount(); }
};
try {
  globalThis.fetch = () => { throw new Error("Homepage checks cannot use network transports."); };
  await check("initial Draft is empty with its placeholder, 0 / 12,000 counter and Now cutoff", (app) => {
    const tree = app.render();
    assert.equal(find(tree, "Textarea").props.value, "");
    assert.match(find(tree, "Textarea").props.placeholder, /^Paste a draft with financial figures:/);
    assert.ok(text(tree).includes("0 / 12,000"));
    assert.deepEqual(find(tree, "AsOfPicker").props.asOf, { mode: "now" });
    const checkButton = nodes(tree).find((node) => node.type === "Button" && text(node) === "Check");
    assert.equal(checkButton.props.disabled, false);
    checkButton.props.onClick(); app.shortcut();
    assert.equal(app.requests.length, 0);
  });
  await check("loading examples keeps Draft empty and exposes one labelled, keyboard-ready button per example", async (app) => {
    app.render(); await app.resolve();
    const tree = app.render(), row = group(tree), buttons = exampleButtons(tree);
    assert.equal(find(tree, "Textarea").props.value, "");
    assert.equal(find(tree, "ExamplePicker").props.activeId, null);
    assert.equal(text(nodes(row).find((node) => node.props?.id === row.props["aria-labelledby"])), "Try a saved example:");
    assert.deepEqual(buttons.map(text), examples.map((example) => example.title));
    assert.match(row.props.className, /\bflex-wrap\b/);
    for (const button of buttons) {
      assert.equal(button.props.type, "button");
      assert.ok(!button.props.disabled && button.props.tabIndex !== -1);
      assert.equal(typeof button.props.onClick, "function");
      assert.equal(button.props.variant, "outline"); assert.equal(button.props.size, "sm");
      assert.match(button.props.className, /\bmax-w-full\b/);
      assert.match(button.props.className, /\bwhitespace-normal\b/);
    }
    assert.equal(app.requests.length, 0);
  });
  await check("text and cutoff entered before examples arrive survive the deferred response", async (app) => {
    const tree = app.render();
    find(tree, "Textarea").props.onChange({ target: { value: "My own draft: revenue $12 billion." } });
    const cutoff = { mode: "custom", value: "2026-08-26T20:00" };
    find(tree, "AsOfPicker").props.onChange(cutoff);
    await app.resolve();
    const ready = app.render();
    assert.equal(find(ready, "Textarea").props.value, "My own draft: revenue $12 billion.");
    assert.deepEqual(find(ready, "AsOfPicker").props.asOf, cutoff);
    assert.equal(group(ready), undefined);
    app.shortcut();
    assert.deepEqual(app.requests.map(({ request }) => request), [{
      text: "My own draft: revenue $12 billion.", as_of: "2026-08-26T20:00:00Z", context: null, example_id: null,
    }]);
  });
  for (const example of examples) {
    await check(`${example.title}: one click requests its saved replay, even while live checks are paused`, async (app) => {
      app.render({ livePaused: true }); await app.resolve();
      const button = exampleButtons(app.render({ livePaused: true })).find((node) => text(node) === example.title);
      button.props.onClick();
      assert.deepEqual(app.requests.map(({ request }) => request), [{
        text: example.text, as_of: example.as_of, context: example.context, example_id: example.id,
      }]);
      const results = app.render({ livePaused: true });
      assert.equal(group(results), undefined);
      assert.equal(find(results, "HighlightedDraft").props.text, example.text);
    });
  }
  await check("ExamplePicker still loads without running; Check dispatches the selected example", async (app) => {
    app.render(); await app.resolve();
    const example = examples[0];
    find(app.render(), "ExamplePicker").props.onPick(example.id);
    const loaded = app.render();
    assert.equal(find(loaded, "Textarea").props.value, example.text);
    assert.equal(find(loaded, "ExamplePicker").props.activeId, example.id);
    assert.equal(group(loaded), undefined);
    assert.equal(app.requests.length, 0);
    nodes(loaded).find((node) => node.type === "Button" && text(node) === "Check").props.onClick();
    assert.equal(app.requests[0].request.example_id, example.id);
  });
  await check("clearing user text restores the saved-example row", async (app) => {
    app.render(); await app.resolve();
    find(app.render(), "Textarea").props.onChange({ target: { value: "My draft" } });
    const typed = app.render(); assert.equal(group(typed), undefined);
    find(typed, "Textarea").props.onChange({ target: { value: "" } });
    assert.equal(exampleButtons(app.render()).length, examples.length);
    assert.equal(app.requests.length, 0);
  });
  for (const outcome of ["empty", "failed"]) {
    await check(`${outcome} example loading leaves an editable empty draft without unusable shortcuts`, async (app) => {
      app.render();
      if (outcome === "empty") await app.resolve([]); else await app.reject();
      const tree = app.render();
      assert.equal(find(tree, "Textarea").props.value, "");
      assert.equal(group(tree), undefined);
      if (outcome === "failed") assert.equal(find(tree, "ExamplePicker").props.error, "Examples unavailable");
      assert.equal(app.requests.length, 0);
    });
  }
  console.log(`\n${passed} homepage checks passed; no network or model calls.`);
} finally {
  globalThis.fetch = realFetch;
}
