# Private extraction on Modal

Private check uses **Muse Glimmer in the operator's Modal workspace** for draft extraction, followed by
the same deterministic verifier and local SEC evidence snapshot. It is a separate explicit mode, not
an automatic fallback. The draft does not go to Meta Model API, Fireworks or Baseten through this flow.

This is not offline processing. The browser, NoCap API host and configured Modal deployment handle the
draft. Their infrastructure and retention policies still apply. NoCap does not write the private draft,
extraction response or verdict stream to its response caches, and does not log draft text or draft hashes.
The ledger stores accounting reservations only. The UI holds input and results in memory, clears stale
results on edits, and provides a clear action. It does not use localStorage or sessionStorage for drafts.

## Configuration

Complete the [Modal deployment setup](open-weights.md#modal-user-setup-before-deployment) first. After an
approved deployment, set the following **server-side** configuration and restart the API:

```dotenv
RECEIPTS_PRIVATE_MODE=1
RECEIPTS_MODAL_BASE_URL=https://YOUR-DEPLOYMENT.us-east.modal.direct/v1
```

The existing `MODAL_PROXY_TOKEN` or `MODAL_PROXY_TOKEN_ID` plus `MODAL_PROXY_TOKEN_SECRET` is required.
Enter secrets privately; never send them in a browser request or commit them. Generic
`RECEIPTS_EXTRACT_*` overrides cannot change the private model, credentials or destination. Only HTTPS
`*.modal.run/v1` Web Function endpoints or `NAME.REGION.modal.direct/v1` Server endpoints without credentials,
ports, queries or fragments are accepted. Server regions are the six documented Modal routing regions.
The [official Server architecture](https://modal.com/blog/serverless-servers) describes this domain format. The operator is
responsible for selecting their own deployment and reviewing its logging settings.

`GET /api/private/status` reports **configured availability**, not a successful live probe, and lists the
`recorded_samples` it replays at $0 ([recorded sample](#recorded-sample)). Missing or
invalid setup disables private checks without sending the draft elsewhere. `POST /api/private/check`
accepts only `text`, optional `context`, and optional `as_of`. Inputs are bounded like ordinary Check;
requests cannot supply endpoints, models or verdicts. Both routes return `Cache-Control: no-store`.
The dedicated HTTP client does not follow redirects, so a Modal redirect cannot forward the draft POST
to another host.

Modal Proxy Tokens are workspace scoped on accounts without RBAC. A token dedicated by name to NoCap is
not an endpoint-level permission boundary. The API sends that credential only to its configured Modal
origin; do not share it with browsers. An RBAC workspace can restrict access to an environment containing
only this deployment. The bounded smoke deployment is stopped after validation; configuration alone does
not mean an always-running private service has been enabled.

Every SDK attempt needs a reservation from the application's shared spend guard and the same rate limiter.
Private extraction uses one passage at a time, no ordinary retry, and the existing bounded schema fallback
only to the same Modal endpoint. **Modal charges GPU time separately**: the token-price admission estimate
is not measured GPU cost or a Modal billing ceiling. A cold start may exceed the interactive deadline;
warm the approved deployment before a demo and track GPU charges in Modal.

Fix with receipts, Ask, and Check a slide use their own routes and may use Meta. Private mode does not
silently offer those paid hosted-model actions for the private draft. Switching modes must clear the
private component rather than copy its text into another workflow.

## Recorded sample

**Private → Replay a recorded Private check** replays one real run of a public synthetic NVIDIA draft
(`web/lib/samples.json`, id `nvda-q2-fy2027-private`) at $0. `POST /api/private/check` replays a recording
only for that exact text, whitespace included, with the same context and as-of time. The replay comes before
the live gate and the Modal configuration check, so it works on the public site and while live requests are
paused. It sends nothing to Modal or any model and takes no rate-limit slot or ledger reservation. `meta`
carries `cached: true` and `recorded_utc`; the claims, verdicts and `done` accounting are the original run's.
The browser accepts a cached Private stream only for the bundled sample's exact text, so a user's own draft
is never shown as a recorded result. The UI labels the replay **Recorded Private check** with its recording
time. A replay is not re-verified against later code. Nothing is written or logged for any other draft.

The recording (`data/snapshot/recordings/private/57ae4142….json`) came from one bounded run on 2026-10-30
(`scripts/record_samples.py --private --confirm-spend`) from committed source `5dd78c3` with a clean tree:

| | |
|---|---|
| Result | 2 claims: revenue $96.2 billion **✓** (10-Q: $96.221 billion); GAAP diluted EPS $2.22 **✗**, basis mismatch (GAAP $2.46; $2.22 is the non-GAAP figure) |
| Model | `meta-models/Muse-Glimmer-30B` at revision `cec94fcfd6512f2cfe708138c32533b346ea4f69`, BF16 on one H100 with vLLM `v0.28.0` (`deploy/modal_glimmer.py`, sha256 `02231915…`) |
| Evidence | `data/snapshot`, fingerprint `51c18eeb54efe9124a5adddb`; code fingerprint `f1ca0d60179c7b98` |
| Access | an unauthenticated request got **401** |
| Timing | cold start to healthy 157.1 s; request 25.8 s; per vLLM `/metrics`: time to first token 1.28 s, 2,223 prompt and 519 output tokens, 22.4 output tokens/s while decoding |
| Cost | Modal reported **$0.2418** for the app (H100 $0.2238, CPU $0.0107, memory $0.0073), deploy to stop 4 min 18 s, covered by credits. One warm sequential request uses about $0.03 of GPU time (25.8 s at $4.27/h). |
| Shutdown | a 900 s watchdog was armed before deployment; the app was stopped right after the request and confirmed **stopped with 0 tasks and 0 containers** |

These are one-run observations, not latency or cost guarantees. The run record keeps its provenance in the
recording's `runtime` field.

## Integration and validation

`create_private_router()` in `receipts/private.py` receives the app's settings, shared ledger, rate limiter,
evidence-store loader and read-only recordings. The frontend component is `PrivateCheck` in
`web/components/private-check.tsx`.

Offline tests exercise the actual SDK wire format against a local fake HTTP transport: fixed Modal route,
cache isolation, unavailable setup, rejection of caller-supplied destinations, budget rejection and schema
fallback accounting. Recording tests replay the sample with Modal unconfigured, live or paused, and with no
backend, client or reservation. Browser-client tests reject public-model attribution, incomplete streams,
fabricated spans, unsafe receipt URLs and mismatched time cutoffs, with no fallback request, and accept a cached
stream only for the bundled recorded sample. The focused private-routing
and deployment suite passed **38 tests** in the 2026-10-29 follow-up.

### Earlier pipeline smoke

The earlier [bounded live smoke](private-live-validation-2026-10-29.json) separately exercised
`private.private_events` against the real authenticated Modal Glimmer server and SEC verifier: one model call,
two claims, **1 verified / 1 wrong**, complete stream, and **401** for an unauthenticated request. It did not
exercise the live HTTP private route or a live browser request. No private draft or response was cached.
All four app attempts are independently confirmed **stopped with zero tasks**; the server is not left
available for demos. The provider's observed app costs total **$0.70780581**, subject to delayed metering.
See [billing scope and timing limits](open-weights.md#modal-live-validation) for the separate workspace summary
and cumulative wall-time estimate. No additional model/GPU run was needed for this follow-up. A two-claim
smoke is not a quality benchmark or a guarantee of cold-start latency or future GPU availability.

### Real browser and HTTP acceptance

The later [October 29 browser acceptance](private-http-live-validation-2026-10-29.json) completed the
actual Chromium → production-built Next UI → local FastAPI HTTP route → authenticated Modal Glimmer →
deterministic verifier → rendered UI path. One public synthetic two-claim draft produced **1 verified /
1 wrong** with **one model call**. The wrong GAAP EPS received the accounting-basis mismatch verdict and
the expected filed value. The request included explicit company/period context and an as-of cutoff.
No provider response was mocked in this successful run. This tested the local NoCap HTTP service against
real Modal; it did not enable or validate a public Vercel Private service.

The browser checks passed result rendering, SEC receipt links, clear, and clearing the private draft on
mode changes. Only `/api/private/check` received a browser POST; no fallback request occurred. Responses
were `no-store`, browser storage held no draft, response-cache file counts stayed zero, and accounting
reservations settled. The in-memory SSE observation copy was deleted after validation. Runtime artifact
scans found no configured secret, exact draft text or serialized claim payload; retained screenshots mask
draft and result content. These checks do not audit Modal or operating-system retention.

The warm-up took **125.517 seconds**, followed by **29.920 seconds** for the browser request and result
checks. These are one-run observations, not latency guarantees. Two earlier validation attempts are
retained: one hit Playwright's canceled-stream body limitation; the other passed HTTP/model/verdict checks
but failed a test selector that omitted the status glyph. The observer and selector were fixed and tested
offline before the successful live run; product routing and verdict code were unchanged for these retries.

The same successful deployment then completed the separate fixed **50-question Glimmer Q&A** batch.
All three new task apps were polled until **stopped with zero tasks**. Their provider-reported usage totals
**$1.25997788** at the follow-up audit, including the two validation retries, warm-up, Private check and
shared Q&A. Count that GPU usage once. The earlier pipeline apps cost **$0.70780581**, bringing observed
Modal app usage across both rounds to **$1.96778369**; delayed metering may update these amounts. The
new round's conservative compute wall-time estimate is **$1.338062**, excluding build, storage and network.
The machine-readable record preserves app IDs, billing scope, source hashes, failed attempts and the
separate offline self-tests. The recorded source hashes identify the tested runtime; subsequent code
changes require their own validation. The deployment remains stopped.
