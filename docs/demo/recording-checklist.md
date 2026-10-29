# Recording and evidence checklist

The current recording is the 165-second interaction-led export. Its reproducible
commands and result are in the [demo README](README.md). No product code or
provider response is modified to make the recording appear successful.

## Local setup

Start an existing production build of the real frontend:

```bash
npm --prefix web run start -- --hostname 127.0.0.1 --port 3106
```

The capture harness does not need an external API server. It derives Check/GET
responses from a local FastAPI TestClient with model factories forbidden and a
zero model budget. All other inference responses must match the exact allowlisted
recorded input. External browser requests and unapproved POSTs are blocked.
Missing response files fail instead of making new paid requests.

Python Playwright, local Chrome, ffmpeg/ffprobe and the licensed Kokoro assets are
required. The browser viewport is 1600 × 780 CSS pixels, recorded at those exact
dimensions and scaled to 1920 × 936. A separate 144-pixel subtitle band produces
a 1920 × 1080 export. A setup-only synchronization slate is removed before export.

## Evidence checked for this export

| Flow | Expected result / source |
|---|---|
| NVIDIA Check | 7 verified, 2 wrong, 2 unverifiable; cached extraction, deterministic verification |
| Growth receipt | The draft's 18% is QoQ; the filed YoY calculation is approximately 105.85% |
| EPS receipt | $2.22 is non-GAAP; GAAP EPS is $2.46 |
| Fix | October 29 real ASGI live Wonder response; 105.9% and $2.46, 9 verified / 0 wrong / 2 unverifiable; cached full recheck |
| Number coverage | Fresh Fix response has original and revised coverage metadata; revised digit-candidate coverage is 10/10 |
| Historical cutoff | At 2026-07-29 16:00 UTC, Microsoft has two not-public-yet claims; its filing was accepted at 20:04:53 UTC |
| Now | Eight verified, one wrong; Alphabet's misattributed EPS remains wrong |
| Slide | Identical branded image bytes from October 29 live smoke; four readings, two verified and two wrong; honest Spark-region fallback |
| Ask | Exact recorded query and response, verified answer and SEC footnotes; tab visible for 8.990 seconds |

The slide is explicitly labeled a seeded draft, not an SEC filing. It is an input
claim source, never verification evidence. All drafts are repository examples;
no unpublished participant text, account screens, terminal credentials or secrets
are captured. Do not add held-out examples or new headline results to this video.

## Export checks

- Full MP4 and GIF decode; 165-second H.264/AAC video; 20-second GIF.
- 18 matching English SRT/VTT cues, all bounded to the exported runtime.
- In-frame cache/replay disclosures; original provider waits omitted, no latency claim.
- Manifest hashes for original responses, capture/render/export scripts and outputs.
- Browser errors and external/unapproved requests both zero.
- MP4/VTT under `web/public/demo/` match repository assets byte for byte.
- Contact sheet and selected full-size scenes reviewed for readability and alignment.
- Human listening/editorial approval is not claimed; deployment acceptance is separate.

Narration uses local Kokoro-82M v1.0 / af_heart under the documented permissions.
Do not use the historical macOS System Voice export for public distribution.
The former screenshot capture/render tools remain only for historical reproduction;
current assets use `capture_walkthrough.py`, `render_walkthrough.py`,
`export_walkthrough.py` and `validate_media.py`.
