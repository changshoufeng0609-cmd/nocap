# Demo narration provenance

The current repository demo export uses **Kokoro-82M v1.0, `af_heart`**, synthesized locally on the CPU with
**kokoro-onnx 0.6.1**. It is a generic synthetic voice, not Robert Chang's recorded voice or a custom
voice clone. No cloud speech service, paid model endpoint or GPU instance is used for narration.

## Sources and permissions

- Model and voice: [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), whose official model card
  declares Apache-2.0 and describes production use. [`af_heart` is listed in its voice catalog](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).
- Runtime: [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), under the
  [MIT license](https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE). Its documentation separately
  identifies the model's Apache-2.0 license.
- Local assets: the maintainer's [`model-files-v1.1` release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)
  supplies `kokoro-v1.0.onnx` and `voices-v1.0.bin`. These are the English v1.0 model and voices, not a
  different v1.1 speech model. The model/voice binaries and installed packages are not redistributed in
  this repository. The published artifact is narration generated from this project's original script.

The official model card and runtime licenses provide the permission basis for this public demonstration.
No claim of endorsement by the model authors or exclusive rights to a synthetic voice is made. These
sources were inspected on 2026-10-29; the retained model card, license files and release metadata are local
under `data/eval_runs/narration-replacement-20261029/`.

| Asset | Published SHA-256, checked against downloaded bytes |
|---|---|
| `kokoro-v1.0.onnx` | `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a` |
| `voices-v1.0.bin` | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |

## Reproduction and review

Install the optional local media dependencies, outside the product's runtime requirements:

```bash
.venv/bin/pip install kokoro-onnx==0.6.1 soundfile==0.14.0
```

Download those two named assets from the linked release into the local directory above. Then run
`docs/demo/render_walkthrough.py` with the project's Python environment and original continuous capture.
The assets are read from the named local directory. The renderer performs
CPU inference with ONNX telemetry disabled; it never downloads weights or sends narration text to a server.

The current export contains **18 English cues over 165 seconds**, following actual browser actions.
Spoken-only substitutions separate
“No Cap”, pronounce GAAP as “gap”, and spell EPS, SEC and UTC by their letters. The manifest records each
caption, spoken text, source-audio hash and timing adjustment; the local speech cache retains generated
phonemes. Audio is normalized and fitted to the continuous recording's cue durations. This media verification is not a claim of
human listening or editorial approval.

## Historical narration replacement

The earlier local macOS Samantha narration was replaced before public distribution. Apple's
[macOS Tahoe license §2F](https://images.apple.com/legal/sla/docs/macOSTahoe.pdf) restricts System Voices to
personal, noncommercial use and separately restricts public sharing. Earlier media was preserved only
in the private repository history and a gitignored local backup; it is not distributed in current exports.

That first Kokoro replacement preserved all 29 captions and screenshot frames of the old 179.937663-second
montage. Its metadata remains in [the historical manifest](assets/historical/2026-10-29-screenshot-manifest.json)
and [historical validation](assets/historical/2026-10-29-screenshot-validation.json). The later interaction-led
export replaces the old narration script and picture sequence; it uses the same licensed model and voice,
with 18 newly timed speech segments. Neither replacement claims human listening or editorial approval.

The current asset hashes, decode/audio-level results and local public-file comparisons are recorded in
[`assets/validation.json`](assets/validation.json); per-segment TTS provenance is in
[`assets/manifest.json`](assets/manifest.json). Deployment acceptance is recorded separately from these local exports.
