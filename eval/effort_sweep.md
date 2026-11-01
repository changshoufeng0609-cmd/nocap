# Reasoning-effort sweep: claim extraction

NoCap's claim extraction ran once, live, at each of three reasoning efforts on the same test set:

- **Model:** Muse Spark 1.3 (`muse-spark-1.3`, Standard mode), Meta Model API, Responses.
- **Efforts:** `minimal` (production), `low` and `medium`.
- **Run:** 2026-10-30, 19:45:26–19:46:31 UTC (2026-10-31 in Taipei).
- **Test set:** 10 passages of NVIDIA's Q2 FY2027 press release with 27 hand-labeled claims, the gold set behind [`bench_results.md`](bench_results.md).
- **Grading:** the same as `bench_results.md`.
- **Total spend:** $0.25.
- **Full record:** [`effort_sweep.json`](effort_sweep.json).

## Results

| Effort | Strict recall | Precision | Metric / basis / period correct | Raw claims wrong | Verdicts ✓ / ? / ✗ (28 claims) | Gold verdicts same as minimal | Unflagged errors | Wall time | Call p50 / max | Output tokens (reasoning) | Cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **minimal** (production) | 27/27 | 27/28 | 27 / 27 / 27 | 0/23 | 12 / 16 / 0 | — | 0 | **7.2 s** | 4.5 / 7.2 s | 7,985 (5,133) | **$0.043** |
| low | 27/27 | 27/28 | 27 / 27 / 27 | 0/23 | 12 / 16 / 0 | 27/27 | 0 | 16.2 s | 7.4 / 16.2 s | 16,780 (13,888) | $0.080 (1.9×) |
| medium | 27/27 | 27/28 | 27 / 27 / 27 | 0/23 | 12 / 16 / 0 | 27/27 | 0 | ≈ 21.4 s* | 9.9 / 21.4 s | 27,919 (25,035) | $0.127 (3.0×) |

\* Medium ran in two waves (6 calls, then 4) to keep the spend cap. Its wall time is its slowest call, which is how `bench_models.py` reports a mixed run. Minimal and low ran all 10 calls in parallel, as the product does.

**Identical at every level:**
- 23 raw model claims, all with verbatim spans; 0 label errors, 0 schema rejections, 0 failed calls.
- 22,134 input tokens, of which 17,288 were cached. Effort only changed output tokens.
- Verdicts from `receipts.verify`: 12 ✓ (match), 14 ? (forward-looking) and 2 ? (no evidence: capital returned and remaining repurchase authorization).

**Cost and speed:**
- Low used 2.7× the reasoning tokens of minimal; medium used 4.9×.
- Per passage, minimal cost $0.0043, low $0.0080 and medium $0.0127.

## What the data says

On this set, more reasoning bought no measurable accuracy:
- Recall, precision, field labels and every claim's verdict were identical at all three levels.
- Low cost 1.9× as much and took 2.2× as long as minimal. Medium cost 3.0× as much and took about 3× as long.
- Medium's slowest call (21.4 s, on a 116-character passage) is already past Spike B2's 20-second target for a whole check (`spikes/b2_claims.py`), and at 71% of the product's 30-second per-call timeout (`receipts/extract.py`, `INTERACTIVE_TIMEOUT_S`).

The data supports keeping minimal. It does not show that higher effort never helps: minimal already scores 27/27 here, so this set has no room to show a gain (see Limitations).

## Every disagreement, read by hand

**Across levels.** 19 of the 23 raw claims differ between levels in at least one field, ignoring JSON number formatting such as `18` versus `18.0`. None changes a grade or a verdict.

| Field | Raw claims | What differs |
|---|---|---|
| `metric_text` | 12 | Display wording only, for example "revenue" vs "revenue growth" for 18%. The canonical `metric` never differed. |
| `span_text` | 10 | Quote boundaries, for example with or without "GAAP and" in front. Every span stayed verbatim and contained its value; code computes the offsets. |
| `period_text` | 5 | 75.0%, $2.46 and $2.22: minimal copied "For the quarter", low and medium wrote "second quarter ended July 26, 2026". $99.0 billion: "the end of the second quarter" vs "As of the end of the second quarter". All resolve to the same period and pass the period check. $500 billion of third-party capital: medium wrote "over time", the others the quarter; that gold label has no period, and the claim is forward-looking `other`, so it is ? at every level. |
| `period_type` | 1 | $0.25 dividend: minimal "unknown", low and medium "quarter". The claim is forward-looking, so it is ? either way. |

These fields never differed: canonical metric, value, unit, scale, bases, comparison, forward-looking, entity and segment. No claim appeared at one level and not another.

**Within one level.** Same-effort variation is as large as anything above. This study's minimal run sent byte-identical requests to the 2026-10-29 minimal run cached in `data/cache/bench`, and 11 of 23 raw claims still differed.
- The material case is the "plus or minus 50 basis points" margin tolerance. On 10-29 it came back with no accounting basis.
- Today, at all three levels, it came back with both GAAP and non-GAAP, so code expands it into two claims.
- That is why precision is 27/28 here and 27/27 in `bench_results.md`. The extra claim is forward-looking and ? in every run.

## Method

- **Harness:** the driver calls `eval/bench_models.py`'s own grading: `load_gold`, `match_gold` (with `_assign`), `_text_match`, `raw_report`, `verdicts` and `call_latencies`.
  - Extraction is `receipts.extract.extract_detailed`, exactly as `run_target` calls it: one call per passage, 10 in parallel, strict `text.format` schema, `max_output_tokens` 8,000, the batch retry policy and 120 s per attempt.
  - The CLI itself was not run. Its `main()` writes to `data/` and rebuilds `bench_results.md`, and its `spark` target ignores the effort override.
- **Effort:** the code's existing override, `receipts.extract.resolve_backend({"RECEIPTS_EXTRACT_EFFORT": level})`. No product code or setting changed.
  - Request hashes confirm that the minimal requests are byte-identical to production's, and that low and medium differ only in `reasoning.effort`.
- **Live calls:** each level used a new, empty response cache outside `data/`, so 30 of 30 calls were live. The API echoed `muse-spark-1.3` and status `completed` for all 30.
- **Evidence and code:** `receipts.verify` read the committed `data/snapshot` at commit `58201eb` (facts.sqlite SHA-256 `9a96c3ec…`). The snapshot was extracted from git rather than read from the working tree, which another agent was editing.
  - That agent also had uncommitted edits to `receipts/store.py` and `receipts/verify.py`.
  - Re-verifying every claim of every level offline with the committed code of `58201eb` reproduced all 84 verdicts exactly.
  - The extraction code, `receipts/extract.py`, was unchanged from that commit.
- **Validation:** before any live call, the driver rescored the cached 2026-10-29 gold responses offline and reproduced the Muse Spark row of `bench_results.md`: 27/27, 0/23 raw wrong, 23/23 verbatim spans, 0 label errors, 0 unflagged, 7.9 s slowest call, $0.041.

## Spend

| | Minimal | Low | Medium | Total | Cap |
|---|---|---|---|---|---|
| USD | 0.042587 | 0.079966 | 0.127306 | **0.249860** | 0.35 |

- **Estimate before the run:** $0.23–0.28.
  - Minimal, from `bench_results.md` and Spike B2.
  - Low, from Spike B2.
  - Medium, extrapolated; there was no prior medium data.
- **Hard cap:** a level ran as one parallel wave only if it fit the remaining cap under a data-driven per-call bound. Otherwise, at most remaining ÷ $0.038 calls were in flight; $0.038 is a 3,000-token uncached input plus the full 8,000-token output limit. That rule split medium into 6 + 4.
- **Cost basis:** the API's reported token usage at list prices, not a provider invoice.

## Limitations

- **Small sample:** one live run per level on one document of 27 claims. This is not a general accuracy claim, and it gives no variance estimate.
- **Ceiling effect:** minimal already scores 27/27, so this set cannot reveal a gain from more reasoning. Tables, multi-company roundups, qualifiers and long passages may behave differently. The dev, trap and held-out sets were not run.
- **Noise:** same-effort variation on this set is at least one shipped claim (see above).
- **Gold labels:** still provisional and awaiting human review.
- **Medium's wall time** is a proxy (its slowest call), because it ran in two waves.
- **Latency:** one sample per call, taken under the server load of that minute.
- **Scope:** only claim extraction was tested. Slide reading, Ask, whole-filing quotes and Fix keep their own effort settings.

## Files

- [`effort_sweep.json`](effort_sweep.json) contains:
  - the summary, denominators and agreement checks;
  - every field-level disagreement with the values at each level;
  - same-effort variation, per-call latency, tokens and cost;
  - per-gold-claim matches and verdicts, and all 28 shipped claims with verdicts;
  - the API echoes;
  - the driver and record-builder source with SHA-256 hashes, so the run can be reproduced.
- **To rerun:** save `driver.source` as `sweep.py`, then run `python -I sweep.py --out OUT.json --scratch DIR --evidence-dir DIR` with directories outside `data/`. It needs `MODEL_API_KEY` and costs about $0.25.
