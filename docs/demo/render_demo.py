#!/usr/bin/env python3
"""Build an edited screenshot demo with captions and disclosed local TTS.

Only the original presentation cards and subtitle bands are drawn; product
pixels are actual screenshots from capture_demo.py. Narration uses local Kokoro
ONNX inference with predownloaded weights; no network or paid model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = Path('/System/Library/Fonts/Supplemental')
BG, INK, MUTED, GREEN = '#f5f4ef', '#142721', '#52635c', '#1c5d45'
TTS_DIR = ROOT / 'data/eval_runs/narration-replacement-20261029'
VOICE = 'af_heart'


def spoken_text(caption):
    # Pronunciation-only substitutions; visible captions retain the original text.
    words = {'NoCap': 'No Cap', 'GAAP': 'gap', 'EPS': 'E P S', 'SEC': 'S E C', 'UTC': 'U T C'}
    return re.sub(r'\b(?:NoCap|GAAP|EPS|SEC|UTC)\b', lambda match: words[match.group()], caption)


def local_narrator(model_path, voices_path):
    import onnxruntime
    from kokoro_onnx import Kokoro

    onnxruntime.disable_telemetry_events()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 4
    session = onnxruntime.InferenceSession(str(model_path), sess_options=options,
                                           providers=['CPUExecutionProvider'])
    return Kokoro.from_session(session, str(voices_path))


def font(size, bold=False, serif=False):
    name = 'Georgia' if serif else 'Arial'
    return ImageFont.truetype(str(FONT_DIR / f'{name}{" Bold" if bold else ""}.ttf'), size)


def wrapped(draw, text, xy, width, size=40, color=INK, bold=False, leading=1.25):
    f = font(size, bold)
    lines, line = [], ''
    for word in text.split():
        candidate = f'{line} {word}'.strip()
        if draw.textlength(candidate, font=f) > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    for i, line in enumerate(lines):
        draw.text((xy[0], xy[1] + i * size * leading), line, font=f, fill=color)
    return len(lines) * size * leading


def paste_image(canvas, path, box, crop=None):
    image = Image.open(path).convert('RGB')
    if crop:
        image = image.crop(crop)
    scaled = ImageOps.contain(image, (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
    x = box[0] + ((box[2] - box[0]) - scaled.width) // 2
    y = box[1] + ((box[3] - box[1]) - scaled.height) // 2
    canvas.paste(scaled, (x, y))
    ImageDraw.Draw(canvas).rounded_rectangle((x - 1, y - 1, x + scaled.width, y + scaled.height), radius=2, outline='#d2d9d3', width=2)


def card(kind, title, caption, source, output, part=0):
    canvas = Image.new('RGB', (1920, 1080), BG)
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, 1920, 9), fill=GREEN)
    d.text((70, 36), 'NoCap', font=font(28, True), fill=GREEN)
    d.text((1190, 43), 'EDITED DEMO · RECORDED RESPONSES', font=font(21, True), fill=MUTED)
    d.text((70, 92), title, font=font(46, True), fill=INK)
    if kind == 'title':
        d.text((95, 250), 'Check the numbers.', font=font(80, True, True), fill=INK)
        d.text((95, 350), 'Keep the evidence.', font=font(80, True, True), fill=GREEN)
        wrapped(d, 'Financial drafts, checked against SEC filings before publishing.', (100, 485), 760, 37, MUTED)
        d.text((100, 660), 'Robert Chang · Team NoCap', font=font(34, True), fill=INK)
        d.text((100, 715), 'Meta Global AI Developer Hackathon', font=font(27), fill=MUTED)
        paste_image(canvas, source / '02-check.png', (1010, 175, 1855, 885), (170, 290, 1270, 1000))
    elif kind == 'check':
        paste_image(canvas, source / '02-check.png', (80, 166, 1840, 880), (164, 155, 1270, 1000))
    elif kind in ('growth', 'eps'):
        label = '18% → 105.85%' if kind == 'growth' else '$2.22 → $2.46'
        subtitle = 'The comparison period matters.' if kind == 'growth' else 'GAAP and adjusted EPS are different.'
        d.text((110, 250), label, font=font(80, True), fill=GREEN)
        wrapped(d, subtitle, (115, 375), 730, 44, INK, True)
        wrapped(d, 'Inspect the calculation, basis, accession and filing evidence before accepting a correction.', (115, 505), 700, 37, MUTED)
        name = '03-growth-receipt.png' if kind == 'growth' else '04-eps-receipt.png'
        crop = (0, 0, 368, 620) if kind == 'growth' else (0, 0, 368, 700)
        if kind == 'eps' and part > 0:
            crop = (0, 420, 368, 953)
        paste_image(canvas, source / name, (930, 170, 1810, 885), crop)
    elif kind == 'fix':
        crop = (0, 0, 1104, 740) if part == 0 else (0, 400, 1104, 1130)
        paste_image(canvas, source / '05-fix.png', (75, 170, 1845, 885), crop)
    elif kind == 'recheck':
        paste_image(canvas, source / '07-all-rechecked.png', (90, 170, 1830, 885))
    elif kind == 'cutoff':
        paste_image(canvas, source / '08-time-cutoff.png', (70, 167, 1850, 885), (164, 0, 1275, 900))
    elif kind == 'now':
        paste_image(canvas, source / '10-time-now.png', (70, 167, 1850, 885), (164, 110, 1275, 960))
    elif kind == 'slide':
        paste_image(canvas, source / '12-slide.png', (75, 180, 1145, 395), (180, 715, 868, 858))
        paste_image(canvas, source / '12-slide.png', (75, 405, 1145, 885), (180, 940, 860, 1364))
        crop = (904, 490, 1274, 850) if part < 2 else (904, 860, 1274, 1425)
        paste_image(canvas, source / '12-slide.png', (1180, 175, 1845, 885), crop)
    elif kind == 'scope':
        wrapped(d, '10 companies. 8 quarters.', (100, 240), 850, 66, INK, True)
        wrapped(d, 'AAPL  AMD  AMZN  BAC  GOOGL\nJPM  META  MSFT  NFLX  NVDA', (105, 380), 820, 35, MUTED)
        wrapped(d, 'A question mark is a boundary to investigate, not a green light.', (105, 550), 780, 43, GREEN, True)
        paste_image(canvas, source / '11-guidance.png', (1080, 180, 1810, 880))
    elif kind == 'architecture':
        cards = [('Muse Spark', 'Extract typed claims'), ('Code + SEC evidence', 'Compute every verdict'), ('Muse Wonder', 'Propose numeric edits'), ('Spark + code', 'Recheck the whole draft')]
        for i, (heading, body) in enumerate(cards):
            x, y = 105 + (i % 2) * 870, 210 + (i // 2) * 235
            d.rounded_rectangle((x, y, x + 805, y + 190), radius=18, fill='#ffffff', outline='#cbd7cf', width=2)
            d.text((x + 32, y + 32), heading, font=font(43, True), fill=GREEN)
            d.text((x + 32, y + 104), body, font=font(35), fill=INK)
        wrapped(d, 'Fireworks Muse Spark Open: one small live benchmark through the same verifier.', (110, 730), 1700, 35, MUTED)
    elif kind == 'metrics':
        d.text((100, 195), 'SEEDED DEVELOPMENT SET · 224 ITEMS', font=font(30, True), fill=MUTED)
        values = [('104 / 109', 'planted errors caught', '95.4%'), ('0 / 107', 'original items with false alarms', ''), ('51 / 250', 'true claims unverifiable', '20.4%')]
        for i, (value, label, pct) in enumerate(values):
            x = 100 + 590 * i
            d.rounded_rectangle((x, 285, x + 550, 700), radius=16, fill='white', outline='#d0d9d2', width=2)
            d.text((x + 30, 350), value, font=font(68, True), fill=GREEN)
            wrapped(d, label, (x + 30, 475), 480, 34, INK)
            d.text((x + 30, 610), pct, font=font(37, True), fill=MUTED)
        d.text((105, 770), 'Small implementation dev sets. Held-out split not evaluated.', font=font(34, True), fill=MUTED)
    elif kind == 'closing':
        d.text((105, 235), 'Numbers to review.', font=font(90, True, True), fill=INK)
        d.text((105, 365), 'Evidence to inspect.', font=font(90, True, True), fill=GREEN)
        d.text((110, 560), 'Robert Chang · Team NoCap', font=font(43, True), fill=INK)
        d.text((110, 635), 'Built during the hackathon · New implementation', font=font(32), fill=MUTED)
        d.text((110, 735), 'Research and fact-checking. Not investment advice.', font=font(35, True), fill=MUTED)
    else:
        raise ValueError(kind)
    d.rectangle((0, 916, 1920, 1080), fill='#142721')
    wrapped(d, caption, (78, 938), 1765, 39, '#ffffff', leading=1.16)
    d.text((80, 1044), 'Recorded responses · NoCap brand refresh · Timing is not a benchmark', font=font(19), fill='#b9cec1')
    d.text((1390, 1044), 'Synthetic narration: Kokoro-82M', font=font(19), fill='#b9cec1')
    canvas.save(output)


SCENES = [
    ('title', 15, 'Check the numbers. Keep the evidence.', [
        'A financial draft can sound convincing while comparing the wrong quarters or mixing accounting bases.',
        'I built NoCap after seeing unreliable numbers in an earlier portfolio-agent project.',
        'This is the verification layer I wanted before publishing.']),
    ('check', 20, 'One draft. Two planted errors.', [
        'Here is an NVIDIA earnings story with two planted errors.',
        'Muse Spark extracts the numeric claims. Deterministic code checks them against stored SEC filing evidence.',
        'Most numbers pass, two need correction, and the outlook stays unverifiable.']),
    ('growth', 12, 'Wrong comparison period', [
        'Eighteen percent is the quarter-over-quarter growth rate, but the draft says year over year.',
        'The receipt shows the comparison and the filing behind it.']),
    ('eps', 11, 'Wrong accounting basis', [
        'The EPS claim uses an adjusted number under a GAAP label.',
        'The filed GAAP figure is two dollars and forty-six cents.']),
    ('fix', 15, 'Fix with receipts · recorded live response', [
        'Fix with receipts asks Muse Wonder to propose numeric substitutions.',
        'Code restricts those edits, checks each candidate, then extracts and verifies the whole revised draft again.']),
    ('recheck', 12, 'Resolved errors. Visible uncertainty.', [
        'Here both original errors are resolved. The two guidance figures still have question marks.',
        'I can review the changes and copy the draft with its correction receipts.']),
    ('cutoff', 14, 'What had reached EDGAR at the cutoff?', [
        'The right number can still be unavailable at a historical cutoff.',
        'At sixteen hundred UTC on July twenty-ninth, Microsoft had not filed. NoCap separates those claims from numeric mistakes.']),
    ('now', 10, 'Latest filings: two more figures are verifiable', [
        'Switching to the latest filings makes those two figures verifiable.',
        'That cutoff describes EDGAR availability, not every possible public channel.']),
    ('scope', 20, 'Coverage and uncertainty', [
        'NoCap currently covers ten companies and eight quarters.',
        'A question mark means the evidence or interpretation is insufficient. Guidance remains a forecast.',
        'The snapshot is not a live feed of every SEC filing. A green number does not validate the wider story.']),
    ('architecture', 22, 'The model proposes. Code decides.', [
        'Spark extracts claims and helps map earnings tables. Wonder proposes corrections and generated adversarial drafts.',
        'The evidence store covers ten companies across eight quarters.',
        'We also validated Muse Spark Open through Fireworks, using the same extraction and verification interface.']),
    ('metrics', 20, 'Development results, with the boundaries visible', [
        'Our seeded development set caught one hundred and four of one hundred and nine planted errors.',
        'No false alarms across one hundred and seven original items. Twenty percent of true claims remained unverifiable.',
        'These small development sets guided implementation. They do not guarantee accuracy on arbitrary financial writing.']),
    ('closing', 9, 'NoCap · Financial fact-checking', [
        'NoCap gives an editor numbers to review and evidence to inspect.',
        'Built during this hackathon by Team NoCap. Research and fact-checking, not investment advice.']),
]


def run(command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration,size:stream=codec_type,codec_name,width,height', '-of', 'json', str(path)], text=True))


def timestamp(seconds):
    ms = round(seconds * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/eval_runs/demo_capture')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/demo/assets')
    parser.add_argument('--frames-only', action='store_true')
    parser.add_argument('--with-slide', action='store_true')
    parser.add_argument('--tts-model', type=Path, default=TTS_DIR / 'kokoro-v1.0.onnx')
    parser.add_argument('--tts-voices', type=Path, default=TTS_DIR / 'voices-v1.0.bin')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    work = args.source / 'render-kokoro'
    work.mkdir(parents=True, exist_ok=True)
    narrator = None
    narration = {'model': 'hexgrad/Kokoro-82M v1.0', 'voice': VOICE,
                 'model_license': 'Apache-2.0', 'runtime': 'kokoro-onnx', 'runtime_license': 'MIT',
                 'execution': 'local CPU', 'new_provider_calls': 0, 'segments': []}
    if not args.frames_only:
        import soundfile
        for path in (args.tts_model, args.tts_voices):
            assert path.is_file(), f'Download the local TTS assets first: {path}'
        narration.update(model_sha256=hashlib.sha256(args.tts_model.read_bytes()).hexdigest(),
                         voices_sha256=hashlib.sha256(args.tts_voices.read_bytes()).hexdigest(),
                         runtime_version=importlib.metadata.version('kokoro-onnx'),
                         onnxruntime_version=importlib.metadata.version('onnxruntime'),
                         phonemizer_version=importlib.metadata.version('phonemizer'),
                         soundfile_version=importlib.metadata.version('soundfile'),
                         model_source='https://huggingface.co/hexgrad/Kokoro-82M',
                         export_source='https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1')
        narrator = local_narrator(args.tts_model, args.tts_voices)
        narration['voice_tensor_sha256'] = hashlib.sha256(narrator.voices[VOICE].tobytes()).hexdigest()
    scenes = list(SCENES)
    if args.with_slide:
        assert (args.source / '12-slide.png').exists(), 'Capture the validated slide flow first'
        scenes[8] = ('slide', 20, 'Check a slide · printed values, independent evidence', [
            'Same seeded figures, refreshed NoCap branding. This replays the original image response.',
            'SAM returned no region here, so Spark supplied the region and read the values.',
            'Code caught both planted errors against SEC evidence. The image itself is not proof.'])
    frames, clips, srt = [], [], []
    elapsed, index = 0, 0
    for kind, seconds, title, captions in scenes:
        word_counts = [len(c.split()) for c in captions]
        allocated = 0
        for part, caption in enumerate(captions):
            index += 1
            count = round(seconds * 24 * word_counts[part] / sum(word_counts)) if part < len(captions) - 1 else seconds * 24 - allocated
            allocated += count
            duration = count / 24
            frame = work / f'frame-{index:02}.png'
            card(kind, title, caption, args.source, frame, part)
            frames.append({'kind': kind, 'frame': str(frame.relative_to(ROOT)),
                           'sha256': hashlib.sha256(frame.read_bytes()).hexdigest(),
                           'start_s': elapsed, 'duration_s': duration, 'caption': caption})
            srt.append(f'{index}\n{timestamp(elapsed)} --> {timestamp(elapsed + duration)}\n{caption}\n')
            elapsed += duration
            if args.frames_only:
                continue
            spoken = spoken_text(caption)
            phonemes = narrator.tokenizer.phonemize(spoken, lang='en-us')
            speech_key = hashlib.sha256(json.dumps({
                'text': spoken, 'voice': VOICE, 'model': narration['model_sha256'],
                'voices': narration['voices_sha256'], 'runtime': narration['runtime_version'],
                'speed': 1.0, 'lang': 'en-us', 'phonemes': phonemes,
            }, sort_keys=True).encode()).hexdigest()[:16]
            speech = work / f'kokoro-{VOICE}-{index:02}-{speech_key}.wav'
            if not speech.exists() or speech.stat().st_size < 8192:
                samples, sample_rate = narrator.create(phonemes, voice=VOICE, speed=1.0,
                                                       lang='en-us', is_phonemes=True)
                soundfile.write(str(speech), samples, sample_rate, subtype='PCM_16')
            speech_duration = float(probe(speech)['format']['duration'])
            tempo = max(1.0, speech_duration / max(0.5, duration - 0.25))
            narration['segments'].append({'caption': caption, 'spoken_text': spoken,
                'phonemes': phonemes, 'speech_sha256': hashlib.sha256(speech.read_bytes()).hexdigest(),
                'raw_duration_s': speech_duration, 'tempo': tempo})
            clip = work / f'clip-{index:02}.mp4'
            run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-loop', '1', '-i', str(frame), '-i', str(speech),
                 '-t', str(duration), '-af', f'atempo={tempo:.6f},loudnorm=I=-17:TP=-3:LRA=11,aresample=48000,alimiter=limit=0.65:level=0,apad', '-r', '24',
                 '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage', '-crf', '25', '-pix_fmt', 'yuv420p',
                 '-c:a', 'aac', '-b:a', '160k', '-ar', '48000', '-movflags', '+faststart', str(clip)])
            clips.append(clip)
            print(f'Encoded {index}: {kind}, {duration:.2f}s', flush=True)
    (args.output / 'receipts-demo.en.srt').write_text('\n'.join(srt))
    manifest = {'duration_planned_s': elapsed, 'source_capture': json.loads((args.source / 'capture-record.json').read_text()),
                'product_brand': 'NoCap',
                'render_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'format': 'edited actual UI screenshots; captions burned in',
                'voice': 'Kokoro-82M af_heart, synthetic narration', 'narration': narration,
                'new_model_spend_usd': 0,
                'slide_segment': args.with_slide, 'frames': frames}
    if args.with_slide:
        slide_response = ROOT / 'data/eval_runs/slide_live/nvda-draft-events.json'
        slide_capture = json.loads((args.source / 'slide-capture-record.json').read_text())
        display_hash = hashlib.sha256((ROOT / 'examples/slides/nvda-draft.png').read_bytes()).hexdigest()
        assert display_hash == slide_capture['branded_display_input_sha256']
        manifest['slide_source'] = {**slide_capture,
                                    'branded_display_input': 'examples/slides/nvda-draft.png',
                                    'branded_display_input_sha256': display_hash,
                                    'response_sha256': hashlib.sha256(slide_response.read_bytes()).hexdigest(),
                                    'screenshot_sha256': hashlib.sha256((args.source / '12-slide.png').read_bytes()).hexdigest(),
                                    'screenshot_health': '11,175 facts; refreshed from the actual evidence snapshot',
                                    'capture': 'actual NoCap UI with original-input response replay; 2 verified, 2 wrong',
                                    'region_source': 'SAM returned zero regions; Spark fallback region'}
    if not args.frames_only:
        concat = work / 'clips.txt'
        concat.write_text(''.join(f"file '{clip.as_posix()}'\n" for clip in clips))
        video = args.output / 'receipts-demo.mp4'
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat),
             '-t', '179.84', '-c', 'copy', '-movflags', '+faststart', str(video)])
        manifest['video_probe'] = probe(video)
        exported_duration = float(manifest['video_probe']['format']['duration'])
        if exported_duration < elapsed:
            # Keep the original 29 cue boundaries, including its bounded last cue.
            srt[-1] = srt[-1].replace(timestamp(elapsed), timestamp(min(exported_duration, 179.839)))
            (args.output / 'receipts-demo.en.srt').write_text('\n'.join(srt))
    gif_scenes = [('check', 3, 'One draft. Two planted errors.'),
                  ('growth', 3, 'Find the comparison that went wrong.'),
                  ('eps', 4, 'Trace the correction to its filing.'),
                  ('fix', 5, 'Propose. Constrain. Recheck.'),
                  ('cutoff', 5, 'Check what EDGAR knew at the cutoff.')]
    gif_list = []
    for number, (kind, duration, caption) in enumerate(gif_scenes):
        frame = work / f'gif-{number}.png'
        card(kind, 'NoCap · check → inspect → fix → time travel', caption, args.source, frame, 1 if kind == 'eps' else 0)
        # GIF has no audio: change the narration disclosure to a concise replay disclosure.
        gif_frame = Image.open(frame).convert('RGB')
        draw = ImageDraw.Draw(gif_frame)
        draw.rectangle((1380, 1038, 1920, 1080), fill='#142721')
        draw.text((1400, 1044), '20-second edited screenshot sequence', font=font(19), fill='#b9cec1')
        gif_frame.save(frame)
        gif_list.append(f"file '{frame.as_posix()}'\nduration {duration}\n")
    gif_list.append(f"file '{frame.as_posix()}'\n")
    concat = work / 'gif.txt'
    concat.write_text(''.join(gif_list))
    if not args.frames_only:
        gif = args.output / 'receipts-demo.gif'
        run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-t', '20',
             '-vf', 'fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a',
             '-loop', '0', str(gif)])
        manifest['gif_probe'] = probe(gif)
        assert gif.stat().st_size <= 5 * 1024 * 1024, 'GIF exceeds the requested 5 MiB size'
        manifest['asset_sha256'] = {name: hashlib.sha256((args.output / name).read_bytes()).hexdigest()
                                    for name in ('receipts-demo.mp4', 'receipts-demo.gif', 'receipts-demo.en.srt')}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Wrote {len(frames)} caption frames; planned duration {elapsed:.2f}s; $0 new model spend')


if __name__ == '__main__':
    main()
