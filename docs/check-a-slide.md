# Check a slide

Upload one earnings chart or table as a PNG or JPEG. SAM 3.1 locates visual regions; Muse Spark 1.3 reads the
financial labels and their company, period, scale and accounting basis. The existing deterministic verifier
compares each extracted claim with the SEC evidence store. Click a highlighted reading to see the same filing
receipt used by text checks.

The image is an input claim, **not filing evidence**. Vision can misread a digit, infer the wrong period or omit
a figure. A green mark means that the extracted reading agrees with a filing; it does not certify that the
image was transcribed perfectly or that every figure on the slide was found. Confirm the highlighted reading
against the image before publishing.

## Readings and verdicts

- A legible printed financial label can receive ✓, ✗, ?, or ≈ from the ordinary verifier.
- A value estimated from a chart axis can receive only ≈ or ?. It cannot establish a numerical error and cannot
  receive ✓. An estimate that agrees at the displayed precision receives ≈; otherwise it remains ?.
- Uncertain readings and unresolved company, metric, period or basis remain ?. Guidance remains ?.
- `as_of` uses SEC acceptance timestamps exactly as in text checks. An axis estimate whose evidence was not yet
  public remains `unverifiable` with the `look_ahead` reason. The UI shows its blue “Not public yet” badge and
  retains the axis-estimate label; the API's `done.counts` includes it under `not_public`, like every ◷ look-ahead
  result, and not under `unverifiable`.
- Financial labels are compared with existing evidence for the same 11 supported companies. Uploading a slide
  does not ingest a filing or expand the evidence coverage.

## API

`POST /api/slide`, with `Content-Type: application/json`:

```json
{
  "image_data_url": "data:image/png;base64,...",
  "context": "Bank of America, Q3 2026",
  "as_of": "2026-10-28T00:00:00Z"
}
```

Only `image_data_url`, optional `context` and optional `as_of` are accepted. Arbitrary image URLs, PDF files,
client-provided claims, verdicts, citations and segmentation results are rejected. Context is limited to 300
characters and is treated as untrusted data. If the slide already supplies company and period, context can be
omitted.

The response is `text/event-stream`, using the existing error envelope and keep-alive comments:

| Event | Payload |
| --- | --- |
| `meta` | `models`, `image: {width, height}`, `cached: false`, `as_of` |
| `progress` | `stage`: `segmenting`, `reading`, or `verifying` |
| `region` | `id`, `label`, `box`, `source`: `sam` or `spark` |
| `claim` | The ordinary Claim fields, plus `region_id`, `box`, and `reading`: `printed` or `axis_estimate` |
| `verdict` | The ordinary Verdict, including SEC evidence and point-in-time explanation |
| `done` | `counts`, `claims`, `model_calls`, `latency_s`, `cost_usd`, `cost_is_estimate`, `warnings` |
| `error` | `message`, `retryable`, and available `cost_usd`, `cost_is_estimate`, `model_calls`; terminal, with no subsequent `done` |

All boxes use `[left, top, right, bottom]`, normalized to 0–1000 over the full, correctly oriented image. Image
dimensions in `meta` describe the normalized working image. Region boxes locate a chart/table; claim boxes
locate the value label or estimated mark. Model grounding can be imprecise. The `span_text` is a vision
transcription, and `span_start: 0` / `span_end` refer only to that transcription, not to image pixels.

SAM uses the two concepts **bar chart** and **table**, one request each. The real-slide spike found that generic
“chart” and “graph” prompts missed BAC's bars. Candidate regions are sent with the full image to Spark so titles,
units and quarter headings remain visible. Spark grounding handles line charts and supplies regions when SAM
finds no usable match or segmentation fails. The source
field makes this fallback explicit. The entire structured result is validated before any claim/verdict is
published; malformed or excessive output fails the check instead of silently dropping a trailing claim.
Numeric tokens and signs must agree with their transcription. Code checks finite values after scale conversion,
rejects scaled percentage/per-share readings, and returns ? for conflicting accounting-basis labels.

## Limits, retention and spend

- One non-animated PNG/JPEG, at most **2 MiB**, 32–4,096 pixels per side and **8 megapixels** total. The JSON body
  is limited to 3 MiB, below the preview platform's request limit.
- The server checks dimensions before decoding pixels, applies EXIF orientation, strips metadata and scales
  the longest side to at most 2,048 pixels. Small print may need a tighter crop.
- At most 8 model-returned chart/table regions and 32 readings. One reading applying to GAAP and non-GAAP can
  expand into two typed claims. SAM candidate regions may also appear in the overlay.
- At most **two SAM image calls** (`bar chart`, `table`) **and one Spark response**, with no automatic retries. Calls use Standard-mode
  `sam-3.1` and `muse-spark-1.3`, independently of the text extraction backend. Spark uses minimal reasoning,
  structured output and an 8,192 output-token ceiling.
- A slide shares the API's per-instance live-request limiter and durable daily ledger. Its reservation is
  persisted before work, and every SDK attempt passes a dispatch gate. Unknown usage retains per-call estimates
  ($0.0025 for SAM, $0.10 for Spark); uncertain settlement keeps at least the entire reservation. A worker crash
  leaves the precharge intact. SQLite coordinates processes sharing its file; optional PostgreSQL coordinates
  instances sharing the database/namespace. Vercel requires the shared backend by default. See
  [spend protection](spend-protection.md); this is not a provider-enforced spending ceiling.
- Calls observe per-call timeouts and the API's overall deadline. SAM receives a timeout of at most 15 seconds
  per call; an in-flight synchronous call still depends on its transport timeout, so this is not a strict
  wall-clock completion guarantee. Closing the stream closes its dispatch gate before settlement.
- Successful results and errors with accounting retain the estimate flag and call count. Missing/malformed
  error accounting displays **Model cost unavailable**, never an invented zero. Check and Fix now also preserve
  conservative accounting for failed or late attempts.
- No uploaded image, transcript or raw model response is written to local storage or the response cache. The
  server forwards a normalized image to the Meta Model API with `store: false`; this describes application
  storage behavior, not a broader guarantee about the provider's retention policy.
- Logs contain completion state, call counts, latency and cost only. Raw provider errors are not exposed.

## Validation

The current accounting changes passed **809 Python tests**, **90 frontend checks**, typecheck, lint and build
with **0 model calls / $0 new model spend**. They are deployed in authenticated Preview source `885a46f` with
the fixed shared Neon primary ledger. Actual PostgreSQL validation passed **8/8**; hosted Slide admission
rejected missing-ledger and exhausted-budget requests before any model call, including across redeploys.
These checks validate spending protection, not a new live vision run. The live measurements below are historical.

The seeded NVIDIA image now displays the **NoCap** product name. Its financial labels and table pixels are
unchanged from the original live-tested image; only the corner branding changed. The earlier live measurements
below refer to that original input, not a new call on the branded bytes. See the
[replay provenance](demo/slide-replay-provenance.json) for the original and displayed input hashes.

The product tests use controlled model responses and the ordinary deterministic verifier. They cover image
validation and orientation, malformed requests, strict model schemas, independent Meta routing, printed-value
errors, guidance, uncertainty, point-in-time checks, the axis-estimate rule, SAM fallback, deadlines and usage
settlement. Live product smoke checks on 2026-10-28 used Standard Meta models and the snapshot evidence. The first BAC run used a generic `chart` prompt; review of the original spike showed that `bar chart` was required, and the final run uses the shipped prompt:

| Image | Observed result | Live wall time | Meta cost |
| --- | --- | ---: | ---: |
| Original seeded NVIDIA draft (4 readings) | 2 verified, 2 wrong; both planted basis/period errors caught | 6.86 s | $0.012412 |
| BAC expense slide, initial generic prompt (32 readings) | 4 verified, 28 unverifiable, no wrong flags | 18.73 s | $0.030795 |
| BAC expense slide, final bar-chart prompt (23 readings) | 4 verified, 19 unverifiable; one SAM region and one Spark region | 22.04 s | $0.026919 |

Each made two SAM requests and one Spark request, with reported usage for every call. SAM found no usable
region on the NVIDIA table, so the UI honestly shows a Spark region; this is the intended grounding fallback.
Total Meta usage across all three runs was **$0.07012575**. These two example images do not measure general image-reading accuracy or
coverage. The older Spike C 12/12 result is not a measurement of this endpoint. See the
[smoke record](../eval/slide_dev_validation.json) and [seeded image](../examples/slides/nvda-draft.png).

### Real SEC slide sample (2026-10-30)

The Slide workspace's **Try a real SEC slide** button loads page 9 of Bank of America's 2Q26 earnings
presentation as filed with the SEC, used unmodified (8-K 0000070858-26-000353, Exhibit 99.2, accepted 2026-07-14 10:45:08 UTC;
[source image](https://www.sec.gov/Archives/edgar/data/70858/000007085826000353/bac06302026ex992009.jpg)).
Its live run through the shipped endpoint is recorded and replays at $0 for that exact request, also while live
requests are paused (`receipts/recordings.py`).

| Run | Regions | Readings | Verdicts | Wall time | Meta cost |
| --- | --- | ---: | --- | ---: | ---: |
| BAC 2Q26, page 9 (the shipped sample) | 2 SAM bar charts; Spark added the line chart and the commentary box | 32 (reading limit reached) | 5 verified: every GAAP net-interest-income bar, 2Q25–2Q26, against BAC's 10-Qs and 10-K; 27 unverifiable; 0 wrong | 21.02 s | $0.030198 |
| BAC 3Q26, page 9 (first attempt, not shipped) | 2 SAM, 2 Spark | 32 | 4 verified, 28 unverifiable: the 3Q26 bar is ? because the snapshot has no BAC 3Q26 filing yet | 22.83 s | $0.033016 |

The FTE totals are non-GAAP; they return ? and the explanation says the wording "FTE" qualifies the figure.
Yields and the net-interest-income mix are not tracked metrics. Each run made two SAM requests and one Spark
request. This is one real slide, not an accuracy or coverage measurement.

The frontend passed 22 client checks and 9 browser acceptance groups using recorded real responses, with
zero extra provider calls. Desktop/mobile, selectable overlays, receipts, invalid files, keyboard access and
stale-request cancellation were checked.

Official API references checked during implementation:
[image understanding](https://dev.meta.ai/docs/image-understanding),
[SAM requests](https://dev.meta.ai/docs/sam/segmenting), and
[SAM output grammar](https://dev.meta.ai/docs/sam/reading-segmentation).
[Standard pricing](https://dev.meta.ai/docs/pricing-rate-limits) was rechecked on 2026-10-28:
SAM costs $0.0025 per image; Spark input/cached input/output cost $1.25/$0.15/$4.25 per million tokens.
