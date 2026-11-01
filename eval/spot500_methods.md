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
