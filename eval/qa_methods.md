# Closed-book financial Q&A benchmark

This development experiment asks four model backends the same 50 financial questions, then checks their proposed numbers with NoCap's deterministic verifier. It is separate from the draft-extraction benchmark and does not use the Ask planner. Models receive a question and a typed answer schema, **without filings, retrieval, search or tools**. Results cannot establish gains for a retrieval-augmented system or broad hallucination reduction.

## Frozen design and provenance

Run ID: `qa-closed-book-20261029`. Seed: `20261029`. The [manifest](qa_manifest.json), [core](qa_core.py), [runner](qa_benchmark.py) and 28 offline tests were independently reviewed and committed as `e66c5d9` before inference. Manifest SHA-256: `cbb66ebe97df238be8aa0df1e05fcda60507b605344a19097ce8c3470abe8224`.

Questions were selected only from the public SEC snapshot, with evidence cutoff `2026-10-29T06:48:28Z`. No held-out corpus was read or run for this experiment. The set includes 11 companies, eight GAAP metrics, and fiscal quarters in FY2023–FY2026. Every company has at least four metrics and four periods; the seed assigns six companies a fifth question. Eligible visible filing versions must agree on one value, cover a 70–110-day fiscal quarter, use consolidated USD or USD/share facts, and be supported by the existing verifier. Selection therefore favors unambiguous, supported evidence.

Each question asks for a dollar amount to the nearest million or EPS to the nearest cent. The manifest contains the exact question, reference value, complete source Fact records, filing accession and acceptance times, source URLs, snapshot hashes, prompt/request hashes and code hashes. The snapshot is copied and hash-checked for the run. Reference labels are automatically derived and provisional; independent human adjudication remains pending. No question or reference was changed in response to model output.

## Model and runtime settings

| Target | Hosting | Output-token limit | Reasoning | Concurrency |
|---|---|---:|---|---:|
| Muse Spark 1.3 | Meta Model API, Standard | 768 | minimal | 2 |
| Muse Wonder 1.0 | Meta Model API, Standard | 768 | minimal | 2 |
| Muse Spark Open | Baseten, `meta-models/muse-spark-open-1026` | 2,048 | low | 2 |
| Muse Glimmer | Temporary self-hosted Modal H100 | 2,048 | low | 2 |

Each target gets one planned attempt per question, no retries, at most 120 seconds per request and a 720-second batch allowance. All 50 questions remain in reported denominators if a call fails or never starts. Meta uses its Responses API JSON schema contract; open models use the configured compatible chat interface. Provider dates, cache behavior, reasoning modes, output limits and formats differ. Latency excludes Modal deployment and warmup. These are operational observations, not a controlled model speed/cost ranking.

The shared token-API admission ledger reserves conservative input/output cost before each attempt, with a $3.50 ceiling for the three hosted token providers. Known usage settles against recorded token counts; failed or unknown-usage attempts retain their reservations. SDK retries and HTTP redirects are disabled. The independent offline usage audit recomputes Decimal-priced totals and checks the ledger. Usage-priced estimates are not invoices. Glimmer's compute is charged by a separate shared Modal GPU session, not by tokens; a zero token price never means free compute.

## Answer contract, gate and independent scorer

The model must either abstain or return an actual `value_text` with entity, metric, fiscal year, fiscal period, basis and unit. The deterministic gate checks those fields against the question, including requested reporting precision, and passes a typed proposed claim to the existing point-in-time verifier. It receives no reference/gold value. It can accept or withhold an answer; it cannot correct the proposed number. Unknown or conflicting bindings are withheld.

The independent scorer compares the retained value with the frozen reference using Decimal `ROUND_HALF_UP` at the requested reporting precision. It does not derive correctness from the verifier's status. Verified precision is correct accepted answers divided by all accepted answers; **zero accepted answers gives `null` / N/A**, not 100%. Verified coverage is accepted answers divided by all 50 planned questions. An unsafe pass is any accepted answer not independently scored correct. Raw abstention, format failure, API failure and not-run counts remain explicit.

## Original contract and post-run sensitivity analysis

The original parser required exact unit/period metadata labels. Real responses used alternatives such as `USD millions` instead of `USD`, or `Q4 FY2023` instead of `Q4`. Primary records preserve that strict contract behavior. Their `raw_wrong` category combines contract/binding failures and numeric errors: **it is not a financial-error rate**.

After observing those label mismatches, a separate [gold-blind normalizer](qa_rescore.py) was specified, independently reviewed, tested with 31 adversarial cases and committed as `f31cbf9` before applying it to real records. This is a **post-run sensitivity analysis**, not a preregistered result. Original prompts, answers, numbers, primary records and question selection remain unchanged.

The normalizer receives only the model answer, never the question or reference. It accepts a small explicit alias set: USD thousand/million/billion labels only when `value_text` explicitly agrees with that magnitude; per-share aliases only with explicit, unscaled per-share numeric text; and quarter labels containing a fiscal year only when that year agrees with the response's separate year field. It never repairs a number, company, metric, basis or stated year. Unknown labels and contradictory magnitude/year fields remain unassessable. The same rules apply to all four targets and every saved response.

Sensitivity outcomes distinguish correct, numeric wrong, wrong question binding, model abstention, unassessable metadata, format failure, API failure and not run. Unknown metadata is not silently called a financial error or removed from overall denominators. The report separately shows accepted-answer precision and coverage, making withholding visible.

## Reproduction and records

Original per-target records live under [`qa_runs/qa-closed-book-20261029/`](qa_runs/qa-closed-book-20261029/). Once all four targets finish, the reviewed offline analysis writes a new `sensitivity.json`, with every original record-file hash and a usage audit. It never invokes a model. `qa_publish.py` exports the manifest, raw responses, source links, original and sensitivity gate decisions, usage and timing into the site's static JSON download. A missing target is shown as pending, never a perfect score.

This comparison uses the **pre-J2 frozen gate**. Later production verifier fixes do not rewrite these 200 records; these metrics describe that historical scoring version, not the latest runtime gate. The public [frozen source overlay](qa_runs/qa-closed-book-20261029/frozen_sources/bundle.json) preserves the seven files actually listed in the preregistered source hashes, the reviewed normalizer, the package initializer and the original snapshot manifest. It is a minimal scoring overlay, **not a complete historical repository commit**. Its index SHA-256 is `4a294dfc9a88ac565d92243115e85795f732a402d3e1becf6cc4318c024a99ea`.

From a public clone with the project Python dependencies installed, run:

```bash
.venv/bin/python eval/qa_reproduce.py --output /tmp/nocap-qa-reproduction.json
```

The [offline reproducer](qa_reproduce.py) reads only public original response files, the public sensitivity report's admission ledger, the frozen source overlay and the committed SEC snapshot. It checks source/evidence/response hashes, creates an isolated temporary copy, strips provider environment settings, forbids model-client construction and all network/SEC activity, and recalculates both primary and sensitivity scores plus usage accounting. Every retained record and aggregate metric must match. It does not require ignored live-run files, a local admission database, keys or a paid request. Input artifacts remain unchanged; the explicit output path must not already exist. Original source hashes intentionally differ from production after subsequent guard fixes.

This is one run on correlated source filings in a supported development domain. It does not measure independent human usefulness, broad financial knowledge, arbitrary retrieval quality or production accuracy. The same evidence store supplies reference labels and verification evidence; the separate scorer is code-independent from the verdict, not a separate human gold source. More diverse questions and blinded human reference review are needed before a stronger claim.
