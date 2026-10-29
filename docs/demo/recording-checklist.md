# Recording and evidence checklist

Use the current `wip/day3-handoff` app. Record the Git commit and whether each shot uses a result cache, a saved response, or new provider calls. Do not call replay timing a live latency measurement.

## Start the real local app

Use two terminals from the repository root, after the [normal local setup](../deploy.md#local-development):

```bash
.venv/bin/uvicorn receipts.api:app --port 8000
```

```bash
npm --prefix web run dev -- --port 3100
```

Open `http://localhost:3100`. Do not use `dev:mock` for recordings claimed to show real verification. The bundled examples replay with no model spend; Fix and a new slide request can make paid Meta calls. Keep existing credentials in `.env`; never show the file or terminal commands containing values.

## Preflight

- [ ] Record source commit with `git rev-parse HEAD`; identify any intended uncommitted recording-only overlays.
- [ ] Use a clean browser, 1920 × 1080 capture, consistent light/dark theme, and readable 100% browser zoom. Hide unrelated tabs, notifications, accounts and secrets.
- [ ] Confirm all displayed source documents are public filings, not a participant's unpublished draft.
- [ ] Confirm the UI and source-data snapshot match the tested commit. Do not rebuild evidence solely to make the demo look better.
- [ ] Watch the actual checked counts; if they differ from the expected results below, stop that shot and investigate.
- [ ] Test receipt navigation and copy buttons. A source link must point to the filing represented by its accession.
- [ ] Record browser errors and failed requests separately from the public footage. Keep raw uploads and model responses out of the public repository.
- [ ] Keep no `test` split command in a recording script or execution plan; this kit uses only examples and already reported dev results.

## Shot-by-shot expected results

| Shot | Expected observation | Recording boundary |
|---|---|---|
| NVIDIA check | 11 claims: 7 verified, 2 wrong, 2 unverifiable. `18%` is a period mismatch; `$2.22` is a basis mismatch. | Selecting the bundled example costs $0. A cached-result badge must remain visible. |
| Growth receipt | YoY comparison is `(96,221 / 46,743 - 1) × 100 ≈ 105.8511%`. The draft's 18% describes QoQ, not YoY. | Show the actual current receipt rather than a hand-built replacement. |
| EPS receipt | GAAP EPS `$2.46`; the quoted `$2.22` is non-GAAP. | Preserve basis, filing type, accession and source. |
| Fix | Previous live smoke: both errors resolved, 9 verified / 0 wrong / 2 unverifiable; guidance retained. | A new call can differ. Do not assume success. If replaying `data/eval_runs/fix_browser/live_events.json`, label it **Recorded response** and report $0 new spend for replay, not the saved response's original charge. |
| Roundup with cutoff | 6 verified; 1 entity mismatch; 2 `look_ahead` claims for Microsoft. The API historically totals 3 `wrong`; the updated UI separates the 2 not-public-yet claims. | Example context and `2026-07-29T16:00:00Z` cutoff must be preserved. |
| Roundup with Now | 8 verified, 1 wrong; Alphabet's `$7.70` EPS still belongs to JPMorgan. | Do not say all claims become correct. Extraction is cached; verification runs again. |
| Slide | Branding-refreshed NVIDIA display: 4 readings, 2 verified / 2 wrong, with overlay and EPS filing receipt. | Replay the original live input's response; do not claim that the new NoCap-branded image was live-checked. SAM originally returned zero regions; Spark supplied the fallback region. This example does not establish general product accuracy. |

The delivered slide shot uses `examples/slides/nvda-draft.png`, a table explicitly labeled **DRAFT · SEEDED DEMO / Not an SEC filing**. Only its branding was changed to NoCap; a pixel comparison confirms that all financial content and layout are unchanged. `slide-replay-provenance.json` identifies the original live input and recorded response; the capture manifest records the different branded display hash. Its HTML source and rendering script are included. A separate BAC earnings-slide smoke is documented by the implementation owner; keep its more complex results separate from this replay.

For a later real-filing shot, the local BAC candidate is `spikes/inputs/slides/bac_0000070858-26-000481_bac09302026ex992009.jpg`; provenance is its committed `.source.json` sibling. It is page 9 of the Q3 2026 earnings presentation, 8-K `0000070858-26-000481`, accepted `2026-10-14T10:44:44Z`. Page 10 is a second candidate. Keep GAAP printed labels distinct from FTE/non-GAAP totals and show the actual observed outcome.

## Capture tools already available

`/opt/homebrew/bin/ffmpeg`, `/opt/homebrew/bin/ffprobe`, and `/usr/bin/say` are installed. Python Playwright and a local Chromium installation were used for the existing browser checks. Prefer reusing that setup; do not install tools merely to capture this demo.

For browser automation, use a Playwright context with `record_video_dir` and a fixed `record_video_size`, close the context to flush the file, then edit/export with ffmpeg. A saved real response can be routed into `/api/fix` for repeatable capture without additional model spend, provided the footage explicitly says it is a replay. Keep such routing confined to the recording harness; do not change the product API.

Human narration is preferred for the submission. If local text-to-speech is used for a draft, label it as a draft narration track and review pronunciation of GAAP, EDGAR, Muse, EPS and the fractions before finalizing.

## Export acceptance

- [ ] GIF is 20 seconds and loops cleanly; its actual duration and size were checked with `ffprobe`.
- [ ] Final video is no longer than 3:00, 1920 × 1080, H.264 MP4 with readable captions and audible narration.
- [ ] Someone watched the entire exported files, including the beginning/end and every correction/receipt shot.
- [ ] All metric cards say **development** and preserve denominators. No correction-quality or slide-accuracy benchmark is implied.
- [ ] Cached, replayed and time-compressed shots are disclosed in-frame or immediately adjacent to the asset.
- [ ] URL and access instructions match the actual deployment. A private preview is not presented as anonymously public.
- [ ] No user quote or user-study outcome appears until it is actually collected and permitted for quotation.
- [ ] Log source commit, capture mode, date, output filenames, new provider spend and review status in `README.md` in this directory.

## Capture record template

```text
Capture date:
Source commit:
App URL / local ports:
Browser and viewport:
Check shot mode: cached / live
Fix shot mode: new live / recorded live response
Slide shot mode: new live / recorded live response / fallback used
New Meta spend:
Output files, durations and sizes:
Reviewed by / date:
Observed differences from script:
```
