# README GIF: 20 seconds of actual operation

The [current GIF](assets/receipts-demo.gif) is the continuous **8–28 second interval**
of the [165-second walkthrough](assets/receipts-demo.mp4), at original speed.
It is 960 × 540, 10 fps, muted and looping. There are no screenshot substitutions
or internal cuts.

| GIF time | Source-video time | Actual operation |
|---|---|---|
| 0–2 s | 8–10 s | Move to Check and click; the cached example is verified |
| 2–10 s | 10–18 s | Inspect 7 verified / 2 wrong / 2 unverifiable results |
| 10–13 s | 18–21 s | Click the 18% growth claim and open its receipt |
| 13–20 s | 21–28 s | Read the filed year-over-year growth comparison and source evidence |

The inherited captions and response-mode disclosure identify cached verification.
The loop is not a model-latency benchmark. Its first and final frames deliberately
jump at the loop boundary, without a fabricated transition or invented result.

`export_walkthrough.py` uses ffmpeg palette generation and dithering to export this
exact interval. `validate_media.py` checks its full decoding, duration, dimensions,
size and hash. The current file is 1,664,486 bytes; its source and SHA-256 are in
[the manifest](assets/manifest.json). The old 304,543-byte screenshot-sequence GIF
is historical and has been replaced.
