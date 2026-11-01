# Demo narration provenance

The current repository demo export (v2.1, October 31, and its X cut) uses **Kokoro-82M v1.0, voice `am_fenrir`**, an American
English male voice, synthesized locally on the CPU with **kokoro-onnx 0.6.1**. It is a generic synthetic
voice, not Robert Chang's recorded voice or a custom voice clone. No cloud speech service, paid model
endpoint or GPU instance is used for narration. Earlier exports used the same model and voice file with
the female voice `af_heart`.

## Sources and permissions

- Model and voices: [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), whose official model card
  declares Apache-2.0 and describes production use. Both [`am_fenrir` and `af_heart` are listed in its voice catalog](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).
- Runtime: [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), under the
  [MIT license](https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE). Its documentation separately
  identifies the model's Apache-2.0 license.
- Local assets: the maintainer's [`model-files-v1.1` release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1)
  supplies `kokoro-v1.0.onnx` and `voices-v1.0.bin`. These are the English v1.0 model and voices, not a
  different v1.1 speech model. The model/voice binaries and installed packages are not redistributed in
  this repository. The published artifact is narration generated from this project's original script.

The official model card and runtime licenses provide the permission basis for this public demonstration.
No claim of endorsement by the model authors or exclusive rights to a synthetic voice is made. These
sources were inspected on 2026-10-29; the retained model card, license files, voice catalog and release
metadata are local under `data/eval_runs/narration-replacement-20261029/`.

| Asset | Published SHA-256, checked against downloaded bytes |
|---|---|
| `kokoro-v1.0.onnx` | `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a` |
| `voices-v1.0.bin` | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |

The v2 renderer and the voice comparison both refuse to run if either hash differs. `am_fenrir` is read from
the same `voices-v1.0.bin` as `af_heart`, so the permission basis above is unchanged by the voice switch.

## Voice choice for v2 (2026-10-30)

The owner asked for a male voice from the same licensed file, preferably American, chosen for
intelligibility. Kokoro's voice catalog grades nine American male voices; three share the best grade,
**C+** (target quality B, hours of training data): `am_michael`, `am_fenrir` and `am_puck`. The other six
are D or lower, so they were not considered.

Nobody listened to the candidates for this export, so the choice uses an automatic intelligibility proxy.
[`select_voice_v2.py`](select_voice_v2.py) synthesizes all 27 v2 narration lines with each candidate at
speed 1.0, then two locally cached Whisper models (`small` and `medium`, run inside a localhost-only
sandbox) transcribe every line. Word error rate compares the normalized transcripts with the narration text
(346 tokens). `af_heart` was scored as a reference only.

| Voice | Catalog grade | Speech at speed 1.0 | WER, Whisper small | WER, Whisper medium | Mean |
|---|---|---|---|---|---|
| **`am_fenrir`** (chosen) | C+ | 141.1 s (155 wpm) | **5.2%** (18) | 6.9% (24) | **6.1%** |
| `am_michael` | C+ | 155.1 s (141 wpm) | 6.4% (22) | 6.6% (23) | 6.5% |
| `am_puck` | C+ | 141.2 s (155 wpm) | 9.0% (31) | 7.5% (26) | 8.2% |
| `af_heart` (v1, reference) | A | 139.9 s (157 wpm) | 6.1% (21) | 5.5% (19) | 5.8% |

`am_fenrir` has the lowest mean error rate and the lowest with Whisper small; `am_michael` is one error
better with Whisper medium, so the two are close. Two further reasons favor `am_fenrir`: `am_puck` peaks at
0 dBFS in its raw output, and `am_michael`'s slower delivery adds about 14 seconds of speech, which would
push this script past the three-minute limit. Most remaining errors are spelling differences rather than
mishearings ("July 29th 4pm UTC", "everyone" for "every one"); the rest are brand names run together, such
as "MuSpark" for "Muse Spark", which the captions spell out. The full transcripts and errors are in
[`voice-selection-v2.json`](voice-selection-v2.json).

This proxy measures how reliably a speech recognizer understands each voice. It says nothing about warmth,
naturalness or listener preference, and **no human listening approval is claimed**.

## Reproduction and review

Install the optional local media dependencies, outside the product's runtime requirements:

```bash
.venv/bin/pip install kokoro-onnx==0.6.1 soundfile==0.14.0
```

Download the two named assets from the linked release into the local directory above, then run
`docs/demo/render_walkthrough_v2.py` on a v2 capture ([checklist](recording-checklist.md)). The renderer
performs CPU inference with ONNX telemetry disabled; it never downloads weights or sends narration text to a
server. `select_voice_v2.py` additionally needs a local Whisper installation with the `small` and `medium`
models already cached.

The v2.1 export has **26 English cues over 171.88 seconds**; the X cut uses 9 of them over 56.28 seconds.
Twenty lines reuse v2's audio files unchanged and six were synthesized for v2.1 (Kokoro output varies by a
few samples between runs, so each file's hash is recorded). Spoken text differs from the captions only where
pronunciation needs help: figures are spelled out, and NoCap, GAAP, SEC, UTC, GPU, API, 10-K, SAM, Baseten
and "backends" are respelled; the respellings were checked against Kokoro's phonemes. The two lines added
last (c23, c24) were also transcribed by local Whisper small and medium, which heard them as written. Each line is trimmed of
leading and trailing near-silence and placed on the timeline. The narration is normalized as in v2 with a
two-pass `loudnorm` to −14 LUFS (its dynamic mode, because a linear gain would exceed the true-peak ceiling),
then mixed with the music bed below; the mix passes a lookahead limiter and a final linear `loudnorm` pass
(measured −14.0 LUFS integrated, −1.5 dBTP true peak). The [manifest](assets/manifest.json) records each
caption, spoken text, start time, duration and audio hash. This media verification is not a claim of human
listening or editorial approval.

## Music bed (v2.1)

v2.1 adds a quiet ambient bed under the narration. It is an **original work made for this project**:
[`music_bed.py`](music_bed.py) computes every sound with numpy from sine-wave wavetables and seeded noise (a
slow D-major pad, a soft bass on the chord roots, sparse bell tones and a synthetic room reverb). It uses no
samples, loops, presets, sound fonts, generative music models or downloads, so no third-party license applies.
For a given length and seed the output is bit-for-bit identical; the manifest records the seed and the PCM
hash.

The bed is band-limited to 70 Hz–2.4 kHz to stay out of the speech-presence range, ducked 7 dB under every
narration line, and set to −26 LUFS integrated in the pre-master mix (about −25.3 LUFS in the export after the
final loudness pass). The progression ends on the tonic. The renderer then catches the few peaks where speech
and music coincide with a lookahead limiter at −2.5 dBFS, so the final pass to −14 LUFS can be a plain linear
gain that never lifts the music in pauses. Nobody has listened to the mix yet; **no human listening approval
is claimed**.

## Historical narration

The 168.68-second v2 walkthrough (27 cues, October 30) used `am_fenrir` without music. The 165-second v1
walkthrough (18 cues) and the earlier 179.937663-second screenshot montage (29 cues) used `af_heart` from the
same files. Their records are in [`assets/historical/`](assets/historical/).

Before that, local macOS Samantha narration was replaced before public distribution. Apple's
[macOS Tahoe license §2F](https://images.apple.com/legal/sla/docs/macOSTahoe.pdf) restricts System Voices to
personal, noncommercial use and separately restricts public sharing. That media was preserved only in the
private repository history and a gitignored local backup; it is not distributed in current exports.
