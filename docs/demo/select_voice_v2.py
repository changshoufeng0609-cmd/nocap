#!/usr/bin/env python3
"""Compare local Kokoro voices for the v2 narration with an automatic intelligibility proxy.

Every candidate reads the same v2 cues from the existing licensed voices-v1.0.bin. A local Whisper model
transcribes each cue and the script reports the word error rate against the narration text. This is an
automated proxy for intelligibility, not a listening test and not a human preference.
Nothing leaves the machine: Kokoro runs on the CPU and Whisper runs inside a localhost-only sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from narration_v2 import CUES  # noqa: E402
from render_demo import local_narrator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TTS = ROOT / 'data/eval_runs/narration-replacement-20261029'
OUT = ROOT / 'data/eval_runs/demo-walkthrough-v2-20261030/voice-eval'
VOICES_SHA256 = 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'
CANDIDATES = ['am_michael', 'am_fenrir', 'am_puck']
REFERENCE = 'af_heart'       # the v1 voice, for comparison only
SANDBOX = '(version 1)\n(allow default)\n(deny network-outbound)\n(allow network-outbound (remote ip "localhost:*"))\n(allow network-outbound (remote unix-socket))\n'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def catalogue_grades():
    grades = {}
    for line in (TTS / 'Kokoro-VOICES.md').read_text().splitlines():
        cells = [cell.strip().strip('*').replace('\\_', '_') for cell in line.strip().strip('|').split('|')]
        if len(cells) == 6 and re.fullmatch(r'[a-z]{2}_[a-z]+', cells[0]):
            grades[cells[0]] = {'target_quality': cells[2] or None, 'training_duration': cells[3].strip('_ 🤏') or None,
                                'overall_grade': cells[4] or None}
    return grades


def canonical(text):
    text = re.sub(r'\b([a-z]) (?=[a-z]\b)', r'\1', text)          # "s e c" -> "sec", "g p u" -> "gpu"
    text = re.sub(r'\bno cap\b', 'nocap', text)
    text = re.sub(r'\bgap\b', 'gaap', text)
    text = re.sub(r'\b10 k\b', '10k', text)
    return text.split()


def edit_distance(ref, hyp):
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1]


def whisper(model, paths, texts, sandbox):
    request = json.dumps({'model': model, 'paths': [str(p) for p in paths]})
    output = subprocess.run(['sandbox-exec', '-f', str(sandbox), '/opt/anaconda3/bin/python', '-I',
                             str(Path(__file__).with_name('whisper_transcribe.py'))],
                            input=request, capture_output=True, text=True, check=True)
    transcripts = json.loads(output.stdout)
    normalizer = ('from whisper.normalizers import EnglishTextNormalizer;import json,sys;'
                  'n=EnglishTextNormalizer();print(json.dumps([n(t) for t in json.load(sys.stdin)]))')
    normalized = subprocess.run(['sandbox-exec', '-f', str(sandbox), '/opt/anaconda3/bin/python', '-I', '-c', normalizer],
                                input=json.dumps(texts + [transcripts[str(p)] for p in paths]),
                                capture_output=True, text=True, check=True)
    values = json.loads(normalized.stdout)
    return [transcripts[str(p)] for p in paths], values[:len(texts)], values[len(texts):]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', nargs='+', default=['small', 'medium'])
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/demo/voice-selection-v2.json')
    args = parser.parse_args()
    voices_path = TTS / 'voices-v1.0.bin'
    assert sha(voices_path) == VOICES_SHA256, 'voices-v1.0.bin differs from the licensed file'
    OUT.mkdir(parents=True, exist_ok=True)
    sandbox = OUT / 'localhost-only.sb'
    sandbox.write_text(SANDBOX)
    narrator = local_narrator(TTS / 'kokoro-v1.0.onnx', voices_path)
    grades = catalogue_grades()
    spoken = [text for _, _, _, text in CUES]
    report = {'method': 'Each voice reads the same v2 cues at speed 1.0. Local Whisper models transcribe each cue; '
                        'word error rate compares normalized transcripts with the narration text. '
                        'An automated intelligibility proxy, not a listening test.',
              'voices_sha256': VOICES_SHA256, 'model_sha256': sha(TTS / 'kokoro-v1.0.onnx'),
              'cue_count': len(CUES), 'reference_words': sum(len(t.split()) for t in spoken), 'voices': {}}
    paths_by_voice = {}
    for voice in CANDIDATES + [REFERENCE]:
        folder = OUT / voice
        folder.mkdir(exist_ok=True)
        paths, seconds = [], 0.0
        for cue_id, _, _, text in CUES:
            path = folder / f'{cue_id}-{hashlib.sha256(text.encode()).hexdigest()[:10]}.wav'
            if not path.exists():
                phonemes = narrator.tokenizer.phonemize(text, lang='en-us')
                samples, rate = narrator.create(phonemes, voice=voice, speed=1.0, lang='en-us', is_phonemes=True)
                sf.write(path, samples, rate, subtype='PCM_16')
            data, rate = sf.read(path, dtype='float32')
            seconds += len(data) / rate
            paths.append(path)
        paths_by_voice[voice] = paths
        report['voices'][voice] = {'catalogue': grades.get(voice), 'speech_seconds': round(seconds, 2),
                                   'words_per_minute': round(report['reference_words'] / seconds * 60, 1),
                                   'peak_dbfs': round(float(20 * np.log10(max(float(np.abs(sf.read(p, dtype='float32')[0]).max()) for p in paths))), 2),
                                   'role': 'candidate' if voice in CANDIDATES else 'v1 reference only'}
        print(voice, report['voices'][voice], flush=True)
    for model in args.models:
        for voice, paths in paths_by_voice.items():
            raw, refs, hyps = whisper(model, paths, spoken, sandbox)
            errors = words = 0
            misses = []
            for (cue_id, _, _, _), ref, hyp, text in zip(CUES, refs, hyps, raw):
                ref_tokens, hyp_tokens = canonical(ref), canonical(hyp)
                distance = edit_distance(ref_tokens, hyp_tokens)
                errors += distance
                words += len(ref_tokens)
                if distance:
                    misses.append({'cue': cue_id, 'errors': distance, 'heard': text})
            report['voices'][voice].setdefault('whisper', {})[model] = {
                'word_error_rate': round(errors / words, 4), 'word_errors': errors, 'reference_tokens': words,
                'cues_with_errors': misses}
            print(model, voice, round(errors / words, 4), flush=True)
    ranked = sorted(CANDIDATES, key=lambda v: [sum(report['voices'][v]['whisper'][m]['word_error_rate'] for m in args.models)]
                    + [report['voices'][v]['whisper'][m]['word_error_rate'] for m in reversed(args.models)])
    report['ranking_by_mean_wer'] = ranked
    report['human_listening_approval'] = False
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'ranking': ranked, 'output': str(args.output)}, indent=2))


if __name__ == '__main__':
    main()
