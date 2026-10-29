#!/usr/bin/env python3
"""Validate the actual exported demo media, without a browser or network."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'docs/demo/assets'


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration,size:stream=codec_type,codec_name,width,height', '-of', 'json', str(path)], text=True))


def seconds(text):
    h, m, s, ms = map(int, re.split('[:,]', text))
    return h * 3600 + m * 60 + s + ms / 1000


video = ASSETS / 'receipts-demo.mp4'
gif = ASSETS / 'receipts-demo.gif'
vp, gp = probe(video), probe(gif)
duration = float(vp['format']['duration'])
assert 178 <= duration <= 180, duration
assert abs(float(gp['format']['duration']) - 20) < 0.05
assert gif.stat().st_size <= 5 * 1024 * 1024
gs = next(s for s in gp['streams'] if s['codec_type'] == 'video')
assert (gs['width'], gs['height'], gs['codec_name']) == (960, 540, 'gif')
vs = next(s for s in vp['streams'] if s['codec_type'] == 'video')
assert (vs['width'], vs['height'], vs['codec_name']) == (1920, 1080, 'h264')
assert any(s['codec_type'] == 'audio' and s['codec_name'] == 'aac' for s in vp['streams'])
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(video), '-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(gif), '-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
analysis = subprocess.run(['ffmpeg', '-hide_banner', '-i', str(video), '-vn', '-af', 'volumedetect', '-f', 'null', '-'], check=True, capture_output=True, text=True).stderr
mean = float(re.search(r'mean_volume: (-?[\d.]+) dB', analysis).group(1))
peak = float(re.search(r'max_volume: (-?[\d.]+) dB', analysis).group(1))
assert -35 < mean < 0 and -20 < peak < -0.1, (mean, peak)
captions = (ASSETS / 'receipts-demo.en.srt').read_text()
assert 'NoCap' in captions and 'Receipts' not in captions
cues = re.findall(r'(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})', captions)
assert cues
last = 0
for start, end in cues:
    a, b = seconds(start), seconds(end)
    assert a >= last - 0.001 and a < b <= duration + 0.001, (a, b, duration)
    last = b
fixture = ROOT / 'examples/slides/nvda-draft.png'
assert Image.open(fixture).size == (1440, 900)
assert fixture.stat().st_size < 2 * 1024 * 1024
record = {'video_probe': vp, 'gif_probe': gp, 'full_video_decode': 'passed', 'full_gif_decode': 'passed',
          'product_brand': 'NoCap', 'caption_brand_check': 'NoCap present; old product name absent',
          'audio_mean_db': mean, 'audio_peak_db': peak, 'caption_cues': len(cues),
          'caption_timing': 'ordered, nonoverlapping, within exported duration',
          'fixture': {'dimensions': [1440, 900], 'bytes': fixture.stat().st_size},
          'new_model_spend_usd': 0,
          'review_scope': 'automated media checks; separate visual frame review documented in README; no human editorial approval claimed'}
record['asset_sha256'] = {name: hashlib.sha256((ASSETS / name).read_bytes()).hexdigest()
                          for name in ('receipts-demo.mp4', 'receipts-demo.gif', 'receipts-demo.en.srt')}
manifest = json.loads((ASSETS / 'manifest.json').read_text())
assert manifest['product_brand'] == 'NoCap'
assert manifest['voice'] == 'Kokoro-82M af_heart, synthetic narration'
narration = manifest['narration']
assert narration['execution'] == 'local CPU' and narration['new_provider_calls'] == 0
assert narration['model_license'] == 'Apache-2.0' and narration['runtime_license'] == 'MIT'
assert len(narration['segments']) == len(cues) == 29
caption_texts = ['\n'.join(block.splitlines()[2:]) for block in captions.strip().split('\n\n')]
assert [segment['caption'] for segment in narration['segments']] == caption_texts
assert all(segment['raw_duration_s'] > 0 and segment['tempo'] >= 1 for segment in narration['segments'])
record['narration'] = {key: value for key, value in narration.items() if key != 'segments'}
record['narration']['segments_checked'] = len(narration['segments'])
record['narration']['pronunciation_check'] = 'NoCap -> No Cap; GAAP -> gap; EPS/SEC/UTC -> letter names; phonemes retained per segment'
record['narration']['listening_review'] = 'Not a human listening or editorial approval'
assert hashlib.sha256((ROOT / 'docs/demo/render_demo.py').read_bytes()).hexdigest() == manifest['render_script_sha256']
assert record['asset_sha256'] == manifest['asset_sha256']
assert hashlib.sha256((ROOT / 'docs/demo/capture_demo.py').read_bytes()).hexdigest() == manifest['source_capture']['capture_script_sha256']
for name, expected_hash in manifest['source_capture']['screenshot_sha256'].items():
    path = ROOT / 'data/eval_runs/demo_capture' / f'{name}.png'
    if path.exists():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
for frame in manifest['frames']:
    path = ROOT / frame['frame']
    if path.exists():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == frame['sha256']
if manifest.get('slide_segment'):
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == manifest['slide_source']['branded_display_input_sha256']
    assert manifest['slide_source']['original_live_input_sha256'] != manifest['slide_source']['branded_display_input_sha256']
    assert manifest['slide_source']['new_branded_input_live_checked'] is False
    provenance = json.loads((ROOT / 'docs/demo/slide-replay-provenance.json').read_text())
    for key, value in provenance.items():
        assert manifest['slide_source'][key] == value
    screenshot = ROOT / 'data/eval_runs/demo_capture/12-slide.png'
    if screenshot.exists():
        assert hashlib.sha256(screenshot.read_bytes()).hexdigest() == manifest['slide_source']['screenshot_sha256']
    record['slide_source'] = manifest['slide_source']
public = ROOT / 'web/public/demo'
assert hashlib.sha256((public / video.name).read_bytes()).hexdigest() == record['asset_sha256'][video.name]
vtt = (public / 'receipts-demo.en.vtt').read_text()
assert vtt == 'WEBVTT\n\n' + re.sub(r'(\d\d:\d\d:\d\d),(\d{3})', r'\1.\2', captions)
record['public_media'] = {'video_matches': True, 'caption_text_and_timing_match': True,
                          'vtt_sha256': hashlib.sha256(vtt.encode()).hexdigest()}
(ASSETS / 'validation.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
