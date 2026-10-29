# README GIF: 20-second storyboard

**Purpose:** show the review loop in one glance. The [generated GIF](assets/receipts-demo.gif) is a 20-second edited sequence of actual browser screenshots, not a continuous live session or measured end-to-end timing.

Target: **20 seconds**, **960 px wide**, **10 fps**, muted, looping. Preserve readable figures and the selected receipt. Use a constant lower caption: **Edited demo · cached checks / recorded Fix response** when reusing the saved live Fix output. If all Fix footage is newly live, change that portion to **live Fix, wait shortened** only when accurate.

| Time | Picture | Short caption |
|---|---|---|
| 0–3 s | NVIDIA results with both planted errors highlighted and all verdict counts visible. | **One draft. Two planted errors.** |
| 3–6 s | Growth-rate receipt and the code-derived year-over-year calculation. | **Find the comparison that went wrong.** |
| 6–10 s | Open the `$2.22` GAAP EPS receipt. Show `$2.46`, basis and accession. | **Trace the correction to the filing.** |
| 10–15 s | Fix side-by-side result and the two retained `?` guidance claims. | **Propose. Constrain. Recheck.** |
| 15–20 s | June-quarter roundup at `2026-07-29 16:00 UTC`; select Microsoft's revenue and show its later acceptance time. | **Check what EDGAR knew at the cutoff.** |

The last shot may jump back to the first without a fade. Keep captions outside the claimed/filing values. Do not animate false counts or draw a checkmark over a `?` claim. The full video, rather than this loop, carries the detailed metric caveats and optional slide segment.

## Capture and export

Use Playwright's video capture or a normal screen recorder. The local machine already has `ffmpeg` and `ffprobe`; no download is required. Save intermediate recordings under a gitignored directory such as `data/eval_runs/demo_capture/`, and keep the final reviewable GIF under `docs/demo/`.

For the delivered screenshot sequence, run `capture_demo.py` and `render_demo.py` as described in the [demo README](README.md). The renderer exports a palette-optimized **960 × 540 GIF, 304,543 bytes**. The command below is an alternative for a future continuous video capture; it was not used to create a fictitious live recording.

After editing the exact 20-second sequence to `data/eval_runs/demo_capture/hero.webm`, this export command creates a palette for that sequence:

```bash
ffmpeg -y -i data/eval_runs/demo_capture/hero.webm -t 20 \
  -vf 'fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a' \
  -loop 0 docs/demo/receipts-demo.gif
ffprobe -v error -show_entries format=duration,size -of json docs/demo/receipts-demo.gif
```

Watch the exported file at its README display size. Confirm the basis, corrected EPS, question marks and cutoff remain legible. If it is too large, lower fps or crop unused whitespace before reducing text legibility. Update the main README placeholder only once the final GIF exists and has passed this review.
