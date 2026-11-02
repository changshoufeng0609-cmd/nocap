// Render the /benchmarks accuracy panel from the generated app/benchmarks/accuracy.json, offline.
// Browser acceptance separately verifies the page's layout at 1440 and 390 px.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const PANEL = resolve(root, "components/accuracy-panel.tsx");
const data = JSON.parse(readFileSync(resolve(root, "app/benchmarks/accuracy.json"), "utf8"));
const GITHUB = "https://github.com/changshoufeng0609-cmd/nocap/blob/main/";

const cache = new Map();
function load(file) {
  if (!extname(file)) file += ".ts";
  if (cache.has(file)) return cache.get(file).exports;
  const loaded = { exports: {} }; cache.set(file, loaded);
  const code = ts.transpileModule(readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const localRequire = (name) => name.startsWith("@/") ? load(resolve(root, name.slice(2)))
    : name.startsWith(".") ? load(resolve(dirname(file), name)) : require(name);
  new Function("require", "module", "exports", code)(localRequire, loaded, loaded.exports);
  return loaded.exports;
}

const { AccuracyPanel } = load(PANEL);
const render = (value = data) => renderToStaticMarkup(React.createElement(AccuracyPanel, { data: value }));
const decode = (s) => s.replace(/&quot;/g, "\"").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
  .replace(/&amp;/g, "&");
const flat = (s) => decode(s.replace(/<[^>]+>/g, "")).replace(/\s+/g, " ").trim();
/** What a sighted reader sees, and what a screen reader says (the mark words instead of the glyphs). */
const visible = (html) => flat(html.replace(/<span class="sr-only">[^<]*<\/span>/g, ""));
const spoken = (html) => flat(html.replace(/<span aria-hidden="true">[^<]*<\/span>/g, ""));
const inner = (html, tag) => [...html.matchAll(new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`, "g"))].map((m) => m[1]);
const clone = () => JSON.parse(JSON.stringify(data));

let passed = 0;
function check(name, fn) { fn(); passed++; console.log(`✓ ${name}`); }

const html = render();
const paragraphs = inner(html, "p").map(visible);

check("the panel is one section labelled by its own h2", () => {
  assert.match(html, /^<section aria-labelledby="accuracy-test"/);
  assert.deepEqual(inner(html, "h2"), ["Comprehensive accuracy test (current verifier)"]);
  assert.match(html, /<h2 id="accuracy-test"/);
  assert.doesNotMatch(html, /<h1\b/);
});

check("the subtitle names the items, companies and date of the one run", () => {
  assert.equal(paragraphs[0],
    "1,000 frozen items from 400 S&P 500 companies, run once on Nov 2, 2026 against the deployed verifier.");
});

check("four tiles show the current verifier's count, rate and 95% interval", () => {
  const tiles = inner(inner(html, "dl")[0], "div").map((tile) => ({
    label: visible(inner(tile, "dt")[0]), said: spoken(inner(tile, "dt")[0]),
    value: inner(inner(tile, "dd")[0], "span").map(visible), interval: visible(inner(tile, "dd")[1]),
  }));
  assert.deepEqual(tiles, [
    { label: "Planted errors caught", said: "Planted errors caught", value: ["306/360", "85.0%"],
      interval: "95% interval 79.9%–89.5%" },
    { label: "Planted errors marked ✓", said: "Planted errors marked verified", value: ["0/360", "0.0%"],
      interval: "95% interval 0.0%–1.1%" },
    { label: "True figures flagged ✗", said: "True figures flagged wrong", value: ["17/1,298", "1.3%"],
      interval: "95% interval 0.7%–2.1%" },
    { label: "When NoCap says ✓, it's right", said: "When NoCap says verified, it's right", value: ["736/738", "99.7%"],
      interval: "95% interval 99.0%–100.0%" },
  ]);
});

check("the companies not used in development and the coverage get their own lines", () => {
  assert.ok(paragraphs.includes("On the 312 companies not used in development or named in fixes (768 items): 235/274 planted errors "
    + "caught (85.8%) and 14/1,018 true figures flagged ✗ (1.4%)."), paragraphs.join("\n"));
  assert.ok(paragraphs.includes("NoCap verified 736/1,298 true figures (56.7%). A figure it cannot settle from the "
    + "filings stays ?."), paragraphs.join("\n"));
});

check("the previous version is one smaller line, with the caveat that the sentences differ", () => {
  const line = paragraphs.find((p) => p.startsWith("Previous version"));
  assert.equal(line, "Previous version (first comprehensive test, code 8d69d3c, 1,000 items from 391 companies): "
    + "310/376 caught, 2/376 marked ✓, 14/1,271 flagged ✗; its ✓ marks were right 741 of 744 times. The two tests "
    + "use different sentences, so this is not a controlled before/after comparison.");
  assert.match(spoken(inner(html, "p").find((p) => p.includes("Previous version"))),
    /2\/376 marked verified, 14\/1,271 flagged wrong; its verified marks were right 741 of 744 times\./);
  assert.ok(paragraphs.includes("Run Nov 2, 2026 on code 8c2fd78 · 940 calls to muse-spark-1.3 · $3.80 measured usage "
    + "cost · 95% intervals: the wider of a Wilson score interval and a company-level cluster bootstrap (2,000 "
    + "resamples, seed 20261102)."), paragraphs.join("\n"));
});

check("the limits note: machine-made labels, later changes excluded, every failure in the methods", () => {
  const note = paragraphs.find((p) => p.startsWith("Limits."));
  assert.match(note, /Labels are machine-made, with no independent human review\./);
  assert.match(note, /Later changes, such as release-statement matching and wording fixes, came after this test and are not in its numbers\./);
  assert.match(note, /The full method and every failure are in the methods document\.$/);
});

check("links reach the methods sections, the methods document and the report script on GitHub, in a new tab", () => {
  const links = [...html.matchAll(/<a ([^>]*)>([\s\S]*?)<\/a>/g)].map(([, attrs, body]) => ({
    href: /href="([^"]*)"/.exec(attrs)[1], text: visible(body), said: spoken(body),
    external: attrs.includes("target=\"_blank\"") && attrs.includes("rel=\"noopener noreferrer\""),
  }));
  assert.deepEqual(links.map(({ href, text }) => [href, text]), [
    [`${GITHUB}eval/spot500_methods.md`, "methods document"],
    [`${GITHUB}eval/spot500_methods.md#results-of-the-one-live-run-november-2-2026-after-round-8`, "This run's results and failures"],
    [`${GITHUB}eval/spot500_methods.md#results-of-the-one-live-run-november-2-2026-all-companies`, "Previous version's results"],
    [`${GITHUB}eval/spot500_report.py`, "Report script that computes every number"],
  ]);
  for (const link of links) {
    assert.ok(link.external, link.href);
    assert.equal(link.said, `${link.text} (opens in a new tab)`);
  }
});

check("every ✓ ✗ ? mark is hidden from screen readers and replaced by its word", () => {
  const marks = [...html.matchAll(/[✓✗?]/g)].length;
  const paired = [...html.matchAll(/<span aria-hidden="true">([✓✗?])<\/span><span class="sr-only">(verified|wrong|unverifiable)<\/span>/g)];
  assert.ok(marks > 0 && paired.length === marks, `${paired.length} of ${marks} marks have a spoken word`);
  for (const [, glyph, word] of paired) assert.equal(word, { "✓": "verified", "✗": "wrong", "?": "unverifiable" }[glyph]);
});

check("the numbers are read from accuracy.json, never typed into the component", () => {
  const source = readFileSync(PANEL, "utf8");
  const shown = [data.current.commit, data.previous.commit, data.current.cost_usd.toFixed(2), data.interval];
  for (const run of [data.current, data.current.without_company_in_development, data.previous]) {
    for (const rate of Object.values(run.rates)) shown.push(`${rate.k}/${rate.n}`, rate.pct, ...rate.ci95_pct);
  }
  for (const text of shown) assert.ok(!source.includes(text), `the component hard-codes ${text}`);
  const edited = clone();
  Object.assign(edited.current.rates.caught, { k: 305, pct: "84.7%", ci95_pct: ["79.0%", "89.0%"] });
  edited.current.without_company_in_development.companies = 311;
  edited.previous.commit = "0000000";
  const changed = render(edited);
  assert.match(visible(changed), /Planted errors caught305\/36084\.7%95% interval 79\.0%–89\.0%/);
  assert.match(visible(changed), /On the 311 companies not used in development or named in fixes/);
  assert.match(visible(changed), /code 0000000, 1,000 items from 391 companies/);
});

check("accuracy.json is internally consistent: each rate is k/n, inside its interval, rounded as shown", () => {
  assert.equal(data.generated_by, "scripts/build_accuracy_panel.py");
  assert.equal(data.github, GITHUB);
  assert.deepEqual([data.current.set, data.previous.set], ["full2", "full"]);
  const near = (text, value) => Math.abs(Number.parseFloat(text) - 100 * value) <= 0.05 + 1e-9;
  for (const run of [data.current, data.current.without_company_in_development, data.previous]) {
    for (const rate of Object.values(run.rates)) {
      const [low, high] = rate.ci95;
      assert.equal(rate.rate, rate.k / rate.n);
      assert.ok(0 <= low && low <= rate.rate && rate.rate <= high && high <= 1, rate.label);
      assert.ok(near(rate.pct, rate.rate) && near(rate.ci95_pct[0], low) && near(rate.ci95_pct[1], high), rate.label);
    }
  }
});

console.log(`\n${passed} accuracy panel checks passed; no network or model calls.`);
