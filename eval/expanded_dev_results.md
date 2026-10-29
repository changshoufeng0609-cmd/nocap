# Expanded dev comparison: 50 fixed items

Both extraction backends caught **24/24 seeded errors**, identified the expected reason for **23/24**, and produced **0/24 false-alarm original sentences**. Both guidance controls stayed unverifiable. This is a development-set diagnostic with provisional labels, **not held-out accuracy or evidence that unrestricted model answers are reliable**. The original [13-item benchmark](bench_results.md) remains unchanged.

| Measure | Muse Spark 1.3 · Meta | Muse Spark Open · Baseten |
|---|---:|---:|
| Evaluated items | 50/50 | 50/50 |
| Seeded errors caught | 24/24 | 24/24 |
| Expected reason | 23/24 | 23/24 |
| Incorrect target marked verified | 0/24 | 0/24 |
| Original sentences flagged wrong | 0/24 | 0/24 |
| Original claims verified / unverifiable | 49 / 7 | 49 / 7 |
| Guidance controls entirely unverifiable | 2/2 | 2/2 |
| Extraction errors / skipped items | 0 / 0 | 0 / 0 |
| New provider calls | 0 | 48 |
| Application response-cache reuse | 50 historical | 2 from this run |
| New usage-priced cost | $0 | **$0.24020610** |

Each of value, period, scale, basis, entity and look-ahead mutations contributed four items. Both models caught all four in every category. Each category had 4/4 expected reasons except entity, at 3/4: `v0-BAC-FY2025Q3-s002429-entity` was rejected as `value_mismatch`, while its provisional label expects `entity_mismatch`. The label and verifier were left unchanged.

## Selection fixed before live calls

[Manifest](expanded_dev_manifest.json): `v0_dev.jsonl` contains 224 dev items. Python `Random(20261029)` samples without replacement from each pool sorted by `item_id`, in this order: 24 originals, four each of value/period/scale/basis/entity/look-ahead, then two guidance controls. No source shortage required adjustment. Every ID, complete input-record SHA-256, corpus SHA-256 and per-backend extraction-request hash was saved before Baseten dispatch. Selection did not use model outcomes. Four items overlap the earlier fixed 13.

The sample covers nine companies: AMD, AMZN, BAC, GOOGL, JPM, META, MSFT, NFLX and NVDA. AAPL was not drawn by the fixed seed. TSLA is not in this corpus, so this comparison does not measure its new evidence coverage. There are 48 unique extraction requests: two original/look-ahead pairs share text and context but have different `as_of` cutoffs. The fresh Baseten response was reused for the second member of each pair; deterministic verification still ran separately at each cutoff. These correlated pairs reduce the effective independence of the 50 items.

## Timing, usage and protection

The live run was **2026-10-29 06:52:06–06:53:01 UTC**. Meta used existing responses dated **2026-10-27 09:45:16–10:59:12 UTC**, with a cache-only client that cannot send a request. Baseten used `meta-models/muse-spark-open-1026` at `https://inference.baseten.co/v1`, the existing prompt-schema backend and a new application-cache directory. Its 48 calls completed without retry, failure or missing usage; recorded per-call median was **4.21 s**. Meta's older cache records contain no comparable latency. Different dates, API formats and provider prompt-cache behavior prevent a controlled speed or cost ranking.

Baseten reported **113,254 input tokens**, including **102,784 cached input tokens**, and **49,812 output tokens**, including **38,347 reasoning tokens**. Reasoning tokens are already within output and were not billed twice. Independent Decimal arithmetic and cache/ledger reconciliation give **$0.24020610** at the published **$1.25 input / $0.15 cached input / $4.25 output per million tokens**, checked on 2026-10-29. This is usage priced from SDK responses, not an invoice or a credit-balance check. [Baseten pricing](https://www.baseten.co/library/muse-spark-open/).

Every attempted SDK dispatch passed one shared lock, fixed request-hash/backend/model/output-limit checks, a **$3 conservative admission budget**, and a **60-attempt maximum**. The ledger atomically reserved UTF-8 request bytes plus framing padding and the complete 12,000-token output allowance before dispatch. Unknown usage or a dispatched failure retains its reservation; only valid usage settles it. The process-local guard is not a provider billing ceiling. No extra gold, raw-answer, Meta live, SEC, held-out or deployment calls were made.

## Reproduction and audit

- [Result record](expanded_dev_runs/expanded-dev-20261029.json): all 100 extracted claim/verdict records, request/cache hashes, response dates, frozen evidence hashes, unchanged verifier hashes, and every SDK attempt.
- [Independent usage audit](expanded_dev_runs/expanded-dev-20261029-audit.json): 48 usage records agree with response caches and the ledger; no retained unknown estimates; $0.24020610.
- [Zero-spend extraction/verifier replay](expanded_dev_runs/expanded-dev-20261029-replay.json): all 100 item outcomes and aggregate quality metrics reproduced with cache-only clients; zero cache misses or model calls.

Rescore the committed claim/verdict records without credentials or the ignored raw cache:

```bash
.venv/bin/python eval/expanded_dev.py --score-record eval/expanded_dev_runs/expanded-dev-20261029.json --output /tmp/nocap-expanded-rescore.json
```

Re-run extraction and verification from the recorded local response caches and frozen evidence, if those ignored artifacts are available:

```bash
.venv/bin/python eval/expanded_dev.py --replay eval/expanded_dev_runs/expanded-dev-20261029.json --output /tmp/nocap-expanded-replay.json
.venv/bin/python eval/audit_expanded_dev.py eval/expanded_dev_runs/expanded-dev-20261029.json
```

A new paid run requires explicit `--live-baseten --run-id <new-id>` and the existing fixed manifest; it refuses changed inputs or reused output/ledger paths. Do not rerun live merely to rescore. The sample was selected from a development corpus used during implementation; there was no label/verifier tuning against this subset. Human review of the provisional labels and broader independent evaluation remain separate work. The held-out test split was not run.
