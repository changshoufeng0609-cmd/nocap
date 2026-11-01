#!/usr/bin/env python3
"""Transcribe local WAV files with an already-downloaded Whisper model (helper for select_voice_v2.py).

Reads {"model": name, "paths": [...]} as JSON on stdin and prints {"path": transcript} as JSON.
Run it inside a localhost-only sandbox: the model must already be in ~/.cache/whisper, and nothing is sent anywhere.
"""
import json
import sys

import whisper


def main():
    request = json.load(sys.stdin)
    model = whisper.load_model(request['model'], device='cpu')
    result = {}
    for path in request['paths']:
        output = model.transcribe(path, language='en', fp16=False, temperature=0.0,
                                  condition_on_previous_text=False, verbose=None)
        result[path] = output['text'].strip()
    json.dump(result, sys.stdout)


if __name__ == '__main__':
    main()
