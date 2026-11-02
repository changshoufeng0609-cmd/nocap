# Frozen spot500 v1: source-only spot check

This is a separate, small spot check of the current 500-company system. It does
not update, repeat, or pool results with the October 29 held-out evaluation.
No model or provider was called while constructing or freezing these items.

## Sampling frame and source screening

The builder uses Python `Random(20261101)` and excludes AAPL, AMD, AMZN, BAC,
GOOGL, JPM, META, MSFT, NFLX, NVDA and TSLA. The archived S&P record supplies
company identities and GICS sub-industries (the archived record has 499 companies
and predates the separate Exxon Mobil addition); an explicit grouping in
`build_spot500.py` maps those labels to the eleven broad GICS sectors.

All financial inputs come from the existing offline SEC archive. The snapshot
is opened read-only for company, filing and fiscal-calendar metadata only;
its financial fact values, extracted release tables and verification results
are not used for selection or labels. A release must be paired with a stored
10-Q whose accession has numeric observations in archived SEC companyfacts.
Companyfacts is the stored XBRL source; a separately archived filing XBRL XML
is hashed when present, but is not required for every accession. Compressed
archive records are checked against their indexed uncompressed SHA-256.

For each eligible company, consider its latest two stored release/10-Q pairs
with an EX-99.1 filename or SEC exhibit header and matching printed quarter/date. This is a
recent-quarter check, not a historical eight-quarter benchmark; 10-K-only Q4
releases are outside the frame. The manifest records the exact quarters.

Reuse `build_corpus.py` HTML block extraction, sentence splitting, number tags,
written-precision tolerances, and stage-1 rejection rules. Reject unrecognized
numeric text, unsupported contexts, counterfactuals, mixed actual/guidance
sentences and XBRL mismatches. Additional conservative gates reject spelled-out
financial quantities the tagger cannot cover, unbound respectively clauses,
monthly fragments, EPS adjustment amounts and ambiguous glued numeric footnotes. Every retained actual number must be consistent
with companyfacts or explicitly self-describing, such as an adjusted measure
or named segment. Self-description establishes the intended meaning, not that
the current verifier has supporting evidence for it.

Extensions cover gross, operating and net margins; margin changes in basis or
percentage points; dated cash, total assets, total debt and outstanding shares;
and bank total revenue. Margins use companyfacts numerator/revenue arithmetic,
bank revenue can use net interest income plus noninterest income, and instant
figures require the balance-sheet date. The five named variable-week companies
have an extended quarter-duration window for 12-, 16- and 17-week periods.

Reserve at least three banks, two REITs/utilities, and two of COST, KR, DPZ, PEP
and AZO with eligible sentences. Reserve source-supported basis/period mutation carriers, five guidance-bearing companies, and available newer-feature coverage, then
fill the least-represented sector until thirty companies are selected. Each
pool starts in stable identifier order before seeded random sampling. This is
a coverage-constrained sample; it is not a simple random sample of all 500
companies. The variable-week company stratum need not mean each sampled quarter
itself lasts 16 or 17 weeks.

## Items and provisional gold

Reuse the value, period, scale, basis, look-ahead and entity mutators from
`build_corpus.py`. The basis extension can replace one explicit adjusted/non-GAAP
label with GAAP when companyfacts establishes a different GAAP value. Direction
reverses one local growth/margin-change word, such as rose/fell or
expanded/contracted. It requires a screened, nonzero source change with the
same sign; it preserves every number and rejects level statements and shared
change verbs that would affect another figure. Basis mutations additionally require
a plain level statement, without adjustment impacts, excluded costs, prior-period
comparisons or other basis labels. Bound thresholds and qualified exclusions are
not mutation targets; segment qualifiers survive intervening basis adjectives.

Choose five mutants per type, considering scarce source-supported types first.
At most two mutants share an original. Fill original coverage for each selected
company and available newer features, then select five guidance controls from
different sampled companies. This selection depends only on source tags and
mutation feasibility. No model extraction, verification output, catch rate or
reason accuracy is consulted. Context/entity/time edits are restricted to a
single numeric claim; period/entity mutants require a different available XBRL
comparison, rather than an assumed mismatch.

Every numeric claim has an exact frozen text span, intended metric/basis, role,
expected status and reason. Originals and unchanged actual figures target
`verified/match`; guidance targets `unverifiable/forward_looking`; the single
mutated claim targets `wrong` with the appropriate reason. Look-ahead is stored
as `wrong/look_ahead`, which the UI renders as ◷. These are machine-made,
provisional labels, with no independent human review. Source truth and current
evidence availability are separate: a true figure returned `?` remains an
abstention, not a caught error or a false alarm.

## Freeze, runner and denominators

`spot500_manifest.json` records the baseline commit, seed, procedure, source
record hashes, selection frame fingerprint, sampled company/quarter records,
per-item and corpus hashes, and counts by company, sector, feature and mutator.
The corpus and manifest are deterministic UTF-8 files without build timestamps.
A second build must reproduce both files byte for byte. Existing frozen files
may only be rebuilt identically; changed bytes require a separate version.

`spot500.py` checks the freeze before running. Live mode uses the actual
`receipts.check.check_detailed`, current deterministic verifier, and Standard
Meta `muse-spark-1.3` extraction. It uses a fresh extraction cache, the existing
harness budget guard and SDK usage ledger, plus the existing attempt-ledger
mechanism with a fixed request whitelist and one dispatch per unique request.
An original and its look-ahead mutant can reuse the same within-run extraction;
verification is still separate for each cutoff. No previous evaluation output
is reused. The runner reads the API key from the shell, never from `.env`.

Every completed, failed or budget-skipped item remains in the result record
and denominators. Finished item records are flushed to a separate append-only
JSONL log. Reusing an output path or its `.run` directory is refused, including
an interrupted attempt. Source and evidence hashes are retained before/after.

Report counts and fractions overall, by mutator and by overlapping source
feature: planted errors caught, correct expected reasons, false alarms on
original items and true figures, `?` on true figures, and guidance marked
verified. Missing targets, extra/conflicting claims, errors and budget skips
are reported rather than discarded. Feature totals overlap and must not be
summed into a new denominator. The oracle stub emits the gold records and must
score 100% by construction; it tests scoring and retention, not product quality.

The live estimate uses `eval/eval.py::DEFAULT_EST_COST_PER_ITEM` ($0.005/item).
The runner defaults to a $0.95 admission cap and refuses a cap of $1 or more.
Actual usage and retained reservations are reported separately from the
estimate. A capped or failed run remains incomplete; do not rerun to improve
its score.

## Limits

The sample is small, correlated across originals/mutants, companies and filings,
and deliberately constrained to source-supported financial sentences. It covers
the newest one or two paired quarters only, rather than the full time series
or every new company. Some accepted adjusted or segment figures are only
self-describing. Labels and extraction matching rules are machine-made and have
not had human review; errors in source tagging or arithmetic can affect gold.
Sector balance and feature reservations also prevent a population-level
accuracy estimate. This check does not measure arbitrary drafts, all financial
numbers, long multi-company passages, or unseen future evidence coverage.

## Results of the one live run (November 1, 2026)

- **Run:** 2026-11-01 09:21:13–09:24:44 UTC, `live:meta:muse-spark-1.3`, [result record](spot500_runs/live-20261101.json).
  - The verifier code and evidence are those of the deployed release: private `42d48aa`, public `c99c9a0`, Production `dpl_2vPGGu27yA9Lx29ovmucC3BTwCvW`, snapshot fingerprint `1381a6507b55e7322c8b369e`.
  - The runner ran from private `19b5cb3`, which differs from `42d48aa` only in `eval/`, tests and one document.
  - Source and evidence hashes were identical before and after the run (`inputs_unchanged: true`).
- **Completed:** 75/75 items, with 0 errors, 0 budget skips and 0 missing records.
- **Planted errors:**
  - **27/35 caught (77%)** as ✗, or as ◷ for look-ahead; 22/35 with the expected reason (63%).
  - **None of the 35 was marked ✓.** The other 8 returned `?`.
- **False alarms:** **0/35 original items and 0/72 true figures** were marked ✗.
- **True figures:** **54/72 verified (75%)**; **18/72 returned `?` (25%)**, all with reason `no_evidence`.
  - Diluted EPS accounts for 8 of the 18: A (three copies through its mutants), IVZ, NRG, PLTR, PWR and WELL.
  - The other ten:
    - net margin, two ABNB figures;
    - revenue, Kroger's $33.9 billion (three copies);
    - operating margin, DIS;
    - a basis-point gross-margin change, KR;
    - net income, MPC;
    - capital returned, CFG;
    - noninterest-income growth, WFC.
- **Guidance marked verified:** **0/7**. Five returned `?` as forward-looking; two were not extracted as matching claims.
- **Overall:** status correct for 85/114 gold claims; status and reason correct for 80/114.
- **Missing, extra and conflicting claims:**
  - 3 missing: FLEX's true $607 million operating income, and two guidance figures (DIS EPS growth and PLTR's operating-income range).
  - 4 extra claims, none marked ✗.
  - 0 duplicate or conflicting matches.
- **Attempts and cost:**
  - 70 model calls for 75 items, because the five look-ahead mutants reused their originals' extraction.
  - Measured usage cost **$0.3735669** against the $0.375 estimate and $0.95 cap, with no unknown-usage reservations.
  - Tokens: 154,160 input (42,821 cached), 53,640 output (39,935 reasoning).

| Mutator | Planted | Caught (✗/◷) | Expected reason | Marked ✓ | `?` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `basis` | 5 | 5 | 4 | 0 | 0 |
| `direction` | 5 | 3 | 3 | 0 | 2 |
| `entity` | 5 | 4 | 0 | 0 | 1 |
| `look_ahead` (◷) | 5 | 3 | 3 | 0 | 2 |
| `period` | 5 | 4 | 4 | 0 | 1 |
| `scale` | 5 | 4 | 4 | 0 | 1 |
| `value` | 5 | 4 | 4 | 0 | 1 |

**Entity mutants.** The four caught entity mutants are reported as `value_mismatch`, not `entity_mismatch`. The named company's own filing contradicts the figure, so NoCap does not say whose figure it is.

**Look-ahead and period mutants.** The `?` look-ahead and period mutants come from originals whose true figure already had no evidence (CFG capital returned, PLTR EPS).

Selected overlapping source features. Each row counts every claim in the items that carry the feature, not only claims of that metric. So `eps_diluted` items include, for example, the net-income claim in the same sentence. These are true figures verified out of true figures, and planted errors caught out of planted errors; do not sum them:

| Feature | True verified | Planted caught |
| --- | ---: | ---: |
| `revenue` | 22/26 | 16/17 |
| `growth` | 30/31 | 13/14 |
| `eps_diluted` | 15/24 | 7/11 |
| `operating_income` | 8/8 | 3/3 |
| `net_income` | 4/8 | 2/4 |
| `non_gaap` | 6/9 | 5/5 |
| `bank` | 7/9 | 2/4 |
| `balance_sheet` | 4/4 | — |
| `reit_or_utility` | 5/6 | 2/2 |
| `variable_week_company` | 4/8 | 3/3 |
| `16_or_17_week_quarter` | 0/1 | — |

All overall, per-mutator and per-feature counts are in the result record's `summary`.

**Validation.** For the oracle plumbing check, the deterministic rebuild and the Python suite, see the validation section below.

## Frozen composition and offline validation

The frozen set has **30 companies, 75 items and 114 gold claim records**: 35
originals, five of each of the seven mutators, and five guidance controls.
It covers all eleven GICS sectors, three banks (CFG, PNC, WFC), and the
variable-week companies AZO and KR. Only one sampled original is itself a
16-week quarter; the calendar-company stratum and long-quarter item count are
different. Sampled period ends range from 2025-09-30 to 2026-09-30 because the
latest *stored paired* releases can lag the current calendar quarter.

Companies: A, ABNB, AMT, APTV, AZO, BBY, CFG, CHD, CIEN, DIS, FE, FLEX, FOXA, HAL, HCA, IP, IVZ, JBHT, KR, MLM, MPC, MTD, NRG, PLTR, PNC, PWR, TKO, TWLO, WELL, WFC.

| Source feature (overlapping) | Items |
| --- | ---: |
| `16_or_17_week_quarter` | 1 |
| `balance_sheet` | 2 |
| `bank` | 8 |
| `bank_revenue` | 1 |
| `capital_returned` | 3 |
| `cash_and_equivalents` | 1 |
| `eps_diluted` | 25 |
| `gross_margin` | 4 |
| `growth` | 28 |
| `guidance` | 5 |
| `margin_change` | 4 |
| `net_income` | 7 |
| `net_interest_income` | 1 |
| `net_margin` | 1 |
| `non_gaap` | 14 |
| `noninterest_expense` | 1 |
| `noninterest_revenue` | 1 |
| `operating_income` | 8 |
| `operating_margin` | 1 |
| `reit_or_utility` | 6 |
| `revenue` | 30 |
| `shares_outstanding` | 1 |
| `total_assets` | 1 |
| `total_debt` | 1 |
| `variable_week_company` | 6 |

- Independent complete source rebuild: corpus and manifest byte-identical.
- [CLI oracle record](spot500_runs/oracle-cli-20261101.json): 75/75 completed;
  114/114 status-and-reason matches, 35/35 targets and reasons, zero original
  false alarms, 7/7 guidance claims unverifiable, zero skips/errors/extra claims.
  This is 100% **by construction**, not a NoCap quality measurement.
- Reusing the same CLI output was rejected with `FileExistsError`; its SHA-256
  stayed unchanged. A preliminary function-level oracle record is also retained.
- Live estimate: **75 × $0.005 = $0.375**; admission cap **$0.95**. This work
  incurred **zero provider calls and $0 spend**.
- The offline suite is run with `eval/spot500_offline_tests.py`, which blocks
  network and dotenv-file access and deselects the one existing test that
  deliberately reads a temporary `.env`. Existing data-dependent skips remain.
  Final suite: **2,977 passed, 4 skipped, 1 deselected** (24.96 seconds; Python 3.12).
  Validation hashes are recorded in [validation JSON](spot500_validation.json).

With `MODEL_API_KEY` already exported in the shell, run once from this worktree:

```bash
/Users/shou-fengchang/Desktop/Meta/.venv/bin/python eval/spot500.py --live --evidence-dir /Users/shou-fengchang/Desktop/Meta/data/snapshot --budget 0.95 --output eval/spot500_runs/live-20261101.json
```

That one run is reported above, failures and abstentions included. It is not to be
rerun to improve its score; any later fix is measured on other sets.

# Frozen spot500 v2: a second source-only spot check

v1 above was run once, on November 1, and is never rerun. Fixes for general gaps
it revealed are being made. v2 is a second frozen set of items nobody has seen.
It measures the fixed system once, live, after the November 2 morning deploy.
Its result is reported beside v1's, never pooled with it, and v1 is not replaced.

No model or provider was called while building or freezing v2. NoCap's verifier,
extraction and live mode have never run on its items. As in v1, selection and
labels use source data only, never NoCap outputs. Nobody tunes on v2.

## Procedure deltas

`eval/build_spot500.py --set v2` runs the v1 procedure unchanged: the same
sampling frame and source screening, the same strata and reservations, the seven
mutators, the counts, the gold rules, and the freeze and hashes. The v2
manifest's source-pool fingerprint equals v1's (`7b610743…`), so the quarters
and screened sentences are identical. Only these differ:

1. **Seed:** `Random(20261102)` instead of `Random(20261101)`, wherever v1 used
   its seed: company sampling, mutation shuffling and the legacy mutators' keyed
   choices.
2. **Exclusions:** v1's 30 sampled companies are excluded on top of the original
   11, for 41 in all. They leave the eligible pool before every reservation and
   the sector fill, which leaves 353 eligible companies (v1: 383).
3. **One quota the exclusions exhaust:** the variable-week stratum is five named
   companies, and only AZO, KR and PEP have eligible sentences in the frame.
   With v1's AZO and KR excluded, only PEP remains, so v2 reserves one
   variable-week company instead of two. The bank (three) and REIT/utility (two)
   quotas are met as in v1.

**Disclosed overlap with the fixes' tests.** Three v2 companies (AMGN, TFC and RF) also appear in the unit tests
of the wording fixes merged on November 1 (`54be292`).

Those tests check general wording rules, using synthetic sentences built on these companies' filed values. The tests
and rules were written before v2 existed, and the v2 companies were then drawn by seed, without regard to them.

v2's items are the companies' real release sentences, which neither the fixes nor their authors saw. From November 1
on, new tests avoid all 30 v2 companies.

The builder now takes `--set`; v1 is the default and still rebuilds byte for
byte. The v1 manifest records the hash of the builder file that froze it
(`eval/build_spot500.py` at `050be2e`). That file has since been parameterized,
so the v1 configuration carries its recorded hash; a test checks the hash against
the git blob. Every other v1 field is recomputed, including the hashes of
`spot500_sources.py` and `build_corpus.py`. v2 records the hash of the
parameterized builder that froze it.

## Composition

The frozen set has **30 companies, 74 items and 119 gold claim records**: 34
originals, five of each of the seven mutators, and five guidance controls (CNP,
DE, RL, SWKS, TSN). As in v1, 21 originals carry the 35 mutants, 14 of them two
each. The gold claims are 75 true figures, 35 targets and 9 guidance figures
(v1: 72, 35 and 7).

Companies: AMGN, AMP, BE, CF, CHTR, CNP, COF, DE, DOC, EXE, FITB, ICE, KEY, KIM,
MCHP, MSCI, PEP, PSX, RF, RL, SWKS, TFC, TROW, TSN, TTWO, UBER, ULTA, UNH, VMC,
WTW. None is a v1 company or one of the original 11 exclusions.

How v2 differs from v1:

- **Sectors:** v2 covers all eleven sectors, but Financials has 10 of the 30
  companies (v1: 4) and Utilities has one, CNP (v1: 2).
  - The reservations, not the sector fill, put the ten financials in. They are
    the three bank-quota picks (FITB, KEY, RF), all five basis-supporting picks
    (AMP, COF, ICE, TFC, TROW), WTW for guidance and MSCI for shares outstanding.
  - The five basis picks were drawn from nine eligible basis-supporting
    companies, six of them financials.
  - The sector fill reached 30 before Utilities got a second company.
- **Banks:** four, FITB, KEY, RF and TFC (v1: CFG, PNC and WFC).
- **Variable-week calendars:** PEP only, in one item (v1: AZO and KR, in six
  items). No sampled quarter lasts 16 or 17 weeks (v1: one).
- **Features:**
  - More balance-sheet items (9 against 2), cash items (6 against 1) and
    bank-revenue items (6 against 1).
  - Fewer diluted-EPS items (19 against 25) and operating-income items (2
    against 8).
  - No capital-returned items (v1: 3) and no bank net-interest-income or
    noninterest items (v1: one of each).
- **Quarters:** sampled period ends range from 2025-12-31 to 2026-09-30 (v1:
  2025-09-30 to 2026-09-30).
- **Mutation candidates among the sampled companies:**

  | Set | value | scale | direction | look-ahead | period | entity | basis |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | v2 | 109 | 72 | 57 | 28 | 20 | 12 | 10 |
  | v1 | 102 | 61 | 37 | 38 | 14 | 18 | 12 |

Items by overlapping source feature and mutator are below. A row counts every
item that carries the feature, so rows overlap and must not be summed. `·` means
none.

| Source feature (overlapping) | Original | `value` | `period` | `scale` | `basis` | `look_ahead` | `entity` | `direction` | Guidance | v2 items | v1 items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `16_or_17_week_quarter` | · | · | · | · | · | · | · | · | · | 0 | 1 |
| `balance_sheet` | 5 | · | 1 | 1 | · | 2 | · | · | · | 9 | 2 |
| `bank` | 4 | · | 3 | · | · | · | 2 | 1 | · | 10 | 8 |
| `bank_revenue` | 2 | · | 2 | · | · | · | 1 | 1 | · | 6 | 1 |
| `capital_returned` | · | · | · | · | · | · | · | · | · | 0 | 3 |
| `cash_and_equivalents` | 2 | · | 1 | 1 | · | 2 | · | · | · | 6 | 1 |
| `eps_diluted` | 9 | 1 | 1 | · | 3 | 1 | 2 | 1 | 1 | 19 | 25 |
| `gross_margin` | 1 | 1 | · | · | · | · | · | 1 | · | 3 | 4 |
| `growth` | 11 | 2 | 4 | 2 | · | · | 3 | 4 | 1 | 27 | 28 |
| `guidance` | · | · | · | · | · | · | · | · | 5 | 5 | 5 |
| `impact` | 1 | · | · | · | · | · | · | · | · | 1 | 0 |
| `margin_change` | 2 | 1 | · | · | · | · | · | 1 | · | 4 | 4 |
| `net_income` | 4 | · | · | · | 1 | · | · | · | 1 | 6 | 7 |
| `net_interest_income` | · | · | · | · | · | · | · | · | · | 0 | 1 |
| `net_margin` | 1 | · | · | · | · | · | · | · | · | 1 | 1 |
| `non_gaap` | 8 | · | · | · | 5 | · | · | · | 2 | 15 | 14 |
| `noninterest_expense` | · | · | · | · | · | · | · | · | · | 0 | 1 |
| `noninterest_revenue` | · | · | · | · | · | · | · | · | · | 0 | 1 |
| `operating_cash_flow` | 1 | · | · | · | · | · | · | · | · | 1 | 0 |
| `operating_income` | 1 | · | · | · | · | · | · | · | 1 | 2 | 8 |
| `operating_margin` | 2 | · | · | · | · | 1 | · | · | · | 3 | 1 |
| `opex` | 1 | · | · | · | 1 | · | · | · | · | 2 | 0 |
| `reit_or_utility` | 3 | · | · | · | · | · | · | · | 1 | 4 | 6 |
| `revenue` | 10 | 3 | 3 | 4 | · | 1 | 3 | 3 | 1 | 28 | 30 |
| `shares_outstanding` | 1 | · | · | · | · | · | · | · | · | 1 | 1 |
| `tax_rate` | · | · | · | · | · | · | · | · | 1 | 1 | 0 |
| `total_assets` | 1 | · | · | · | · | · | · | · | · | 1 | 1 |
| `total_debt` | 1 | · | · | · | · | · | · | · | · | 1 | 1 |
| `variable_week_company` | 1 | · | · | · | · | · | · | · | · | 1 | 6 |
| **All items** | 34 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 74 | 75 |

## Freeze and offline validation

- **Corpus:** `eval/corpus/spot500_v2.jsonl`, SHA-256
  `50552717a00912d4199553796438d4d395247ad614ed533261de4119af067072`.
- **Manifest:** `eval/spot500_v2_manifest.json`, SHA-256
  `f5ddd98250554631e1c1a6c9c7461bb2da5559c6277d46d775325e14afef703f`.
  - It records the seed, the exclusions and their source (v1's manifest and
    corpus hashes), the procedure and its changes from v1, and the per-item,
    source-record and builder hashes.
  - It also records counts by company, sector, feature and mutator.
- **Rebuilds:**
  - An independent complete source rebuild, in a separate process, produced
    both v2 files byte for byte.
  - Rebuilding v1 with the parameterized builder reproduced v1's corpus
    (`7b1c119b…`) and manifest (`3afa85b0…`) byte for byte.
- **Oracle:** only the oracle stub has run on v2. Its
  [CLI record](spot500_runs/v2-oracle-20261101.json):
  - 74/74 items completed, with 119/119 status-and-reason matches and 35/35
    targets and reasons.
  - There were zero original false alarms, 9/9 guidance claims unverifiable, and
    zero skips, errors or extra claims.
  - This is 100% **by construction**. It tests the runner's freeze checks,
    scoring and retention, not NoCap.
  - Reusing the output was refused with `FileExistsError`, and its SHA-256 stayed
    unchanged.
- **Isolation:** the builds and the oracle ran under an audit hook that refused
  network access, dotenv files and writes under the source data root. There were
  zero provider calls and $0 spend.
- **Tests:** the offline suite ran with `eval/spot500_offline_tests.py`, with
  `SPOT500_DATA_ROOT` set so that the full source rebuild test also ran.
  - Final suite: **3,111 passed, 4 skipped, 1 deselected**. The skips are
    existing data-dependent skips. As in v1, the deselected test is the one
    that deliberately reads a temporary `.env`.
  - Validation details are in the [v2 validation JSON](spot500_v2_validation.json).

## Runner rules and the one live run

`eval/spot500.py --set v2` loads only files labelled `spot500_v2` and applies
v1's freeze checks. v1 remains the default set, and its behavior is unchanged.

- A v2 record must be `eval/spot500_runs/v2-oracle-<date>.json` or
  `eval/spot500_runs/v2-live-<date>.json`.
- Each output and its `.run` directory are single use.
- A live v2 run is refused once any `v2-live-*` record exists, finished or
  interrupted.

The live estimate is **74 × $0.005 = $0.37**, under the $0.95 admission cap. The
runner refuses a cap of $1 or more.

Run it once, after the November 2 morning deploy. Use a checkout that has the
deployed verifier code and these v2 files. Point `--evidence-dir` at the
deployed snapshot, and export `MODEL_API_KEY` in the shell first:

```bash
/Users/shou-fengchang/Desktop/Meta/.venv/bin/python eval/spot500.py --set v2 --live --evidence-dir /Users/shou-fengchang/Desktop/Meta/data/snapshot --budget 0.95 --output eval/spot500_runs/v2-live-20261102.json
```

Report that run the way v1's is reported, failures and abstentions included,
beside v1 and not pooled with it. It is not rerun to improve its score.

## Results of the one live run (November 2, 2026)

**Run.** 2026-11-01 16:46:36–16:50:14 UTC (November 2, 00:46 Taipei), `live:meta:muse-spark-1.3`, [result record](spot500_runs/v2-live-20261102.json).

**Code and evidence measured.**
- The deployed November 2 release: private `f22e56c`, public `1de7b7b`, Production `dpl_EAfhreQrZdaWr3HJg4q3jHuuVVsR`.
- That release includes every wording and false-alarm fix merged after v1.
- The runner checked that `receipts/` and `data/snapshot` equal the exported commit, and the input hashes were identical before and after (`inputs_unchanged: true`).
- v2 is a different sample from v1 (more financials and balance-sheet items, fewer diluted-EPS items), so the two runs are not a before/after comparison of one item set.

**Completed.** 74/74 items, with 0 errors, 0 budget skips and 0 missing records. 69 model calls; measured usage cost **$0.38824965** against the $0.37 estimate and the $0.95 cap.

**Planted errors.**
- **27/35 caught (77%)** as ✗, or ◷ for look-ahead.
- 22/35 caught with the expected reason.
- **1/35 was marked ✓**; the other 7 returned `?`.
- The ✓ is a period mutant: "diluted EPS increased 8 percent compared to first quarter of 2025".
  - The verifier ignored the stated comparison base and checked year-over-year growth, which is 8.5%.
  - The extraction also took the item context's quarter.

**False alarms.** **2/34 original items, and 4/75 true figures marked ✗.** Two sentences account for all four:
- *"Gross margin of 33.4% increased 668 basis points compared to 26.7%."* The 26.7% names no period. The verifier read it as the current quarter and called it a Q2 2025 value. This one sentence appears in three items: the original, plus the direction and value mutants, where it is an unchanged true figure.
- CenterPoint's *"compared to $0.45 per diluted share in the comparable period of 2025"*. The verifier resolved "the comparable period of 2025" as the full year and compared the figure with annual EPS. This claim was extracted with a span the matcher counts as an extra claim (`extra_wrong` 1), and it is also its item's one false alarm.

**True figures.**
- **48/75 verified (64%)**, **21/75 returned `?` (28%)**, and 4/75 were marked ✗.
- 7 true figures were not extracted as matching claims; the six net-income figures and the non-GAAP misses are among them.
- **Guidance marked verified: 0/9.** 4 returned `?` as forward-looking, and 5 were not extracted as matching claims.

**Overall.** Status correct for 79/119 gold claims; status and reason correct for 74/119. There were 13 extra claims (1 marked ✗, the CenterPoint figure above), 12 missing claims and 0 duplicate or conflicting matches.

| Mutator | Planted | Caught (✗/◷) | Expected reason | Marked ✓ | `?` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `basis` | 5 | 2 | 1 | 0 | 3 |
| `direction` | 5 | 5 | 5 | 0 | 0 |
| `entity` | 5 | 4 | 0 | 0 | 1 |
| `look_ahead` (◷) | 5 | 4 | 4 | 0 | 1 |
| `period` | 5 | 2 | 2 | 1 | 2 |
| `scale` | 5 | 5 | 5 | 0 | 0 |
| `value` | 5 | 5 | 5 | 0 | 0 |

Selected overlapping source features count every claim in the items that carry the feature. Do not sum them.

| Feature | True verified | True marked ✗ | Planted caught |
| --- | ---: | ---: | ---: |
| `revenue` | 23/32 | 0 | 14/17 |
| `growth` | 27/33 | 0 | 11/15 |
| `eps_diluted` | 13/17 | 1 | 6/9 |
| `balance_sheet` | 4/5 | 0 | 4/4 |
| `bank` | 6/7 | 0 | 3/6 |
| `margin_change` | 5/9 | 3 | 2/2 |
| `non_gaap` | 2/11 | 0 | 2/5 |
| `net_income` | 2/8 | 1 | 0/1 |

**Reading v1 and v2 together.**
- Both runs caught 27 of 35 planted errors.
- v1, on the November 1 code, marked no planted error ✓ and no true figure ✗.
- v2, on the November 2 code and a fresh sample, found one false ✓ and two false-alarm sentences. All three involve a figure's comparison base: an unlabelled "compared to X", "the comparable period of <year>", and an explicit earlier base quarter.
- Non-GAAP figures and the extraction of some net-income and EPS figures remain the largest sources of `?` and misses.
- Both sets are small, correlated and machine-labelled. Neither is rerun, and their results are not pooled.

# Frozen spot500 v3: a fresh exam for the fixed verifier

v3 measures the version deployed at 02:00 on November 2: private `84b8fd4`, with six rounds of verifier fixes. The owner
approved one live run and committed to publishing its result, whatever it shows. v1 and v2 are not rerun or replaced.
v3's result is reported beside theirs, never pooled with them.

No model or provider was called while building or freezing v3. NoCap's verifier, extraction and live mode have never
run on its items. As in v1 and v2, selection and labels use source data only, never NoCap outputs. Nobody tunes on v3.

## Procedure deltas

`eval/build_spot500.py --set v3` runs the v1 procedure as v2 does, over the same sampling frame and source screening.
The v3 manifest's source-pool fingerprint equals v1's (`7b610743…`). Only these differ:

1. **Seed:** `Random(20261103)`, everywhere v1 used its seed.
2. **Exclusions:** 95 distinct companies leave the eligible pool before every reservation and the sector fill, which
   leaves 306 eligible companies (v1: 383, v2: 353). They are:
   - the original 11, plus v1's 30 and v2's 30 sampled companies;
   - 28 companies named in the verifier-fix tests added since v1, and 7 named in the lines the fixes added to the
     verifier (see the exclusion list below);
   - BAX, CSGP and TGT, which the reviewer used in probes.
3. **Quotas the exclusions exhaust**, each lowered to what remains:
   - **Variable-week companies, 2 → 0.** AZO and KR were v1 companies and PEP a v2 company. COST and DPZ have no
     eligible sentences in the frame.
   - **Basis mutants, 5 → 2.** v3's whole eligible pool holds only two basis candidates, both from SNDK (v1's pool
     held 26, v2's 14). The basis-supporting reservation takes the only two basis-supporting companies, BLK and SNDK.
   - The bank (three) and REIT/utility (two) quotas are met, as in v1.
4. **Coverage the exclusions remove, without a quota change:** no eligible company carries net margin, total debt,
   outstanding shares or a 16- or 17-week quarter any more. The procedure reserves such features only when available,
   so v3 has none of them.

The builder takes `--set v3`; v1 and v2 are unchanged and still rebuild byte for byte. Like v1, v2 now carries the
recorded hash of the builder that froze it, the git blob of `eval/build_spot500.py` at `7bdcb11`; a test checks both
pins against git. The quota caps apply to v3 only.

## The exclusion list and its method

`eval/spot500_exclusions.py` derives the list mechanically into `eval/spot500_v3_exclusions.json` (SHA-256
`2e5ecef757b5f553e531b7e11871aa0efafd8c945476e54a1671e89d91af786e`). The manifest embeds that record. The builder checks
the file's hash and its lists, and a test re-derives it from git history and the snapshot's companies table.

- **Scanned files.** `git diff --name-only --diff-filter=A 50a4eac..84b8fd4 -- tests/` lists two files. The spot500 set
  tests are dropped, which leaves `tests/test_wording_gaps.py`. No other test file changed in that range, so this one
  file holds every verifier-fix test added since v1's live run.
- **Reference table.** The snapshot's `companies` table: 500 rows of ticker, name and aliases (fingerprint
  `0a4fdf75…`).
- **Rules.** A line names a company by any of three rules:
  - **alias:** one of its aliases appears as whole words, with a capital letter, not inside a number;
  - **leading word:** the first word of its name or an alias appears that way, if the word has at least four letters
    and starts no other company's name;
  - **ticker:** the ticker appears case-sensitively as a separate token. A one-letter ticker must be quoted or in
    parentheses, and a ticker inside another company's matched name does not count.

  The rules favour recall, since over-matching only removes companies from the sample.
- **Named in the tests (28):** AKAM, AMD, AMGN, AXP, CARR, CMCSA, CNC, DIS, ED, EXR, FDS, GEHC, HPE, INTC, JPM, LEN,
  MNST, MRK, MSI, PFE, RF, SJM, SYY, TFC, TGT, TSLA, UDR, VLO.
  - Seven were already excluded: AMD, JPM and TSLA are among the original 11, DIS was a v1 company, and AMGN, RF and
    TFC were v2 companies.
  - Leading words catch names the table's aliases lack: Valero, FactSet and Akamai.
  - ED is matched only by Comcast's row label "Pro Forma Consolidated Revenue", so it is probably not a reference to
    Consolidated Edison. It stays excluded, which costs only one company.
  - The record also lists the candidates the rules rejected:
    - A, the article "A";
    - F, from "F/X";
    - Q, from "10-Q";
    - J and GE, inside "J.M. Smucker" and "GE HealthCare";
    - MMM, the "3M" in "$8,810.3M";
    - FSLR, HD and TTWO, from lowercase "first", "home" and "take".
- **Named in the fixed verifier (7):** CMCSA, DIS, ED, EL, FDS, MPC, SYY. This scan goes beyond the requested test
  scan: it applies the same rules to the lines the fixes added under `receipts/`. Only EL is new. A fix comment cites
  Estée Lauder's "Organic Net Sales, Non-GAAP", so excluding EL keeps v3 fresh for wording the fix was written for.
- **Newly excluded in v3 (24):** AKAM, AXP, BAX, CARR, CMCSA, CNC, CSGP, ED, EL, EXR, FDS, GEHC, HPE, INTC, LEN, MNST,
  MRK, MSI, PFE, SJM, SYY, TGT, UDR, VLO.

## Composition

The frozen set has **30 companies, 69 items and 115 gold claim records**. There are 32 originals; five each of the
value, period, scale, look-ahead, entity and direction mutants, and two basis mutants; and five guidance controls (AEE,
AWK, CLX, DECK, KDP). 19 originals carry the 32 mutants, 13 of them two each. The gold claims are 74 true figures, 32
targets and 9 guidance figures.

Companies: AEE, ALB, AMAT, APP, AWK, AXON, BLK, BMY, C, CCI, CLX, DECK, DLR, DVN, GWW, HST, ILMN, KDP, KO, MOS, MTB,
OMC, PHM, SNDK, TECH, TJX, TMUS, TRGP, USB, WMB. None is in any exclusion group.

How v3 differs from v1 and v2:

- **Sectors:** all eleven, with two to four companies each. Financials has four (BLK, C, MTB, USB; v2: 10). Utilities
  (AEE, AWK), Industrials, Information Technology and Materials have two each, and the other six sectors three each.
- **Banks:** C, MTB and USB (v1: CFG, PNC, WFC; v2: FITB, KEY, RF, TFC).
- **Variable-week calendars:** none (v1: six items; v2: one).
- **Basis:** two mutants, both from SNDK (v1 and v2: five each).
- **Features:**
  - More net-income items (12; v1: 7, v2: 6) and REIT/utility items (10; v1: 6, v2: 4).
  - Fewer non-GAAP items (6; v1: 14, v2: 15), diluted-EPS items (15; v1: 25, v2: 19) and revenue items (22; v1: 30,
    v2: 28).
  - First appearances: operating cash flow (6 items), free cash flow (3) and segment revenue (2).
- **Quarters:** sampled period ends range from 2025-09-30 to 2026-10-02.
- **Mutation candidates among the sampled companies:**

  | Set | value | scale | direction | look-ahead | period | entity | basis |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | v3 | 97 | 60 | 46 | 25 | 21 | 10 | 2 |
  | v2 | 109 | 72 | 57 | 28 | 20 | 12 | 10 |
  | v1 | 102 | 61 | 37 | 38 | 14 | 18 | 12 |

Items by overlapping source feature and mutator are below. A row counts every item that carries the feature, so rows
overlap and must not be summed. `·` means none.

| Source feature (overlapping) | Original | `value` | `period` | `scale` | `basis` | `look_ahead` | `entity` | `direction` | Guidance | v3 items | v2 items | v1 items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `16_or_17_week_quarter` | · | · | · | · | · | · | · | · | · | 0 | 0 | 1 |
| `balance_sheet` | 2 | 1 | · | 1 | · | 1 | · | · | · | 5 | 9 | 2 |
| `bank` | 3 | · | · | · | · | 1 | · | 1 | · | 5 | 10 | 8 |
| `bank_revenue` | 1 | · | · | · | · | · | · | · | · | 1 | 6 | 1 |
| `capital_returned` | · | · | · | · | · | · | · | · | · | 0 | 0 | 3 |
| `cash_and_equivalents` | 1 | 1 | · | · | · | · | · | · | · | 2 | 6 | 1 |
| `eps_diluted` | 8 | 1 | · | · | 2 | 1 | 1 | · | 2 | 15 | 19 | 25 |
| `free_cash_flow` | 2 | 1 | · | · | · | · | · | · | · | 3 | 0 | 0 |
| `gross_margin` | 1 | 1 | · | · | · | · | · | 1 | · | 3 | 3 | 4 |
| `growth` | 10 | · | 3 | 3 | · | 3 | 2 | 4 | · | 25 | 27 | 28 |
| `guidance` | · | · | · | · | · | · | · | · | 5 | 5 | 5 | 5 |
| `impact` | · | · | · | · | · | · | · | · | · | 0 | 1 | 0 |
| `margin_change` | 1 | 1 | · | · | · | · | · | 1 | · | 3 | 4 | 4 |
| `net_income` | 7 | · | 2 | · | · | · | 1 | 1 | 1 | 12 | 6 | 7 |
| `net_interest_income` | · | · | · | · | · | · | · | · | · | 0 | 0 | 1 |
| `net_margin` | · | · | · | · | · | · | · | · | · | 0 | 1 | 1 |
| `non_gaap` | 4 | · | · | · | 2 | · | · | · | · | 6 | 15 | 14 |
| `noninterest_expense` | · | · | · | · | · | · | · | · | · | 0 | 0 | 1 |
| `noninterest_revenue` | 1 | · | · | · | · | 1 | · | 1 | · | 3 | 0 | 1 |
| `operating_cash_flow` | 3 | 2 | · | 1 | · | · | · | · | · | 6 | 1 | 0 |
| `operating_income` | 1 | · | · | · | · | 1 | 1 | · | · | 3 | 2 | 8 |
| `operating_margin` | 1 | · | · | · | · | · | · | · | · | 1 | 3 | 1 |
| `opex` | · | · | · | · | · | · | · | · | · | 0 | 2 | 0 |
| `reit_or_utility` | 5 | 1 | · | 1 | · | 1 | · | · | 2 | 10 | 4 | 6 |
| `revenue` | 9 | · | 3 | 3 | · | 1 | 2 | 2 | 2 | 22 | 28 | 30 |
| `segment_revenue` | 1 | · | · | · | · | · | · | · | 1 | 2 | 0 | 0 |
| `shares_outstanding` | · | · | · | · | · | · | · | · | · | 0 | 1 | 1 |
| `tax_rate` | 1 | · | · | · | · | · | · | · | · | 1 | 1 | 0 |
| `total_assets` | 1 | · | · | 1 | · | 1 | · | · | · | 3 | 1 | 1 |
| `total_debt` | · | · | · | · | · | · | · | · | · | 0 | 1 | 1 |
| `variable_week_company` | · | · | · | · | · | · | · | · | · | 0 | 1 | 6 |
| **All items** | 32 | 5 | 5 | 5 | 2 | 5 | 5 | 5 | 5 | 69 | 74 | 75 |

## Freeze and offline validation

- **Corpus:** `eval/corpus/spot500_v3.jsonl`, SHA-256
  `5eed4cb0a5c7f52d99e90054e825e6fdf0ebe87eab6e2af14453ccbde4555f46`.
- **Manifest:** `eval/spot500_v3_manifest.json`, SHA-256
  `fb9844b4385a8d0b94054fd93b2cb224c4d4021097578baa9965c9eab46e1ba0`.
  - It records the seed, every exclusion group, the embedded derivation and the procedure's changes from v1.
  - It also records v1's and v2's manifest and corpus hashes, the per-item, source-record and builder hashes, and
    counts by company, sector, feature and mutator.
- **Rebuilds:**
  - An independent complete source rebuild, in a separate process, produced both v3 files byte for byte.
  - The v1 and v2 builds, run in place through the CLI with the v3 builder, were accepted unchanged: the frozen writer
    refuses any changed byte.
- **Oracle:** only the oracle stub has run on v3. Its [CLI record](spot500_runs/v3-oracle-20261102.json):
  - 69/69 items completed, with 115/115 status-and-reason matches and 32/32 targets and reasons.
  - There were zero original false alarms, 9/9 guidance claims unverifiable, and zero skips, errors or extra claims.
  - This is 100% **by construction**. It tests the runner's freeze checks, scoring and retention, not NoCap.
  - Reusing the output was refused with `FileExistsError`, and its SHA-256 stayed unchanged.
- **Isolation:** the builds, the derivation and the oracle ran under an audit hook that refused network access, dotenv
  files and writes under the source data root. There were zero provider calls and $0 spend.
- **Tests:** the offline suite ran with `eval/spot500_offline_tests.py`, with `SPOT500_DATA_ROOT` set so that the full
  source rebuild of v1, v2 and v3 also ran.
  - Final suite: **3,521 passed, 4 skipped, 1 deselected**. The skips are existing data-dependent skips. As in v1,
    the deselected test is the one that deliberately reads a temporary `.env`.
  - Validation details are in the [v3 validation JSON](spot500_v3_validation.json).

## Runner rules and the one live run

`eval/spot500.py --set v3` follows v2's rules. It loads only files labelled `spot500_v3` and applies v1's freeze
checks. v1 remains the default set, and v1 and v2 behave as before.

- A v3 record must be `eval/spot500_runs/v3-oracle-<date>.json` or `eval/spot500_runs/v3-live-<date>.json`.
- Each output and its `.run` directory are single use.
- A live v3 run is refused once any `v3-live-*` record exists, finished or interrupted. A v2 record never blocks it.

The live estimate is **69 × $0.005 = $0.345**, under the $0.95 admission cap. The runner refuses a cap of $1 or more.

This branch is the deployed code (`84b8fd4`) plus these evaluation files; its `receipts/` is unchanged. The source data
snapshot equals the one committed at `84b8fd4`. Run once, from this worktree or a merge that keeps `receipts/` and the
snapshot identical, with `MODEL_API_KEY` exported in the shell:

```bash
/Users/shou-fengchang/Desktop/Meta/.venv/bin/python eval/spot500.py --set v3 --live --evidence-dir /Users/shou-fengchang/Desktop/Meta/data/snapshot --budget 0.95 --output eval/spot500_runs/v3-live-20261102.json
```

Report that run as v1 and v2 are reported, failures and abstentions included, beside them and not pooled. It is
published whatever it shows and is not rerun to improve its score.

## Results of the one live run (November 2, 2026, morning)

**Run.** 2026-11-02 00:20:49–00:24:48 UTC (08:20 Taipei), `live:meta:muse-spark-1.3`, [result record](spot500_runs/v3-live-20261102.json).

**Code and evidence measured.**
- The deployed November 2 release with all six rounds of verifier fixes: private `84b8fd4`, public `0d80009`, Production `dpl_HhDVAiVh9cWB4LqaVw6yTLhKaXMh`.
- The runner checked that `receipts/` and `data/snapshot` equal the exported commit, and the input hashes were identical before and after (`inputs_unchanged: true`).

**Completed.** 69/69 items, with 0 errors and 0 budget skips. 64 model calls; measured usage cost **$0.37353815** against the $0.345 estimate and the $0.95 cap.

**Planted errors.**
- **29/32 caught (91%)** as ✗, or ◷ for look-ahead.
- 24/32 caught with the expected reason.
- **None was marked ✓**; 3 returned `?`.
- The four caught entity mutants were reported as value mismatches, as in v1 and v2.

**False alarms.** **1/32 original items, and 1/74 true figures marked ✗.**
- The figure is BlackRock's *"second quarter adjusted operating margin was 45.9%"*.
- The margin reading compared it with the GAAP operating margin from the release (41.16%) and ignored "adjusted".
- With no non-GAAP margin evidence the answer should have been `?`.
- The extracted span differed from the gold span, so the matcher also counts it as an extra claim (`extra_wrong` 1).

**True figures.**
- **58/74 verified (78%)**, **15/74 returned `?` (20%)**, 1 marked ✗.
- 1 true figure was not extracted as a matching claim.
- **Guidance marked verified: 0/9.** 7 returned `?` as forward-looking, and 2 were not extracted as matching claims.

**Overall.** Status correct for 94/115 gold claims; status and reason correct for 89/115. There were 5 extra claims (1 marked ✗, the BlackRock figure), 3 missing claims and 0 duplicate or conflicting matches.

| Mutator | Planted | Caught (✗/◷) | Expected reason | Marked ✓ | `?` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `basis` | 2 | 2 | 2 | 0 | 0 |
| `direction` | 5 | 4 | 4 | 0 | 1 |
| `entity` | 5 | 4 | 0 | 0 | 1 |
| `look_ahead` (◷) | 5 | 4 | 4 | 0 | 1 |
| `period` | 5 | 5 | 4 | 0 | 0 |
| `scale` | 5 | 5 | 5 | 0 | 0 |
| `value` | 5 | 5 | 5 | 0 | 0 |

Selected overlapping source features count every claim in the items that carry the feature. Do not sum them.

| Feature | True verified | True marked ✗ | Planted caught |
| --- | ---: | ---: | ---: |
| `revenue` | 28/28 | 0 | 10/11 |
| `growth` | 29/30 | 0 | 12/15 |
| `eps_diluted` | 12/15 | 0 | 5/5 |
| `net_income` | 14/16 | 0 | 4/4 |
| `operating_cash_flow` | 7/8 | 0 | 3/3 |
| `balance_sheet` | 2/4 | 0 | 3/3 |
| `bank` | 5/6 | 0 | 0/2 |
| `reit_or_utility` | 4/11 | 0 | 3/3 |
| `non_gaap` | 4/5 | 1 | 2/2 |

**Where `?` remains.** Among the 15 true figures returned `?`:
- tax rates and segment revenue growth, which are not tracked;
- REIT and utility per-share and net-income figures;
- a bank's noninterest-income growth outside the banks whose stored revenue formula carries that line;
- a basis-point gross-margin change;
- free cash flow and operating cash flow lines;
- one dated cash balance.

**Three runs, three versions.**

| Run | Code | Planted caught | Marked ✓ | True verified | True marked ✗ |
| --- | --- | ---: | ---: | ---: | ---: |
| v1 | Nov 1 (`42d48aa`) | 27/35 | 0 | 54/72 | 0/72 |
| v2 | Nov 2, early (`f22e56c`) | 27/35 | 1 | 48/75 | 4/75 |
| v3 | Nov 2, current (`84b8fd4`) | 29/32 | 0 | 58/74 | 1/74 |

These are different samples drawn by the same procedure, so the table does not show a controlled before/after effect.

Across all three runs:
- a ✓ was right 160 times out of 161;
- a ✗ on a planted or true figure was right 72 times out of 77.

Each set is small, correlated and machine-labelled. Each was run once, and none is rerun or pooled into a new score.

# Frozen spot500 full: every company, 1,000 items

The full set measures, once, the version deployed on November 2: private `8d69d3c`, public `7a42a29`. After that run
the verifier code freezes until submission. The owner approved about $5 for the run, with a $6 cap. The README reports
this set instead of v1–v3. Those three stay in this document, and none of the four results is pooled with another.

**The first freeze was superseded before any run, at the owner's request, to include every company.** That freeze
(`7a68626`, merged as `370d943`; corpus `2494d90b…`, manifest `fd6c45e8…`) excluded 52 development companies. Its
oracle record stays at `spot500_runs/full-oracle-20261102.json`. The manifest's `superseded` entry records it, and a
test checks those hashes against git.

No model or provider was called while building or freezing the full set. NoCap's verifier, extraction and live mode
have never run on its items. Selection and labels use source data only, never NoCap outputs. Nobody tunes on it.

## Procedure deltas

`eval/build_spot500.py --set full` keeps v1's source screening, mutators, feasibility checks and gold rules, scaled to
every company:

1. **Seed:** `Random(20261104)`.
2. **Frame:** v1's frame plus the original development companies, screened the same way.
   - `spot500_sources.py` excludes the original 11 at load time. For this set the builder clears that one constant
     while loading; the file itself is unchanged.
   - Without those companies the frame reproduces v1's fingerprint (`7b610743…`) exactly, and the build refuses to
     run otherwise.
   - TSLA has no stored release and 10-Q pair, so 10 of the 11 enter the frame.
3. **No company is excluded.** Every company with an unused historical sentence is in the set: 391 companies, each
   with one to three original sentences (249 with one, 101 with two, 41 with three), chosen by seed from its latest
   two stored release/10-Q pairs.
4. **Every sentence used by v1, v2 or v3 is removed:** 116 by source record SHA-256 (40, 39 and 37), plus 3 frame
   sentences with identical text. Re-running them would double-count items whose failure patterns the fixes
   targeted. Their companies stay in with other sentences: 88 of v1–v3's 90 companies are in the set. The other
   two, SNDK and WELL, have no unused sentence left.
5. **Selection order:**
   1. 50 guidance controls at distinct companies, by seed;
   2. every basis candidate, because a mutator with no more candidates than its quota goes first;
   3. feature coverage, scarcest feature first, to 20 items each;
   4. 60 each of the other six mutators, scarce first, with v1's preferences;
   5. one original for every company still without one;
   6. a seeded round-robin fill to 1,000 items.

   As in v1, a source carries at most two mutants, and every mutant's original is itself an item. The
   three-sentence cap counts mutant sources.
6. **Company history:** after selection, every item carries one feature, `company_in_development` or
   `company_fresh` (defined below). The runner's `by_feature` summary therefore gives both slices from the same run.
   Selection never reads the tag.

**Deviations:**

- **Basis:** 16 mutants instead of 60. All of the frame's basis candidates are used.
- **Features below 20 items** (marked † in the table):
  - every frame sentence carrying the feature is used: 16- or 17-week quarters (5 items), costs and expenses (7),
    net margin (1), shares outstanding (2), total assets (3) and total debt (1);
  - the three-sentence cap leaves the rest out: net interest income (18 items; 25 sentences at 10 companies) and
    variable-week companies (12 items; 14 sentences at 3 companies).
- **No other quota changed.** The set has exactly 1,000 items.

## Company history and its method

`company_in_development` covers 52 companies:

- the original 11 development companies: AAPL, AMD, AMZN, BAC, GOOGL, JPM, META, MSFT, NFLX, NVDA, TSLA;
- every company named in verifier-fix tests or in the lines the fixes added to the verifier, under v3's mechanical
  rules (alias, unique leading word, ticker) against the snapshot's companies table:
  - v3's lists (28 from the tests and 7 from the fix code) are carried over unchanged;
  - re-derived over `50a4eac..8d69d3c` into `eval/spot500_full_exclusions.json` (SHA-256
    `ff3886abce011286d41388919966fef2e75c8853b3072751d70143ab93579db8`): 38 from the tests (AKAM, AMD, AMGN, AXP,
    CARR, CMCSA, CNC, DAL, DIS, ED, EXR, FDS, FRT, GEHC, GPC, HPE, INTC, ITW, IVZ, JPM, LEN, MNST, MRK, MSI, O, PFE,
    RF, SJM, SNA, SYY, TFC, TGT, TSLA, UDR, VLO, WMB, WMT, WSM) and 9 from the fix code
    (CMCSA, DAL, DIS, ED, EL, FDS, MPC, NVDA, SYY);
  - the file name dates from the superseded freeze, when the list excluded companies; it now only tags them;
- the probe companies BAX, CSGP, TGT and ADI, plus LNT. The owner names Alliant (LNT), though no scanned line does.

Probable false positives of the rules stay tagged: ED, GPC, O and WMB; the derivation record holds each match.
`company_fresh` covers every other company.

Of the 52, 39 are in the set: AAPL, ADI, AKAM, AMD, AMGN, AMZN, AXP, BAC, BAX, CMCSA, CNC, CSGP, DIS, EL, EXR, FDS,
GOOGL, GPC, HPE, INTC, ITW, IVZ, JPM, LEN, META, MNST, MPC, MRK, MSFT, MSI, NFLX, NVDA, RF, SJM, SYY, TFC, WMB, WMT,
WSM.
The other 13 cannot appear:

- CARR, ED, GEHC, LNT, PFE, SNA, TGT, TSLA, VLO have no stored release and 10-Q pair in the frame;
- DAL, FRT, O, UDR have no historical sentence that passes the screening.


## The 109 S&P 500 companies without items

NoCap itself holds filed evidence for all 500 companies in the snapshot: for example, GE has 469 stored facts, Texas
Instruments 486, NextEra 347 and Exxon Mobil 334. The test is narrower because the builder must write every gold label
without running NoCap. A company needs:
- an earnings release (an 8-K item 2.02 with an EX-99.1 exhibit), filed after the quarter ends and no later than a
  10-Q that has XBRL facts;
- a sentence from it that passes the source screen and was not used by v1–v3.

[`spot500_frame_gaps.py`](spot500_frame_gaps.py) follows the loader's checks in order. For each of the other 109
companies it names the check that fails on the company's latest archived quarter. It needs the gitignored SEC archive.

| Reason | Companies | Tickers |
| --- | ---: | --- |
| a release/10-Q pair exists, but no unused sentence passes the screen | 45 | ABBV, ACGL, AEP, AIG, APA, ARE, ATO, BALL, BRK-B, BRO, BSX, BXP, COST, CPT, DPZ, EG, EMR, EQIX, ES, FDX, FRT, GL, HRL, HUM, JKHY, L, LYB, MAA, NI, O, OXY, PKG, PLD, PPL, PRU, PSA, ROP, SNDK, UDR, VMRK, VST, WEC, WELL, WRB, ZBH |
| the archived release is not labelled EX-99.1 (it says EX-99 or EX-99.01, or has no label) | 36 | ACN, ADP, CARR, CDNS, CL, CMI, CTAS, DG, ETN, F, GE, GEHC, GEN, GEV, GIS, HON, INTU, MAR, MAS, NOC, OTIS, PFE, PFG, PGR, PPG, ROK, RTX, SKYD, SLB, SNA, TGT, TSLA, TXN, VLO, VZ, XEL |
| no 10-Q with XBRL facts for the quarter in the archived companyfacts | 14 | CMS, D, DTE, ED, EIX, ETR, EVRG, EXC, LNT, NEE, PCG, SO, SRE, URI |
| the release exhibit for the quarter is not in the archive | 9 | APO, CEG, EOG, EQT, FANG, FERG, PEG, PNW, REGN |
| no Q1-Q3 quarters in the snapshot | 2 | FDXF, VYLR |
| not in the archived S&P list the builder uses | 1 | XOM |
| the release does not name the quarter | 1 | DAL |
| no 8-K earnings release filed after the quarter and by the 10-Q | 1 | ECHO |

Most rows are rules kept for label quality, not gaps in NoCap's coverage:
- 36 companies' release exhibits are not labelled EX-99.1: 30 say EX-99, 5 say EX-99.01, which the builder's rule
  does not read as 99.1, and Tesla's has no label.
- 14, mostly utilities, have no 10-Q XBRL for the quarter in their own archived companyfacts.
- SNDK and WELL fall in the first row because v1–v3 already used every sentence of theirs that passes.
- FRT, O and UDR are development companies with no passing sentence.

## Composition

The frozen set has **391 companies, 1,000 items and 1,736 gold claim records**. There are 574 originals; 376 mutants
(60 each of value, period, scale, look-ahead, entity and direction, and 16 basis); and 50 guidance controls at 50
companies. The gold claims are 1,271 true figures, 376 targets and 89 guidance figures. 211 originals carry the 376
mutants, 165 of them two each. Sampled period ends range from 2025-09-30 to 2026-10-02.

Mutation candidates in the frame without the earlier sets' sentences: value 1,362, scale 888, direction 591,
look-ahead 268, period 198, entity 112, basis 16.

By company-history slice:

| Slice | Companies | Items | Originals | `value` | `period` | `scale` | `basis` | `look_ahead` | `entity` | `direction` | Guidance | Gold claims |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `company_in_development` | 39 | 123 | 67 | 6 | 10 | 6 | 6 | 7 | 9 | 4 | 8 | 208 |
| `company_fresh` | 352 | 877 | 507 | 54 | 50 | 54 | 10 | 53 | 51 | 56 | 42 | 1528 |
| **All** | 391 | 1000 | 574 | 60 | 60 | 60 | 16 | 60 | 60 | 60 | 50 | 1736 |

Items by overlapping source feature, mutator and slice are below. A row counts every item that carries the feature, so
rows overlap and must not be summed. `·` means none, and † marks a feature below 20 items. The last column gives the
frame sentences carrying the feature, with their companies.

| Source feature (overlapping) | Original | `value` | `period` | `scale` | `basis` | `look_ahead` | `entity` | `direction` | Guidance | Items | In development | Fresh | Frame sentences (companies) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `16_or_17_week_quarter` | 3 | 1 | · | 1 | · | · | · | · | · | 5 † | 0 | 5 | 3 (1) |
| `balance_sheet` | 24 | 9 | 1 | 6 | · | 7 | · | · | · | 47 | 5 | 42 | 28 (17) |
| `bank` | 34 | 2 | 4 | 4 | 1 | 4 | 1 | 4 | · | 54 | 20 | 34 | 188 (13) |
| `bank_revenue` | 12 | 1 | 3 | 3 | 1 | 1 | 1 | 1 | · | 23 | 14 | 9 | 16 (6) |
| `capital_returned` | 20 | 4 | 2 | 4 | · | · | 1 | · | · | 31 | 2 | 29 | 26 (19) |
| `cash_and_equivalents` | 20 | 8 | 1 | 6 | · | 6 | · | · | · | 41 | 4 | 37 | 24 (14) |
| `costs_and_expenses` | 3 | 1 | 1 | 1 | · | · | · | 1 | · | 7 † | 7 | 0 | 3 (2) |
| `eps_diluted` | 112 | 17 | 8 | 4 | 12 | 14 | 19 | 7 | 19 | 212 | 35 | 177 | 907 (255) |
| `free_cash_flow` | 24 | 4 | · | 6 | · | · | · | · | 1 | 35 | 1 | 34 | 229 (93) |
| `gross_margin` | 23 | 8 | · | 1 | · | · | 1 | 1 | 1 | 35 | 6 | 29 | 121 (41) |
| `growth` | 232 | 18 | 43 | 27 | · | 14 | 15 | 58 | 6 | 413 | 49 | 364 | 2284 (314) |
| `guidance` | · | · | · | · | · | · | · | · | 50 | 50 | 8 | 42 | 352 (114) |
| `impact` | 21 | 1 | · | · | · | · | · | · | · | 22 | 4 | 18 | 124 (56) |
| `margin_change` | 21 | 6 | · | · | · | · | · | 3 | · | 30 | 3 | 27 | 80 (35) |
| `net_income` | 60 | 8 | 5 | 7 | · | 7 | 7 | 5 | 3 | 102 | 7 | 95 | 398 (141) |
| `net_interest_income` | 13 | 1 | · | 1 | · | 1 | · | 2 | · | 18 † | 2 | 16 | 25 (10) |
| `net_margin` | 1 | · | · | · | · | · | · | · | · | 1 † | 0 | 1 | 1 (1) |
| `non_gaap` | 128 | 12 | · | 5 | 16 | · | · | 1 | 21 | 183 | 29 | 154 | 1556 (269) |
| `noninterest_expense` | 23 | 1 | · | 2 | · | · | · | 1 | 2 | 29 | 4 | 25 | 100 (45) |
| `noninterest_revenue` | 13 | 1 | · | 1 | · | 2 | · | 3 | · | 20 | 0 | 20 | 13 (6) |
| `operating_cash_flow` | 35 | 8 | 7 | 13 | · | 6 | · | 3 | 1 | 73 | 4 | 69 | 145 (73) |
| `operating_income` | 36 | 3 | 1 | 3 | 2 | 5 | 5 | 5 | 4 | 64 | 4 | 60 | 507 (123) |
| `operating_margin` | 25 | 4 | · | · | · | · | · | 2 | 3 | 34 | 4 | 30 | 267 (84) |
| `opex` | 19 | 1 | 2 | 2 | 1 | 1 | · | 4 | 1 | 31 | 1 | 30 | 86 (28) |
| `reit_or_utility` | 26 | 5 | · | 2 | 1 | 2 | · | · | 4 | 40 | 1 | 39 | 67 (21) |
| `revenue` | 143 | 10 | 34 | 23 | 1 | 17 | 27 | 30 | 13 | 298 | 40 | 258 | 814 (262) |
| `segment_revenue` | 35 | 2 | · | · | · | · | · | · | 5 | 42 | 5 | 37 | 1222 (209) |
| `shares_outstanding` | 2 | · | · | · | · | · | · | · | · | 2 † | 1 | 1 | 2 (2) |
| `tax_rate` | 20 | · | · | · | · | · | · | · | 4 | 24 | 0 | 24 | 318 (115) |
| `total_assets` | 1 | 1 | · | · | · | 1 | · | · | · | 3 † | 0 | 3 | 1 (1) |
| `total_debt` | 1 | · | · | · | · | · | · | · | · | 1 † | 0 | 1 | 1 (1) |
| `variable_week_company` | 7 | 2 | · | 2 | · | · | · | 1 | · | 12 † | 0 | 12 | 14 (3) |
| **All items** | 574 | 60 | 60 | 60 | 16 | 60 | 60 | 60 | 50 | 1000 | 123 | 877 | |

| Sector | Companies | In development | Items | Originals | Mutants | Guidance | Gold claims |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Communication Services | 16 | 5 | 49 | 25 | 22 | 2 | 90 |
| Consumer Discretionary | 42 | 4 | 93 | 56 | 34 | 3 | 148 |
| Consumer Staples | 27 | 5 | 82 | 47 | 30 | 5 | 139 |
| Energy | 13 | 2 | 30 | 18 | 12 | 0 | 39 |
| Financials | 63 | 7 | 185 | 113 | 68 | 4 | 295 |
| Health Care | 53 | 4 | 115 | 70 | 33 | 12 | 226 |
| Industrials | 63 | 1 | 145 | 85 | 55 | 5 | 265 |
| Information Technology | 69 | 9 | 197 | 99 | 87 | 11 | 362 |
| Materials | 20 | 0 | 56 | 30 | 23 | 3 | 88 |
| Real Estate | 18 | 2 | 35 | 22 | 11 | 2 | 61 |
| Utilities | 7 | 0 | 13 | 9 | 1 | 3 | 23 |
| **All** | 391 | 39 | 1000 | 574 | 376 | 50 | 1736 |

Items per company: 142 companies have 1, 60 have 2, 94 have 3, 50 have 4, 22 have 5, 16 have 6, 6 have 7 and 1 has 8.

Items per company by sector; `*` marks a development company:

- **Communication Services:** APP 1, CHTR 2, CMCSA* 6, DIS* 3, FOXA 1, GOOGL* 2, LYV 5, META* 6, NFLX* 3, NWSA 2, OMC
  1, RDDT 3, T 3, TKO 7, TMUS 3, TTWO 1.
- **Consumer Discretionary:** ABNB 1, AMZN* 3, APTV 3, AZO 6, BBY 1, BKNG 2, CCL 1, CMG 3, CVNA 2, DASH 3, DECK 3, DHI
  1, DRI 1, EBAY 1, EXPE 1, GM 1, GPC* 1, HAS 4, HD 2, HLT 3, LEN* 4, LOW 1, LULU 1, LVS 4, MCD 1, MGM 1, NCLH 2, NKE
  4, NVR 1, ORLY 1, PHM 3, RCL 3, RL 6, ROST 1, SBUX 1, TJX 1, TPR 2, TSCO 3, ULTA 6, WSM* 1, WYNN 1, YUM 2.
- **Consumer Staples:** ADM 1, BF-B 3, BG 1, CASY 8, CHD 6, CLX 3, DLTR 5, EL* 3, HSY 4, KDP 1, KHC 3, KMB 3, KO 4, KR
  5, KVUE 2, MDLZ 3, MKC 2, MNST* 3, MO 2, PEP 1, PG 1, PM 2, SJM* 4, STZ 1, SYY* 1, TSN 5, WMT* 5.
- **Energy:** BKR 3, COP 3, CVX 1, DVN 3, EXE 1, HAL 7, KMI 6, MPC* 1, OKE 1, PSX 1, TPL 1, TRGP 1, WMB* 1.
- **Financials:** AFL 3, AIZ 2, AJG 1, ALL 1, AMP 2, AON 2, ARES 3, AXP* 1, BAC* 4, BEN 1, BLK 1, BNY 7, BX 1, C 5, CB
  1, CBOE 5, CFG 4, CINF 1, CME 1, COF 3, COIN 3, CPAY 4, ERIE 1, FDS* 3, FIS 3, FISV 1, FITB 1, GPN 2, GS 4, HBAN 3,
  HIG 2, HOOD 7, IBKR 4, ICE 6, IVZ* 6, JPM* 7, KEY 5, KKR 1, MA 4, MCO 2, MET 1, MRSH 3, MS 1, MSCI 4, MTB 3, NDAQ 7,
  NTRS 3, PNC 3, PYPL 1, RF* 5, RJF 2, SCHW 3, SPGI 3, STT 3, SYF 1, TFC* 4, TROW 4, TRV 1, USB 4, V 1, WFC 6, WTW 2,
  XYZ 2.
- **Health Care:** A 2, ABT 1, ALGN 4, AMGN* 1, BAX* 1, BDX 1, BIIB 2, BMY 1, CAH 1, CI 3, CNC* 6, COO 1, COR 3, CRL
  3, CVS 4, DGX 1, DHR 4, DVA 3, DXCM 3, ELV 1, EW 3, GILD 1, HCA 2, HSIC 1, IDXX 1, ILMN 2, INCY 1, IQV 4, ISRG 2,
  JNJ 1, LH 3, LLY 1, MCK 1, MDT 2, MRK* 4, MRNA 2, MTD 4, PODD 1, RMD 5, RVTY 1, SOLV 1, STE 1, SYK 4, TECH 3, TMO 3,
  UHS 1, UNH 2, VEEV 3, VRTX 2, VTRS 2, WAT 2, WST 2, ZTS 1.
- **Industrials:** ALLE 3, AME 3, AOS 1, AXON 2, BA 5, BE 3, BR 4, CAT 1, CHRW 3, CPRT 1, CSX 2, DD 3, DE 1, DOV 1,
  EFX 4, EME 1, EXPD 1, FAST 1, FIX 1, FTV 1, GD 1, GNRC 1, GWW 3, HII 4, HONA 3, HUBB 3, HWM 1, IEX 2, IR 1, ITW* 3,
  J 2, JBHT 2, JCI 1, LDOS 3, LHX 4, LII 3, LMT 1, LUV 2, MMM 3, NDSN 3, NSC 1, ODFL 3, PAYX 1, PCAR 3, PH 2, PNR 3,
  PWR 1, ROL 1, RSG 4, SWK 1, TDG 3, TT 1, TXT 1, UAL 3, UBER 3, UNP 4, UPS 3, VLTO 6, VRSK 2, VRT 1, WAB 3, WM 1, XYL
  6.
- **Information Technology:** AAPL* 3, ADBE 1, ADI* 3, ADSK 3, AKAM* 1, AMAT 4, AMD* 2, ANET 3, APH 4, AVGO 4, CDW 4,
  CIEN 1, COHR 5, CRM 1, CRWD 5, CSCO 5, CTSH 4, DDOG 4, DELL 3, FFIV 1, FICO 1, FLEX 6, FSLR 3, FTNT 4, GDDY 5, GLW
  6, GRMN 3, HPE* 2, HPQ 4, IBM 3, INTC* 2, IT 2, JBL 3, KEYS 1, KLAC 2, LITE 2, LRCX 4, MCHP 1, MPWR 1, MRVL 2, MSFT*
  4, MSI* 3, MU 1, NOW 3, NTAP 5, NVDA* 5, NXPI 1, ON 3, ORCL 3, P 3, PANW 3, PLTR 4, PTC 2, Q 3, QCOM 2, SMCI 1, SNPS
  1, STX 5, SWKS 4, TDY 3, TEL 5, TER 1, TRMB 2, TWLO 2, TYL 4, VRSN 1, WDAY 1, WDC 3, ZBRA 1.
- **Materials:** ALB 2, AMCR 1, APD 3, AVY 3, CF 1, CRH 1, DOW 3, ECL 2, FCX 4, IFF 4, IP 3, LIN 6, MLM 4, MOS 1, NEM
  1, NUE 4, SHW 3, STLD 5, SW 4, VMC 1.
- **Real Estate:** AMT 1, CBRE 1, CCI 1, CSGP* 5, DLR 1, DOC 1, ESS 3, EXR* 1, HST 4, INVH 1, IRM 2, KIM 1, REG 1,
  SBAC 3, SPG 1, VICI 5, VTR 1, WY 2.
- **Utilities:** AEE 2, AES 1, AWK 2, CNP 1, DUK 2, FE 4, NRG 1.

## Freeze and offline validation

- **Corpus:** `eval/corpus/spot500_full.jsonl`, SHA-256
  `53e2fd50615d696e7358f81d4e443eaf553374e9718904a370570d35781f5a01`.
- **Manifest:** `eval/spot500_full_manifest.json`, SHA-256
  `e25ef0b979833e16e96ee4438bc3943825744a062032b0c123f20d029eb4a124`.
  - It records the superseded freeze, the frame check, the removed sentences, the company-history groups and their
    derivations, and v1–v3's hashes.
  - It also records the procedure's changes from v1 with each deviation, the feature coverage, the per-item,
    source-record and builder hashes, and counts by company, sector, feature, mutator and slice.
- **Rebuilds:**
  - An independent complete source rebuild, in a separate process, produced both full files byte for byte.
  - The v1, v2 and v3 builds, run in place through the CLI with this builder, were accepted unchanged. Each pins
    its freezing builder: `050be2e`, `7bdcb11` and `02d6e41`.
- **Oracle:** only the oracle stub has run on this freeze. Its
  [CLI record](spot500_runs/full-oracle-20261102-every-company.json):
  - 1,000/1,000 items completed, with 1,736/1,736 status-and-reason matches and 376/376 targets and reasons.
  - There were zero original false alarms, 89/89 guidance claims unverifiable, and zero skips, errors or extra
    claims.
  - In its `by_feature` summary, `company_in_development` covers 123 items and `company_fresh` 877.
  - This is 100% **by construction**. It tests plumbing, not NoCap.
  - Reusing the output was refused with `FileExistsError`, and its SHA-256 stayed unchanged.
- **Isolation:** the builds and the oracle ran under an audit hook that refused network access, dotenv files and
  writes under the source data root. There were zero provider calls and $0 spend.
- **Tests:** the offline suite ran with `eval/spot500_offline_tests.py`, with `SPOT500_DATA_ROOT` set so that the full
  source rebuilds of v1–v3 and of the full set also ran.
  - Final suite: **3,584 passed, 4 skipped, 1 deselected**. The skips are existing data-dependent skips. As in v1,
    the deselected test is the one that deliberately reads a temporary `.env`.
  - Validation details are in the [full validation JSON](spot500_full_validation.json).

## Runner rules and the one live run

`eval/spot500.py --set full` follows v2's rules. It loads only files labelled `spot500_full` and applies v1's freeze
checks. A record must be `eval/spot500_runs/full-oracle-<date>.json` or `full-live-<date>.json`; each output and its
`.run` directory are single use. A live full run is refused once any `full-live-*` record exists.

- **Budget.** Only this set has an explicit allowance: an admission cap of at most $6 and a harness estimate of at
  most $5.50. Every other set still refuses a cap of $1 or more.
  - The harness estimate is 1,000 × $0.005 = **$5.00**.
  - v1–v3 measured $0.0053–0.0058 per extraction request. Over the 940 unique requests (each look-ahead mutant
    reuses its original's extraction), that forecasts **$5.0–5.5**.
- **Concurrency.** `--concurrency` takes 1 to 4.
  - The attempt ledger is lock-protected and admits one dispatch per request.
  - Items that share a request, an original and its look-ahead mutant, run one after another, so the later one reads
    the extraction cache.
  - A synthetic four-worker test checks that no request is sent twice and that the ledger total is exact. Per-item
    `cost_usd_approx` is approximate above concurrency 1.
- **Expected runtime:** about 13–15 minutes at concurrency 4, or about 50 minutes at concurrency 1 (v1–v3 took
  2.8–3.5 seconds per item).

The run used the `receipts/` and `data/snapshot/` of `8d69d3c`; the run script refused to start otherwise. It was made
once, from the private checkout at `5da9131` (the merged set, pushed before the run), with this command:

```bash
/Users/shou-fengchang/Desktop/Meta/.venv/bin/python eval/spot500.py --set full --live --evidence-dir /Users/shou-fengchang/Desktop/Meta/data/snapshot --budget 6 --concurrency 4 --output eval/spot500_runs/full-live-20261102.json
```

It is reported below as v1–v3 are, failures and abstentions included, overall and for each company-history slice.
It is published as it came out and is not rerun to improve its score.

## Results of the one live run (November 2, 2026, all companies)

**Run.** 2026-11-02 03:10:44–03:26:14 UTC (11:10–11:26 Taipei), `live:meta:muse-spark-1.3`, concurrency 4,
[result record](spot500_runs/full-live-20261102.json).

**Code and evidence measured.**
- The deployed November 2 release: private `8d69d3c`, public `7a42a29`, Production `dpl_48sU1dZPDN1ST9JcTvFAGuL6o4Zn`.
- The run script checked that `receipts/` and `data/snapshot` equal the exported commit, and the input hashes were
  identical before and after (`inputs_unchanged: true`).

**Completed.** 1,000/1,000 items, with 0 errors and 0 budget skips. 940 model calls (each look-ahead copy reused its
original's extraction); measured usage cost **$3.69412935** against the $5.00 estimate and the $6 cap.

**How the numbers below are computed.** [`spot500_report.py`](spot500_report.py) recomputes them from the record with
the runner's own scorer, and refuses a record whose summary it cannot reproduce:

```bash
.venv/bin/python eval/spot500_report.py eval/spot500_runs/full-live-20261102.json --slice company_in_development --slice company_fresh
```

Each 95% interval is the wider of a Wilson interval and a company-level cluster bootstrap (2,000 resamples, seed
20261102). The bootstrap resamples a company's items together, because a planted-error copy shares its company and its
original sentence.

| Measure | All 391 companies | 352 fresh companies | 39 development companies |
| --- | ---: | ---: | ---: |
| Planted errors caught (✗, or ◷ for look-ahead) | **310/376 (82.4%; 77.2–87.2%)** | **264/328 (80.5%; 74.8–86.0%)** | 46/48 (95.8%; 86.0–100%) |
| Planted errors marked ✓ | **2/376 (0.5%; 0.0–1.9%)** | 2/328 | 0/48 |
| Planted errors returned `?` | 64/376 | 62/328 | 2/48 |
| True figures marked ✗ | **14/1,271 (1.1%; 0.5–1.8%)** | **11/1,126 (1.0%; 0.4–1.7%)** | 3/145 (2.1%) |
| True figures verified | 741/1,271 (58.3%; 53.9–62.6%) | 645/1,126 (57.3%) | 96/145 (66.2%) |
| True figures returned `?` (neither ✓ nor ✗) | 510/1,271 (40.1%) | 466/1,126 | 44/145 |
| A ✓ that landed on a true figure | **741/744 (99.6%; 98.8–100%)** | 645/647 | 96/97 |
| A ✗ that landed on a planted error | **259/273 (94.9%; 91.6–97.6%)** | 220/231 | 39/42 |
| Guidance figures marked ✓ | 1/89 | 0/74 | 1/15 |

The remaining 6 true figures: 1 marked ≈ and 5 not extracted as a matching claim. All 51 ◷ landed on look-ahead
copies. Status was correct for 1,091/1,736 gold claims, and status and reason for 1,024/1,736. There were 146 extra
claims (34 ✓, 4 ✗), 130 missing claims, 6 duplicate matches and 1 conflicting match; 13/574 original items carried a
false alarm.

**Development companies.** The 39 companies used in development or fixes scored higher on planted errors (46/48
against 264/328). This is why the fresh slice is reported separately: it is the better estimate for companies NoCap
was not tuned on.

The tag is mechanical: it marks companies named in development lists, fix tests or fix code. 81 of the 352 fresh
companies appeared in v1–v3 with other sentences, and three of them (BLK, BE and CNP, 5 items here) had earlier sentences
that prompted the round-6 and round-7 fixes without being named in the fix tests or code. The 271 fresh companies that
appear in no earlier set give nearly the same result:

| Fresh companies | Companies | Items | Planted caught | Marked ✓ | True marked ✗ | True verified | A ✓ on a true figure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| In no earlier set | 271 | 656 | 202/252 (80.2%; 73.7–86.2%) | 2 | 6/878 (0.7%; 0.1–1.5%) | 525/878 (59.8%) | 525/527 |
| In v1, v2 or v3 | 81 | 221 | 62/76 (81.6%) | 0 | 5/248 (2.0%) | 120/248 (48.4%) | 120/120 |

| Mutator | Planted | Caught (✗/◷) | Expected reason | Marked ✓ | `?` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `basis` | 16 | 14 | 8 | 0 | 2 |
| `direction` | 60 | 47 | 47 | 0 | 13 |
| `entity` | 60 | 49 | 0 | 1 | 10 |
| `look_ahead` (◷) | 60 | 51 | 51 | 0 | 9 |
| `period` | 60 | 45 | 38 | 1 | 14 |
| `scale` | 60 | 53 | 53 | 0 | 7 |
| `value` | 60 | 51 | 51 | 0 | 9 |

As in v1–v3, caught entity copies were reported as value mismatches.

Selected overlapping source features count every claim in the items that carry the feature. Do not sum them.

| Feature | True verified | True marked ✗ | Planted caught |
| --- | ---: | ---: | ---: |
| `revenue` | 307/405 | 0 | 121/142 |
| `growth` | 445/663 | 1 | 141/175 |
| `eps_diluted` | 193/267 | 2 | 63/81 |
| `net_income` | 100/150 | 0 | 33/39 |
| `operating_income` | 54/85 | 2 | 23/24 |
| `operating_margin` | 26/69 | 2 | 2/6 |
| `gross_margin` | 34/60 | 2 | 11/11 |
| `operating_cash_flow` | 68/92 | 0 | 31/37 |
| `free_cash_flow` | 31/56 | 1 | 10/10 |
| `balance_sheet` | 22/33 | 0 | 23/23 |
| `bank` | 26/65 | 2 | 14/20 |
| `reit_or_utility` | 14/52 | 0 | 7/10 |
| `non_gaap` | 109/290 | 9 | 29/34 |
| `segment_revenue` | 14/77 | 1 | 2/2 |
| `tax_rate` | 1/31 | 1 | · |

| Sector | Companies | Planted caught | Marked ✓ | True verified | True marked ✗ |
| --- | ---: | ---: | ---: | ---: | ---: |
| Communication Services | 16 | 19/22 | 0 | 54/65 | 1 |
| Consumer Discretionary | 42 | 28/34 | 0 | 53/110 | 2 |
| Consumer Staples | 27 | 26/30 | 1 | 54/100 | 2 |
| Energy | 13 | 12/12 | 0 | 16/27 | 0 |
| Financials | 63 | 56/68 | 1 | 105/220 | 2 |
| Health Care | 53 | 30/33 | 0 | 111/171 | 3 |
| Industrials | 63 | 38/55 | 0 | 107/199 | 3 |
| Information Technology | 69 | 75/87 | 0 | 185/258 | 0 |
| Materials | 20 | 17/23 | 0 | 34/60 | 1 |
| Real Estate | 18 | 9/11 | 0 | 17/45 | 0 |
| Utilities | 7 | 0/1 | 0 | 5/16 | 0 |

**The 2 planted errors marked ✓.** Both are scored as failures under the frozen labels; the labels are not revised
after the run.
- *Goldman Sachs, entity copy.* The copy replaced the ticker in "(NYSE: GS)" with "Vertex Pharmaceuticals" and set
  the story's company to Vertex, but the sentence still says "The Goldman Sachs Group, Inc. … reported net revenues
  of $17.23 billion". NoCap checked the figure against the company the sentence names and verified it. A conflict
  between the story's company and the sentence's should have returned `?`.
- *Mondelez, period copy.* The copy changed "year-over-year" to "quarter-over-quarter" in the clause explaining the
  growth ("driven by a favorable … change in mark-to-market impacts"), not in the figure's own wording. NoCap verified
  "up 144.9 percent" as year-over-year EPS growth, which the 10-Q supports.

**The guidance figure marked ✓.** Walmart's "guidance is based on the following Q2 FY26 figures: … adjusted
operating income: $7.9 billion" quotes a past quarter as the base for guidance. NoCap verified it against that
quarter's reported $7.876 billion.

**The 14 true figures marked ✗**, by cause (fresh companies unless marked *development*):
- **A change read as a level (4).** Charter's free cash flow "a decrease of $192 million", Norfolk Southern's adjusted
  EPS "up $0.32", and Truist's taxable-equivalent net interest income "up $32 million" and "up $27 million"
  (*development*, 2) were compared with the level of the measure.
- **A charge read as a level (1).** Martin Marietta's "$22 million" inventory charge was compared with aggregates
  gross profit.
- **An adjusted or comparable figure compared with other evidence (4).** Revvity's adjusted operating income ($113
  million and $89 million) was compared with GAAP operating income. Estée Lauder's "adjusted gross margin of
  approximately 75.0%" (*development*) lost its basis in extraction and was compared with GAAP margins. Constellation
  Brands' "reported ~12%" tax rate was compared with a 20.2% release rate that matches the "comparable ~20%" in the
  same line.
- **A part of the business compared with the whole (3).** Allegion's adjusted operating margin "in the region" (13.9%)
  and MGM's same-store revenue growth (3%) were compared with company totals. Axon's "adjusted gross margin … from
  51.1% a year ago" was compared with the company-wide 63.3% from its year-ago release; the sentence names no
  segment.
- **A line with no period of its own (1).** Aptiv's "Adjusted net income per share of $2.56" was compared with the
  quarter's adjusted EPS ($1.63).
- **Rounding (1).** Illumina's "non-GAAP operating margin of 22.5%" was compared with 22.43% computed from the
  release's own table, outside the written precision.

Nine of the 14 come from sentences with non-GAAP wording. Each is a pattern for a later fix; the verifier is frozen
for this release, so none is fixed here.

**Where `?` remains.** The 510 true figures returned `?`, grouped by the explanation NoCap gave:
- 162: a kind of figure NoCap does not track yet, such as segment revenue, tax rates, charges and capital returned;
- 95: a qualifier NoCap will not match to a filed figure, such as "same store", "representing" or "common
  stockholders";
- 86: a non-GAAP line with no matching release fact for that period;
- 30 constant-currency figures and 25 "organic" measures, which filings do not report;
- 19: a period or company it could not resolve; 17: no single filing it holds reports both compared periods;
- 16: read as guidance about a future period;
- 60 others, mostly segment or qualified lines with no filing fact (Union Pacific's "operating revenue", a bank's
  noninterest income growth), and 9 figures where a company name did not match: "Lowe’s Companies, Inc." with a curly
  apostrophe, "D.R. Horton", "SBA" and "Old Dominion". The same names also turned 4 planted errors into `?`.

**Four runs, four samples.**

| Run | Code | Items | Planted caught | Marked ✓ | True verified | True marked ✗ |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| v1 | Nov 1 (`42d48aa`) | 75 | 27/35 | 0 | 54/72 | 0/72 |
| v2 | Nov 2, early (`f22e56c`) | 74 | 27/35 | 1 | 48/75 | 4/75 |
| v3 | Nov 2, morning (`84b8fd4`) | 69 | 29/32 | 0 | 58/74 | 1/74 |
| full | Nov 2, current (`8d69d3c`) | 1,000 | 310/376 | 2 | 741/1,271 | 14/1,271 |

The full set covers far more figure types and companies than v1–v3, so its rates are not a before/after comparison
with them. Each set was run once, and none is rerun or pooled into a new score.

# Frozen spot500 full2: the round-8 version, on new sentences

full2 measures, once, the version that round 8 fixes: false-✗ patterns the full run found, and company-name
resolution. The full set ran once, and it is never rerun. full2 is a new comprehensive set built by the full
procedure, so its result is reported beside the full set's, never pooled with it.

No model or provider was called while building full2. NoCap's verifier, extraction and live mode never run on its
items before the one live run. Selection and labels use source data only, never NoCap outputs. Nobody tunes on it.

**Status:** frozen against the fixed code `8c2fd78` on `wip/day3-handoff`, and run once on November 2 (results below). The range
`22cd636..8c2fd78` holds the names fix (`950f7e9`), the merged round-8 fixes (`df90e73`) and a period-rule fix
(`8c2fd78`). The manifest's `commit` field records `8c2fd78` as the code full2 measures.

## Procedure deltas

`eval/build_spot500.py --set full2` runs the full set's procedure. Only these differ:

1. **Seed:** `Random(20261105)`. The seeds 20261101–20261104 belong to v1, v2, v3 and full.
2. **Frame:** the full set's frame, unchanged, plus the companies whose earnings 8-K has no exhibit the loader reads as
   EX-99.1 (see "The 109 S&P 500 companies without items" above).
   - **Two fallback labels are read as the release:**
     - a release headed EX-99.01;
     - a release headed plain EX-99, when it is the 8-K's single EX-99-type exhibit.
   - **When the fallback applies:** only to an 8-K none of whose archived exhibits passes the EX-99.1 rule, and only
     for companies with no pair in the full set's frame. No earlier company's pairs change, and the build refuses to
     run unless the strict part of the frame reproduces the full set's fingerprint.
   - **What it adds:** 36 companies and 71 release documents. That is 30 EX-99 and 5 EX-99.01 companies from the gap
     list, plus D (Dominion), whose latest quarter has no 10-Q XBRL but whose Q1 2026 quarter pairs with a single
     plain EX-99.
   - **Not added:** TSLA's release has no label and stays out. PGR's releases pass the rule, but none of its
     sentences passes the screening.
   - `spot500_sources.py` is unchanged: the builder loads the added companies with the rule passed in, and each new
     document records the label it was read under.
3. **Sentences:** every sentence used by v1, v2, v3 or full is removed, by source record SHA-256 and by identical text.
4. **Company history, exactly one tag per item:**
   - **`company_in_development`:** the full set's 52, plus every company named in the round-8 and name-resolution fix
     tests or fix code. The list is derived by v3's mechanical rules over `22cd636..<round-8 merge>` into
     `eval/spot500_full2_history.json`. This scan also reads the lines added to test files that already existed.
   - **`company_new`:** companies that no earlier set (v1, v2, v3, full) sampled.
   - **`company_seen`:** every other company. These appeared in an earlier set, with other sentences.

   Round 8 also re-verified all of the full set's recorded claims offline, as a regression check.

   **The derivation** (`eval/spot500_full2_history.json`, SHA-256
   `3874b07e871278317b8f390106b2fb6c7eaf0dcae81b49579f01dddeb3785f65`):
   - It reads 5 added test files (`test_release_names.py`, `test_context_inference.py`,
     `test_period_named_in_draft.py` and two release-text fixtures), lines added to `test_api.py`,
     `test_entity_scale.py` and `test_wording_gaps.py`, and the lines the fixes add under `receipts/`.
   - The tests name 65 companies and the code 48.
   - 52 companies are named beyond the full set's 52, so `company_in_development` has 104: ALLE, AOS, AXON, BDX, CASY,
   CBOE, CHRW, CHTR, COF, COO, COST, D, DHI, DLTR, DOV, DPZ, FCX, FLEX, FSLR, GM, GPN, GS, GWW, HD, HIG, ILMN, INCY,
   JBHT, KEY, KEYS, LOW, MCD, MCO, MGM, MLM, MPWR, MRSH, MTD, NDAQ, NSC, ODFL, ON, ORLY, PRU, RVTY, SBAC, SO, STZ,
   VRTX, VTRS, WM, WRB.
   - Probable false positives of the unchanged rules, kept as tags: FSLR ("First Quarter" in the Microsoft fixture), SO
     ("Norfolk Southern"), and GM, GPN and COF (Nasdaq market tiers in an exchange-listing pattern). CBOE and NDAQ are
     matched as exchange names.
   - Of the 104, 88 are in the set. The other 16 cannot appear: ED, LNT, SO, TSLA have no stored release and 10-Q
   pair; COST, DAL, DPZ, FRT, O, PRU, UDR, WRB have no historical sentence that passes the screening; BDX, DOV, FCX,
   NSC have no sentence an earlier set did not use.
5. **Scale:** as for the full set:
   - 1,000 items, at most three originals per company, and every eligible company at least once;
   - 60 mutants per mutator, with every basis candidate that remains;
   - 50 guidance controls at distinct companies, and at least 20 items per feature where the frame allows.

## Composition

The frozen set has **400 companies, 1,000 items and 1,747 gold claim records**. There are 590 originals; 360 mutants
(60 each of value, period, scale, look-ahead and direction, 59 entity and 1 basis); and 50 guidance controls at 50
companies. The gold claims are 1,298 true figures, 360 targets and 89 guidance figures. 190 originals carry the 360
mutants, 170 of them two each. Sampled period ends range from 2025-09-30 to 2026-10-04.

**Removed sentences:** v1 40, v2 39, v3 37 and full 624, by source record SHA-256, plus 15 identical texts.

**Mutation candidates** after those removals: value 1,161, scale 743, direction 525, look-ahead 181, period 146,
entity 60, basis 1.

**Deviations:**

- Basis: 1 mutant instead of 60. It is the frame's only basis candidate left; v1–full used the rest.
- Entity: 59 instead of 60. Of the 60 candidates, one is blocked by the three-sentence cap or the two-mutant limit.
- **Features below 20 items** (marked † in the table):
  - every frame sentence carrying the feature is used: net margin (3 items);
  - the three-sentence cap leaves the rest out: balance sheet (5), bank revenue (5), cash and equivalents (5), net
    interest income (11) and variable-week companies (4).

By company-history slice:

| Slice | Companies | Items | Originals | `value` | `period` | `scale` | `basis` | `look_ahead` | `entity` | `direction` | Guidance | Gold claims |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `company_in_development` | 88 | 232 | 137 | 13 | 15 | 10 | 1 | 15 | 19 | 13 | 9 | 384 |
| `company_new` | 28 | 78 | 44 | 8 | 3 | 6 | 0 | 3 | 2 | 5 | 7 | 142 |
| `company_seen` | 284 | 690 | 409 | 39 | 42 | 44 | 0 | 42 | 38 | 42 | 34 | 1221 |
| **All** | 400 | 1000 | 590 | 60 | 60 | 60 | 1 | 60 | 59 | 60 | 50 | 1747 |

The 28 `company_new` companies: ACN, ADP, CDNS, CL, CMI, CTAS, DG, ETN, F, GE, GEN, GEV, GIS, HON, INTU, MAR, MAS,
NOC, OTIS, PFG, PPG, ROK, RTX, SKYD, SLB, TXN, VZ, XEL. All 28 come from the exhibit rule. Of the other eight
companies it adds, seven are in development (CARR, D, GEHC, PFE, SNA, TGT, VLO), and PGR has no item.

Items by overlapping source feature, mutator and slice are below. A row counts every item that carries the feature, so
rows overlap and must not be summed. `·` means none, and † marks a feature below 20 items.

| Source feature (overlapping) | Original | `value` | `period` | `scale` | `basis` | `look_ahead` | `entity` | `direction` | Guidance | Items | In development | New | Seen | Frame sentences (companies) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `balance_sheet` | 2 | 1 | · | 1 | · | 1 | · | · | · | 5 † | 5 | 0 | 0 | 4 (2) |
| `bank` | 26 | 1 | 1 | 4 | · | 2 | · | 6 | · | 40 | 14 | 0 | 26 | 154 (13) |
| `bank_revenue` | 3 | · | · | 1 | · | · | · | 1 | · | 5 † | 0 | 0 | 5 | 4 (1) |
| `capital_returned` | 12 | 3 | 1 | 4 | · | · | · | · | · | 20 | 4 | 7 | 9 | 13 (9) |
| `cash_and_equivalents` | 2 | 1 | · | 1 | · | 1 | · | · | · | 5 † | 5 | 0 | 0 | 4 (2) |
| `eps_diluted` | 120 | 17 | 10 | 1 | · | 10 | 22 | 5 | 17 | 202 | 43 | 21 | 138 | 862 (262) |
| `free_cash_flow` | 29 | 10 | · | 9 | · | · | · | · | 1 | 49 | 7 | 8 | 34 | 248 (105) |
| `gross_margin` | 25 | 4 | · | · | · | 1 | · | · | 1 | 31 | 9 | 5 | 17 | 102 (40) |
| `growth` | 241 | 12 | 42 | 19 | · | 16 | 15 | 59 | 6 | 410 | 88 | 24 | 298 | 2321 (313) |
| `guidance` | · | · | · | · | · | · | · | · | 50 | 50 | 9 | 7 | 34 | 327 (120) |
| `impact` | 19 | 1 | · | · | · | · | · | · | 1 | 21 | 4 | 2 | 15 | 113 (57) |
| `margin_change` | 25 | 3 | · | · | · | · | · | 1 | · | 29 | 9 | 3 | 17 | 73 (33) |
| `net_income` | 59 | 8 | 4 | 10 | · | 5 | 6 | 4 | 1 | 97 | 27 | 9 | 61 | 377 (140) |
| `net_interest_income` | 9 | · | · | · | · | 1 | · | 1 | · | 11 † | 8 | 0 | 3 | 12 (6) |
| `net_margin` | 1 | 1 | · | 1 | · | · | · | · | · | 3 † | 0 | 3 | 0 | 1 (1) |
| `non_gaap` | 112 | 12 | · | 6 | 1 | · | · | · | 17 | 148 | 30 | 7 | 111 | 1525 (270) |
| `noninterest_expense` | 21 | · | · | 2 | · | · | · | 2 | 3 | 28 | 5 | 0 | 23 | 76 (40) |
| `operating_cash_flow` | 34 | 16 | 9 | 16 | · | 6 | · | 4 | 1 | 86 | 12 | 10 | 64 | 135 (78) |
| `operating_income` | 39 | 5 | 5 | 9 | 1 | 6 | 6 | 5 | 4 | 80 | 19 | 5 | 56 | 542 (134) |
| `operating_margin` | 30 | 6 | · | 2 | · | 1 | · | 2 | 3 | 44 | 14 | 1 | 29 | 268 (85) |
| `opex` | 20 | 1 | · | 1 | · | 3 | · | 1 | 1 | 27 | 9 | 1 | 17 | 68 (26) |
| `reit_or_utility` | 25 | 5 | 1 | 2 | · | · | · | 1 | · | 34 | 6 | 4 | 24 | 46 (15) |
| `revenue` | 154 | 5 | 31 | 18 | · | 26 | 25 | 37 | 16 | 312 | 63 | 12 | 237 | 724 (253) |
| `segment_revenue` | 60 | 1 | · | · | · | · | · | · | 2 | 63 | 16 | 5 | 42 | 1338 (233) |
| `tax_rate` | 27 | · | · | · | · | · | · | · | 3 | 30 | 8 | 4 | 18 | 329 (123) |
| `variable_week_company` | 4 | · | · | · | · | · | · | · | · | 4 † | 0 | 0 | 4 | 7 (2) |
| **All items** | 590 | 60 | 60 | 60 | 1 | 60 | 59 | 60 | 50 | 1000 | 232 | 78 | 690 | |

| Sector | Companies | In development | New | Seen | Items | Gold claims |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Communication Services | 17 | 6 | 2 | 9 | 43 | 77 |
| Consumer Discretionary | 42 | 11 | 2 | 29 | 96 | 166 |
| Consumer Staples | 29 | 9 | 3 | 17 | 70 | 129 |
| Energy | 13 | 3 | 1 | 9 | 33 | 47 |
| Financials | 63 | 16 | 1 | 46 | 156 | 237 |
| Health Care | 54 | 13 | 0 | 41 | 121 | 240 |
| Industrials | 74 | 11 | 12 | 51 | 210 | 355 |
| Information Technology | 72 | 14 | 5 | 53 | 194 | 359 |
| Materials | 19 | 1 | 1 | 17 | 38 | 58 |
| Real Estate | 12 | 3 | 0 | 9 | 25 | 54 |
| Utilities | 5 | 1 | 1 | 3 | 14 | 25 |
| **All** | 400 | 88 | 28 | 284 | 1000 | 1747 |

Items per company: 151 companies have 1, 81 have 2, 76 have 3, 54 have 4, 16 have 5, 6 have 6, 8 have 7, 2 have 8, 5
have 9 and 1 has 10.

Items per company by sector; `*` marks a development company and `+` a new one:

- **Communication Services:** CHTR* 4, CMCSA* 6, DIS* 2, FOXA 2, GOOGL* 4, LYV 3, META* 3, NFLX* 2, NWSA 2, OMC 1, RDDT 3, SKYD+ 1, T 2, TKO 1, TMUS 3, TTWO 1, VZ+ 3.
- **Consumer Discretionary:** ABNB 2, AMZN* 4, APTV 1, AZO 1, BBY 2, BKNG 2, CCL 1, CMG 1, CVNA 1, DASH 3, DECK 2, DHI* 3, DRI 1, EBAY 4, F+ 1, GM* 1, GPC* 1, HAS 2, HD* 1, HLT 3, LEN* 9, LOW* 1, LULU 1, LVS 8, MAR+ 1, MCD* 2, MGM* 3, NCLH 2, NKE 4, NVR 1, ORLY* 1, PHM 2, RCL 5, RL 4, ROST 3, SBUX 2, TPR 1, TSCO 1, ULTA 4, WSM* 1, WYNN 1, YUM 2.
- **Consumer Staples:** ADM 4, BF-B 1, CASY* 3, CHD 4, CL+ 2, CLX 2, DG+ 5, DLTR* 3, EL* 1, GIS+ 5, HSY 1, KDP 2, KHC 2, KMB 3, KO 1, KR 3, KVUE 2, MDLZ 2, MKC 1, MNST* 2, MO 3, PG 2, PM 1, SJM* 1, STZ* 2, SYY* 3, TGT* 6, TSN 1, WMT* 2.
- **Energy:** BKR 4, COP 2, CVX 1, DVN 4, HAL 8, KMI 1, MPC* 1, OKE 1, PSX 1, SLB+ 3, TPL 4, VLO* 2, WMB* 1.
- **Financials:** AFL 1, AIZ 1, AJG 2, ALL 2, AMP 2, AON 1, ARES 3, AXP* 3, BAC* 1, BEN 1, BLK 1, BNY 9, BX 1, C 5, CB 1, CBOE* 1, CFG 5, CME 1, COF* 1, COIN 2, CPAY 2, ERIE 1, FDS* 4, FIS 3, FISV 2, FITB 3, GPN* 1, GS* 4, HBAN 2, HIG* 1, HOOD 1, IBKR 2, ICE 7, IVZ* 1, JPM* 1, KEY* 3, KKR 1, MA 7, MCO* 1, MET 1, MRSH* 9, MS 1, MSCI 3, MTB 3, NDAQ* 4, NTRS 1, PFG+ 2, PNC 3, PYPL 2, RF* 4, RJF 1, SCHW 1, SPGI 2, STT 3, SYF 1, TFC* 5, TROW 6, TRV 1, USB 4, V 1, WFC 1, WTW 2, XYZ 4.
- **Health Care:** A 3, ABT 1, ALGN 1, AMGN* 1, BAX* 2, BIIB 2, BMY 2, CAH 3, CI 3, CNC* 6, COO* 2, COR 4, CRL 2, CVS 7, DGX 2, DHR 1, DVA 7, DXCM 1, ELV 5, EW 2, GEHC* 1, GILD 1, HCA 1, HSIC 1, IDXX 2, ILMN* 1, INCY* 4, IQV 3, ISRG 3, JNJ 1, LH 1, LLY 1, MCK 3, MDT 1, MRK* 2, MRNA 1, MTD* 2, PFE* 1, PODD 1, RMD 4, RVTY* 1, SOLV 4, STE 3, SYK 1, TECH 3, TMO 4, UHS 1, UNH 1, VEEV 3, VRTX* 1, VTRS* 1, WAT 2, WST 2, ZTS 2.
- **Industrials:** ADP+ 2, ALLE* 7, AME 4, AOS* 2, AXON* 3, BA 3, BE 1, BR 1, CARR* 4, CAT 1, CHRW* 2, CMI+ 4, CPRT 1, CSX 3, CTAS+ 4, DD 1, DE 1, EFX 3, EME 3, ETN+ 3, EXPD 4, FAST 3, FIX 2, GD 3, GE+ 2, GEV+ 5, GNRC 5, GWW* 3, HII 4, HON+ 2, HONA 3, HUBB 3, HWM 1, IEX 1, IR 2, ITW* 4, J 1, JBHT* 2, JCI 1, LDOS 4, LHX 5, LII 4, LMT 1, LUV 1, MAS+ 4, MMM 5, NDSN 4, NOC+ 6, ODFL* 5, OTIS+ 3, PAYX 1, PCAR 3, PH 1, PNR 4, PWR 1, ROK+ 2, ROL 4, RSG 1, RTX+ 2, SNA* 2, SWK 1, TDG 3, TT 1, TXT 1, UAL 2, UBER 3, UNP 1, UPS 10, VLTO 5, VRSK 3, VRT 2, WAB 2, WM* 5, XYL 4.
- **Information Technology:** AAPL* 1, ACN+ 1, ADBE 4, ADI* 3, ADSK 4, AKAM* 2, AMAT 3, AMD* 1, ANET 1, APH 1, AVGO 3, CDNS+ 1, CDW 2, CIEN 3, COHR 3, CRM 2, CRWD 3, CSCO 6, CTSH 4, DDOG 3, DELL 3, FFIV 4, FICO 1, FLEX* 9, FSLR* 3, FTNT 7, GDDY 3, GEN+ 4, GLW 2, GRMN 3, HPE* 5, HPQ 3, IBM 1, INTC* 1, INTU+ 1, IT 1, JBL 4, KEYS* 2, KLAC 2, LITE 1, LRCX 2, MPWR* 3, MRVL 4, MSFT* 2, MSI* 4, MU 1, NOW 4, NTAP 1, NVDA* 3, NXPI 1, ON* 1, ORCL 4, P 5, PANW 3, PLTR 2, Q 1, QCOM 1, SMCI 1, SNPS 1, STX 1, SWKS 1, TDY 7, TEL 3, TER 1, TRMB 1, TWLO 9, TXN+ 4, TYL 1, VRSN 4, WDAY 1, WDC 3, ZBRA 3.
- **Materials:** ALB 1, AMCR 3, APD 1, AVY 2, CRH 2, DOW 1, ECL 4, IFF 1, IP 3, LIN 4, MLM* 1, MOS 1, NEM 1, NUE 3, PPG+ 1, SHW 1, STLD 4, SW 3, VMC 1.
- **Real Estate:** CBRE 3, CCI 1, CSGP* 2, ESS 1, EXR* 1, INVH 2, IRM 2, KIM 1, SBAC* 3, SPG 1, VICI 7, VTR 1.
- **Utilities:** AWK 1, CNP 3, D* 2, DUK 4, XEL+ 4.

## Freeze and offline validation

- **Corpus:** `eval/corpus/spot500_full2.jsonl`, SHA-256 `10aeb6cf7dc9bae3f1301e697cc56aea68dd174cea074eb4e58626b19ea7fed0`.
- **Manifest:** `eval/spot500_full2_manifest.json`, SHA-256 `343cc6795d087e300c16fd67f80c9bfaa5f4d08588cc6874f27fd13483094235`.
  - It records the frame rule, the companies and documents it adds, the removed sentences and the three
    company-history groups with their derivation.
  - It also records the four earlier sets' hashes, the changes from v1 with each deviation, the feature coverage,
    the per-item, source-record and builder hashes, and counts by company, sector, feature, mutator and slice.
- **Frame:** the strict part of the frame reproduces the full set's fingerprint (`d02356ff…`).
  The build refuses to run otherwise.
- **Rebuilds:**
  - An independent complete source rebuild, in a separate process, produced both full2 files byte for byte.
  - The v1, v2, v3 and full builds, run in place with this builder, were accepted unchanged. The full set now pins
    its freezing builder (the git blob at `9bff2eb`), as v1–v3 do.
- **Oracle:** only the oracle stub has run on full2. Its [CLI record](spot500_runs/full2-oracle-20261102.json):
  - 1,000/1,000 items completed, with 1,747/1,747 status-and-reason matches and 360/360 targets.
  - There were zero original false alarms and zero skips, errors or extra claims.
  - Its `by_feature` summary gives the three slices: 232, 78 and 690 items.
  - This is 100% **by construction**. Reusing the output was refused with `FileExistsError`.
- **Isolation:** the builds and the oracle ran under an audit hook that refused network access, dotenv files and
  writes under the source data root. There were zero provider calls and $0 spend.
- **Tests:** the offline suite ran with `SPOT500_DATA_ROOT` set, so the source rebuild of all five sets also ran.
  - Final suite: **3,787 passed, 4 skipped, 1 deselected**. The skips are existing data-dependent skips, and the
    deselected test is the one that deliberately reads a temporary `.env`.
  - Validation details are in the [full2 validation JSON](spot500_full2_validation.json).

## Runner rules and the one live run

`eval/spot500.py --set full2` follows the full set's rules:
- an admission cap of at most $6 and a harness estimate of at most $5.50;
- records under `eval/spot500_runs/full2-oracle-<date>.json` or `full2-live-<date>.json`;
- each output single use, and a live run refused once any `full2-live-*` record exists.

**Cost and runtime.** The harness estimate is 1,000 × $0.005 = **$5.00**, within the $5.50 limit. The full set's
live run made 940 calls for $3.69 ($0.0039 per call) in 15.5 minutes at concurrency 4. full2 has the same shape, 940
unique requests, so expect about $3.7 (at most about $5.5 at v1–v3's highest rate) and about 15 minutes.

This branch is `8c2fd78` plus these evaluation files: its `receipts/` and `data/snapshot/` are those of `8c2fd78`.
Run once, from this worktree (or a merge that keeps them), with `MODEL_API_KEY` exported in the shell:

```bash
/Users/shou-fengchang/Desktop/Meta/.venv/bin/python eval/spot500.py --set full2 --live --evidence-dir /Users/shou-fengchang/Desktop/Meta/data/snapshot --budget 6 --concurrency 4 --output eval/spot500_runs/full2-live-20261102.json
```

If `receipts/` changes again before the run, re-derive the development list against the new commit and refreeze.

## Results of the one live run (November 2, 2026, after round 8)

**Run.** 2026-11-02 06:05:10–06:33:16 UTC (14:05–14:33 Taipei), `live:meta:muse-spark-1.3`, concurrency 4,
[result record](spot500_runs/full2-live-20261102.json), at the owner's explicit request.

**Code and evidence measured.**
- The deployed round-8 release: private `8c2fd78`, public `8abbd66`, Production `dpl_H39vsq9VaVdogc9ZBqXPtRK4xrBr`.
- The run script checked that `receipts/` and `data/snapshot` equal the exported commit, and the input hashes were
  identical before and after (`inputs_unchanged: true`).

**Completed.** 1,000/1,000 items, with 0 errors and 0 budget skips. 940 model calls; measured usage cost
**$3.79727435** against the $5.00 estimate and the $6 cap.

Every number below comes from `spot500_report.py`:

```bash
.venv/bin/python eval/spot500_report.py eval/spot500_runs/full2-live-20261102.json --slice company_new --slice company_seen --slice company_in_development --slice-without company_in_development
```

The 95% intervals are computed as for the full set.

| Measure | All 400 companies | 312 not in development | 28 new | 88 in development |
| --- | ---: | ---: | ---: | ---: |
| Planted errors caught (✗, or ◷ for look-ahead) | **306/360 (85.0%; 79.9–89.5%)** | **235/274 (85.8%; 79.8–90.8%)** | 18/27 (66.7%) | 71/86 (82.6%) |
| Planted errors marked ✓ | **0/360 (0.0–1.1%)** | 0/274 | 0/27 | 0/86 |
| Planted errors returned `?` | 54/360 | 39/274 | 9/27 | 15/86 |
| True figures marked ✗ | **17/1,298 (1.3%; 0.7–2.1%)** | **14/1,018 (1.4%; 0.6–2.3%)** | 2/102 | 3/280 |
| True figures verified | 736/1,298 (56.7%; 52.5–60.6%) | 574/1,018 (56.4%) | 51/102 (50.0%) | 162/280 (57.9%) |
| True figures returned `?` (neither ✓ nor ✗) | 533/1,298 (41.1%) | 423/1,018 | 49/102 | 110/280 |
| A ✓ that landed on a true figure | **736/738 (99.7%; 99.0–100%)** | 574/576 | 51/51 | 162/162 |
| A ✗ that landed on a planted error | **257/275 (93.5%; 89.8–96.6%)** | 198/213 | 16/18 | 59/62 |
| Guidance figures marked ✓ | 2/89 | 2/71 | 0/13 | 0/18 |

**Other counts.**
- Of the 1,298 true figures, 12 were not extracted as a matching claim.
- All 49 ◷ marks landed on look-ahead copies.
- Status was correct for 1,098/1,747 gold claims, and status and reason for 1,029/1,747.
- There were 125 extra claims (15 ✓, 2 ✗), 99 missing claims, 2 duplicate matches and 0 conflicting matches.
- 12/590 original items carried a false alarm.

**The slices.**
- The 28 new companies are the ones the exhibit-label fallback added. They appear in no earlier set. They caught
  fewer planted errors (18/27), on a small sample.
- The 284 `company_seen` companies appeared in v1–v3 or full with other sentences. Round 8 re-verified full's recorded
  claims for every one of them.

| Mutator | Planted | Caught (✗/◷) | Expected reason | Marked ✓ | `?` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `basis` | 1 | 1 | 0 | 0 | 0 |
| `direction` | 60 | 45 | 45 | 0 | 15 |
| `entity` | 59 | 54 | 0 | 0 | 5 |
| `look_ahead` (◷) | 60 | 49 | 49 | 0 | 11 |
| `period` | 60 | 52 | 44 | 0 | 8 |
| `scale` | 60 | 53 | 53 | 0 | 7 |
| `value` | 60 | 52 | 51 | 0 | 8 |

Selected overlapping source features count every claim in the items that carry the feature. Do not sum them.

| Feature | True verified | True marked ✗ | Planted caught |
| --- | ---: | ---: | ---: |
| `revenue` | 317/401 | 4 | 118/142 |
| `growth` | 444/650 | 6 | 136/163 |
| `eps_diluted` | 153/250 | 1 | 62/65 |
| `net_income` | 90/142 | 0 | 26/37 |
| `operating_income` | 67/95 | 0 | 37/37 |
| `operating_margin` | 36/71 | 0 | 9/11 |
| `gross_margin` | 26/55 | 5 | 2/5 |
| `operating_cash_flow` | 78/97 | 2 | 46/51 |
| `free_cash_flow` | 43/73 | 2 | 18/19 |
| `bank` | 27/53 | 0 | 9/14 |
| `reit_or_utility` | 20/64 | 2 | 8/9 |
| `non_gaap` | 78/248 | 3 | 19/19 |
| `segment_revenue` | 22/119 | 2 | 1/1 |
| `tax_rate` | 4/47 | 0 | · |

| Sector | Companies | Planted caught | Marked ✓ | True verified | True marked ✗ |
| --- | ---: | ---: | ---: | ---: | ---: |
| Communication Services | 17 | 12/16 | 0 | 42/59 | 1 |
| Consumer Discretionary | 42 | 30/34 | 0 | 81/128 | 1 |
| Consumer Staples | 29 | 16/21 | 0 | 41/99 | 2 |
| Energy | 13 | 12/14 | 0 | 21/33 | 0 |
| Financials | 63 | 44/54 | 0 | 79/176 | 2 |
| Health Care | 54 | 35/37 | 0 | 112/184 | 0 |
| Industrials | 74 | 64/84 | 0 | 138/252 | 3 |
| Information Technology | 72 | 73/77 | 0 | 175/260 | 6 |
| Materials | 19 | 12/12 | 0 | 25/39 | 0 |
| Real Estate | 12 | 6/8 | 0 | 21/46 | 1 |
| Utilities | 5 | 2/3 | 0 | 1/22 | 1 |

**No planted error was marked ✓.** All 54 missed copies returned `?`.

**The 2 guidance figures marked ✓** are both TE Connectivity's "14% sales growth and 19% EPS growth in third quarter
of fiscal 2026". These are actual results that its headline line puts next to the word "guidance". NoCap verified them
against the 10-Q (13.8% and 19.2%), which is correct on review. The frozen labels still count them as guidance.

**The 17 true figures marked ✗**, by cause (*development* marks a development company):
- **A comparison base read as the wrong period (4).** Teledyne's "compared with $343.1 million" (two items) and
  Comfort Systems' "compared to $2.17 billion in 2025" were read as full-year bases. CenterPoint's "$0.53 per diluted
  share in the comparable period of 2025", the base of a non-GAAP EPS, was compared with GAAP EPS.
- **An adjusted, core or segment margin compared with another measure (6).**
  - Corning's "Core gross margin" (39.1%) and General Mills' "Adjusted gross margin … 33.2 percent of net sales" were
    compared with other gross margins.
  - Ameriprise's "Adjusted operating expenses … $2.3 billion" was compared with total operating expenses.
  - PulteGroup's "Home Sale Gross Margin of 24.8%" was compared with a computed company margin.
  - Axon's "Connected Devices gross margin … 51.9% from 48.6%" (*development*, 2) was compared with a 60.4% segment
    margin from its release.
- **A part of the business compared with the whole (2).** Nasdaq's "Other revenues of $2 million and $4 million"
  within one division (*development*) and AT&T's operating expenses "up 11.4%", in a sentence whose segment is named
  earlier, were compared with company totals.
- **A conditional figure (1).** Crown Castle's growth "increases to 3.5% if DISH revenues are excluded" was compared
  with reported revenue growth.
- **A different revenue definition (1).** Altria's "Net revenues increased 0.8%" was compared with revenue growth
  of 0.99%.
- **A line with no period of its own (2).** TE Connectivity's "Free cash flow was $1.3 billion, up 17% year over
  year" was compared with the quarter's $680 million.
- **A label error (1).** In a direction copy of Texas Instruments' sentence, the flipped word governs both figures
  ("Revenue decreased 11% sequentially and 28% from the same quarter a year ago"). NoCap's ✗ on the 28% is right; the
  frozen label calls it true.

These patterns are recorded for later fixes. The run is not repeated after any fix.

**Where `?` remains.** The 533 true figures returned `?`, grouped by NoCap's explanation:
- 156: a kind of figure NoCap does not track;
- 143: a qualifier it will not match to a filed figure;
- 77: a non-GAAP line with no matching release fact for the period;
- 30 constant-currency and 16 "organic" figures;
- 27: a period or company it could not resolve; 9: no single filing reports both compared periods;
- 17: read as guidance;
- 58 others.

**Five runs.**

| Run | Code | Items | Planted caught | Marked ✓ | True verified | True marked ✗ | A ✓ right |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1 | Nov 1 (`42d48aa`) | 75 | 27/35 | 0 | 54/72 | 0/72 | 54/54 |
| v2 | Nov 2, early (`f22e56c`) | 74 | 27/35 | 1 | 48/75 | 4/75 | 48/49 |
| v3 | Nov 2, morning (`84b8fd4`) | 69 | 29/32 | 0 | 58/74 | 1/74 | 58/58 |
| full | Nov 2, before round 8 (`8d69d3c`) | 1,000 | 310/376 | 2 | 741/1,271 | 14/1,271 | 741/744 |
| full2 | Nov 2, round 8 (`8c2fd78`) | 1,000 | 306/360 | 0 | 736/1,298 | 17/1,298 | 736/738 |

full and full2 use different sentences, and full2 adds 28 companies, so the table is not a controlled before/after
comparison. Each set ran once and is never rerun or pooled.

## After the two comprehensive tests: round 9 and release statements

Two changes came after full2 ran, prompted by drafts the owner pasted on the live site. Neither set is rerun, and
neither set's numbers above include them. The checks below re-verify the runs' recorded claims offline at $0, with the
same scripts as round 8. They are regression checks, not accuracy results.

**Round 9 (`895d819`, merged `0354ba6`).** Words that never change what a figure measures no longer block a check:
- hedges away from their number, discourse and degree adverbs ("also", "slightly"), evaluative adjectives and
  markdown emphasis;
- bounds ("more than", "nearly", "below") stay qualifiers.

| Set | True figures | Planted errors |
| --- | --- | --- |
| full | `?`→✓ 7 | `?`→✗ 5, `?`→◷ 2 |
| full2 | `?`→✓ 10 | `?`→✗ 4, `?`→◷ 1 |

No verdict moved away from correct.

**Release statements (index `382208a` by Codex; matcher `7b8f293` and receipt `878a7a0`, merged `73a3682`).**
- **What it does.** A figure that stored facts leave `?` with no evidence becomes ✓ when the company's own filed 8-K
  earnings release for that quarter states it.
- **What must match.** The same number at its written precision, measure, qualifiers, direction and comparison. Every
  companion figure must be in the sentence, and no rival figure for the measure.
- **What it never does.** It never gives ✗. Guidance is excluded.

Results:
- **Planted errors and guidance figures that moved to ✓:** 0 in full and 0 in full2.
- **True figures that moved from `?` to ✓:** 208 in full and 189 in full2.
  - These gains are **circular**: those items are sentences taken from the same releases. They show that the matcher
    finds the sentences, not how often real drafts get confirmed.
- **Perturbation check.** Every claim the matcher confirmed was perturbed: 1,834 copies with a changed number, basis or
  direction. No copy with a changed number still matched.
- **Dev replay.** 45 of 693 claims moved from `?` to ✓, all release statements. Each is a true figure: 36 in unmutated
  items, and 9 unaffected figures in mutated items, such as the "up 19% in constant currency" beside a planted
  "$38.3 million".
- **Saved examples.** Their verdicts are unchanged.
- **The three drafts the owner pasted,** which these fixes target, so this is not a measurement:
  - Microsoft's: all six remaining `?` (constant-currency growth, Microsoft Cloud revenue) are confirmed.
  - Costco's: net sales, its growth and base, the tariff benefit and the warehouse count are confirmed. Members,
    renewal rates and comparable sales stay `?`: the first two are not in the release, and table rows are not
    indexed yet.
  - McDonald's: global and U.S. comparable sales, systemwide sales and three loyalty figures are confirmed. Round 9
    confirms its 4% and 3%.
