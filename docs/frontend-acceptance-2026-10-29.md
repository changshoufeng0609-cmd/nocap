# Frontend acceptance — 2026-10-29

The story checked here is draft or question → guarded API → deterministic SEC verdict or exact quote → visible evidence and export. The new public-player route is `/demo`; publishing it is a separate operator action.

## Build and offline checks

Next.js production build passed with `/demo` among 9 generated routes/pages. All 144 frontend checks passed (Fix 14, presentation 18, Slide 22, cost 36, coverage 29, Private 8, Ask 17); TypeScript and ESLint passed without warnings. Quote export now preserves the backend notice as well as the provenance disclaimer and Unicode offsets.

The public MP4 is an unchanged copy of the existing recording: 4,223,302 bytes, SHA-256 `f508649ad60221a4f3c39114078816f00bae71ba55a790bdb5945ef4d0fc94d1`. Its 29 SRT cues were converted to WebVTT without changing caption text. The page discloses screenshot editing, synthetic narration, recorded responses, seeded examples and historical development denominators. Ask and Private are explicitly outside the video recording.

## Browser evidence

All browser interaction used `cua_repl`. The actual production Next server ran on `127.0.0.1:3102`, behind a local same-origin acceptance wrapper on `127.0.0.1:8102`. Its model factories were prohibited and its admission budget was zero. Draft used the real cached API. Ask used deterministic Store results or clearly labeled replay of the separate live smoke. Filing quotations used a clearly labeled synthetic fixture passed through the real exact matcher. No browser action made a new provider request.

| Flow | Observed result |
|---|---|
| Draft | NVIDIA cached example: 7 verified, 2 wrong, 2 unverifiable; digit coverage 10/10; cached replay accounting shown. |
| Mode navigation | Draft, Slide, Ask and Private render distinct workspaces. ArrowRight, ArrowLeft wrapping, Home and End move selection and focus. |
| Ask numeric answer | Exact accepted sentence, interpreted company/period/basis/metric, two matching SEC footnotes and acceptance timestamps rendered. The newly recorded live response was accepted by the actual frontend. |
| Ask copy | Clipboard contained the exact accepted answer, `[1][2]`, accessions, URLs, cutoff and interpreted lookup. |
| Ask cancellation/stale state | Editing cleared old answer and copy status; a delayed fixture showed progress, Cancel cleared it, and its late result did not overwrite the subsequent state. |
| Ask error accounting | Controlled failure displayed the supplied estimated $0.0120 cost instead of claiming a free failure. This fixture cost is simulated, not new billing. |
| Filing quotes | Exact synthetic quote, Unicode code-point range 14–90, source metadata, 1 rejected proposal, and provenance-not-truth notice rendered. No financial verified badge was shown. |
| Quote copy | Exact quote, offset, source, generic provenance disclaimer and backend notice all reached the clipboard. |
| Filing cutoff | A cutoff before filing acceptance prevented submission and displayed a clear eligibility error; stale quotation results were cleared. |
| Private | Unconfigured local service explicitly unavailable; Check privately disabled. A typed sentinel disappeared on unmount and was not transferred into Draft or restored on return. Operator Modal / not offline wording was visible. |
| Demo | Header/footer entry and workspace return link worked. Native video controls started/stopped playback. Browser reported duration 179.839193 seconds, one English track with mode `showing`; captions were visible. |
| Console | No warnings/errors returned by the browser log inspection during the successful desktop flows. |

Screenshots are local review artifacts: `data/eval_runs/final_ui/ask-recorded-live.jpg`, `filing-quotes-fixture.jpg` and `demo-playing.jpg`. The synthetic quote is not a real SEC quotation and must not be used as product evidence.

## Explicit browser limitations

- Chrome's file-chooser API required extension access to local file URLs. That permission was not expanded. A separate native-browser attempt did not produce an upload verification. The new Slide response was instead accepted by the actual `streamSlide` client offline (4 claims, 2 verified / 2 wrong); the new image upload/overlay browser journey remains unconfirmed in this run.
- Clicking Download text did not yield a completed browser download event within 10 seconds. Export contents were verified through the same export function and the clipboard; an actual downloaded file was not inspected. Browser policy blocked opening the internal downloads page, and that block was not bypassed.
- The documented viewport override returned successfully but the actual browser remained 1470 CSS pixels wide. A labeled local 390-pixel iframe harness remained blank. No mobile-layout pass is claimed from this run; the override was reset. This is distinct from previous mobile acceptance evidence in the handoff.

## Separate paid smoke

One live Ask and one newly branded seeded slide were run through the actual Python routes with a separate local SQLite ledger, a $1 reservation allowance, SDK retries disabled and a persisted one-shot guard. Only the designated Standard Meta models were permitted. No Neon ledger, environment defaults, deployment or test split was changed.

- Ask: 1 Spark call, $0.006007 measured usage; NVIDIA GAAP Q2 FY2027 revenue $96.221 billion with two SEC footnotes.
- Slide: 2 SAM calls + 1 Spark call, $0.012618 measured usage; four readings, two verified and two wrong. The planted GAAP EPS and YoY/QoQ errors were identified. Both SAM calls completed, but the final earnings-table region came from Spark fallback. No duplicate/conflicting readings were reported; whole-image completeness remains unmeasured.
- Total: 4 HTTP attempts, $0.018625 settled, no retries. The image hash is `7e93576180457003522abf1c46a4cfb4889869d476cc9a735d72d81545fa7c7c`.

The committed summary and response hashes are in [live smoke metadata](demo/live-smoke-2026-10-29.json). Full local events, usage, ledger and client-contract result are under `data/eval_runs/final_ui/live/`. This does not convert the older video replay into a live recording or establish general extraction accuracy.

## Follow-up: actual mobile viewport, upload and downloads

A later Playwright run used headless installed Chrome with a real **390 × 844 CSS-pixel viewport** against
the same production build and local wrapper. This closes the three earlier browser limitations for this
local build. The first navigation exposed a wrapper-only defect: it omitted Next's
`next-router-segment-prefetch` and `next-url` headers, causing a repeated RSC prefetch redirect and preventing
network idle. Forwarding those headers fixed the harness; product code was unchanged.

**Five flow groups passed**, with seven layout states checked: initial/result Draft, numeric Ask, filing
quotes, Slide overlays/receipt and Private. In every state, `innerWidth`, document width and body scroll
width were **390** and the mobile media query matched. Screenshots were visually reviewed. The mode tab
strip can scroll within its own container; the page has no horizontal overflow.

- Actual cached NVIDIA Check rendered **7 verified / 2 wrong / 2 unverifiable**, with **10/10** digit candidates linked.
- The exact NoCap-branded image from the recorded smoke was uploaded through the browser's file input.
  Its outgoing bytes matched SHA-256 `7e93576180457003522abf1c46a4cfb4889869d476cc9a735d72d81545fa7c7c`.
  The labeled recorded response rendered four numbered overlays and **2 verified / 2 wrong**; clicking an
  overlay opened the corresponding SEC receipt. Removing the image cleared overlays and disabled Check.
- Both browser downloads completed and their saved UTF-8 contents were read: numeric answer **637 bytes**,
  with the exact answer, resolved lookup, cutoff, accessions and SEC URLs; synthetic filing quotations
  **710 bytes**, with the exact quote, Unicode offsets and provenance limitation. Their SHA-256 values are
  recorded in the acceptance result.
- An explicitly labeled unavailable-Private status fixture kept Check privately disabled with a nonempty
  draft; changing modes removed the sentinel draft/context and did not transfer them into Draft. A separate
  read-only probe of the actually configured status confirmed that typing normally enables the button;
  no Private draft was submitted. Configuration is not a live service availability test.

The final run had **0 page errors, console warnings/errors, HTTP errors or external browser requests**.
The wrapper still forbade model factories and had a zero admission allowance. Ask/Slide results were
historical replay, quotations synthetic, and Private unavailability an explicit UI fixture: **0 new
provider calls / $0 new spend**. Evidence, downloaded files and screenshots are under
`data/eval_runs/finish_ui/`, with the final report at `result.json`. The browser was closed after the run.
