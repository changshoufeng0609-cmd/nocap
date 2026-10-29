# NoCap API contract (updated 2026-10-29)

The frontend (`web/`) and the Python API (`receipts/api.py`) are built in parallel against this contract. Change it only through the main session.

The hosted contract is deployed in authenticated Preview source `778d5f8`, with the fixed shared Neon primary
ledger. Eight actual PostgreSQL checks and hosted missing-ledger/budget-rejection checks across deployments
passed with no model calls. Paid live Check/Fix/Slide smoke results remain historical; they were not repeated
on this build. See [spend protection](spend-protection.md) and [deployment state](deploy.md#current-deployment-state).
The number-coverage fields below are included in this protected Preview. Missing-ledger and across-deployment retention evidence comes from the preceding `885a46f` release.

## POST /api/check
**Request body (JSON):**
```json
{"text": "draft text", "as_of": "2026-08-26T20:00:00Z" | null, "context": "NVIDIA, Q2 FY2027" | null, "example_id": "nvda-ai-story" | null}
```

**Response:** `text/event-stream`. Events arrive in this order, with claims streamed as soon as each passage is extracted:

| event | data (JSON) |
|---|---|
| `meta` | `{"draft_hash": str, "cached": bool, "passages": int, "as_of": str or null}` |
| `claim` | `{"claim": Claim}`. `Claim` is `receipts.types.Claim.model_dump(mode="json")`. `span_start` and `span_end` index into the request `text`. |
| `verdict` | `{"claim_id": str, "verdict": Verdict}`. `Verdict` is `receipts.types.Verdict.model_dump(mode="json")`, and `evidence` holds the full Fact receipts. |
| `done` | `{"counts": {"verified": n, "wrong": n, "unverifiable": n, "approximate": n}, "latency_s": float, "cost_usd": float, "cost_is_estimate": bool, "model_calls": int, "model": "muse-spark-1.3"}` |
| `error` | `{"message": str, "retryable": bool}` plus the available cost tuple described below. |

**Behavior:**
- **Status display:** ✓ verified, ✗ wrong, ? unverifiable, ≈ approximate.
- **Reasons:** `verdict.reason` is one of the ReasonCode values in `receipts/types.py`.
- **Explanation:** `verdict.explanation` is one plain English sentence the UI can show verbatim.
- **Cache:** results are cached by `sha256(text + as_of + context)`. A cached response replays the same event sequence, with `cached: true`.
- **Limits:** text of at most 6,000 characters, a per-instance per-IP rate limit, and durable estimated spend admission. SQLite coordinates processes sharing its file; optional PostgreSQL coordinates instances sharing the primary database and namespace. Vercel requires a shared ledger by default and rejects paid work when it is unavailable; cached examples remain usable. Unknown attempt usage retains conservative charges. These are admission controls, not a provider billing ceiling; see [spend protection](spend-protection.md).

### Number coverage (text drafts)

Current Check `done` includes `coverage`; Fix `result` includes `original_coverage` for `original_text`
and `coverage` for the returned `rewritten_text`. CLI JSON exposes `coverage` too:

```json
{"version": 1, "candidate_count": 2, "covered_count": 1, "excluded_count": 0,
 "uncovered": [{"start": 53, "end": 56, "text": "42%"}]}
```

Offsets are zero-based Python Unicode code points, with an exclusive end. Candidates are digit-based
number occurrences, excluding recognized dates, fiscal labels and identifiers. A candidate is covered
only when its numeric core is inside a uniquely located `Claim.value_text`; the surrounding claim sentence
does not cover its other numbers. Multiple accounting-basis claims can share one candidate.
`candidate_count = covered_count + len(uncovered)`. `excluded_count` counts recognized non-candidates.

Coverage means linked to an extracted claim, not verified. Uncovered candidates display **Not checked**,
separate from ✓/✗/?/≈, without fabricated verdicts. This heuristic cannot prove that every financial
statement was found: written-out numbers, unfamiliar formats and semantic extraction errors can remain.
Failed Check streams end with `error`, never a complete-coverage `done`. Fix retains its existing
extraction/semantic guards and also rejects any candidates missed by this scan before accepting a rewrite.
Older recordings may omit coverage; clients show it as unavailable. Invalid coverage metadata must never
be interpreted as zero omissions. Slide results do not carry image-coverage guarantees.

### Cost fields across streaming endpoints

Current Check `done`, Fix `result`, and Slide `done` events include `cost_usd`, `cost_is_estimate`, and
`model_calls` (started SDK attempts, including retries). `cost_is_estimate: false` means available usage was
priced at configured rates, not independently reconciled billing. Unknown or invalid usage retains estimates.
Errors carry available accounting, including explicit zero when the application knows no call started.
Transport/platform failures or older recordings can omit accounting; omission never means $0.

The UI accepts error accounting only as a complete tuple: finite nonnegative cost, boolean estimate flag and
nonnegative safe-integer call count. Missing/malformed tuples display **Model cost unavailable**. It preserves
estimate labels on successful and failed checks. Old Check/Fix successes remain readable without the optional
`cost_is_estimate` and `model_calls` fields; absent flags are not synthesized as false. Cached replay costs and
call counts describe the original run, while the replay adds $0 model spend.

## POST /api/fix

**Request:** `{"text": "draft text", "as_of": "2026-08-26T20:00:00Z" | null, "context": "NVIDIA, Q2 FY2027" | null}`.
The same text, time and context limits as `/api/check` apply. Extra fields are rejected: the server recomputes claims and verdicts from the draft and never accepts client-supplied receipts, offsets or verdicts.

**Response:** `text/event-stream`, with `progress` events followed by exactly one `result` or `error` event. Keep-alive comments have no payload.

| event | data |
|---|---|
| `progress` | `{"stage": "checking" | "rewriting" | "verifying"}` |
| `result` | The object below. |
| `error` | `message`, `retryable`, and available `cost_usd`, `cost_is_estimate`, `model_calls`; no unverified proposal is returned. |

```text
{
  original_text: string,
  rewritten_text: string,
  status: "corrected" | "partial" | "unchanged",
  changes: [{claim_id, before, after, start, end, explanation, evidence: Fact[]}],
  remaining: [{claim_id, reason, explanation}],
  results: [{claim: Claim, verdict: Verdict}],
  counts: {verified, wrong, unverifiable, approximate},
  cost_usd: number,
  cost_is_estimate: boolean,
  model_calls: number,
  latency_s: number,
  model: "muse-wonder-1.0",
  extraction_model: string
}
```

`changes.start/end` are Python character offsets in the **original** draft. Rechecked `Claim` offsets refer to `rewritten_text`. `claim_id` in changes/remaining belongs to this request's server-side original extraction; it need not match IDs from an earlier `/api/check` stream. Receipts always come from the evidence store and deterministic verifier.

Wonder proposes replacements for numeric fragments only. Code preserves the company, period, accounting basis and all other prose; each accepted replacement must verify, and the entire revised draft is extracted and checked again under the original context and as-of time. A missing claim or a changed meaning causes the proposal to be withheld. Look-ahead errors, uncertain values, unsupported numeric formats and ambiguous overlapping edits remain for manual review. Each Wonder request contains at most 24 candidate corrections and 40 KiB of serialized request data; omitted candidates remain unresolved. The output limit is 2,048 tokens, including reasoning. Wonder is not automatically retried; any extraction format fallback is accounted separately. Dispatch observes the remaining deadline and per-call timeout.

`corrected` means every original **wrong** claim was corrected; it does **not** mean the whole draft is verified. Guidance and other `?` claims remain visible in `results`. `partial` has accepted changes plus unresolved wrong claims. `unchanged` returns the original text. The UI offers a separate copy action; the editor is never overwritten automatically.

Fixes share the check rate limit and ledger. Before work, a durable reservation covers both extraction passes,
the bounded Wonder request and additional room for numeric edits. Each SDK attempt must pass the reservation's
dispatch gate. Unknown usage retains at least the reservation; known costs above it increase the debit.
Original/revised extraction can reuse Spark caches; Wonder proposals and final fix results are not cached.
A user retry can incur new charges. The extraction backend remains selectable; Wonder always uses Meta
Standard mode and its own Meta client. See [spend protection](spend-protection.md) for estimates and settlement.

## POST /api/slide

**Request:** `{"image_data_url": "data:image/png;base64,...", "context": "Bank of America, Q3 2026" | null,
"as_of": "2026-10-28T00:00:00Z" | null}`. PNG/JPEG only, at most 2 MiB, 4,096 pixels per side and 8 megapixels;
the JSON body is capped at 3 MiB. Extra fields and remote image URLs are rejected.

**Response:** `text/event-stream`: `meta`, `progress`, `region`, `claim`/`verdict`, then `done` or terminal `error`.
Unlike the text-check wrappers, each slide `claim` event directly carries Claim fields plus `region_id`,
`box` and `reading`; each `verdict` event directly carries Verdict fields. Boxes use full-image coordinates
normalized to 0–1000. SAM 3.1 regions guide Muse Spark 1.3 vision; all verdicts and filing receipts come from
deterministic code. Axis estimates can only receive ≈ or ?, and uncertain readings remain ?.

Slide checks use a separate Standard Meta client and share the live rate limiter and durable ledger. Each
attempt is admitted against the precharged reservation; unknown usage keeps a conservative charge.
Uploaded images and model transcripts are not saved or cached. Vision can misread or omit
labels, and an uploaded image is never treated as SEC evidence. See [Check a slide](check-a-slide.md) for the
complete event schema, request limits, fallback behavior, accounting and retention details.

## GET /api/examples
Returns `[{"id", "title", "description", "text", "as_of", "context"}]`. These drafts are pre-cached, so the demo never waits on the model.

## GET /api/health
Returns `{"ok": true, "data_snapshot": "<date>", "companies": ["NVDA", ...], "facts": n}`.

## Receipt fields the UI shows (from Fact)
- `form`, `accession`, `accepted_at` (shown in UTC)
- `url`, a link to the EDGAR document
- `locator`, the verbatim row label or cell text for press-release facts
- `concept`, the us-gaap concept for XBRL facts
- `source`: xbrl, press_release, or derived
- `fiscal_year` and `fiscal_period`
- `value` with its `unit`, in full units. The UI formats it, for example $96.221B.


## Additional guarded workflows (2026-10-29)

Ask uses `GET /api/ask/options` and `POST /api/ask`; exact quotation search uses
`GET /api/grounding/filings` and `POST /api/grounding`. See [Ask and quotation contract](ask.md) for the
bounded inputs, SSE terminal records, question binding, provenance and accounting semantics.
Private extraction uses `GET /api/private/status` and `POST /api/private/check`; see
[Private mode](private-mode.md) for its no-fallback/no-response-cache boundary and operational limits.

Health additionally includes optional `live_requests_paused: boolean`. All six paid workflows respect the
operator live-window policy documented in [deployment controls](deploy.md#public-release-controls-2026-10-29).
A paused request is rejected with zero calls/cost before dispatch; cached examples remain available.
