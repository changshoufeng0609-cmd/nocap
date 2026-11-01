# NoCap API contract (updated 2026-10-31)

The frontend (`web/`) and the Python API (`receipts/api.py`) are built in parallel against this contract. Change it only through the main session.

This document describes the current source contract. Runtime review additions, including
`draft_cache_policy`, have offline regression coverage but are not yet deployed or validated on Production.
See [current deployment state](deploy.md#current-deployment-state) for the source actually hosted.

Historical contract validation used authenticated Preview source `778d5f8`, with the fixed shared Neon primary
ledger. Eight actual PostgreSQL checks and hosted missing-ledger/budget-rejection checks across deployments
passed with no model calls. Paid live Check/Fix/Slide smoke results remain historical; they were not repeated
on this build. See [spend protection](spend-protection.md) and [deployment state](deploy.md#current-deployment-state).
The number-coverage fields below were included in that protected Preview. Missing-ledger and across-deployment retention evidence comes from the preceding `885a46f` release.

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
| `done` | `{"counts": {"verified": n, "wrong": n, "not_public": n, "unverifiable": n, "approximate": n}, "latency_s": float, "cost_usd": float, "cost_is_estimate": bool, "model_calls": int, "model": "muse-spark-1.3"}` |
| `error` | `{"message": str, "retryable": bool}` plus the available cost tuple described below, and `reason` when the request was refused before any model call ([errors](#errors)). |

**Behavior:**
- **Status display:** ✓ verified, ✗ wrong, ◷ not public yet, ? unverifiable, ≈ approximate. ◷ is a verdict with
  `reason: "look_ahead"`: its evidence was accepted after `as_of`. It keeps its `status` (`wrong`, or `unverifiable`
  for a slide's axis estimate) and is counted under `counts.not_public`, never under `wrong`; each verdict is
  counted exactly once (`receipts.types.count_key`). Check, Fix, Slide and Private `counts` use the same buckets, as
  do the web UI and the CLI. Streams recorded before 2026-10-31 omit `not_public` and count ◷ under its status;
  clients accept both forms.
- **Reasons:** `verdict.reason` is one of the ReasonCode values in `receipts/types.py`.
- **Explanation:** `verdict.explanation` is plain English the UI can show verbatim. Derived net margins and margin changes name their input figures, periods and filing accessions.
- **Cache:** enabled recorded caches use `sha256(text + as_of + context)`. A cached response replays the same event sequence, with `cached: true`. Vercel defaults to reading committed snapshot caches only; ordinary drafts create no new extraction/event-cache files. Local persistence remains enabled by default. See [retention policy](deploy.md#draft-cache-policy-and-retention).
- **Limits:** text of at most 12,000 characters and a 64 KiB JSON body, a per-instance per-IP rate limit, and durable estimated spend admission. Blank-line passages longer than 6,000 characters are split with source offsets preserved; extraction keeps at most 10 passage calls in flight, with the existing output limit and deadline. Admission reserves every passage's request-size estimate and allowed attempts before dispatch, so long or highly fragmented drafts can be refused by the daily budget. The body is read incrementally; the application rejects before retaining a chunk that exceeds the limit and stops consuming the remainder. SQLite coordinates processes sharing its file; optional PostgreSQL coordinates instances sharing the primary database and namespace. Vercel requires a shared ledger by default and rejects paid work when it is unavailable; cached examples remain usable. Unknown attempt usage retains conservative charges. These are admission controls, not a provider billing ceiling; see [spend protection](spend-protection.md).

All six paid routes perform synchronous ledger admission in a worker thread. Cancellation while admission
is running prevents model dispatch by that route but cannot stop a transaction already committing;
any resulting pending precharge remains in the ledger rather than being speculatively refunded.
Rate identity defaults to the socket peer. Vercel uses its validated platform-owned forwarding header;
other proxies need explicitly trusted socket-peer CIDRs and correctly configured ASGI proxy handling.
See the [trusted proxy boundary](deploy.md#trusted-proxy-boundary).

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

### Errors

An `error` event always has `message` (one sentence the UI can show verbatim) and `retryable`. An admission
refusal, which starts no model call and carries `cost_usd: 0`, `cost_is_estimate: false` and `model_calls: 0`,
also names its `reason`, so a client can offer the $0 saved examples and recorded samples instead of a retry:

| `reason` | Meaning | `retryable` |
|---|---|---|
| `live_paused` | The operator's live window is closed. | false |
| `budget_unavailable` | The spending ledger (or the whole-filing count) cannot be read; paid work fails closed. | true |
| `daily_budget` | Today's cap cannot hold this request's reservation. Slide, Ask and whole-filing reservations must also leave `RECEIPTS_CHECK_FIX_RESERVE_USD` (default $1.50) for Check and Fix. | false |
| `filing_question_limit` | Today's `RECEIPTS_FILING_QUESTIONS_PER_DAY` (default 5) new whole-filing questions are used; recorded sample questions still replay. | false |
| `rate_limited` | Too many live requests from this network in the rate window. | true |

Other errors (invalid input, unavailable evidence, provider failures) omit `reason`; clients must not infer one.
The Private route does not emit `reason` yet. Limits and storage are described in
[spend protection](spend-protection.md#daily-sub-caps).

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
  counts: {verified, wrong, not_public, unverifiable, approximate},
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
dispatch gate. An attempt with unknown usage settles at its own conservative estimate; only an attempt missing
from the request's accounting retains the whole reservation. Known costs above the reservation increase the debit.
Original/revised extraction follows the same cache policy as Check: Vercel defaults to committed snapshot
reads only; persistent ordinary extraction caches require opt-in on Vercel. Wonder proposals and final fix results are not cached.
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
Returns:

```text
{
  ok: boolean,
  data_snapshot: string | null,
  companies: string[],
  facts: number,
  live_requests_paused: boolean,
  draft_cache_policy: "snapshot_only" | "persistent" | "disabled"
}
```

Unavailable evidence returns HTTP 503 with `ok: false`, `data_snapshot: null`, an empty company list and
zero facts. Both policy fields remain present. `live_requests_paused: false` means the operator permits
live requests; it does not guarantee provider credentials, budget, database or GPU availability.

| `draft_cache_policy` | Meaning |
|---|---|
| `snapshot_only` | Ordinary draft response caches are neither read nor written; committed snapshot caches remain usable. Default on Vercel. |
| `persistent` | Ordinary extraction/event-cache persistence is configured. Stored fragments and model output have no application TTL. Default locally. |
| `disabled` | All response-cache reads/writes are disabled by `RECEIPTS_NO_CACHE`; even saved examples require model admission. |

`RECEIPTS_PERSIST_DRAFT_CACHES=false` selects snapshot-only behavior without changing the local ledger
location or shared database/namespace. It does not clear existing cache files. Persistence opt-in and
provider/host retention are explained in the [deployment policy](deploy.md#draft-cache-policy-and-retention).
Clients should treat missing or unrecognized cache-policy values from older servers as unknown rather
than promising that the server does not retain drafts.

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
The status also returns `recorded_samples: [{"sample_id": str, "recorded_utc": str or null}]`, the public
samples whose recorded Modal run this server replays (empty when recordings are off; older servers omit it).
A check request that exactly matches one (text, normalized context and canonical `as_of`) replays before the
live gate at $0: `meta` adds `cached: true` and `recorded_utc`; claims, verdicts and the `done` accounting are
the original run's. Every other private check has `meta.cached: false` and is never served from a cache.

All six paid workflows respect the operator live-window policy documented in
[deployment controls](deploy.md#public-release-controls-2026-10-29).
A paused request is rejected with zero calls/cost before rate-limit allowance is consumed, ledger admission
or provider dispatch. Enabled cached examples remain available. Older servers can omit the health
policy fields; clients must not infer availability or retention from missing fields.
