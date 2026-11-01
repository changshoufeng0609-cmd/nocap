# README GIF: 20 seconds of the v2.1 walkthrough

The [current GIF](assets/receipts-demo.gif) is the continuous **16.56–36.56 second interval** of the
[v2.1 walkthrough](assets/receipts-demo.mp4), at original speed. It is 960 × 540, 10 fps, muted, looping,
and keeps the burned-in captions. It contains the edit's cuts and camera moves but no frame that is not in
the MP4.

| GIF time | Source-video time | What happens |
|---|---|---|
| 0–2.6 s | 16.56–19.2 s | The Draft tab with the saved NVIDIA story and the green "Live checks are open until 2026-11-17 16:00 UTC" notice; at 18.3 s the page scrolls to the draft and its Check button |
| 2.6–6.3 s | 19.2–22.86 s | The cursor moves to Check and clicks it at 21.86 s (GIF 5.3 s); the verdicts appear |
| 6.3–12.2 s | 22.86–28.78 s | Close-up of the verdict tiles: 7 verified, 2 wrong, 2 unverifiable, 0 approximate |
| 12.2–17.1 s | 28.78–33.66 s | The paragraph with "up 18% from a year ago", struck, and "✗ 105.85%" |
| 17.1–20 s | 33.66–36.56 s | The camera moves to the receipt: claimed 18%, filed 105.85%, and the calculation |

The loop's first and last frames jump at the loop boundary; no transition was added. The interval is not a
latency measurement: Check replays a saved example. In v2.1 the scene label sits in the top-right corner, so
the Check button stays visible while it is clicked.

`export_walkthrough_v2.py` cuts this interval with ffmpeg palette generation (`stats_mode=diff`) and
Sierra dithering. `validate_media_v2.py` checks full decoding, duration, dimensions, the 5 MiB limit and the
hash. The current file is 4,766,072 bytes, SHA-256
`c61698a43e3d655f021bca96367a78397af5dba24f9798717e18dafabd9a45c6`. It shows the same footage as the v2 GIF
(4,980,489 bytes, source 15.55–35.55 s), one second later in the video because the v2.1 cold open is longer.
The v1 GIF was 1,664,486 bytes (source 8–28 s of the 165-second walkthrough).
