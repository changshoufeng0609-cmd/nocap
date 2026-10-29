# Closed-book financial Q&A — 50 fixed questions, four backends

All **200 planned calls completed**, with no retries, API errors, unstarted questions or unknown token usage. This comparison did not use the held-out corpus. Models answered directly from their own knowledge with a schema, without filings, retrieval or tools. The reference labels come from supported public SEC snapshot facts and remain provisional.

The pre-registered parser was too strict about unit/period spellings. Its results are retained below. The second table is an explicitly **post-run, independently reviewed metadata sensitivity analysis**; it never changes numeric text, companies, metrics or stated years. Neither table is a broad model ranking. [Methods, exact sources, frozen hashes and limitations](qa_methods.md).

The experiment uses the **pre-J2 frozen gate**. Subsequent production verifier fixes do not rewrite these 200 records. The public source overlay reproduces this historical version; these metrics are not a new evaluation of the latest runtime gate.

## Original exact-contract result

| Model | Correct under contract | Binding/numeric rejection | Abstained | Format failures | Accepted | Verified precision |
|---|---:|---:|---:|---:|---:|---:|
| Spark 1.3 | 0 | 21 | 21 | 8 | 0/50 | N/A |
| Wonder 1.0 | 0 | 29 | 6 | 15 | 0/50 | N/A |
| Spark Open / Baseten | 0 | 23 | 27 | 0 | 0/50 | N/A |
| Glimmer / Modal | 0 | 13 | 0 | 37 | 0/50 | N/A |

The rejection column mixes metadata and numeric problems. **It must not be described as a financial-error count.** An unsupported spelling can reject an otherwise correct value. Zero accepted answers gives N/A precision.

## Post-run metadata sensitivity

| Model | Correct | Wrong number/binding | Abstained | Unassessable metadata | Format failures | Verified correct/accepted | Coverage | Incorrect passed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Spark 1.3 | 14/50 | 7 | 21 | 0 | 8 | 14/14 (100%) | 14/50 (28%) | 0 |
| Wonder 1.0 | 0/50 | 0 | 6 | 29 | 15 | 0/0 (N/A) | 0/50 (0%) | 0 |
| Spark Open / Baseten | 17/50 | 4 | 27 | 2 | 0 | 17/17 (100%) | 17/50 (34%) | 0 |
| Glimmer / Modal | 6/50 | 6 | 0 | 1 | 37 | 6/6 (100%) | 6/50 (12%) | 0 |

Within this small supported set, the gate withheld all 17 assessable wrong answers/bindings. That precision comes with substantial withholding and does not establish general safety. Baseten's four wrong outcomes comprise three numeric errors and one question-binding error. No wrong-binding outcome is counted as a numerically wrong value by itself.

Wonder has **no assessable answer under this policy**: 23 records retain unsupported accounting-basis labels, five retain unrecognized entity metadata and one retains an unsupported period label; another 15 fail the response contract and six abstain. Its 0/50 correct figure is not evidence of zero financial knowledge. Glimmer's 37 format failures likewise remain contract failures, not financial errors. All providers returned completed responses; these format failures are validation failures, not request timeouts. The policy was not expanded again after these results.

## Cost, latency and settings

| Model | Calls | Usage-priced API cost | Median request | Batch wall | Output limit / reasoning |
|---|---:|---:|---:|---:|---|
| Spark 1.3 | 50 | $0.084264 | 2.75 s | 73.19 s | 768 / minimal |
| Wonder 1.0 | 50 | $0.347180 | 4.28 s | 109.91 s | 768 / minimal |
| Spark Open / Baseten | 50 | $0.137061 | 2.41 s | 66.17 s | 2,048 / low |
| Glimmer / Modal | 50 | GPU billed separately | 19.37 s | 526.73 s | 2,048 / low |

All targets used concurrency two. Token-priced usage totals **$0.568505**, independently recomputed against the public admission ledger; no unknown-usage reservation remains. This is usage-priced accounting, not a provider invoice. Different providers, formats, caches and reasoning/output settings prevent a controlled speed or cost ranking.

Observed Modal GPU metering totals **$1.25997788 across three shared validation attempts**. The successful final session accounts for **$0.82043920** and includes warmup, a Private browser request and all 50 Glimmer Q&A requests. It cannot be attributed entirely to Q&A or counted twice. All three apps were confirmed stopped with zero tasks. Metering may lag. [Private validation and GPU billing scope](../docs/private-http-live-validation-2026-10-29.json).

## Reproduction and artifacts

- [Frozen questions and reference sources](qa_manifest.json).
- [Original responses and source overlay](qa_runs/qa-closed-book-20261029/).
- [Sensitivity decisions, original-file hashes and usage ledger](qa_runs/qa-closed-book-20261029/sensitivity.json).
- [Public-input offline reproduction: 200/200 records and every aggregate metric matched](qa_runs/qa-closed-book-20261029/reproduction.json).
- [Local production browser acceptance](qa_runs/qa-closed-book-20261029/browser_acceptance.json): desktop 1440px and actual mobile 390px, six collapsed/expanded states without page overflow, two downloaded JSON files matching the export, zero console/page/HTTP errors; GET only, no model calls or fixtures.
- [Offline reproduction command and safeguards](qa_methods.md#reproduction-and-records).

The reproduction uses only committed public artifacts, the frozen source overlay and the public snapshot. Model-client construction and network/SEC access are blocked; no live-run database, key or ignored response file is required. The website offers every question, raw response, gate decision and filing link, plus a downloadable JSON record. The dataset, gate and scoring reference share source filings; independent human adjudication and broader out-of-domain evaluation remain open.

Validation passed the production build (including TypeScript), page lint and 21 top-level QA/publication/reproduction tests. Two of those tests launch the original 28 benchmark and 31 normalization cases in isolated frozen-code subprocesses; these 59 nested cases are not additional top-level test counts. Public-only replay still matched all 200 records after the main production core and snapshot manifest were changed for J2 fixes.
