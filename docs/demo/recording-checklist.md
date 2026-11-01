# Recording and evidence checklist

The current export is the **v2.1 walkthrough** (October 31) and its X cut. Its scenes, timings and sources are in the
[video script](video-script.md); how the footage was captured and edited is in the
[recording notes](walkthrough-script.md). No product code or provider response is modified to make the
recording appear successful.

## Local setup

Use a localhost-only sandbox profile for the API, so it cannot reach Meta, SEC or a GPU even by mistake.
Save this as `localhost-only.sb` outside the repository:

```scheme
(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
```

API on port 8106, from the committed snapshot, with live requests paused and draft caches off:

```bash
cd data/snapshot
env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=$HOME LANG=en_US.UTF-8 TMPDIR=$TMPDIR \
  RECEIPTS_DATA_DIR=$PWD RECEIPTS_LIVE_REQUESTS_ENABLED=false RECEIPTS_PERSIST_DRAFT_CACHES=false \
  sandbox-exec -f /path/to/localhost-only.sb ../../.venv/bin/uvicorn receipts.api:app \
  --app-dir ../.. --host 127.0.0.1 --port 8106 --no-proxy-headers
```

Frontend on port 3106, from the existing production build of `web/`:

```bash
cd web && NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next start --hostname 127.0.0.1 --port 3106
```

`GET http://127.0.0.1:8106/api/health` must report `"live_requests_paused": true` and
`"draft_cache_policy": "snapshot_only"`; the capture refuses to start otherwise.

Also required: Python Playwright with local Chrome, ffmpeg/ffprobe, the licensed local Kokoro files
([narration provenance](narration-license.md)), and a production build of `web/` (the renderer draws the
text cards with the product's own Geist and Newsreader fonts from `web/.next/static/media`).

## Commands

v2.1 re-edits the October 30 capture (made at commit `4bd6f4c`) and adds one short Private take recorded on
October 31 at commit `d6f7ebc`. The first capture command is therefore not repeated. For the Private take, the
API runs exactly as above plus `RECEIPTS_PRIVATE_MODE=false`, so `/api/private/status` reports Private mode
unavailable, as on the public deployment; the recorded sample still replays. The renderer only reads both
capture folders and refuses an `--out` inside the main one.

```bash
.venv/bin/python docs/demo/capture_walkthrough_v2.py --output data/eval_runs/demo-walkthrough-v2-20261030/take2
.venv/bin/python docs/demo/capture_walkthrough_v2.py --shot private --output data/eval_runs/demo-private-take-20261031
OUT=data/eval_runs/demo-walkthrough-v21-endcard-20261101
.venv/bin/python docs/demo/render_walkthrough_v2.py --out $OUT --cut main --private-take data/eval_runs/demo-private-take-20261031
.venv/bin/python docs/demo/validate_walkthrough_v2.py $OUT --cut main
.venv/bin/python docs/demo/render_walkthrough_v2.py --out $OUT --cut x
.venv/bin/python docs/demo/validate_walkthrough_v2.py $OUT --cut x
.venv/bin/python docs/demo/export_walkthrough_v2.py --source $OUT
.venv/bin/python docs/demo/validate_media_v2.py
```

`capture_walkthrough_v2.py --quick` is a short selector rehearsal without video. `render_walkthrough_v2.py
--plan-only` prints the edit plan, and `--cards-only` writes the text cards as PNG files for review. The main cut
includes the Private-check shot only with `--private-take <capture folder>`. The renderer reuses v2's
Kokoro files for unchanged lines (`--voice-cache`, default `<capture>/render`) and synthesizes only new text.
`.venv/bin/python docs/demo/music_bed.py 30 preview.flac` writes a 30-second preview of the music bed.
`select_voice_v2.py` reruns the voice comparison (it needs a local Whisper install with cached models).

## Evidence checked for this export

| Flow | Expected result | Source |
|---|---|---|
| NVIDIA Check | 7 verified, 2 wrong, 2 unverifiable, 0 approximate; 11 claims | Saved example, replayed by the real API |
| Growth receipt | 18% claimed; filed year-over-year growth 105.85%, with the calculation | Same response; deterministic receipt |
| EPS receipt | $2.22 claimed as GAAP; filed GAAP EPS $2.46; $2.22 is the non-GAAP figure | Same response |
| Fix | 2 corrections; 9 verified, 0 wrong, 2 unverifiable; 3.4 s, $0.0111 on the original run | [Recorded October 29 response](fix-live-2026-10-29.json), byte-identical |
| Time travel | As of 2026-07-29 16:00 UTC: 6 verified, 1 wrong, 2 not public yet; Microsoft revenue not public until 20:04:53 UTC; filed later $90.007B | Saved roundup example, replayed by the real API |
| Real SEC slide | 32 readings: 5 verified, 0 wrong, 27 unverifiable; 2 SAM and 2 Spark regions; original run 21 s, $0.0302, 3 model calls | Recorded October 30 live run, replayed by the real API |
| Whole-filing quotes | Meta 10-K, 516,028 characters, 101,135 input tokens, 4 exact quotes; original run 6.1 s, $0.13, 1 model call | Recorded October 30 live run, replayed by the real API |
| Backends card (v2.1, a text card) | Spark Open 27/27 gold claims on Fireworks and Baseten; 200 closed-book answers, 0 wrong numbers passed, 0–34% accepted | [Open-weights record](../open-weights.md), [closed-book Q&A](../../eval/qa_results.md) |
| Private check (v2.1 take) | "Recorded Private check · Muse Glimmer · 2 claims": $96.2 billion verified (10-Q $96.221 billion); GAAP diluted EPS $2.22 wrong (non-GAAP; GAAP $2.46); original run 26 s, 1 model call; $0 this replay | Live Modal Glimmer run of 2026-10-30 18:17 UTC in `data/snapshot/recordings/private/`, replayed by the real API; [Private mode](../private-mode.md) |

All drafts and documents are repository examples or public SEC filings. No unpublished participant text,
account page, terminal or secret appears in any frame.

## Export checks

- Full MP4 and GIF decode; 1920 × 1080 H.264 High (yuv420p, 25 fps) with AAC 48 kHz stereo; `+faststart`.
- Integrated loudness about −14 LUFS (accepted range −15 to −13) and true peak at or below −1 dBTP; the final
  mix pass must be linear.
- Music: the bed ends on its tonic (Dmaj9) and sits between −30 and −20 LUFS in the export; `silencedetect`
  (−50 dB, 0.5 s) finds no dead air between the fade-in and the fade-out.
- SRT and VTT cues match the manifest, in order, without overlap, inside the runtime; captions are burned in.
- Capture logs: 0 page errors, 0 blocked or refused requests, 0 provider calls, $0; every forwarded POST was a cached
  replay (the Private take forwarded exactly one, the recorded sample).
- Manifest hashes for the master, both cuts, captions, cards, narration segments and every demo script; v2's
  records are kept in `assets/historical/`.
- MP4 and VTT under `web/public/demo/` match the repository assets byte for byte.
- Contact sheet and full-size key frames reviewed for legibility and scene alignment.
- Human listening or editorial approval is not claimed; deployment acceptance is separate.

The v1 scripts (`capture_walkthrough.py`, `render_walkthrough.py`, `validate_walkthrough.py`,
`export_walkthrough.py`, `validate_media.py`) and the older screenshot tools remain only for reproducing
historical exports.
