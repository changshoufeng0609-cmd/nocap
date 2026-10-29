# NoCap video script — 2:45

The [current MP4](assets/receipts-demo.mp4) is a **165-second continuous browser recording**,
with 18 English captions and licensed local Kokoro-82M synthetic narration.
Author: Robert Chang, Team NoCap. The voice is not a human recording or clone.

The product remains visible throughout; there are no report-style title/metrics cards.
The recording uses real offline verification and exact recorded responses, labeled in-frame.
Original provider waits are omitted. The edited duration is not a model-latency measurement.

| Time | Exact caption / narration | Actual browser action |
|---|---|---|
| 0:00–0:08 | Here's an NVIDIA earnings draft. Two figures look plausible. Let's check them before publishing. | Clear and paste the seeded NVIDIA draft; move to Check. |
| 0:08–0:18 | Muse Spark extracts the claims. Code checks the SEC evidence. Seven pass, two are wrong, and two remain unverifiable. | Click Check. Show 7 verified / 2 wrong / 2 unverifiable. |
| 0:18–0:27 | Click eighteen percent. The draft says year over year, but eighteen percent is quarter-over-quarter growth. | Click the 18% claim; show the period mismatch. |
| 0:27–0:35 | The receipt calculates the year-over-year change: about one hundred and six percent. We can inspect both filed values. | Point to 105.85%, the calculation and filed source values. |
| 0:35–0:42 | Now click earnings per share. The draft labels two dollars and twenty-two cents as GAAP. | Click the $2.22 GAAP EPS claim. |
| 0:42–0:50 | That is the adjusted figure. The GAAP receipt says two dollars and forty-six cents, with the filing attached. | Show $2.46 filed GAAP EPS, the non-GAAP distinction and accession. |
| 0:50–0:59 | Let's fix both. Muse Wonder proposes numeric edits; code constrains them and verifies the revised draft again. | Click Fix with receipts; show the fresh recorded result. |
| 0:59–1:06 | The original stays on the left. The rechecked version shows exactly which numbers changed. | Keep original/revised text side by side; revised growth is 105.9%. |
| 1:06–1:18 | Each correction keeps its receipt. This is a recorded successful response, replayed through the actual interface, not a live speed test. | Scroll to the correction receipts and their filing evidence. |
| 1:18–1:28 | All eleven claims are checked again. The two errors are resolved. Guidance still has question marks; a forecast is not a filed fact. | Inspect every rechecked claim; guidance retains question marks. |
| 1:28–1:34 | Copy the revised draft with its correction receipts. | Click Copy with receipts and verify clipboard text. |
| 1:34–1:44 | A correct number can still arrive too late. Here is a roundup checked as of July twenty-ninth, sixteen hundred UTC. | Edit the draft, select the July 29 roundup, Check, and open Microsoft revenue. |
| 1:44–1:50 | Microsoft's filing arrived later. These claims are marked not public yet. | Hold on Not public until 2026-07-29 20:04:53 UTC and the 16:00 cutoff. |
| 1:50–2:01 | Switch to Now and check again. Those two numbers become verifiable. The cutoff follows EDGAR acceptance time. | Click Now and Check. Show 8 verified / 1 wrong. |
| 2:01–2:13 | The same workflow handles a slide. Upload this seeded earnings table and select Check slide. SAM proposes regions; Spark reads the figures. | Select Slide, upload the identical seeded image, enter context, and Check slide. |
| 2:13–2:27 | Click a numbered reading to open its receipt. Both planted errors are caught. Here Spark supplied the region after SAM found none. The image is a claim, not evidence. | At 133 s click reading 2; show its EPS receipt and actual fallback region. |
| 2:27–2:37 | Ask for one figure. Spark proposes the lookup; code builds a verified answer. Inspect its SEC footnotes. | Select Ask, enter the recorded question, submit and reveal its answer/footnotes. |
| 2:37–2:45 | NoCap: eleven companies, SEC receipts. Built during this hackathon by Robert Chang. Explore the saved examples. | Return to Draft; show the product header and compact public URL overlay. |

The spoken text separates “No Cap”, pronounces GAAP as “gap”, and spells SEC/UTC
where needed. Exact spoken text and segment timing are in the [manifest](assets/manifest.json).
The SRT/VTT match the visible caption wording. The narration totals 314 words.

## Sources and boundaries

- **Check:** the bundled NVIDIA example and current deterministic API, 11 companies /
  11,554 stored facts, with model factories forbidden during capture.
- **Fix:** the [October 29 live ASGI source](fix-live-2026-10-29.json), one real Wonder
  call costing $0.01108. Browser capture replays that exact response; full recheck
  uses cached extraction. The accepted edits are 18% → 105.9% and $2.22 → $2.46.
- **Cutoff:** `examples/roundup_q2_2026.txt`, exact example context and
  `2026-07-29T16:00:00Z`. Availability follows EDGAR acceptance, not every public channel.
- **Slide / Ask:** [October 29 seeded live smoke](live-smoke-2026-10-29.json).
  The slide response was obtained for the identical NoCap-branded bytes. SAM found
  no region, and Spark supplied the fallback. No general slide accuracy is implied.
- **Ask exposure:** 8.990 seconds from actual tab-change timestamps; the closing
  returns to Draft, so the end card does not extend Ask exposure.
- No participant quote, customer claim, held-out example or headline evaluation
  number is embedded. Human listening/editorial approval is not claimed.

See [recording controls and reproduction](walkthrough-script.md). This replaces the
historical 29-frame screenshot script, whose old ten-company scope, original-input
slide replay and macOS voice references do not describe the current export.
