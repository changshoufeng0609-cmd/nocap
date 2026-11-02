# NoCap recording notes — v2 capture (October 30), v2.1 edit (October 31)

How the [v2.1 walkthrough](assets/receipts-demo.mp4) and its [X cut](assets/nocap-x.mp4) were captured and
edited. v2.1 re-edits the October 30 v2 capture and adds one Private take recorded on October 31; no v2 scene
was re-recorded. Scene timings and on-screen sources
are in the [video script](video-script.md); setup commands are in the [checklist](recording-checklist.md).

## Capture (`capture_walkthrough_v2.py`)

- **Browser.** Local Chrome through Playwright, 1280 × 640 CSS viewport at device scale 2, light theme.
  The page is the production build of `web/` on port 3106.
- **Frames.** [`screencast_recorder.py`](screencast_recorder.py) asks Chrome for its own compositor frames
  (CDP `Page.screencastFrame`) at 2560 × 1280 and encodes them with x264 (CRF 12) into a 25 fps master.
  Chrome sends a frame only when the page changes; each frame is repeated until the next one, using Chrome's
  own timestamps. Take 2 received 1,689 frames over 219.88 s (up to 60 per second during scrolls). This
  replaces Playwright's built-in 1 Mbit/s VP8 recorder, which blurred small text once zoomed.
- **API.** Every `/api/*` request goes through the harness:
  - `GET` health, examples, Ask options, filing index and Private status are fetched from the real API on
    port 8106 (snapshot only, live requests disabled, draft caches off, localhost-only sandbox).
  - `POST /api/check`, `/api/slide` and `/api/grounding` are forwarded unchanged to that API. A response
    counts only if it completes and is marked cached; anything else is logged as refused and fails the take.
  - `POST /api/fix` is answered with the recorded October 29 response, after checking that the request is
    the saved NVIDIA example with no cutoff and that the file's hash matches [`fix-live-2026-10-29.json`](fix-live-2026-10-29.json).
  - `/api/health` keeps the real response except `live_requests_paused=false` and
    `live_until=2026-11-17T16:00:00Z`, the owner-approved judging-window values. This is the only edited
    response; it is recorded in the capture log and the manifest.
  - Every other request, and every request to another host, is aborted and logged. Take 2 logged none.
- **Preflight.** The harness refuses to start unless the API reports `live_requests_paused: true`,
  `draft_cache_policy: snapshot_only`, 11 companies and 11,554 facts.
- **Overlays.** An init script adds the recording cursor (a white arrow that follows real mouse events) and
  smooth scrolling. Nothing else on the page is changed. Before each close-up the cursor is parked away
  from the figures the camera will show.
- **Interactions.** All clicks are real Playwright clicks on the rendered page: Check, the $2.22 claim,
  Fix with receipts, Edit draft, the Example picker, Check at the cutoff, the Microsoft claim, the Slide tab,
  "Try a real SEC slide", the region toggle, reading 10, the Ask tab, "Filing quotes", the recorded question,
  "Find exact quotes", the Benchmarks link and question 09.
- **Marks.** At each step the harness logs the master time, scroll position and bounding boxes of the
  elements the edit will frame (verdict tiles, receipt parts, SAM regions, the token line, answer cards).
  These boxes drive the camera, so the framing follows the actual layout.

Take 2 made 13 API requests: 5 health, 1 examples, 1 filing index, 1 Ask options, 2 Check (the NVIDIA
example and the roundup at 2026-07-29 16:00 UTC), 1 Fix, 1 Slide and 1 grounding. All forwarded POSTs
were cached replays. Page errors, blocked requests and refused requests: 0. Provider calls: 0.

The API process started at 16:00:14 (+0800), three minutes before commit `4bd6f4c` (a qualifier fix in
`receipts/verify.py`) was made on this branch. An in-process replay with `4bd6f4c`, model factories
forbidden, reproduced both captured Check responses byte for byte. The Slide and Ask samples replay recorded
runs as recorded.

Take 1 used the same harness; its framing review found the cursor resting on several key figures, so take 2
parks it first and holds the Ask filing view longer. Take 1 is not used.

## Private take (`capture_walkthrough_v2.py --shot private`, October 31)

- **Source.** Recorded at commit `d6f7ebc` with the same production build, browser and recorder settings. The
  product code was the committed tree; the only local changes under `web/`, `receipts/` and `data/snapshot/`
  were the demo media in `web/public/demo/`, which this page does not load. The manifest lists both footage
  commits: `4bd6f4c` for the walkthrough capture and `d6f7ebc` for this take.
- **API.** The same paused, snapshot-only, localhost-only API, also with `RECEIPTS_PRIVATE_MODE=false`, so
  `/api/private/status` reports "Private mode is unavailable until the operator configures Modal Glimmer.", as
  on the public deployment. `/api/health` is passed through unchanged (paused); the Private tab does not read it.
- **The one POST.** The harness forwards `POST /api/private/check` only for the exact public sample (text,
  company and period, as-of time) and requires the real API to answer it as a replay. The API answers from the
  committed recording of a live Modal Glimmer run (2026-10-30 18:17 UTC, 1 model call, 25.8 s; recording
  SHA-256 `6659c1a4…`), with no model call.
- **Interactions.** Real clicks on the Private tab and on "Replay a recorded Private check"; the form fills
  with the sample, the result appears below, and the page scrolls to it.
- **Log.** 6 API requests (3 health, 1 examples, 1 Private status, 1 Private check, cached), 0 page errors, 0
  blocked or refused requests, 0 provider calls; 488 frames over 19.52 s.

## Edit (`render_walkthrough_v2.py`)

- **Timeline.** Each shot is as long as its narration plus a short hold; the narration drives the cut.
  The renderer asserts that no shot runs past the next action in the capture, so a shot never shows an
  unplanned scroll or click.
- **Camera.** Each shot has keyframes: a 2:1 crop (in CSS pixels) computed from the logged boxes, with eased
  moves between them. Crops are resampled from the 2560 × 1280 master with Lanczos at sub-pixel precision.
  The maximum zoom is 2.4×, which still upscales the master only 1.2×.
- **Cuts.** Hard cuts within a scene; 0.35–0.45 s cross-fades between scenes and cards. No speed changes,
  no frozen frames, no reordering inside a shot. The cold open reuses footage recorded right after Check.
- **Cards.** Stakes, title and disclosure, backends, results and end cards (title and end cards in the X cut)
  are drawn in the product's own fonts (Geist, Geist Mono and Newsreader from the web build) on a dark
  background, so they never look like product UI. The web fonts have no ✓ or ✗ glyph, so the marks on the
  results card are drawn as vectors in the product's verdict colors. Their PNGs are hashed in the manifest.
- **Scene labels.** A small label naming the Meta model's role appears for four seconds at the start of six
  scenes. On the Check and Private shots it sits top-right, clear of the Check button and of the draft field
  that fills on the replay click; on the wide slide shot it sits bottom-right so the NoCap header stays visible.
- **Private shot.** It opens on the Private form card rather than the full viewport, because the status line
  above the card is cut in half at the top of the take; after the page scrolls, the camera frames the result.
- **SAM highlight.** On the real SEC slide, an amber outline and a 45% spotlight dim mark the two regions the
  product labels "Bar Chart · SAM", with the label "SAM 3.1 · two bar-chart regions". The boxes are the DOM
  boxes logged at the `slide-regions` mark, after the page stopped scrolling, and the highlight is drawn only
  while the page is static. The manifest lists it under `edit_overlays`.
- **Captions.** A separate 120-pixel band below the 1920 × 960 product area; up to two lines; one caption per
  narration line, shown while it is spoken (34 px; 44 px in the X cut).
- **Audio.** Kokoro speech per cue (silence trimmed), placed on the timeline and normalized as in v2 (two-pass
  `loudnorm` to −14 LUFS, −1.5 dBTP). The original music bed ([`music_bed.py`](music_bed.py)) is ducked 7 dB
  under each line and set to −26 LUFS; the mix passes a −2.5 dBFS lookahead limiter and a final linear
  `loudnorm` pass to −14 LUFS, then 48 kHz stereo AAC at 192 kbit/s.
- **Video.** x264 `slow`, CRF 17, High profile, BT.709, yuv420p, keyframe every 2 s, `+faststart`.

## Earlier recordings

The v1 recording (October 29, `take3`) was one continuous 165-second take at 1600 × 780 with Playwright's
recorder, per-frame response-mode labels and `af_heart` narration; its records are in
[`assets/historical/`](assets/historical/). Its scripts (`capture_walkthrough.py`, `render_walkthrough.py`,
`validate_walkthrough.py`, `export_walkthrough.py`, `validate_media.py`) remain for reproducing it.
