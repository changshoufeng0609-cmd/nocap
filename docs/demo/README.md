# NoCap demo kit

[Watch the 2:58 pitch demo](assets/receipts-demo.mp4) · [60-second vertical X/Threads cut](assets/nocap-x.mp4) · [20-second GIF](assets/receipts-demo.gif) · [English SRT](assets/receipts-demo.en.srt) · [English VTT](assets/receipts-demo.en.vtt)

## Current export: the pitch demo (November 4)

The submission video is a **2:58 pitch demo**. Illustrative animation carries the story; real footage of the NoCap
interface proves each step:

| Time | Part | What is on screen |
|---|---|---|
| 0:00–0:27 | Animation: the problem | One wrong number becomes a public correction; AI drafts make numbers cheap to write and costly to check |
| 0:27–1:53 | **Real interface** | Check an AI-drafted NVIDIA story (7 ✓ 2 ✗ 2 ?); receipts for 18% vs 105.85% and $2.22 vs $2.46; Fix with receipts (9 ✓ 0 ✗); time travel as of July 29 (6 ✓ 1 ✗ 2 ◷); a Bank of America slide (SAM 3.1, 32 labels); a whole Meta 10-K in one Muse Spark request; Private on Muse Glimmer |
| 1:53–2:19 | Animation: under the hood | Four Meta models propose; deterministic code decides against SEC EDGAR |
| 2:19–2:58 | Animation: proof, how to use, call to action | All 500 S&P 500 companies; one-shot test: 306 of 360 planted errors caught, none marked verified, 17 of 1,298 true figures flagged; then how to use it: paste a draft, press Check, click any number for its receipt, no sign-up |

The four key product moments are enlarged and framed, and every product segment carries a chip naming the Meta
model it uses. The screen footage is a new capture of the current interface (November 4, from the release source)
with live model requests paused: draft checks replay cached extraction, and Fix, Slide, Ask and Private replay
recorded live runs for the same inputs (Fix from October 29; the slide, the 10-K question and the Private check from
October 30). The capture made **zero provider calls and $0 of new provider usage**. Figures in the animation come only
from NoCap's real checks; the end card says the animation is illustrative.

| Artifact | Verified properties |
|---|---|
| MP4 | 177.83 s (2:57.8); 1920 × 1080; H.264, yuv420p, 24 fps; AAC 48 kHz; `+faststart`; SHA-256 `60fd6b694dcde7d527bf755f5fa485c87de408dfaec9c4171c3e4ce8a10a33c2`. A 7.67-second how-to-use beat was inserted at 2:39.0 on November 4 |
| Audio | Integrated −14.0 LUFS; true peak −1.4 dBTP; English narration (edge-tts `en-US-BrianNeural`) over a music bed made from free sample libraries |
| Captions | 41 English cues, burned in; SRT `00b05325…`, VTT `0fb6ea61…` |
| Checks | Reading-time check 0 misses; text contrast meets WCAG targets in every scene; speech recognition of the final mix scores 0.962 against the script; no black frames |
| Vertical cut | `nocap-x.mp4` for X and Threads: 59.83 s, 1080 × 1920, 24 fps; 16 English cues; −14.0 LUFS; SHA-256 `7c1ac2cc71033b6177c30863d5b51be612f0d3da1341c46a7d002e702326501d`. Same animation series, with each product moment lifted from the same capture and enlarged to nearly the full frame width; ends on the one-shot test (306/360 caught, none passed as verified, 17/1,298 flagged, verified calls right 736/738) |
| GIF | `receipts-demo.gif`: the pitch demo's 0:28–0:48 at original speed; 960 × 540, 10 fps; 5,595,702 bytes; SHA-256 `638f7d73b97abcc3529b197f783a6a51712b4be63e1b38dd098156677a3b2d8f` |
| Records | [validation](assets/validation.json), [manifest](assets/manifest.json); the previous walkthrough's records are in [historical](assets/historical/) |

## Previous version

Version **2.1** (October 31, superseded on November 4): a re-edit of the October 30 v2 recording of the real
NoCap interface plus one short take of the Private tab recorded on October 31; no v2 scene was re-recorded.
It opens on the stakes, shows one disclosure card, then walks through Check, a receipt close-up, Fix with
receipts, time travel, a real SEC slide (SAM 3.1 regions, 32 readings, five verified GAAP bars), exact quotes
from Meta's complete 10-K in one Muse Spark request, a "same verifier, four backends" card, a recorded Private
check on Muse Glimmer, a results card and an end card. The [timed script](video-script.md) lists every
scene, line and on-screen number with its source; the [recording notes](walkthrough-script.md) explain how the
footage was captured. Commit hashes in this kit refer to the private development history, which is not in the
public repository; only the public source `9db6cfc` under [Public player](#public-player) resolves there.

Changes from v2, following the October 30 review:

- The cold open says the two errors were **planted**.
- On the real SEC slide, an edit-layer highlight outlines the two regions SAM 3.1 found, and the narration
  accounts for all 32 readings: 5 GAAP bars verified; 27 non-GAAP, untracked or ambiguous readings left unverifiable.
- One backends card replaces the benchmark page and the static Private card.
- The results card showed only held-out results (until the November 2 update below); the closed-book Q&A moved to
  the backends card, labeled a development benchmark.
- A new Private-check shot: the product replays a recorded live run of Muse Glimmer on our own Modal GPU
  (one figure verified, one planted error caught). It was recorded on October 31 at commit `d6f7ebc`.
- The disclosure card adds that the green "Live checks are open" notice shows the judging-window setting.
  The X cut's title card carries a one-line disclosure.
- The Check label moved to the top-right corner, so the Check button stays visible.
- An original music bed fills the pauses.
- The voice says "back ends" for "backends", which it had stressed on the second syllable.

**End-card update, October 31 (owner-approved).** The end card of both cuts gained one line, "Now covering 499
S&P 500 companies": the footage dates from October 30, and coverage grew on October 31 to 499 of the 500 index
members (all but Exxon Mobil). Nothing else was edited. Durations, captions and audio are identical (the same AAC
packets). Decoded frames match the first v2.1 export up to the last keyframe before the card (159.44 s; X cut
43.36 s). From that keyframe to the card (0.84 s; X cut 1.72 s), x264 re-encoded the same pixels because it plans
each keyframe group ahead, at PSNR ≥ 62.4 dB (X cut ≥ 56.9 dB) against the earlier files. Rendered with the old
card, the pipeline reproduces both earlier MP4s byte for byte. The GIF is unchanged.

**Coverage-line update, November 1 (owner-approved).** Exxon Mobil joined on November 1, so the line on both end
cards now reads "Now covering all 500 S&P 500 companies", in the same place, size and color. Nothing else was
edited. Durations, captions and audio are identical (the same AAC packets). Decoded frames match the October 31
export up to the last keyframe before the card (159.44 s; X cut 43.36 s); from there to the card (0.84 s; X cut
1.72 s), x264 re-encoded the same pixels at PSNR ≥ 63.7 dB (X cut ≥ 57.9 dB). Rendered with the October 31 card,
the pipeline reproduces both October 31 MP4s byte for byte. The GIF is unchanged.

**Results-card update, November 2 (owner-approved, walkthrough only).** The results card (2:30.23–2:40.65) and its
line now lead with the comprehensive test of the November 2 round-8 release: 1,000 frozen test items from 400 S&P 500
companies, 306/360 planted errors caught and none marked ✓, a ✓ right 736 of 738 times, and 17 of 1,298 true figures
flagged ✗. The October 29 held-out results (44/48, 1/47, 20/20) stay as one secondary line, under a caveat line (one
frozen run, machine-made labels, details in the [methods](../../eval/spot500_methods.md)). The new line, "Across 1,000
test items, NoCap caught 306 of 360 planted errors and never marked one verified.", was synthesized locally with the
same voice. It runs 8.24 s, against 8.52 s for the old line, and the slot keeps its length. The runtime (171.88 s), the
26 cues and every shot and cue start are therefore unchanged; caption 25 changed its text and now ends at 2:39.57.
- **Audio:** the render reused the November 1 loudness measurements and its narration outside 2:30.28–2:40.03. The
  AAC packets are identical up to 2:30.72. Before encoding, the mix differs only from 2:30.73 to 2:42.63: the new
  line, then the music duck settling (at most −49 dBFS after the card). From 2:30.72 the encoder chose different
  packets for the unchanged end-card audio (decoded, they differ about 35 dB below the signal); the last 0.9 s of
  packets are identical again.
- **Video:** decoded frames are identical (same packets) up to 2:29.40 and from 2:41.44. x264 re-encoded the same
  pixels just before the card (2:29.44–2:30.20, PSNR ≥ 57.9 dB) and at the start of the end card (2:40.68–2:41.40,
  PSNR ≥ 52.8 dB).
- **Not re-exported:** the X cut and the GIF contain no results card.

## How it was made

- **Real interface, recorded locally.** The production build of `web/` ran on port 3106. Chrome's own
  compositor frames were recorded at 1280 × 640 CSS pixels and 2× device scale (2560 × 1280), so the edit
  can zoom into receipts while the text stays sharp.
- **Real API, paused.** `receipts.api` ran on port 8106 from `data/snapshot` with
  `RECEIPTS_LIVE_REQUESTS_ENABLED=false` and `RECEIPTS_PERSIST_DRAFT_CACHES=false`, inside a localhost-only
  macOS sandbox. The saved examples, the Bank of America slide and the Meta 10-K question replayed through
  that API at $0. It would have refused anything else; nothing was refused.
- **Fix** replays the exact recorded October 29 response (one authorized Muse Wonder call, $0.01108; [evidence](fix-live-2026-10-29.json)),
  byte-identical to the file the v1 harness used.
- **Private take (October 31, commit `d6f7ebc`).** The same harness (`--shot private`), build and paused API,
  with Private mode off as on the public deployment. The real API replays the committed recording of a live
  Modal Glimmer run (2026-10-30 18:17 UTC, 1 model call, 25.8 s; Modal billed $0.2418 in credits for that
  session, see [Private mode](../private-mode.md)). The take made one POST, a cached replay, and no model call.
  `/api/health` was not substituted; the Private tab does not read it.
- **Also disclosed here and in the manifest:** besides serving Fix from its recording (above), the harness passed `/api/health` through with
  `live_requests_paused=false` and `live_until=2026-11-17T16:00:00Z`, the owner-approved judging-window
  values in [deploy.md](../deploy.md). The green "Live checks are open until…" notice and the enabled Fix
  button therefore show what judges will see after the release deployment, not the local paused state.
- **Browser guard:** external requests blocked and only allowlisted API calls permitted. The capture log has
  0 page errors, 0 blocked or refused requests, 0 provider calls and $0 of new spend.
- **Edit:** cuts and camera moves inside each scene, framed from element boxes logged during capture; no
  speed changes; no product UI redrawn or altered. The edit layer adds only short scene labels in a corner
  and, on the SEC slide, an amber outline with a 45% spotlight dim around the two regions the product itself
  labels "Bar Chart · SAM", drawn from the page's logged boxes. The manifest lists every edit-layer element
  with its times under `edit_overlays`. Cards are separate full-frame text graphics. Captions sit in their own band.
- **Narration:** Kokoro-82M v1.0, voice `am_fenrir` (American English, male), synthesized on the local CPU.
  Not a person and not a clone. 20 of the 26 lines reuse v2's audio files; 5 were synthesized for v2.1 on October 31
  and 1, the results line, on November 2.
  [Voice choice and permissions](narration-license.md).
- **Music:** an original ambient bed computed by [`music_bed.py`](music_bed.py) from sine wavetables and seeded
  noise (no samples, loops or downloads), ducked 7 dB under each line. [Music provenance](narration-license.md#music-bed-v21).

The disclosure card at 0:11 states the same in six lines. Rendering, narration, export and validation
made **zero provider calls and $0 of new provider usage**.

## Version 2.1 exports (October 31, superseded)

| Artifact | Verified properties |
|---|---|
| MP4 | 171.88 s (2:51.9); 1920 × 1080; H.264 High, yuv420p, 25 fps; AAC 48 kHz stereo; `+faststart`; 37,102,660 bytes; SHA-256 `61282a2702ca9442adb1e9f4bf02fa4f950be45bd721093c5479c31723b95ef0` |
| Audio | Integrated −14.0 LUFS; true peak −1.5 dBTP; loudness range 3.5 LU. Music bed −25.3 LUFS, ending on its tonic; no stretch of 0.5 s or longer below −50 dB between the fade-in and the fade-out |
| Captions | 26 English cues, burned in; SRT `168d2610…`, VTT `a464bf20…` |
| X cut | 56.28 s, same format, 9 cues at 44 px; 13,247,539 bytes; SHA-256 `3b03ca6cb3e7d37936f0710139d1a68f72039be32381b0e48b1725de5dffa714`; −14.0 LUFS, −1.7 dBTP; SRT `d7cf51e5…`, VTT `de468b03…` |
| GIF | 20.000 s; 960 × 540; 10 fps; 4,766,072 bytes; source 16.56–36.56 s at original speed ([storyboard](gif-storyboard.md)) |
| Masters | v2 capture (commit `4bd6f4c`): 2560 × 1280, 25 fps, 219.88 s, 1,689 distinct Chrome frames, SHA-256 `67b19ad7…`. Private take (commit `d6f7ebc`): 2560 × 1280, 25 fps, 19.52 s, 177 distinct Chrome frames, SHA-256 `32ea4cff…`. Both local, not committed |
| Narration | Kokoro-82M v1.0 / `am_fenrir` through kokoro-onnx 0.6.1; 26 segments, 140.2 s of speech, 338 words |

Hashes, per-segment speech and timing, card hashes, the edit timeline, edit overlays, the audio chain and the
capture log are in the [manifest](assets/manifest.json); check results are in [validation.json](assets/validation.json).

## Verification and review

`validate_walkthrough_v2.py` decoded both cuts and checked `+faststart`, loudness, stream formats, that the music
ends on its tonic and leaves no dead air, the SRT/VTT cues against the manifest, and the capture provenance (2
Check, 1 Fix, 1 Slide and 1 grounding POST in the walkthrough capture and 1 Private check POST in the Private
take, all replays; both footage commits). It also wrote contact sheets and full-size key frames.
`validate_media_v2.py` re-checked the repository and public copies, script hashes, the voice record and the
historical records. The agent inspected the contact sheets and the key frames of the cold open, disclosure
card, Check click, verdict tiles, growth receipt, sources, EPS receipt, Fix counts, correction receipts,
not-public-yet receipt, slide summary, SAM highlight, verified GAAP bars, slide receipt, token line, quotes,
the backends card, the Private shot (click and result), and the results and end cards, plus the X cut's first
frame and its title and end cards. For the October 31 end-card update both validators ran again; every decoded
frame and audio packet was compared with the first v2.1 export, and the agent inspected the new end cards and
their fades in both cuts. The same checks ran for the November 1 coverage-line update, against the October 31 export.
For the November 2 results-card update the walkthrough's validators ran again; every decoded frame, audio packet and
pre-encode audio sample was compared with the November 1 export, and the agent inspected the new card, its fades,
caption 25 and the end card. Local Whisper small and medium, run in a sandbox with no network access, heard the new
line as written.

The v2 capture's API process started at 16:00:14 (+0800), three minutes before commit `4bd6f4c` landed on
the development branch. An in-process replay with `4bd6f4c` reproduced both captured Check responses byte for byte, so the
footage matches the committed verifier.

**Not claimed:** human listening (narration or music) or editorial approval. Deployed playback is covered only
by the automated start, duration, caption and hash checks in the November 1 release record; the November 2 export
has not been deployed yet. A person should watch and listen to both MP4s before the submission.

## Public player

`web/public/demo/` holds byte-identical v2.1 copies of the MP4 and VTT. The page text in
`web/app/demo/page.tsx` describes v2.1.

The November 2 results-card export is in the repository only. Deployments from November 1 on served the November 1
export (`9d5bb223…`). Its anonymous production acceptance, on November 1 with Production deployment
`dpl_2vPGGu27yA9Lx29ovmucC3BTwCvW` (public source `c99c9a0`), confirmed four things on desktop and on 390px mobile:
- v2.1 plays;
- it runs 171.88 s;
- it loads 26 English captions;
- the browser-downloaded MP4 and VTT match their SHA-256 hashes.

The [release record](../public-release-2026-11-01.json) has the details. The next release must deploy the new MP4 and
VTT and check their new hashes. <!-- verify-at-release -->

## Reproduce

Setup, the sandbox profile and the exact commands are in the [recording checklist](recording-checklist.md).
The recording harness fails rather than sending a model call when a recorded response is missing; a fresh
clone includes the scripts and final media, while the raw master and capture logs stay in gitignored
`data/eval_runs/demo-walkthrough-v2-20261030/`, the Private take in `data/eval_runs/demo-private-take-20261031/`,
the v2.1 render folder (both cuts, November 1 end card) in `data/eval_runs/demo-walkthrough-v21-endcard-20261101/`
and the November 2 walkthrough render (new results card and line) in `data/eval_runs/demo-walkthrough-v21-results-20261102/`.

## History

| Export | Date | Length | Notes |
|---|---|---|---|
| **v2.1 walkthrough** (current) | Oct 31 | 171.88 s | Re-edit of the v2 capture plus a recorded Private-check take: planted-error wording, SAM highlight, backends card, Private replay, results card, original music; 56.28 s X cut. End card of both cuts updated Oct 31 with the S&P 500 coverage line, which reads "all 500" since Nov 1. Since Nov 2 the walkthrough's results card and line show the comprehensive test |
| v2.1 walkthrough, November 1 coverage line | Nov 1 | 171.88 s | The walkthrough with the held-out-only results card and the end-card line "Now covering all 500 S&P 500 companies"; 37,044,660 bytes, SHA-256 `9d5bb223148b3cc80d19ed5985433d8c501cd855212ff2bb8667825d91274897`, kept in the private git history at commit `78847a2`; [manifest](assets/historical/2026-11-01-walkthrough-v21-coverage-manifest.json), [validation](assets/historical/2026-11-01-walkthrough-v21-coverage-validation.json). Its X cut is still the current one |
| v2.1 walkthrough, October 31 end card | Oct 31 | 171.88 s | The same cuts with the end-card line "Now covering 499 S&P 500 companies"; 37,036,160 bytes, SHA-256 `3dd8818760b8ce605757f48cfe3fbde1df5db72fd9a2af7c56dc903b59768cbd`; X cut 13,239,421 bytes, SHA-256 `e5e20d5d312701e11501b706da68694f0e57c2d21ae0d3d032cd94434aeed688`; both kept in the private git history at commit `5d6f7ed`; [manifest](assets/historical/2026-10-31-walkthrough-v21-endcard-manifest.json), [validation](assets/historical/2026-10-31-walkthrough-v21-endcard-validation.json) |
| v2.1 walkthrough, first export | Oct 31 | 171.88 s | The same cuts before the end-card coverage line; 36,901,732 bytes, SHA-256 `364b7773da0751b605f0707f2f29017c9e6f989e7b9ce2861c41dffad00e5011`; X cut 13,121,872 bytes, SHA-256 `f001ecd0f910d1aa20c617bbe52650918f4d81da1c6ec9723045c66789a5ce0a`; both kept in the private git history at commit `121a6cb`; [manifest](assets/historical/2026-10-31-walkthrough-v21-manifest.json), [validation](assets/historical/2026-10-31-walkthrough-v21-validation.json) |
| v2 walkthrough | Oct 30 | 168.68 s | Edited recording with zooms; benchmark page and a static Private card; no music; 27 cues; 35,000,399 bytes, SHA-256 `fec139b4bbc12cc0271f3654f0055d7f4eca90db0535b69e8bede79327c00231`, kept in the private git history at commit `1300f84`; [manifest](assets/historical/2026-10-30-walkthrough-v2-manifest.json), [validation](assets/historical/2026-10-30-walkthrough-v2-validation.json) |
| v1 walkthrough | Oct 29 | 165.000 s | One continuous recording, 18 cues, `af_heart`; 12,443,559 bytes, SHA-256 `8e8701cc6e0b33abecba1b93019789ba41f5b420819c672e35932520c9b92f1b`; [manifest](assets/historical/2026-10-29-walkthrough-165s-manifest.json), [validation](assets/historical/2026-10-29-walkthrough-165s-validation.json) |
| Screenshot montage, Kokoro | Oct 29 | 179.937663 s | 29 frames; 5,295,052 bytes, SHA-256 `a21a91487a1c96b2134f1dfd8d612fd2de2c1cfa0a094544550e0f9dc84ab602`; [manifest](assets/historical/2026-10-29-screenshot-manifest.json), [validation](assets/historical/2026-10-29-screenshot-validation.json) |
| Screenshot montage | Oct 28 | — | 10-company / 11,175-fact snapshot, earlier Fix and pre-branding slide responses |

v1 differed from v2 in ways judges noticed: per-frame response-mode labels and a disclaimer line, smaller
UI text, a seeded slide on which SAM found no region (Spark supplied it), an Ask answer then shown as
`$96221000000` (now rendered `$96,221,000,000`), no results and a closing on the saved examples. v1 was
deployed in `dpl_DWzZqy43YFywgeQTrW5S9REN9jz3`, where anonymous desktop and mobile checks on October 30
started playback with a 165-second duration and 18 optional cues ([release record](../public-release-fix-2026-10-30.json)).
Those observations are not evidence about v2 or v2.1. The [slide replay record](slide-replay-provenance.json),
[live smoke](live-smoke-2026-10-29.json) and v1 scripts are kept for reproducing earlier exports.

Before public distribution on October 29, macOS Samantha narration was replaced with licensed Kokoro
synthesis; the restricted media stays out of the public repository. Development began October 27; the clean
public export does not imply all work began on its export date. Real participant feedback remains separate
in the [user-study kit](../user-study/README.md); no user quotes or human-review outcomes are invented.
