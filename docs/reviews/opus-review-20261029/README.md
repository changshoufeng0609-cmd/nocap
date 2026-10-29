# NoCap independent review — October 29, 2026

Reviewed runtime: private commit `d2296df7946b48d426dcef5941cc5a160914aee7`.
This review adds documentation and offline reproduction artifacts only. Product code,
evidence, model configuration and deployment are unchanged.

The owner requested actual Claude Code Opus 5.5 at max effort, with additional specialist
reviews. Claude Code 2.1.312 was started in the visible right-hand cmux terminal with
`--model claude-opus-5-5 --effort max --permission-mode plan --tools Read,Grep,Glob
--strict-mcp-config`. Its UI displayed **Opus 5.5 with max effort · Claude Max**;
response metadata also identifies `claude-opus-5-5`. Three separate Codex specialist
reviews covered correctness, product/demo, and runtime/privacy/cost. These are AI reviews,
not human acceptance or official hackathon judging.

The [original Claude report](claude-report.md) is preserved verbatim, including opinions
that were narrowed during cross-checking. [Session provenance](review-provenance.json)
records the model, tool scope and report hash. Claude performed static review; the
specialists and primary reviewer executed the offline probes below.

## Priorities supported by reproduction

| ID | Priority | Finding | Evidence |
|---|---|---|---|
| R1 | P1 | Comparison wording for one amount can validate another amount against the wrong period | `correctness_results.json`, F2 |
| R2 | P1 | `US$` spelling suppresses strict bound recognition | `claude_bounds_results.json` |
| R3 | P1 | A cropped model value token can lose a source scale, sign or currency | `correctness_results.json`, F1 |
| R4 | P1 | Revenue-growth fast paths ignore unsupported qualifications | `correctness_results.json`, F3 |
| R12 | P1 | Missing comparison metadata can override explicit source YoY/QoQ wording | `claude_followup_results.json`, C3 |
| R5 | P1, known limitation | Number-only quotes can omit source metric qualifications | `correctness_results.json`, F4 |
| R6 | P2 | Switching modes discards ordinary drafts and results | Static component lifecycle inspection |
| R7 | P2 | Aborted Check requests can still emit an old error callback | `product_results.json` |
| R13 | P2 | Ambiguous `up to … from …` is forced into a strict upper-bound reading | `claude_followup_results.json`, C4 |
| R8 | P2 | Check/Fix enforce the body limit after buffering the entire request | `runtime_results.json` |
| R9 | P2, deployment-dependent | Untrusted forwarding headers can select a new rate-limit identity | `runtime_results.json` |
| R10 | P2, live mode | Synchronous database admission blocks the ASGI event loop | `runtime_results.json` |
| R11 | P3 | Demo recording notes still describe release acceptance as pending | Static documentation comparison |
| R14 | P3 | Paused live mode still offers the first Fix action on saved results | Static UI/API inspection |
| R15 | Optional presentation | Emphasize small accepted-answer denominators on benchmark cards | Existing disclosures are correct; no data-integrity defect found |
| R16 | P3 | Ordinary draft caches have no application TTL or submission-time retention explanation | Static default-path inspection; no leak established |

P1 means a high-priority verification correctness failure, not evidence of its frequency
in live model output. Synthetic model proposals exercise accepted pipeline inputs; this
review did not call a live extractor or estimate a general false-verification rate.

### R1 — Bind comparison periods to the current amount

`receipts/verify.py:1382` searches the entire claim span for comparison-period wording.
For a Q2 FY2027 revenue claim, `Revenue was $46.743 billion, up from $40 billion a year
ago.` validates the **first** amount using Q2 FY2026's receipt. Removing the unrelated
comparison clause returns the appropriate wrong-period verdict.

Scope the comparison phrase to the relevant value occurrence and clause. Explicit
current-period metadata must not be overridden merely because another amount in the
same span names an earlier period. Test two-value clauses and their reversed order.

### R2 — Recognize the complete supported currency token before a bound

`receipts/verify.py:706,714` and `receipts/extract.py:1060` do not consistently handle
`US$` when identifying or restoring an adjacent bound. With the committed snapshot,
`more than US$96.221 billion` revenue gets Verified even though the filed amount is
exactly $96.221B. `more than US$59.7 billion` net income also verifies against $59.688B.
Replacing `US$` with `$` correctly returns Wrong.

Claude independently identified this issue; a specialist and the primary reviewer
reproduced it. Twelve paired cases include five false Verified results, six correctly
rejected `$` controls, and one `US$97B` case that remains Wrong despite losing the bound.
Normalize supported currency tokens consistently, while preserving strict versus
inclusive operators and keeping bounds separate from equality rounding tolerance.

### R3 — Normalize the source token, not only a model-selected substring

`receipts/extract.py:1114` normalizes the model's `value_text`. A fully retained source
sentence saying `$96.221 million` can become Verified against $96.221B if the proposal
selects only `$96.221` and labels its scale billions. Omitting a neighboring minus sign
or euro symbol similarly produces false Verified results in the supplied probes.

Recover the complete numeric token at the source offset, including sign, currency and
scale. Contradictory or unsupported source information must prevent Verified. Cover
explicit scale suffixes, negative signs, parentheses and unsupported currencies.

### R4 — Apply qualification checks to growth readings

`receipts/verify.py:582,589` returns a revenue-growth reading before the level-metric
qualification checks. `revenue growth excluding China` at 106% becomes Verified using
consolidated revenue growth, even when the qualifier appears in both the model's metric
description and the complete source quote.

Validate the growth metric's base scope and value-local source description before
matching total-company evidence. Unsupported exclusions should return Unverifiable.

### R5 — Retain source semantics when quotes contain only numbers

`receipts/extract.py:1091,1100` can retain only a numeric quote. If both the model's
metric description and quote omit `excluding stock compensation`, a qualified net-income
sentence can verify using consolidated GAAP net income. Keeping the complete quote
correctly triggers the existing J2 guard.

This is a **known residual limitation**, consistent with the stated scope of the J2
closure and `_local_level_description`; it is not a newly discovered failure of the
specific J2 regressions. Recover a value-local metric clause, or withhold Verified when
the source semantics cannot be established reliably.

### R12–R13 — Preserve explicit comparisons and linguistic ambiguity

`receipts/verify.py:1174,1312` tries either YoY or QoQ when the model proposes
`comparison="none"`. `Revenue rose 18% year over year` therefore becomes Verified by
matching QoQ; `Revenue rose 106% sequentially` becomes Verified by matching YoY.
Correctly typed comparison controls both return Wrong. This requires an incorrect
model field; its live frequency was not measured. Bind explicit source comparison
wording to the relevant value before applying the unspecified-comparison fallback.

At `receipts/verify.py:88`, `up to` is always treated as an inclusive upper bound.
`revenue was up to $96.2 billion from $46.7 billion a year earlier` consequently returns
Wrong, while replacing `was up to` with `rose to` returns Verified. The former can mean
growth to a rounded endpoint, so Unverifiable is an acceptable ambiguity outcome.
This is a linguistic expectation, not an independently adjudicated human label; do
not simply delete all legitimate upper-bound interpretations.

### R6–R7 — Preserve ordinary work and ignore superseded requests

`web/components/check-workspace.tsx:51` conditionally unmounts each mode. Draft state
is local to `ReceiptsApp`, so Draft → Slide/Ask → Draft loses the user's edits, cutoff,
check and Fix results. Lift ordinary Draft state to the workspace or preserve the
component while suspending background work. Keep Private's explicit clear-on-leave
behavior. This finding is established by component lifecycle inspection, not a new
browser run.

`web/lib/api.ts:135–143` checks cancellation before awaiting error-body parsing, then
calls `onError` without rechecking. The actual TypeScript client, compiled in memory
with a synthetic 503 response, emits one callback after abort. `web/hooks/use-check.ts:102,111`
also lacks callback run guards and cancellation invalidation. Recheck after awaits,
guard all callbacks by the current run, and invalidate the run on cancel. The browser
toast/Retry consequences are inferred from the inspected hook and component code.

### R8–R10 — Bound resource use before enabling live requests

At `receipts/api.py:569`, `await request.body()` precedes the 64 KiB limit. An offline
ASGI stream retained all 196,611 bytes from three chunks before rejection. Read in
bounded chunks and stop consuming the stream once the limit is crossed. Platform and
reverse-proxy limits can reduce the impact; no oversized live request was sent.

At `receipts/api.py:323`, the application unconditionally trusts forwarded-IP headers.
The same socket receives a fresh per-IP allowance by changing `x-real-ip`. This is
confirmed for direct/untrusted-proxy use; **Vercel exploitability was not tested** and
the shared daily budget is not bypassed. Default to the socket peer and accept forwarded
identity only through a defined trusted-proxy boundary.

Async routes call `_reserve()` synchronously (`receipts/api.py:644,700,757`,
`receipts/ask_api.py:90`, `receipts/private.py:159`), reaching synchronous psycopg calls
at `receipts/spend.py:331`. A controlled 200 ms ledger stub delays an unrelated 10 ms
coroutine until approximately 200 ms. This is **not a Neon latency measurement**.
Offload synchronous admission or use an async driver while preserving transactional,
fail-closed accounting. The current live-paused wrapper rejects before database admission.

### R11 — Reconcile the demo recording note

`docs/demo/README.md:50` still says deployment and anonymous acceptance of the 165-second
export are future steps. `docs/public-release-update-2026-10-29.json` records their
completion. Link the current record and keep the 179.94-second release clearly historical.

### R14–R16 — Reduce dead-end actions and clarify retention

The completed saved-example flow mounts Fix (`receipts-app.tsx:521`), whose initial
button only checks whether a request is running (`fix-with-receipts.tsx:202`). While
live mode is paused, that first action is offered but the API will reject it, after
consuming an IP allowance (`receipts/api.py:687,700`). Pass the known paused state into
Fix, explain availability and link the disclosed recording. The existing pause banner
and demo disclosures are clear; this is UX friction, not evidence of dishonest demo claims.

The benchmark cards show precision prominently before the exact-contract table
(`web/app/benchmarks/page.tsx:60–106`). Adding an adjacent post-hoc label and emphasizing
14/14, 17/17 and 6/6 can improve scanning. Existing copy already says the sensitivity
analysis followed observation of outputs, and each card shows accepted counts and
coverage. This is an optional hierarchy improvement, not a confirmed misleading-data bug.

Default Vercel settings permit writing ordinary Check/Fix extraction and event caches
under the temporary `receipts` directory (`receipts/api.py:125–128,663,711`). Claims and
model output can contain source-draft fragments; the cache implementations have no
application TTL. Draft UI lacks a retention explanation. Verify deployment overrides,
choose a bounded retention or no-write policy, and explain it at submission time.
No cloud cache was inspected and no private-data exposure was demonstrated. Private
mode's explicit cache restrictions remain separate.

## Cross-check corrections to the original Claude report

- Claude's findings 1 and 2 are retained as **P1**, rather than its P0 labels: the
  reproduced failures have specific trigger conditions. Five new P1 correctness
  categories are confirmed across R1–R4 and R12; R5 is a prior known limitation.
- Claude finding 1's exact newswire-style examples were also reproduced, including
  the minimal metric-to-value quote. Its discovery agrees with the specialist's
  independently constructed two-value example.
- Claude finding 3 is now reproduced offline, with its model-field-error premise
  stated explicitly. Finding 4 is reproduced under the stated ambiguity expectation.
- Switching Draft tabs loses work. However, explicitly loading a new example is a
  replacement action, and clearing an old Fix after editing or changing the cutoff
  can correctly invalidate obsolete results. Those are not all equivalent defects.
  Do not keep Private mounted or introduce browser storage as an automatic fix.
- Neither the paused Fix button nor benchmark typography establishes dishonesty or
  an inevitable change in judging scores. R14/R15 use the narrower descriptions above.
- **Paused does not prove zero retained-data exposure.** Pause does not clear caches;
  cached reads happen before live admission and can still populate event caches.
  Existing contents and platform retention were not inspected.
- **Not all provider requests include `store=False`.** The Meta Responses path does;
  Fireworks/Baseten/Modal Chat requests do not carry that field. That flag does not
  establish a universal provider-retention guarantee or prevent application caching.
- Historical evaluation figures and the 165-second demo are not regenerated or
  reclassified by this review. No new independent human labels or participant results
  are claimed.

## Reproduce and limitations

From the repository root:

```sh
.venv/bin/python -B docs/reviews/opus-review-20261029/correctness_probe.py
.venv/bin/python -B docs/reviews/opus-review-20261029/claude_bounds_probe.py
.venv/bin/python -B docs/reviews/opus-review-20261029/claude_followup_probe.py
.venv/bin/python -B docs/reviews/opus-review-20261029/runtime_probe.py
node docs/reviews/opus-review-20261029/product_probe.mjs
```

The scripts write only their adjacent result JSON. They preserve source hashes and
actual observations. Correctness probes use the committed snapshot and block socket
connections. Runtime probes execute selected unchanged source definitions with controlled
stubs; the product probe replaces fetch and compiles the actual TypeScript client in memory.
The primary reviewer reran all five scripts after reading them. The correctness
sets contain 9 initial, 12 currency-bound and 12 Claude-follow-up cases; runtime has
three focused probes, and the client cancellation probe is separate. These targeted
cases are not a replacement headline benchmark or a full-suite test run.

No NoCap provider, SEC or GPU calls were made by these probes: additional application
usage was **$0**. Claude ran under the existing Claude Max login and completed in
18m 55s. Its session UI displayed **$7.03**; this is a displayed usage-cost figure,
not an independently verified additional charge or invoice. No subscription change
was made, and additional-usage billing was not checked.
No held-out evaluation was rerun. Hash comparison found all 126 frozen files unchanged.

Fixes should be validated with independent regressions, development checks and saved
example acceptance. Preserve the historical held-out results and their frozen source
version; do not present those results as measurements of a subsequently changed verifier.
