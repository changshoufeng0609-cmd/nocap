# NoCap demo kit

[Watch the 165-second walkthrough](assets/receipts-demo.mp4) · [20-second GIF](assets/receipts-demo.gif) · [English SRT](assets/receipts-demo.en.srt) · [English VTT](assets/receipts-demo.en.vtt)

The current export is one continuous recording of the actual NoCap browser interface:
paste a seeded NVIDIA draft, Check, open the growth and EPS receipts, Fix and recheck,
inspect a historical cutoff, then show Slide and a short Ask flow. Product pixels
come from Playwright video; screenshots and report cards do not replace interactions.
The only recording overlays are the visible cursor, response-mode disclosure and closing links.

Check uses the real offline API with bundled extraction caches. Fix, Slide and Ask
replay exact recorded successful provider responses, with their original wait omitted.
The in-frame disclosures and captions identify cached/recorded operation. The runtime
is not a live latency measurement. [Timed script](video-script.md) and
[recording details](walkthrough-script.md) describe every scene.

## Current exports

| Artifact | Verified properties |
|---|---|
| MP4 | 165.000 seconds; 1920 × 1080; H.264/AAC; 12,443,559 bytes |
| GIF | 20.000 seconds; 960 × 540; 10 fps; 1,664,486 bytes; source video 8–28 s at original speed |
| Captions | 18 English cues; burned into the video; matching SRT/VTT sidecars |
| Ask | Actual tab visibility 8.990 seconds; closing returns to Draft |
| Scope | 11 companies; 11,554 stored facts; only seeded repository examples/public SEC evidence |
| Narration | Local CPU Kokoro-82M v1.0 / af_heart, through kokoro-onnx 0.6.1; synthetic, not the author's voice |

Full MP4/GIF decoding, caption bounds/text, source and output hashes, and local
public-player copies passed. Audio mean is −16.8 dB and peak is −1.9 dB. Agent
review inspected the contact sheet and selected full-size receipt, Fix, cutoff,
Slide and closing frames. This does not claim human listening or editorial approval.
See the [manifest](assets/manifest.json), [validation](assets/validation.json),
[narration permissions](narration-license.md) and [GIF source interval](gif-storyboard.md).

Recording, narration, export and validation made **zero provider calls / $0 new
provider usage**. The separately authorized fresh Fix source cost **$0.01108 for
one Wonder call**; its full recheck used cached extraction. [Fix evidence](fix-live-2026-10-29.json)
records the exact request match, response hash, 105.9% / $2.46 corrections,
9 verified / 0 wrong / 2 unverifiable claims, and number-coverage metadata.

Slide and Ask use the October 29 [live smoke](live-smoke-2026-10-29.json):
the slide response is for the **identical NoCap-branded image bytes**, with four readings,
two verified and two wrong. SAM found no region; Spark supplied the fallback region.
The image is a claim, not filing evidence. These seeded successes are not a general
accuracy or correction-quality benchmark. No held-out result is embedded in the video.

## Hosting boundary

The public player is [receipts-nocap.vercel.app/demo](https://receipts-nocap.vercel.app/demo).
This **165-second export** is deployed there. Production deployment
`dpl_34X9vCXnCGf3SHSYTvs1uQezGKji` serves public source `04ecc91` with
byte-identical MP4/VTT copies from `web/public/demo/`. Anonymous desktop and mobile
checks on October 29 started muted playback with a reported 165-second duration and
18 optional English cues. The sidecar track is off by default because captions are
burned in. The browser-fetched MP4 and VTT matched these files' hashes.
See the [current release record](../public-release-update-2026-10-29.json).
These are automated playback-start and download checks, not human viewing or
listening approval. Public live model requests remain paused; saved examples remain available.

The earlier **179.937663-second, 29-cue** version passed the same kinds of checks
in its own release. Those observations are historical and are not evidence about
this export. [Earlier release record](../public-release-2026-10-29.json).
The current source/download target is the [public repository](https://github.com/changshoufeng0609-cmd/nocap).

## Reproduce on the recording machine

Use the existing Python/Playwright environment, local Chrome, system Arial fonts,
ffmpeg/ffprobe, and licensed local Kokoro assets. See the [setup checklist](recording-checklist.md)
and [narration reproduction](narration-license.md#reproduction-and-review).
The recording harness fails if the required local response files are missing; it
never silently sends a replacement model call. A fresh clone includes final media
and scripts, but the original raw response/capture files remain gitignored.

Start the built frontend on port 3106, then run:

```bash
.venv/bin/python docs/demo/capture_walkthrough.py
.venv/bin/python docs/demo/render_walkthrough.py
.venv/bin/python docs/demo/validate_walkthrough.py data/eval_runs/demo-walkthrough-20261029/take3
.venv/bin/python docs/demo/export_walkthrough.py
.venv/bin/python docs/demo/validate_media.py
```

Export copies files only; it does not publish, deploy, or submit. The recording
harness blocks external requests and unapproved POSTs; provider factories are
forbidden during real offline Check verification. Inputs are synthetic repository
drafts and public filing facts, never a participant's private draft.

## Historical provenance

The October 28 export was an edited 29-frame screenshot sequence. It used the
10-company / 11,175-fact snapshot, the earlier Fix response and the original
pre-branding slide response. It is superseded by this continuous, 11-company recording.

Before public distribution on October 29, macOS Samantha narration was replaced
with licensed Kokoro synthesis. That superseded public MP4 was 179.937663 seconds,
5,295,052 bytes, SHA-256
`a21a91487a1c96b2134f1dfd8d612fd2de2c1cfa0a094544550e0f9dc84ab602`.
Its [manifest](assets/historical/2026-10-29-screenshot-manifest.json) and
[validation](assets/historical/2026-10-29-screenshot-validation.json) retain the
29-frame narration and original-input replay provenance. The earlier
[slide replay record](slide-replay-provenance.json) applies to that export;
the current recording uses the later identical-image live response.

The public repository's clean initial root excluded the restricted Samantha media.
Development began October 27; the clean export does not imply all work began on
the export date. [Narration history](narration-license.md) retains the permissions
rationale. Real participant feedback remains separate in the [user-study kit](../user-study/README.md);
no user quotes or human-review outcomes are invented.
