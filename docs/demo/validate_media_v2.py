#!/usr/bin/env python3
"""Validate the current repository demo exports (v2.1 and its X cut) offline, without models, browsers or network."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_walkthrough_v2 import DURATION_LIMITS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'docs/demo/assets'
PUBLIC = ROOT / 'web/public/demo'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                               'format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels',
                                               '-of', 'json', str(path)], text=True))


def cues(path):
    pattern = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})\n([^\n]+)'
    result = []
    for match in re.finditer(pattern, path.read_text()):
        f = [int(v) for v in match.groups()[:8]]
        result.append((round(f[0] * 3600 + f[1] * 60 + f[2] + f[3] / 1000, 3), round(f[4] * 3600 + f[5] * 60 + f[6] + f[7] / 1000, 3), match.group(9)))
    return result


def loudness(path):
    report = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(path), '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                            capture_output=True, text=True).stderr
    summary = report[report.rfind('Summary:'):]
    return (float(re.search(r'I:\s+(-?[\d.]+) LUFS', summary).group(1)), float(re.search(r'Peak:\s+(-?[\d.]+) dBFS', summary).group(1)))


def check_video(path, captions, expected_duration, limits, srt, vtt, resolution=(1920, 1080)):
    vp = probe(path)
    duration = float(vp['format']['duration'])
    assert abs(duration - expected_duration) < 0.05 and limits[0] <= duration <= limits[1] + .05, duration
    vs = next(s for s in vp['streams'] if s['codec_type'] == 'video')
    assert (vs['width'], vs['height'], vs['codec_name']) == (*resolution, 'h264'), (vs['width'], vs['height'], vs['codec_name'])
    assert any(s['codec_type'] == 'audio' and s['codec_name'] == 'aac' for s in vp['streams'])
    head = open(path, 'rb').read(4096)
    assert head.find(b'moov') != -1 and (head.find(b'mdat') == -1 or head.find(b'moov') < head.find(b'mdat')), 'not +faststart'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'null', '-'], check=True, capture_output=True)
    lufs, peak = loudness(path)
    assert -15 <= lufs <= -13 and peak <= -1.0, (lufs, peak)
    expected = [(c['start'], c['end'], c['caption']) for c in captions]
    assert cues(srt) == cues(vtt) == expected
    return vp, lufs, peak, len(expected)


def main():
    manifest = json.loads((ASSETS / 'manifest.json').read_text())
    assert manifest['schema_version'] == 4 and manifest['product_brand'] == 'NoCap' and manifest['version'] == 'v2.1 walkthrough'
    video, gif = ASSETS / 'receipts-demo.mp4', ASSETS / 'receipts-demo.gif'
    vp, lufs, peak, caption_cues = check_video(video, manifest['captions'], manifest['duration_s'], DURATION_LIMITS['main'],
                                               ASSETS / 'receipts-demo.en.srt', ASSETS / 'receipts-demo.en.vtt')
    gp = probe(gif)
    gs = next(s for s in gp['streams'] if s['codec_type'] == 'video')
    assert (gs['width'], gs['height'], gs['codec_name']) == (960, 540, 'gif')
    assert abs(float(gp['format']['duration']) - 20) < .05 and gif.stat().st_size <= 5 * 1024 * 1024
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(gif), '-f', 'null', '-'], check=True, capture_output=True)
    assert manifest['audio']['music']['final_chord'] == 'Dmaj9'
    assert manifest['audio']['mix']['loudnorm_output']['normalization_type'] == 'linear'
    hashes = {name: sha(ASSETS / name) for name in manifest['asset_sha256']}
    assert hashes == manifest['asset_sha256']
    for name, expected_hash in manifest['script_sha256'].items():
        assert sha(ROOT / 'docs/demo' / name) == expected_hash, name
    for name in ('receipts-demo.mp4', 'receipts-demo.en.vtt'):
        assert sha(PUBLIC / name) == hashes[name] == manifest['public_copies'][name]
    capture = manifest['capture']
    assert capture['provider_calls'] == 0 and capture['new_provider_spend_usd'] == 0
    assert not capture['page_errors'] and not capture['blocked_requests'] and not capture['refused_requests']
    fix = json.loads((ROOT / 'docs/demo/fix-live-2026-10-29.json').read_text())
    assert fix['response_sha256'] == capture['fix_source']['sha256']
    voice = json.loads((ROOT / 'docs/demo/voice-selection-v2.json').read_text())
    assert manifest['narration']['voice'] == voice['ranking_by_mean_wer'][0]
    assert manifest['narration']['voices_sha256'] == voice['voices_sha256']
    for entry in manifest['historical_exports'].values():
        for key in ('manifest', 'validation'):
            assert (ASSETS / entry[key]).exists(), entry[key]
    assert {'v1_165s', 'v2_168s'} <= set(manifest['historical_exports'])
    x_record = None
    if manifest['x_cut']:
        x = manifest['x_cut']
        xp, x_lufs, x_peak, x_cues = check_video(ASSETS / 'nocap-x.mp4', x['captions'], x['duration_s'], DURATION_LIMITS['x'],
                                                 ASSETS / 'nocap-x.en.srt', ASSETS / 'nocap-x.en.vtt', resolution=(1080, 1920))
        assert x['audio']['music']['final_chord'] == 'Dmaj9' and x['audio']['mix']['loudnorm_output']['normalization_type'] == 'linear'
        x_record = {'video_probe': xp, 'caption_cues': x_cues, 'audio_integrated_lufs': x_lufs, 'audio_true_peak_dbfs': x_peak,
                    'full_video_decode': 'passed'}
    record = {
        'status': 'passed', 'version': manifest['version'], 'video_probe': vp, 'gif_probe': gp,
        'full_video_decode': 'passed', 'full_gif_decode': 'passed',
        'asset_sha256': hashes, 'caption_cues': caption_cues,
        'caption_timing': 'SRT/VTT texts and times match the manifest captions',
        'audio_integrated_lufs': lufs, 'audio_true_peak_dbfs': peak, 'gif': manifest['gif'],
        'music': {'final_chord': manifest['audio']['music']['final_chord'], 'level_in_export_lufs': manifest['audio']['music']['level_in_export_lufs'],
                  'license': manifest['music_license']},
        'x_cut': x_record,
        'narration': manifest['narration'], 'voice_selection': {'chosen': manifest['narration']['voice'],
                                                                 'ranking_by_mean_wer': voice['ranking_by_mean_wer']},
        'public_media': {'repository_mp4_and_vtt_copies_match': True,
                         'deployed_acceptance': 'Not performed by this export; a deployment must be checked separately'},
        'historical_exports': sorted(manifest['historical_exports']),
        'new_recording_provider_calls': 0, 'new_recording_provider_spend_usd': 0,
        'review_scope': 'Automated decode/hash/caption/loudness checks plus agent contact-sheet and key-frame review; '
                        'no human listening or editorial approval claimed',
        'privacy_review': manifest['privacy_review'],
    }
    (ASSETS / 'validation.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({key: record[key] for key in ('status', 'asset_sha256', 'caption_cues', 'audio_integrated_lufs', 'audio_true_peak_dbfs', 'x_cut')}, indent=2))


if __name__ == '__main__':
    main()
