# NoCap interaction-led recording — October 29

This adopted recording replaces the report-like screenshot montage with one continuous
recording of the actual product. The repository export is 165
seconds, 1920 × 1080 H.264, with burned-in English captions plus SRT/VTT. The product
viewport has its own space above the caption band, so captions do not hide controls.

| Time | Actual browser actions | Evidence mode |
|---|---|---|
| 0–18 s | Clear the editor, paste the NVIDIA draft, click Check, show 7 verified / 2 wrong / 2 unverifiable | Real offline verification with bundled extraction caches |
| 18–35 s | Click the 18% claim, point to filed growth and the calculation | Cached example / deterministic receipt |
| 35–50 s | Click GAAP EPS, compare $2.22 with the $2.46 filing value | Cached example / deterministic receipt |
| 50–94 s | Click Fix, inspect original/revised text, correction receipts, all rechecked claims, then copy with receipts | Exact October 29 live Wonder response; full recheck uses cached extraction |
| 94–121 s | Select the historical roundup, inspect Microsoft’s late filing, switch to Now and check again | Cached extraction; code recomputes cutoff verdicts |
| 121–147 s | Upload the actual seeded slide, click Check slide, select a numbered reading and its receipt | October 29 live response for the identical branded image bytes |
| 147–157 s | Enter the recorded revenue question, click Ask, inspect answer and SEC footnotes | Exact October 29 successful Spark response |
| 157–165 s | Return to Draft, scroll to the product header, show a small URL overlay | Eleven-company scope, author and public entry points |

The scene disclosure stays visible. Narration and captions explicitly distinguish
cached extraction and recorded responses; elapsed footage is not a model latency
measurement. Recorded responses return without the original provider wait. No artificial product UI or invented verdict is used. The mouse marker
and final links are recording overlays. The seeded slide is labeled as a draft,
not an SEC filing; SAM found no region in that recorded run, so Spark supplied it.

`capture_walkthrough.py` uses Playwright `record_video`, denies external requests,
and permits only exact known API inputs. GET and Check results come from the actual
offline API with provider factories forbidden. The recording makes no paid
model calls. The separately authorized fresh Fix recording cost $0.01108 for one
Wonder call through the real ASGI API; its subsequent browser use is a replay,
not a live browser/provider test. It corrects 18% to 105.9% and $2.22 to $2.46,
then reports nine verified claims, two unverifiable claims, and 10/10 number
coverage. `render_walkthrough.py` uses the existing licensed local Kokoro-82M
v1.0 / `af_heart` assets. Model/runtime permissions remain in `narration-license.md`.

No held-out number is embedded. Future results, if authorized and verified, can be
added to a short end segment without rebuilding the core walkthrough. Human review
and final publication are separate from recording and technical validation.

Reproduce with the existing production Next app on port 3106:

```bash
.venv/bin/python docs/demo/capture_walkthrough.py --output data/eval_runs/demo-walkthrough-20261029/take3 --fix-response data/eval_runs/demo-fix-current-20261029/events.json
.venv/bin/python docs/demo/render_walkthrough.py --source data/eval_runs/demo-walkthrough-20261029/take3
.venv/bin/python docs/demo/validate_walkthrough.py data/eval_runs/demo-walkthrough-20261029/take3
.venv/bin/python docs/demo/export_walkthrough.py
.venv/bin/python docs/demo/validate_media.py
```

Use `--quick` for a selector rehearsal and `--inspect` for the initial DOM/frame.
Neither rehearsal is a performance measurement or a substitute for the timed take.
The recording starts with a magenta synchronization slate that the renderer
detects and removes. The product is captured at 1600 × 780 CSS pixels and scaled
to 1920 × 936, with a separate 144-pixel caption band. The final product footage
runs at its original speed without internal cuts or substitute screenshots.
Tab-change timestamps independently verify that the Ask interface itself is
visible for at most ten seconds, including the transition into the closing shot.

The adopted local recording is `data/eval_runs/demo-walkthrough-20261029/take3/`:
165.000 seconds, 1920 × 1080 H.264/AAC, 12,443,559 bytes, 18 English cues. Its
MP4 SHA-256 is `8e8701cc6e0b33abecba1b93019789ba41f5b420819c672e35932520c9b92f1b`.
Full decoding, matching SRT/VTT cue text and timing, and audio-level checks passed.
The Ask tab is visible for 8.990 seconds. Browser capture logged zero page errors,
zero external or unapproved requests, and zero provider calls. Mean audio volume
is −16.8 dB and peak is −1.9 dB. The contact sheet and selected full-size frames
were inspected for readable product content, captions, and scene alignment.
This is technical/visual review, not human listening or final editorial approval.
`capture.json`, `manifest.json`, `narration.json`, and `validation.json` retain
the local evidence. Export copies the approved MP4 and captions to repository/public
asset paths and derives the 20-second GIF; deployment acceptance is separate.
