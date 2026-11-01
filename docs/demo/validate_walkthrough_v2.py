#!/usr/bin/env python3
"""Verify a rendered v2.1 walkthrough or X cut offline: hashes, decode, loudness, silence, captions, provenance.

Reads OUT/<cut>/ as written by render_walkthrough_v2.py, writes OUT/<cut>/validation.json, a contact sheet and
full-size key frames for visual review. No model, SEC or network calls.

  python validate_walkthrough_v2.py data/eval_runs/demo-walkthrough-v21-20261031 --cut main
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_walkthrough_v2 import DURATION_LIMITS, OUTPUT  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# Key frames per cut: (shot, fraction of the shot); 'sam' is the middle of the SAM highlight overlay.
KEYS = {
    'main': [('cold', .75), ('title', .5), ('check', .5), ('tiles', .6), ('receipt', .8), ('sources', .6), ('eps', .85),
             ('fix', .85), ('fix-receipts', .6), ('cutoff-receipt', .85), ('slide', .2), ('sam', .5), ('slide', .95),
             ('slide-receipt', .6), ('ask-result', .6), ('ask-quotes', .6), ('backends', .6), ('private', .25), ('private', .85),
             ('results', .6), ('end', .6)],
    'x': [('cold', .1), ('cold', .75), ('tiles', .6), ('title-x', .5), ('receipt', .8), ('fix', .85), ('sam', .5), ('end-x', .6)],
}
SILENCE = {'noise_db': -50, 'min_s': 0.5}       # longest digital-silence run allowed between the fade-in and fade-out


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cues(path):
    pattern = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})\n([^\n]+)'
    result = []
    for match in re.finditer(pattern, Path(path).read_text()):
        f = [int(v) for v in match.groups()[:8]]
        result.append((round(f[0] * 3600 + f[1] * 60 + f[2] + f[3] / 1000, 3), round(f[4] * 3600 + f[5] * 60 + f[6] + f[7] / 1000, 3), match.group(9)))
    return result


def loudness(path):
    report = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                            capture_output=True, text=True).stderr
    summary = report[report.rfind('Summary:'):]
    return {'integrated_lufs': float(re.search(r'I:\s+(-?[\d.]+) LUFS', summary).group(1)),
            'loudness_range_lu': float(re.search(r'LRA:\s+(-?[\d.]+) LU', summary).group(1)),
            'true_peak_dbfs': float(re.search(r'Peak:\s+(-?[\d.]+) dBFS', summary).group(1))}


def silences(path):
    report = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af',
                             f"silencedetect=noise={SILENCE['noise_db']}dB:d={SILENCE['min_s']}", '-f', 'null', '-'],
                            capture_output=True, text=True).stderr
    starts = [float(v) for v in re.findall(r'silence_start: (-?[\d.]+)', report)]
    ends = [float(v) for v in re.findall(r'silence_end: (-?[\d.]+)', report)]
    return [(round(s, 2), round(e, 2)) for s, e in zip(starts, ends + [None] * (len(starts) - len(ends))) if e is not None]


def grab(video, seconds, path, width=None):
    command = ['ffmpeg', '-v', 'error', '-y', '-ss', f'{seconds:.3f}', '-i', str(video), '-frames:v', '1']
    if width:
        command += ['-vf', f'scale={width}:-1']
    subprocess.run(command + [str(path)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out', type=Path, help='the folder passed to render_walkthrough_v2.py --out')
    parser.add_argument('--cut', choices=('main', 'x'), default='main')
    parser.add_argument('--source', type=Path, default=ROOT / 'data/eval_runs/demo-walkthrough-v2-20261030/take2',
                        help='the capture folder the render read (master.mp4)')
    args = parser.parse_args()
    folder = args.out / args.cut
    manifest = json.loads((folder / 'manifest.json').read_text())
    name = OUTPUT[args.cut]
    video, srt, vtt = folder / f'{name}.mp4', folder / f'{name}.en.srt', folder / f'{name}.en.vtt'
    assert manifest['cut'] == args.cut and manifest['version'] == 'v2.1', (manifest['cut'], manifest['version'])
    assert sha(video) == manifest['video_sha256']
    assert sha(args.source / 'master.mp4') == manifest['master_sha256']
    if manifest.get('private_take'):
        assert sha(ROOT / manifest['private_take']['path'] / 'master.mp4') == manifest['private_take']['master_sha256']
        assert manifest['footage_commits']['private_take'] == manifest['private_take']['capture']['source_commit']
    assert manifest['footage_commits']['walkthrough_capture'] == manifest['capture']['source_commit']
    capture = manifest['capture']
    assert capture['provider_calls'] == 0 and capture['new_provider_spend_usd'] == 0
    assert not capture['blocked_requests'] and not capture['refused_requests'] and not capture['page_errors']
    posts = Counter(item['path'] for item in capture['api_requests'] if item['method'] == 'POST')
    assert posts == {'/api/check': 2, '/api/fix': 1, '/api/slide': 1, '/api/grounding': 1}, posts
    assert all(item.get('cached') for item in capture['api_requests'] if item['method'] == 'POST' and item['path'] != '/api/fix')
    assert manifest['new_provider_calls'] == 0 and manifest['new_provider_spend_usd'] == 0
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                'format=duration,size:stream=codec_type,codec_name,profile,width,height,r_frame_rate,pix_fmt,sample_rate,channels,color_space',
                                                '-of', 'json', str(video)], text=True))
    v = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    a = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
    assert (v['width'], v['height'], v['codec_name'], v['pix_fmt'], v['r_frame_rate']) == (1920, 1080, 'h264', 'yuv420p', '25/1'), v
    assert (a['codec_name'], a['sample_rate'], a['channels']) == ('aac', '48000', 2), a
    duration = float(probe['format']['duration'])
    low, high = DURATION_LIMITS[args.cut]
    assert low <= duration <= high + .05 and abs(duration - manifest['duration_s']) < .05, (duration, manifest['duration_s'])
    head = open(video, 'rb').read(4096)
    assert head.find(b'moov') != -1 and (head.find(b'mdat') == -1 or head.find(b'moov') < head.find(b'mdat')), 'moov atom is not at the front (+faststart)'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(video), '-f', 'null', '-'], check=True, capture_output=True)
    level = loudness(video)
    assert -15.0 <= level['integrated_lufs'] <= -13.0 and level['true_peak_dbfs'] <= -1.0, level
    music = manifest['audio']['music']
    assert music['final_chord'] == 'Dmaj9' and -30.0 <= music['level_in_export_lufs'] <= -20.0, (music['final_chord'], music['level_in_export_lufs'])
    # With the music bed there should be no dead air between the opening fade-in and the closing fade-out.
    quiet = silences(video)
    inner = [(s, e) for s, e in quiet if e > music['fade_in_s'] and s < duration - music['fade_out_s']]
    assert not inner, inner
    expected = [(c['start'], c['end'], c['caption']) for c in manifest['captions']]
    assert cues(srt) == cues(vtt) == expected
    segments = manifest['narration']['segments']
    assert len(expected) == len(segments) and [c['cue'] for c in manifest['captions']] == [s['cue'] for s in segments]
    assert ('c24' in {s['cue'] for s in segments}) == bool(manifest.get('private_take'))
    assert expected[0][0] >= 0 and expected[-1][1] <= duration + 1e-6
    assert all(expected[i][1] <= expected[i + 1][0] for i in range(len(expected) - 1)), 'overlapping captions'
    assert all(end > start for start, end, _ in expected)
    assert all(s['start_s'] + s['seconds'] <= duration for s in segments)
    speech_s = sum(s['seconds'] for s in segments)
    words = sum(len(c['caption'].split()) for c in manifest['captions'])
    # Contact sheet: one frame every 5 seconds (every 3 seconds for the X cut).
    review = folder / 'review'
    review.mkdir(exist_ok=True)
    step = 3 if args.cut == 'x' else 5
    thumbs = []
    for i, t in enumerate(range(1, int(duration), step)):
        path = review / f'thumb-{i:03}.png'
        grab(video, t, path, 384)
        thumbs.append((t, path))
    cols = 6
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * 384, rows * 216), 'black')
    for i, (t, path) in enumerate(thumbs):
        sheet.paste(Image.open(path).convert('RGB').resize((384, 216)), ((i % cols) * 384, (i // cols) * 216))
        path.unlink()
    sheet.save(folder / 'contact-sheet.jpg', quality=82)
    # Full-size key frames.
    shots = {shot['name']: shot for shot in manifest['timeline']}
    sam = next(o for o in manifest['edit_overlays'] if o['kind'].startswith('SAM region highlight'))
    shots['sam'] = {'start': sam['start_s'], 'end': sam['end_s']}
    keys = {}
    for name_, offset in KEYS[args.cut]:
        if name_ not in shots:
            assert name_ == 'private' and not manifest.get('private_take'), name_
            continue
        shot = shots[name_]
        t = shot['start'] + (shot['end'] - shot['start']) * offset
        path = review / f'key-{name_}-{int(offset * 100):02}.png'
        grab(video, t, path)
        keys[path.name] = round(t, 2)
    result = {
        'status': 'passed', 'version': manifest['version'], 'cut': args.cut,
        'video': video.name, 'video_sha256': sha(video), 'video_bytes': video.stat().st_size, 'duration_s': duration,
        'resolution': [1920, 1080], 'video_codec': f"h264 {v.get('profile', '')} yuv420p 25 fps".replace('  ', ' '), 'audio': 'aac 48 kHz stereo',
        'faststart': True, 'full_decode': 'passed', 'loudness': level,
        'music': {'final_chord': music['final_chord'], 'level_in_export_lufs': music['level_in_export_lufs'],
                  'undocked_level_in_mix_lufs': music['undocked_level_lufs'], 'pcm_sha256': music['pcm_sha256']},
        'silence': {'detector': f"silencedetect noise={SILENCE['noise_db']} dB, d={SILENCE['min_s']} s", 'runs': quiet,
                    'runs_between_fade_in_and_fade_out': 0},
        'caption_count': len(expected), 'caption_words': words, 'speech_s': round(speech_s, 2),
        'words_per_minute_of_runtime': round(words / duration * 60, 1),
        'caption_timing': 'SRT and VTT match the manifest; ordered, non-overlapping, within the runtime',
        'srt_sha256': sha(srt), 'vtt_sha256': sha(vtt), 'master_sha256': manifest['master_sha256'],
        'private_take': bool(manifest.get('private_take')),
        'api_posts': dict(posts), 'provider_calls_during_capture': 0, 'new_spend_during_capture_usd': 0,
        'browser_page_errors': 0, 'blocked_or_refused_requests': 0,
        'contact_sheet': f'contact-sheet.jpg (one frame every {step} seconds)', 'key_frames': keys,
        'human_listening_or_editorial_approval': False, 'published': False,
    }
    (folder / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
