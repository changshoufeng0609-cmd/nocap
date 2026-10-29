# NoCap demo kit

This kit prepares a three-minute demonstration for financial journalists, analysts and hackathon judges. The story is **draft → check → receipt → constrained correction → historical cutoff**. It uses seeded examples, not an unseen accuracy test.

**Review the generated assets:** [three-minute MP4](assets/receipts-demo.mp4), [20-second GIF](assets/receipts-demo.gif), [English captions](assets/receipts-demo.en.srt), [source and media manifest](assets/manifest.json).

Both assets are edited sequences of actual browser screenshots, not a continuous live session or latency measurement. The check frames use offline verification with existing extraction caches; Fix and slide frames replay recorded successful live responses. The video uses clearly labeled **Kokoro-82M `af_heart` synthetic narration**, not a human recording. The voice was generated locally on the CPU; creating these assets incurred **$0 new provider spend**. See [narration sources and permissions](narration-license.md).

- [Three-minute script](video-script.md): timed narration, exact actions and a replaceable slide segment.
- [20-second GIF storyboard](gif-storyboard.md): a compact loop for the README.
- [Recording checklist](recording-checklist.md): setup, expected results, evidence and export settings.
- [User-study kit](../user-study/README.md): a separate plan for real participant sessions.

## Public player

The public player is [receipts-nocap.vercel.app/demo](https://receipts-nocap.vercel.app/demo), with a byte-identical copy of the current MP4, 29 English WebVTT captions, visible synthetic-narration/replay disclosures and links back to the workspace. Media paths are `/demo/receipts-demo.mp4` and `/demo/receipts-demo.en.vtt`. Anonymous desktop/mobile checks confirmed playback started, reported duration was 179.937663 seconds and 29 caption cues loaded; actual browser downloads matched the final MP4/VTT hashes. This does not claim a full-duration viewing or human listening review. The [public GitHub video download](https://raw.githubusercontent.com/changshoufeng0609-cmd/nocap/main/docs/demo/assets/receipts-demo.mp4) also passed anonymous HTTP and exact hash verification. [Public release evidence](../public-release-2026-10-29.json).

## Recording status

The text kit and reviewable media files are prepared. Frame review, full media decoding, audio-level checks and the narration replacement have passed. The owner should review the finished video before submission. No human listening or editorial sign-off is implied.

| Deliverable | State | Evidence to record when finished |
|---|---|---|
| Three-minute script | Prepared | `video-script.md` |
| README GIF storyboard | Prepared | `gif-storyboard.md` |
| README GIF | Generated and checked | 20.000 s; 960 × 540; 304,543 bytes; edited screenshot sequence |
| Three-minute video | Narration refreshed and checked | 179.937663 s; 1920 × 1080; H.264/AAC; 5,295,052 bytes; English captions and disclosed synthetic voice |
| Product slide segment | Included from original-input response replay | Branding-refreshed NVIDIA display: 4 readings, 2 verified / 2 wrong. SAM returned no region on the original input; Spark supplied the fallback region. |
| Human feedback | Not collected | Anonymized notes in `../user-study/`; no fabricated quotes |

## Narration release check

The earlier local MP4 used macOS Samantha. Section 2F of Apple's [macOS Tahoe 26 license](https://images.apple.com/legal/sla/docs/macOSTahoe.pdf) limits System Voices to “personal, non-commercial use” and expressly excludes other publication or redistribution in public-sharing contexts. That audio was replaced with local **Kokoro-82M v1.0 / `af_heart`** before public distribution. Its official model/voice source declares Apache-2.0; the kokoro-onnx runtime uses MIT. The [narration provenance](narration-license.md) records sources, versions, asset hashes and reproduction steps. The current on-screen attribution, manifest and player copy all identify the replacement. The GIF has no voice audio.

The [public repository](https://github.com/changshoufeng0609-cmd/nocap) begins with clean root commit `7856fe65536597b13a96723f54b83b1bc8187df0`, excluding the older voiced exports; the original incremental development history remains private. Anonymous HTTP on October 29 confirmed the public licensing record and downloaded the current MP4: **5,295,052 bytes**, SHA-256 `a21a91487a1c96b2134f1dfd8d612fd2de2c1cfa0a094544550e0f9dc84ab602`. Subsequent Production playback/caption and browser-download checks passed independently; these technical checks do not imply human editorial approval.

## What the demo may claim

The current evidence supports the local draft-check and Fix flows, cached examples, historical EDGAR cutoff, one small Fireworks live extraction benchmark, and the integrated slide flow's smoke tests. The slide segment shows a [NoCap-branded display of the NVIDIA draft](../../examples/slides/nvda-draft.html) with the original input's recorded model response and deterministic verdicts. **The recording itself replays the original image response.** A separate [2026-10-29 live smoke](live-smoke-2026-10-29.json) subsequently checked the NoCap-branded bytes: 4 readings, 2 verified / 2 wrong, $0.012618 for two SAM calls and one Spark call. The video was not changed; the new smoke is one seeded example, not general slide accuracy. Its numbers and layout are pixel-identical to the original outside the footer logo. The original input and recorded response are fixed by [replay provenance](slide-replay-provenance.json); the manifest separately identifies the branded display. This is not a benchmark of arbitrary slide-reading accuracy. SAM returned zero regions on the original input and Spark supplied the fallback region; the narration says so explicitly.

The development results are **104/109 planted errors caught**, **0/107 original items with false alarms**, and **51/250 true claims unverifiable** for v0 + basis5. Keep the denominators and the word **development** visible. These results are not a benchmark of automatic correction quality or arbitrary uploaded slides. See [evaluation methodology](../../eval/README.md) and [Fix validation](../fix-with-receipts.md).

Never claim a general accuracy percentage, guaranteed savings, an existing customer, or a production deployment from these examples. Do not show the held-out corpus. Ask mode is outside this recording.

## Reproduce on this recording machine

The scripts use the existing Python environment, local Chromium, system fonts, local Kokoro/ONNX assets, ffmpeg and ffprobe. Install the optional media dependencies and obtain the exact model assets as described in [narration reproduction](narration-license.md#reproduction-and-review). The renderer itself does not download weights or make paid API calls. Start the real local frontend as described in the [checklist](recording-checklist.md), then run:

```bash
.venv/bin/python docs/demo/render_slide_fixture.py
.venv/bin/python docs/demo/capture_demo.py
.venv/bin/python docs/demo/capture_demo.py --slide-only
.venv/bin/python docs/demo/render_demo.py --with-slide
.venv/bin/python docs/demo/validate_media.py
```

`capture_demo.py` defaults to `http://localhost:3111`; pass `--url http://localhost:3100` for the checklist's default port. Use `localhost` for Next's development origin policy. The draft capture creates a local TestClient with provider factories forbidden and a zero model budget, then routes the UI to those verified saved responses. The slide capture intercepts all model-capable POSTs, checks that uploaded bytes match the branded display fixture and verifies the fixed original response hash. `render_slide_fixture.py` permits only the declared branding substitution in the HTML and compares financial pixels with the original image recovered from the recorded Git commit.

The raw Fix and slide response files under `data/eval_runs/` are intentionally local and gitignored. A fresh clone contains the final assets and source scripts, but needs the original recording files to regenerate the same replay. Missing replay files fail rather than silently issuing a live request. Intermediate screenshots, speech files and clips stay under `data/eval_runs/demo_capture/`.

## Capture and validation record — 2026-10-28

- NoCap draft/Fix/slide capture base commit: `cb033c56c50ba9d599d45d9d61d1c030396a83c0`, with the branding changes present in the working tree. All browser screenshots, cards, captions and synthetic narration were regenerated. The manifest records the capture/render script hashes, each source screenshot, original response hashes, frame hashes and exported assets.
- The recorded UI reports **11,175 facts**, matching the October 28 snapshot's **3,281 SQLite + 7,894 press-release facts**; the later Tesla expansion is outside this recording. The slide footer change is confined to image pixels `(1089, 722)`–`(1356, 741)`; the complete financial content above row 700 is pixel-identical. Original live input SHA-256: `7de51244baf4b71e59dcdffd18a78373b0eea05b94234b110f3486f37183bcc0`. Branded display SHA-256: `7e93576180457003522abf1c46a4cfb4889869d476cc9a735d72d81545fa7c7c`. These new bytes were not sent for live checking during the October 28 capture; the separate October 29 check is recorded above.
- Browser capture: Chromium, 1440 px viewport; no page errors in the capture flows. Frames were cropped for readability without changing any displayed product values.
- Model spend for capture/render: **$0**. Historical provider costs visible inside screenshots belong to their original recorded runs.
- Reviewed all 29 caption frames in a contact sheet and full-size title, receipt, Fix, cutoff, metrics, slide and closing frames. The Check crop preserves its actual verdict counts and Cached badge. `ffprobe` verified dimensions/codecs/durations; full MP4 and GIF decode passed. That original audio measured mean **−16.5 dB**, peak **−1.9 dB**; it was superseded by the October 29 narration refresh below.
- The final video stays below 180 seconds; its SRT's last cue is bounded to the exported runtime. No hosted preview URL, customer quote, public-release claim or user-study outcome is included.

## Narration refresh and current exports — 2026-10-29

The replacement rendered 29 local CPU speech segments with Kokoro-82M v1.0 / `af_heart` through kokoro-onnx 0.6.1. All caption text and cue boundaries remain byte-identical to the original SRT, and the GIF is unchanged. Pixel comparison found the same product content above the narration footer in all 29 frames; only the voice attribution changed. The original recorded responses and development results were not regenerated.

Full MP4/GIF decoding, caption bounds, source/output hashes and public-player copy checks passed. The current MP4 is **179.937663 seconds / 5,295,052 bytes**, with audio mean **−17.6 dB** and peak **−3.7 dB**. The 29-frame contact sheet and a full-size EPS frame were visually reviewed. Pronunciation checks inspected the actual phonemizer output for NoCap, GAAP, EPS, SEC and UTC; they are not human listening or editorial approval. See [validation](assets/validation.json) and the per-segment [manifest](assets/manifest.json). New paid API/GPU usage was **$0**.

| Current export | SHA-256 |
|---|---|
| `receipts-demo.mp4` · 5,295,052 bytes | `a21a91487a1c96b2134f1dfd8d612fd2de2c1cfa0a094544550e0f9dc84ab602` |
| `receipts-demo.gif` · 304,543 bytes | `377407e49462b4e9dbfb654b47ae2005e9090108d256d0fa18409f3e7704f767` |
| `receipts-demo.en.srt` | `b3ec47eb651b0d2e7911205d59745a40084efd3dacebae8a7c5d1c10e7c86158` |
| Public `receipts-demo.en.vtt` | `42cd50967f931a1b7989be326484a3b1bf4deb38c07e9e6e1895100f09d66eed` |
