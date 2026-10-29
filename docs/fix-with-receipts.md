# Fix with receipts

Run a draft check in the [local web interface](deploy.md#local-development). When the completed check has wrong claims, select **Fix with receipts**. Muse Wonder proposes numeric corrections; the verifier accepts or rejects each change and rechecks the entire revised draft. Review the original and revised text side by side, follow each correction's numbered receipt, then choose **Copy draft** or **Copy with receipts**. The editor's original text is retained.

The second check displays all claims, including guidance and unsupported numbers. “Original red flags resolved” means the original wrong claims were corrected, not that every statement is confirmed. Partial and unchanged outcomes show what still needs review. Editing the draft, changing its time cutoff, selecting another example or starting another check clears the old correction result.

The deployed number-coverage feature compares digit-based candidates with exact claim
values in both original and returned drafts. The output shows its own coverage, with original coverage
available for comparison. An unlinked candidate prevents a rewrite from being accepted; a failed extraction
still returns an error and preserves the original. Coverage is a lexical omission hint, not a correctness
verdict or proof of completeness. Written-out numbers and unfamiliar formats can remain undetected.

## What the model can change

The server recomputes the original claims and verdicts. It sends Wonder the eligible wrong claims, expected values and public filing evidence. Wonder returns only claim IDs and replacement numeric expressions; it cannot provide offsets, verdicts or sources. Code locates each original value and permits changes to its numeric portion while preserving signs, currency, units, scale words and all surrounding prose.

Each replacement must be close to the expected value and pass the deterministic verifier. Code then extracts the entire proposed draft again under the original context and cutoff. It matches numeric occurrences, resolved companies and periods, metric interpretations, bases, units, segments and verdicts. A missing or reinterpreted claim causes the proposal to be withheld. Receipts come from the verifier, never from model-written URLs.

Look-ahead errors, shared or overlapping numeric tokens, unsupported formats and changes requiring a different sign or wording remain for manual review. Model extraction is still fallible; these checks reduce risk without establishing that arbitrary prose is correct.

## Models and operating limits

- Wonder uses `muse-wonder-1.0` in Meta Standard mode with `MODEL_API_KEY`, regardless of the selected extraction backend. Original and revised extraction use the configured backend, normally `muse-spark-1.3`.
- Requests accept at most 6,000 draft characters and 300 context characters. Wonder receives at most 24 candidates in a request of at most 40 KiB, with a 2,048 output-token cap. Unsent candidates remain unresolved.
- Fixes share the API's per-instance rate limit and durable spend admission. A request reserves estimates for both extraction passes, the bounded Wonder request and additional room for edits; each SDK attempt must pass a synchronized dispatch gate. Unknown usage or an unsettled crash retains the reservation. Local SQLite coordinates processes sharing a file; optional PostgreSQL shares allowance across instances. See [spend protection](spend-protection.md), including the default requirement for a shared ledger on Vercel.
- Wonder has no automatic retry; extraction format fallback is separately counted. Deadlines stop new work, and late text responses cannot refund frozen accounting or start another retry. These are conservative estimates, not a provider billing ceiling or strict wall-clock guarantee. Extraction responses can be cached; Wonder proposals and final correction results are not. User retries can incur new charges.
- Successful results and errors with available accounting expose `cost_usd`, `cost_is_estimate` and `model_calls`. The UI labels estimates; missing/malformed error accounting displays **Model cost unavailable**, not $0. Old successful recordings remain readable without the new optional flags.
- Mock mode returns an explicit unavailable message for corrections. The feature passed a real authenticated Preview flow before this accounting change. The accounting code and number-coverage feature are deployed in protected Preview source `778d5f8` with a fixed shared Neon primary ledger; database and hosted admission checks passed. No new paid Fix run was made for this release; see [deployment state](deploy.md#current-deployment-state).

See the [API contract](api-contract.md#post-apifix) for the streaming request and response shapes.

## Historical live validation — 2026-10-27

The real local browser → API → Wonder → verifier → full-draft recheck flow corrected both planted errors in the NVIDIA example: `18%` year-over-year growth and `$2.22` GAAP diluted EPS. The recorded second run returned `105.8511435%` and `$2.46`, with **9 verified, 0 wrong and 2 unverifiable** claims; both guidance claims remained `?`. It took **8.0 seconds** and cost **$0.007263**. Two live correction requests together cost approximately **$0.023**; the first request's expense is recorded at the API log's four-decimal precision. This is a smoke test on one draft, not a correction-quality benchmark.

Desktop rendering, correction receipts and both clipboard actions were checked with the live response. A recorded-response replay also verified mobile layout without horizontal overflow and clearing the result when returning to editing. Browser page errors: **0**. Local evidence is retained under gitignored `data/eval_runs/fix_browser/`.

Regression checks:

```bash
.venv/bin/python -m pytest -q tests/
cd web
npm run test:fix
npm run typecheck
npm run lint
npm run build
```

Results: **573 Python tests**, **14 client regression checks**, typecheck, lint and production build passed. The build passed after preserving and moving aside a failed Turbopack build cache; no production configuration change was needed.

Both complete dev evaluations were rerun using existing extraction caches, with no model spend, skips or errors: v0 + basis5 **224 items**, catch **104/109**, correct reason **97/109**, false alarms **0/107**; Wonder traps **15 drafts**, catch **40/40**, correct reason **36/40**, false alarms **0/112 figures and 0/58 sentences**. These evaluate the verifier, not automatic correction quality. **The held-out test split was not run.**

The subsequent 2026-10-28 accounting regression passed **809 Python tests**, **90 frontend checks**, typecheck,
lint and production build, with the same dev results and **0 model calls / $0 new model spend**. This validates
local code and controlled failures. Subsequent actual PostgreSQL validation passed **8/8**; hosted Fix requests
with an unavailable ledger or exhausted shared allowance were rejected before model dispatch across two
deployments, again with **0 model calls / $0**. These are spending-protection checks, not a new live Fix run.
See [spend protection](spend-protection.md).
