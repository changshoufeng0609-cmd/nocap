#!/usr/bin/env python3
"""Caption and narrate continuous browser footage with local licensed Kokoro.

Product pixels come only from Playwright's recorded video. PNGs are limited to
the subtitle band below the product, never used as substitutes for interactions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import soundfile as sf

from render_demo import local_narrator, spoken_text

ROOT = Path(__file__).resolve().parents[2]
TTS = ROOT / 'data/eval_runs/narration-replacement-20261029'
DURATION = 165.0
CUES = [
    (0, 8, "Here's an NVIDIA earnings draft. Two figures look plausible. Let's check them before publishing."),
    (8, 18, "Muse Spark extracts the claims. Code checks the SEC evidence. Seven pass, two are wrong, and two remain unverifiable."),
    (18, 27, "Click eighteen percent. The draft says year over year, but eighteen percent is quarter-over-quarter growth."),
    (27, 35, "The receipt calculates the year-over-year change: about one hundred and six percent. We can inspect both filed values."),
    (35, 42, "Now click earnings per share. The draft labels two dollars and twenty-two cents as GAAP."),
    (42, 50, "That is the adjusted figure. The GAAP receipt says two dollars and forty-six cents, with the filing attached."),
    (50, 59, "Let's fix both. Muse Wonder proposes numeric edits; code constrains them and verifies the revised draft again."),
    (59, 66, "The original stays on the left. The rechecked version shows exactly which numbers changed."),
    (66, 78, "Each correction keeps its receipt. This is a recorded successful response, replayed through the actual interface, not a live speed test."),
    (78, 88, "All eleven claims are checked again. The two errors are resolved. Guidance still has question marks; a forecast is not a filed fact."),
    (88, 94, "Copy the revised draft with its correction receipts."),
    (94, 104, "A correct number can still arrive too late. Here is a roundup checked as of July twenty-ninth, sixteen hundred UTC."),
    (104, 110, "Microsoft's filing arrived later. These claims are marked not public yet."),
    (110, 121, "Switch to Now and check again. Those two numbers become verifiable. The cutoff follows EDGAR acceptance time."),
    (121, 133, "The same workflow handles a slide. Upload this seeded earnings table and select Check slide. SAM proposes regions; Spark reads the figures."),
    (133, 147, "Click a numbered reading to open its receipt. Both planted errors are caught. Here Spark supplied the region after SAM found none. The image is a claim, not evidence."),
    (147, 157, "Ask for one figure. Spark proposes the lookup; code builds a verified answer. Inspect its SEC footnotes."),
    (157, 165, "NoCap: eleven companies, SEC receipts. Built during this hackathon by Robert Chang. Explore the saved examples."),
]


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-6000:])
    return result.stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate', '-of', 'json', str(path)]))


def sync_offset(path):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-t', '8',
        '-vf', 'fps=25,scale=16:16', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1,16,16,3)
    magenta = ((frames[:,:,:,0]>240)&(frames[:,:,:,1]<15)&(frames[:,:,:,2]>240)).mean(axis=(1,2))>.95
    indices = np.flatnonzero(magenta)
    assert len(indices)>=5, 'Missing setup sync slate'
    return (int(indices[-1])+1)/25


def stamp(seconds, dot=False):
    ms = round(seconds * 1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}{"." if dot else ","}{ms%1000:03}'


def caption_image(text, path):
    image = Image.new('RGB', (1920, 144), '#142b22')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 36)
    small = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 18)
    lines, line = [], ''
    for word in text.split():
        candidate = f'{line} {word}'.strip()
        if draw.textlength(candidate, font=font) > 1740 and line:
            lines.append(line); line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    assert len(lines) <= 2, text
    y = 17 if len(lines) == 2 else 35
    for line in lines:
        draw.text(((1920-draw.textlength(line,font=font))/2, y), line, font=font, fill='white')
        y += 43
    draw.text((42, 116), 'Actual browser recording · Cached / recorded responses · Timing is not a benchmark', font=small, fill='#b4cbbf')
    draw.text((1485, 116), 'Local synthetic voice: Kokoro-82M', font=small, fill='#b4cbbf')
    image.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/eval_runs/demo-walkthrough-20261029/take3')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    out = args.source
    work = out / 'render'
    work.mkdir(parents=True, exist_ok=True)
    model, voices = TTS / 'kokoro-v1.0.onnx', TTS / 'voices-v1.0.bin'
    assert model.exists() and voices.exists()
    narrator = None
    sample_rate = 24000
    mix = np.zeros(round(DURATION * sample_rate), dtype=np.float32)
    segments, srt, vtt, concat = [], [], ['WEBVTT\n'], []
    for number, (start, end, text) in enumerate(CUES, 1):
        spoken = spoken_text(text)
        key = hashlib.sha256(spoken.encode()).hexdigest()[:12]
        speech = work / f'voice-{number:02}-{key}.wav'
        if not speech.exists():
            if narrator is None:
                narrator = local_narrator(model, voices)
            phonemes = narrator.tokenizer.phonemize(spoken, lang='en-us')
            samples, rate = narrator.create(phonemes, voice='af_heart', speed=1.0, lang='en-us', is_phonemes=True)
            sf.write(speech, samples, rate, subtype='PCM_16')
            (work / f'phonemes-{number:02}.txt').write_text(phonemes)
        raw, rate = sf.read(speech, dtype='float32')
        assert rate == sample_rate
        available = end - start - .35
        tempo = max(1, len(raw)/rate/available)
        assert tempo <= 1.23, {'cue':number, 'tempo':tempo, 'text':text}
        fitted = work / f'fitted-{number:02}.wav'
        run(['ffmpeg','-v','error','-y','-i',str(speech),'-af',f'atempo={tempo:.8f}', '-ar', str(sample_rate), str(fitted)])
        samples, rate = sf.read(fitted, dtype='float32')
        offset = round((start+.12)*sample_rate)
        mix[offset:offset+len(samples)] += samples
        frame = work / f'caption-{number:02}.png'
        caption_image(text, frame)
        concat.append(f"file '{frame.resolve().as_posix()}'\nduration {end-start}\n")
        srt.append(f'{number}\n{stamp(start)} --> {stamp(end)}\n{text}\n')
        vtt.append(f'{stamp(start,True)} --> {stamp(end,True)}\n{text}\n')
        segments.append({'start_s':start, 'end_s':end, 'caption':text, 'spoken_text':spoken, 'raw_duration_s':len(raw)/rate,
                         'tempo':tempo, 'speech_sha256':hashlib.sha256(speech.read_bytes()).hexdigest()})
        print(f'Narration {number}/{len(CUES)} prepared', flush=True)
    concat.append(f"file '{frame.resolve().as_posix()}'\n")
    (work/'captions.txt').write_text(''.join(concat))
    (out/'nocap-walkthrough.en.srt').write_text('\n'.join(srt))
    (out/'nocap-walkthrough.en.vtt').write_text('\n'.join(vtt))
    sf.write(work/'narration.wav', mix, sample_rate, subtype='PCM_16')
    narration = {'model':'hexgrad/Kokoro-82M v1.0','voice':'af_heart','runtime':'kokoro-onnx 0.6.1','execution':'local CPU',
                 'model_license':'Apache-2.0','runtime_license':'MIT','segments':segments,
                 'model_sha256':hashlib.sha256(model.read_bytes()).hexdigest(),'voices_sha256':hashlib.sha256(voices.read_bytes()).hexdigest(),
                 'new_provider_calls':0,'human_listening_approval':False}
    (out/'narration.json').write_text(json.dumps(narration,indent=2)+'\n')
    if args.prepare_only:
        return
    capture = json.loads((out/'capture.json').read_text())
    assert capture['status']=='captured' and not capture['page_errors'] and not capture['blocked_requests']
    raw_video = out/'walkthrough-raw.webm'
    raw_probe = probe(raw_video)
    # Chromium begins recording before page navigation. Trim only that setup lead-in.
    offset = sync_offset(raw_video) if capture.get('sync_marker') else max(0, float(raw_probe['format']['duration'])-capture['timeline_duration_s'])
    video = out/'nocap-walkthrough-review.mp4'
    run(['ffmpeg','-hide_banner','-v','error','-y','-ss',f'{offset:.5f}','-i',str(raw_video),
         '-f','concat','-safe','0','-i',str(work/'captions.txt'),'-i',str(work/'narration.wav'),
         '-filter_complex','[0:v]scale=1920:936:flags=lanczos,pad=1920:1080:0:0:color=0x142b22[ui];[ui][1:v]overlay=0:936:shortest=1[v];[2:a]loudnorm=I=-16:TP=-2:LRA=11,aresample=48000[a]',
         '-map','[v]','-map','[a]','-t',str(DURATION),'-r','25','-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p',
         '-c:a','aac','-b:a','160k','-movflags','+faststart',str(video)])
    output_probe=probe(video)
    assert float(output_probe['format']['duration']) < 180
    manifest={'format':'continuous Playwright browser recording, caption band, local synthetic narration',
              'video_probe':output_probe,'capture':capture,'narration':narration,'setup_trim_seconds':offset,
              'product_pixels':'Recorded browser video only; no screenshot/card substitution',
              'new_provider_calls':0,'new_provider_spend_usd':0,'published':False,
              'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'video':str(video),'duration':output_probe['format']['duration'],'bytes':output_probe['format']['size']}),flush=True)


if __name__=='__main__':
    main()
