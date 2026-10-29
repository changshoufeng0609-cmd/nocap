# Frozen held-out evaluation — October 29, 2026

The authorized, one-time held-out run caught **44/48 planted errors (91.7%)** in
v0 and **20/20 (100%)** in the seven trap drafts. It produced **one false-alarm
original item out of 47**. These are small, seeded, provisionally labeled tests,
not a general accuracy guarantee.

The run used Meta `muse-spark-1.3` for extraction and the deterministic verifier
after the J2 fixes. Source, corpus and committed evidence were frozen before the
run: **126 files**, manifest source commit
`61e10552409419d940735f91dd41f139a088c992`. The runner import fix is its ancestor
`cd63e53`; that is not a separate evaluation freeze. An independent AI-agent audit rehashed
every file and checked the complete path set after completion: unchanged. No
result-driven changes, label edits, retries or second model run were made.

## Results and denominators

| Metric | v0 held-out | Trap held-out |
| --- | ---: | ---: |
| Completed evaluation items | 97/97 | 7/7 drafts |
| Planted errors caught as wrong | 44/48 (91.7%) | 20/20 (100%) |
| Expected error reason | 42/48 (87.5%) | 18/20 (90.0%) |
| False-alarm original items | 1/47 (2.1%) | Not the trap metric |
| False-alarm original/true figures | 1/110 (0.9%) | 0/52 (0%) |
| Original/true figures returned unverifiable | 22/110 (20.0%) | 3/52 (5.8%) |
| False-alarm scored true sentences | Not the v0 metric | 0/25 (0%) |
| Guidance controls returned unverifiable | 3/3 figures in 2 items | 10/10 figures |
| Evaluation errors / budget skips | 0 / 0 | 0 / 0 |

The 97 v0 items comprise 48 mutants, 47 originals and 2 guidance controls.
Original verdicts were 87 verified, 1 wrong and 22 unverifiable. All four uncaught
mutants returned `?`, rather than verified; no planted target was marked verified.
The false-alarm original is retained as `v0-NVDA-FY2026Q3-s006548` (one wrong and
two verified figures). It was not used to tune this release.

The seven trap drafts contain 20 planted errors, 52 labeled true figures and
10 guidance figures. Their 82 extracted claims partition into 49 verified,
20 wrong and 13 unverifiable. There were no missing planted targets, conflicting
verified verdicts, extra claims or evaluation errors. The sentence denominator
includes non-error sentences with at least one extracted figure; it does not
measure extraction recall over every sentence.

| Error type | v0 caught / targets | v0 expected reason | Trap caught / targets | Trap expected reason |
| --- | ---: | ---: | ---: | ---: |
| Value | 4/6 | 4/6 | 4/4 | 4/4 |
| Period | 8/8 | 8/8 | 2/2 | 2/2 |
| Scale | 7/8 | 7/8 | 4/4 | 4/4 |
| Accounting basis | 5/5 | 5/5 | 4/4 | 4/4 |
| Entity | 10/10 | 8/10 | 4/4 | 2/4 |
| Look-ahead | 10/11 | 10/11 | 2/2 | 2/2 |

## Calls, cache and cost

| Corpus | New SDK calls | Within-run response reuses | Usage-priced cost |
| --- | ---: | ---: | ---: |
| v0 | 86 | 11 | $0.38726840 |
| Trap | 31 | 0 | $0.11107785 |
| Total | **117** | **11** | **$0.49834625** |

All 117 attempts completed with known usage. Failed calls, unknown-usage
reservations and sequential retries were zero. The run stayed within its
$1.50 admission allocation and 250-attempt limit. No pre-existing response cache
entries were copied, and no previous evaluation results were reused. The 11
response-cache hits were repeated exact requests within this run. Evaluation
`fresh` counts refer to items (97 and 7), not SDK calls; the trap console's
117 metered calls is the cumulative run total, not 117 trap calls.

The independent audit repriced each attempt using 259,347 input tokens,
including 238,945 cached tokens, and 102,824 output tokens. Rates were $1.25
per million uncached input, $0.15 cached input and $4.25 output tokens.
The 68,443 reasoning tokens are already included in output and were not added
again. This is API usage-based pricing, not a provider invoice reconciliation.
The audit itself made no model or SEC requests.

## Evidence and limits

- [Freeze manifest](heldout_freeze.json), [run start](heldout_runs/release-20261029/started.json)
  and [run completion](heldout_runs/release-20261029/finished.json).
- [v0 retained rows](heldout_runs/release-20261029/v0.json) and
  [trap retained rows](heldout_runs/release-20261029/trap_v1.json).
- [Attempt ledger](heldout_runs/release-20261029/attempts.json),
  [extraction metadata](heldout_runs/release-20261029/extractions.json) and
  [initial cache inventory](heldout_runs/release-20261029/cache_inputs.json).
- [Independent arithmetic and hash audit](../docs/heldout-review-2026-10-29.json)
  records exact evidence hashes, the retained misses and all denominators.
- [Public source history](https://github.com/changshoufeng0609-cmd/nocap/tree/adaf6ba849b561e07a22367815a35a00a69f7296)
  maps the frozen source checkpoint to the public reconstruction; all 23
  overlapping frozen source files match. The [history map](../docs/source-history-map.json)
  documents this mapping. That branch shows source/config chronology, not the
  complete evaluation/data history or an independent timestamp attestation.

The audit independently recalculated the row-level catch, expected-reason and
original false-alarm totals, counted frozen label metadata and repriced usage.
Trap true-figure status and sentence aggregates were crosschecked against their
retained summary partitions; this was not a second extraction/scoring run or
independent human relabeling.

Labels remain provisional and machine-assisted. The seeded examples share
companies, filings and mutation construction, so observations are correlated.
Supported metrics, source coverage and extraction omissions limit interpretation.
These results do not establish correctness for arbitrary financial drafts.

The [four-model closed-book Q&A comparison](qa_results.md) is a separate
experiment: it used a pre-J2 frozen gate and 200 model answers. Its results must
not be pooled with this held-out draft-verification evaluation.
