# Seeded-error eval (corpus v0)

Real sentences from EX-99.1 earnings press releases, plus copies with exactly one injected error each. Code, never a model, writes every gold label. `eval.py` runs `receipts.check.check()` on each item and reports how many injected errors come back ✗ (catch rate) and how many unmodified sentences get a ✗ anyway (false-alarm rate).

```bash
.venv/bin/python eval/eval.py --stub --split dev --limit 20        # offline harness check, fake check()
.venv/bin/python eval/eval.py --stub oracle --split dev            # offline: a perfect fake must score 100% / 0%
.venv/bin/python eval/eval.py --split dev --limit 40 --budget 0.50 # real verifier, Meta API spend capped
.venv/bin/python -m pytest tests/test_corpus.py -q                 # corpus invariants + harness (offline)
.venv/bin/python eval/build_corpus.py                              # rebuild v0 (cache first, <=100 SEC requests)
.venv/bin/python eval/build_corpus.py --items-only                 # rebuild items from v0_sources.jsonl (offline)
```

**Dev only during development.** `--split dev` is the default. Test-split numbers are reserved for the headline numbers (plan: Mon 11/02, run once). Don't run `--split test` unless that's the explicit ask, and never tune against it.

## What's in v0

**Sources.** 10 companies × the 3 most recent quarters that already have a filed 10-Q, so XBRL ground truth exists. That gives 30 press releases (EX-99.1 of the Item 2.02 8-K that first published each quarter). They were built from cached EDGAR submissions fetched 2026-10-27; the build used 12 live SEC requests.

| | NVDA | AAPL | MSFT | META | AMZN | GOOGL | JPM | BAC | AMD | NFLX |
|---|---|---|---|---|---|---|---|---|---|---|
| quarters (fiscal) | Q3 FY26, Q1–Q2 FY27 | Q1–Q3 FY26 | Q1–Q3 FY26 | Q1–Q3 2026 | Q3 2025, Q1–Q2 2026 | same | same | same | same | Q1–Q3 2026 |
| items | 41 | 19 | 34 | 28 | 42 | 25 | 34 | 16 | 35 | 29 |

**Items.** 321 in total, split 224 dev / 97 test: the original 303 (211 dev / 92 test) plus the 18-item basis appendix (13 dev / 5 test, `"batch": "basis5"`, see below):

| kind | total | dev | test | gold |
|---|---|---|---|---|
| unmodified original | 154 (147 + 7) | 107 | 47 | `verified` / `match` (scored as "no claim may be ✗") |
| forward-looking control | 10 | 8 | 2 | `unverifiable` / `forward_looking` |
| value | 28 | 22 | 6 | `wrong` / `value_mismatch` |
| period | 28 | 20 | 8 | `wrong` / `period_mismatch` |
| scale | 28 | 20 | 8 | `wrong` / `scale_mismatch` |
| basis | 17 (6 + 11) | 12 | 5 | `wrong` / `basis_mismatch` |
| look_ahead | 28 | 17 | 11 | `wrong` / `look_ahead` |
| entity | 28 | 18 | 10 | `wrong` / `entity_mismatch` |

**Basis appendix (Day 3, append-only).** The 1–3-number rule left basis with 6 items. AMD and Microsoft state both bases only in 4–5-number sentences, so stage 1b (`build_corpus.py --add-basis-extra`, offline, from the same 30 cached releases) adds 7 such sentences as rows with `"pool": "basis_extra"`:
- Microsoft: "Net income, on a GAAP basis, was $27.7 billion and increased 12%, and on a non-GAAP basis was $30.8 billion and increased 22% (…)". Two mutants each: "non-GAAP" becomes "GAAP" (the non-GAAP figure and its growth now claim GAAP), or "GAAP" becomes "non-GAAP". The true value is the sentence's own other-basis figure.
- AMD: "On a non-GAAP(*) basis, gross margin was 54%, operating income was $2.2 billion, …". One mutant: "non-GAAP" becomes "GAAP". The true GAAP values come from XBRL, and only figures at least 2× their written precision from it are targets or `affected_value_texts`.

Each number's basis comes from its clause, not the clause-local tagger, and is re-screened against XBRL. The appendix never changes v0: its rows are ignored by the v0 pool, its items carry `"batch": "basis5"`, and `assign_split` runs per batch. `tests/test_corpus.py` checks that building without the appendix gives exactly the committed v0 items. Use `eval.py --batch v0` for numbers comparable with Day 2, and `--batch basis5` for the appendix alone.

**Which sentences qualify.** A sentence enters the original pool only if:
- it states 1–3 financial numbers ($, %, pp, bps) about the reporting quarter;
- it has no guidance, counterfactual, year-to-date, TTM, or annual wording, and no non-financial counts;
- every number uses a metric from the verifier's vocabulary;
- every number is either consistent with the company's XBRL companyfacts or self-describing. Self-describing means it names its segment, a non-GAAP or currency-neutral basis, or its prior period.

The XBRL screen exists to drop sentences that only make sense under a section heading. For example, NVIDIA's Data Center bullet "Second-quarter revenue was $89.0 billion" and JPM's Corporate-segment "Noninterest revenue was $5.2 billion" would both look like wrong firm-wide claims, so they're excluded. JPM's managed-basis "Net revenue was $58.0 billion" is excluded for the same reason. Every pool sentence was also read by hand.

## Mutators

Each mutant has exactly one changed claim. The mutators are deterministic and seeded by `SEED = 20261028` plus the sentence id. `tests/test_corpus.py` checks that every mutant equals its original with one recorded edit applied, and that the edit is the right kind (a number, a period phrase, a basis word, or a company name).

| mutator | what changes | why the mutant is false |
|---|---|---|
| value | one number, by ≥3% **and** ≥2× its written precision (e.g. `$111.2 billion` → `$124.2 billion`, `18%` → `20%`). The new value must not appear anywhere else in the press release. | by construction |
| period | `year-over-year` ↔ `quarter-over-quarter` (and `from a year ago` ↔ `from the previous quarter`, …) on a sentence with exactly one growth figure, or the quarter name in a one-number sentence with no explicit date | XBRL shows the other period's figure differs (16 items); 12 segment-growth items are `assumed`, reviewed by hand |
| scale | `billion` ↔ `million` (or `B` ↔ `M`) on one figure | by construction (×1000) |
| basis | in "GAAP and non-GAAP X were A and B, respectively", remove one basis word, so one figure now carries the other basis | by construction: A and B differ by ≥2× precision |
| look_ahead | text unchanged; `as_of` = the 8-K's acceptance time − 1 hour | the quarter's figures weren't public yet |
| entity | the company name is swapped for another of the 10 that has the same fiscal quarter. In-text when the sentence names the company (3 items), otherwise in the context line (25 items) | XBRL value of the new company differs; only company-level metrics are used |

**Gold fields per item.**
- **Identity:** `item_id`, `ticker`.
- **What `check()` gets:** `text`, `as_of`, `context` (e.g. `"NVIDIA (NVDA), second quarter of fiscal 2027, the quarter ended July 26, 2026"`).
- **Mutation:** `original_item_id`, `mutator`, `target_value_text` and `target_span` (the mutated claim, located in `text`), `expected_status`, `expected_reason`, `expected_value` (the true value, when known), `affected_value_texts` (every claim the edit made wrong, for example all current-quarter figures for look-ahead), `edit`, `mutant_screen`, `mutation`.
- **Provenance:** `accession`, `accepted_at`, `url`, `char_start`/`char_end` (sentence offset in the extracted press-release text), `split`, `group_id`, `item_hash`.

`v0_sources.jsonl` holds the inputs for stage 2:
- `company` rows: the XBRL series used for screening.
- `document` rows: the press release, 8-K accession and acceptance time, the 10-Q that carries ground truth, and its context line.
- `sentence` rows: text, offsets, and per-number tags.

**Split.** 70/30, group-aware: an original and all its mutants share a split, so test never contains a sentence whose twin was in dev. Strata are (company, the group's rarest mutator); inside a stratum, groups are ordered by a seeded hash. The assignment doesn't depend on input order (tested).

## Metrics (`eval.py`)

- **Catch rate per mutator.** The claim matched to `target_value_text`/`target_span` comes back `wrong`. Matching goes by value text plus span, then by value. `reason✓` counts targets whose reason code also matches. `target ?`, `target ✓` and `missing` (target not extracted) explain the misses. For look-ahead, the table also shows how many targets carry `look_ahead` regardless of status. If the verifier's policy makes look-ahead `?` rather than ✗, catches will read 0 while that line stays high.
- **False-alarm rate.** Share of unmodified originals with any claim `wrong`. Also reported: claims by status, the `?` rate, and items with no claims extracted. Forward-looking controls are reported separately: none may be ✗, and every claim should be `?`.
- **Latency.** p50/p95/max over fresh (uncached) calls.
- **Cost.** API-reported token usage observed by wrappers on the openai SDK's `responses.create` / `chat.completions.create`, priced using the configured model rates (Spark $1.25 / $0.15 cached / $4.25 output per M tokens). This is an application-side estimate, not an independently reconciled provider invoice; missing usage can undercount charges.
- **Caching.** Results are cached in `data/eval_cache/<fingerprint>/<item_hash>.json`. The fingerprint hashes `receipts/**/*.py`, `data/facts.sqlite` and every `data/pr_facts/<TICKER>/*.jsonl` (recursive since Day 3; the Day 2 glob missed the ticker folders), so any verifier or evidence change re-runs. Extraction calls are cached separately by request hash, so a rerun costs nothing unless an item's text or the extraction prompt changed.
- **Budget guard.** It reserves `--est-cost-per-item` (default $0.005, adapted upward once real spend is seen) for every item in flight. It stops starting items when spend plus reservations would pass `--budget`, and reports skipped items.
- **Concurrency and run records.** At most 8 items in flight. If the verifier isn't thread-safe, affected items are rerun sequentially. Each run writes `data/eval_runs/<UTC>-<split>-<verifier>.json`, with per-item outcomes for error analysis.

## Known limits of v0

- **Basis is 17 items from 3 companies.** v0's 6 NVIDIA items, plus the appendix's 11 from AMD and Microsoft. Only NVIDIA writes "GAAP and non-GAAP … A and B, respectively" within 1–3 numbers. Other companies don't state both bases in one sentence of these 30 releases.
- **Period has no MSFT or GOOGL items.** Their releases say "increased 18%" without a comparison phrase. Period is mostly YoY→QoQ: 27 comparison swaps, 1 quarter rename.
- **Entity is mostly a context swap (25/28).** Press releases rarely name the company inside a figure sentence. A verifier that ignores the context's company will miss these.
- **Mutants that could accidentally stay true.**
  - Value mutants are ≥3% off.
  - Scale mutants are ×1000.
  - Basis mutants use the release's own other-basis figure.
  - XBRL-screened period and entity mutants are checked against the true figure.
  - The 12 `assumed` period mutants (segment growth YoY→QoQ: AMD and Amazon segments, NVIDIA Data Center) have no XBRL check. On 2026-10-27 I checked each by hand against the prior-quarter column of the release's own segment table, or the adjacent corpus release. All are false; the closest is Amazon North America Q3'25 (claimed 11% QoQ, actual about 6%, 4.8 pp away). Rebuilding v0 doesn't re-check these, so they remain the residual risk.
- **Hard but fair originals.** The pool keeps true sentences that a verifier can still trip on: constant-currency or F/X-neutral growth, "over $28 billion", prior-period comparisons ("compared with $167.7 billion in second quarter 2025"), and non-GAAP labels ("managed revenue"). A ✗ on any of them is a real false alarm.
- **Circularity.** The press-release table layer reads the same EX-99.1 exhibits. Corpus sentences come from the narrative and the evidence from the tables, so this isn't a self-check, but the two are correlated. XBRL from the later 10-Q is independent.

# Trap drafts (corpus trap_v1)

Realistic AI-written earnings stories with subtle planted errors: a harder, document-level test than v0's single sentences. **Muse Wonder proposes, code decides.** `muse-wonder-1.0` (Standard mode, `reasoning.effort` low, strict JSON schema) writes each story from a fact sheet and lists the errors it says it planted. `eval/build_traps.py` then re-reads every figure in the story against the evidence store and writes every gold label itself.

```bash
.venv/bin/python eval/build_traps.py --sheets                       # fact sheets and call plan, no API
.venv/bin/python eval/build_traps.py --validate-dev                 # original Wonder cache + store, dev only ($0)
.venv/bin/python eval/build_traps.py --dry-run                      # same read-only check for the frozen corpus
.venv/bin/python eval/eval.py --corpus trap_v1 --stub oracle        # offline: must score 100% / 0
.venv/bin/python eval/eval.py --corpus trap_v1 --budget 0.30        # dev split, real verifier
.venv/bin/python -m pytest tests/test_traps.py -q                   # validator, scorer, split (offline)
```

**Dev only.** Same rule as v0: `--split dev` is the default, and the 7 test drafts are reserved for the headline numbers. The two demo drafts in `examples/` are dev drafts.

The committed split is frozen. `--validate-dev` reconstructs each dev draft's original fact sheet, loads the
exact Wonder response named by its recorded request hash, and compares the code-confirmed labels with the
committed row. It does not call a model or validate held-out rows. Missing response caches or label drift fail
the check without rewriting the corpus. `--json-out` can save its audit summary; the latest is
[`trap_dev_validation.json`](trap_dev_validation.json). A fresh clone needs the original local Wonder caches
to reproduce this audit; the corpus and the normal dev evaluator remain available separately.

Generation refuses to overwrite a frozen corpus. An incomplete initial build (missing cache or budget stop)
also leaves corpus files intact. New generated drafts belong in a separately versioned corpus.

## How a draft is made

1. **Fact sheet** (offline, from `data/facts.sqlite` and `data/pr_facts`). For each company, its 2 most recent quarters. The draft's dateline (`as_of`) is the earnings 8-K's acceptance time plus 2 hours. Every item has a fact id:
   - CURRENT: revenue, YoY and QoQ growth, net income and its YoY growth, diluted EPS, operating income, gross or operating margin, 1-2 segments (the first with YoY growth), and the non-GAAP twin of any metric whose two bases differ;
   - PRIOR: previous-quarter and year-ago figures;
   - PEER: a rival's latest quarter that was public at `as_of`;
   - LATER: the rival's quarter that was *not* yet public (look-ahead bait), when one exists;
   - GUIDANCE: the release's revenue outlook sentence, read from the cached EX-99.1 (NVDA, AMD, META, AMZN, NFLX).

   CURRENT, PRIOR and PEER use only evidence accepted at or before `as_of`.
2. **Generation.** Two stories per call (a company's two quarters), each with 3 requested error types assigned so the six types stay balanced. Responses are cached by request hash in `data/trap_cache/wonder/`. Every call's usage and cost go to `data/trap_cache/ledger.jsonl`, and `--budget` caps the ledger total.
3. **Code decides.** Every `$`/`%` figure is found by regex and read from its own words: metric, segment, company (the nearest one named before it), period (current, a prior-period cue, or an explicit label such as "quarter ended June 30, 2026"), comparison (YoY/QoQ), basis (GAAP/non-GAAP) and forward-looking. Then:
   - **A planted error** must quote the story verbatim. Typography is repaired; a quote that is another sentence is re-anchored on its value when that value is unique. The words decide which fact it claims; when Wonder put the *source* fact in `fact_id` (12 times, all period swaps), code used the words. The value must miss every filed version of that fact by more than **2× the written rounding**; without a basis word, it must miss both bases. Code assigns the type in the verifier's order: look_ahead, period (same metric within 13 months), scale, basis, entity (a company on the sheet or named in the story), else value. A planted "error" that is true is demoted to a true claim.
   - **Every other figure** must match a fact its words agree with, within rounding. A figure that matches no evidence at all is an accidental error and joins the gold (none occurred). The draft is rejected if a figure sits between 1× and 2× the rounding, can't be read, or has a value that belongs to a different fact than its words say.
   - **Rejected outright:** non-financial numbers, hedges, figures in words, guidance figures that aren't in the release, and fewer than 2 confirmed errors.
   - **Repair** (the only edit code makes): a planted error that contradicts a true figure elsewhere in the story, sits next to give-away wording ("8.0% year over year … sequential momentum"), or is an implausible scale slip ("Reality Labs revenue was $431 billion") has its sentence deleted, and the shorter story is validated again.
4. **Split.** 70/30 by sheet, so both drafts of a sheet share a split. Companies are visited in a seeded order (`SEED = 20261029`) so test spans many companies. The split is order-independent (tested).

## What's in trap_v1 (built 2026-10-27)

- **Generation:** 13 Wonder calls, $1.0697. That counts one superseded single-story call from prompt v1, 10 first-round calls and 2 top-up calls. Each call took 22–37 s, used 1.9–2.2K input and 3.3–5.0K output tokens (2.3–4.1K of them reasoning), and cost $0.07–0.11 for two stories.
- **Stories:** 24 written, **22 accepted** (148–205 words, mean 178), 2 rejected. JPM Q1 'a' and META Q2 'a' fell to 1 confirmed error after code removed self-contradicting or give-away sentences; both sheets got a second story ('b') that passed.
- **Repairs:** 6 accepted drafts had 1 sentence dropped. The top-up prompt (v2.2) told Wonder never to state a corrupted figure's true value, and its 4 stories needed no repair.

| | drafts | errors | period | scale | basis | entity | value | look_ahead | true figures (+ guidance) |
|---|---|---|---|---|---|---|---|---|---|
| dev | 15 | 40 | 8 | 7 | 4 | 7 | 8 | 6 | 112 + 13 |
| test | 7 | 20 | 2 | 4 | 4 | 4 | 4 | 2 | 52 + 10 |
| all | 22 | 60 | 10 | 11 | 8 | 11 | 12 | 8 | 164 + 23 |

All 60 errors are Wonder's planted ones as relabeled by code. Code's type agreed with Wonder's for all 60 after the repairs; none were demoted and there were no accidental errors. Every accepted draft was also read by hand on 2026-10-27, which is how the two over-broad diagnoses noted under Known limits were found.

**Gold fields per draft:**
- **Input:** `item_id`, `ticker`, `sheet_id`, `text` (headline + story), `as_of`, `context` (v0's format).
- **Errors:** `planted` and `accidental`. Each error has `type`, `wonder_type`, `value_text`, `span` (code's offsets), `sentence_span`, `quote`, `fact_id`, `claimed` (the fact with its evidence accessions and `first_public`), `true_value`, `matched_evidence` (whose figure it really is), `expected_status`, `expected_reason`.
- **Everything else:** `true_claims` (every other figure, with its fact id and `forward_looking`), `sentences`, `validation_log`, `generation` (model, effort, prompt version, request hash, usage, cost), `split`, `item_hash`.

## Scoring (`eval.py --corpus trap_v1`)

- **Catch rate per error type:** every claim the extractor makes at the error's value span is collected (a both-bases figure may become two claims). The error is caught if any of them is ✗; `reason✓` means one of those ✗ carries `expected_reason`.
- **Conflicting verdicts:** an error with both ✗ and ✓ is still caught under that definition, but is also counted separately in `✗+✓`; it must not be mistaken for a clean correction.
- **False alarms:** true figures with a ✗, and true sentences (no gold error inside) with any ✗.
- **Approximate figures:** ≈ has its own count and is never included in the ✓ count.
- **'?' rate:** over all claims, plus separate counts for true figures and guidance.
- **Cost and cache:** the same ledger, cache, budget guard and run records as v0.

**Dev recheck, 2026-10-27** (verifier fingerprint `99403132fe75…`; Muse Spark 1.3 extraction reused from cache; all 15 drafts reverified with result caching disabled; **$0.0000 new model spend**, no skipped drafts or errors). This run's p50 was 0.52 s with cached extraction, not a live model latency measurement. The earlier live extraction run cost $0.238 including its smoke check and had p50 6.7 s.

| error type | n | caught | reason✓ |
|---|---|---|---|
| period | 8 | 8 | 8 |
| scale | 7 | 7 | 7 |
| basis | 4 | 4 | 4 |
| entity | 7 | 7 | 3 |
| value | 8 | 8 | 8 |
| look_ahead | 6 | 6 | 6 |
| **all** | **40** | **40 (100%)** | **36 (90%)** |

- **False alarms:** 0 of 112 true figures, 0 of 58 true sentences.
- **Conflicting ✗/✓ verdicts:** 0 of 40 errors. All 15 drafts had every planted error caught.
- **Gold audit:** 15/15 dev rows revalidated from their original Wonder caches; 40 errors, 112 true figures and 13 guidance figures. Only two secondary `types_seen` lists changed after excluding future evidence. Primary labels, draft text and offsets are unchanged; all held-out raw lines are byte-identical (see `trap_dev_validation.json`).
- **'?' rate:** 16 of 167 claims (9.6%): all 13 guidance figures, 1 true figure, and 2 duplicate claims.
- **Entity reasons:** the 4 entity errors that came back `value_mismatch` are a rival's segment figure or a rival's figure for a different quarter, for example Alphabet's June-quarter EPS in a Netflix September-quarter story. The verifier names another company only for the same metric and quarter.

## Known limits of trap_v1

- **Small.** 40 dev errors, with 4–8 per type. One miss moves a type's rate by 12–25 points.
- **The verifier changes underneath it.** Other work on `receipts/` changes the fingerprint, so rerun dev (extraction is cached) before quoting numbers.
- **Templated.** Wonder writes in a recognizable wire-story template, and some stories are less subtle than a human trap. Repeated period swaps all take the form "QoQ rate called year over year", and scale slips are billion to million.
- **Reader heuristics.** Code's reading of company, segment and period is regex-based and tuned to the prompt's house style. Anything it can't read rejects the draft rather than producing a label, and the accepted drafts were read by hand. That hand read found and fixed two over-broad diagnoses: a drifted margin that equaled Amazon's, and a 2023 figure.
- **Dateline.** Every draft has an `as_of`, 2 hours after its release. Look-ahead errors are always a rival's figure from a release that came later.
