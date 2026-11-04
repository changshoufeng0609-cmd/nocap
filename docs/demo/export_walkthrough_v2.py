#!/usr/bin/env python3
"""Copy a validated v2.1 walkthrough (and its X cut, once rendered and validated) into the repository and
public-player paths, and cut the README GIF.

Copies files only; it never deploys, publishes or submits. Before replacing the current files it keeps the
records of the export they belong to: v2's manifest and validation go to
assets/historical/2026-10-30-walkthrough-v2-*.json (the v2 MP4 itself stays in git history at commit 1300f84);
when the October 31 end-card update replaces the first v2.1 export, that export's records go to
assets/historical/2026-10-31-walkthrough-v21-*.json (both of its MP4s stay in git history at commit 121a6cb);
when the November 1 coverage-line update replaces the October 31 end-card export, its records go to
assets/historical/2026-10-31-walkthrough-v21-endcard-*.json (both of its MP4s stay in git history at commit 5d6f7ed);
when the November 2 results-card update replaces the November 1 walkthrough, its records go to
assets/historical/2026-11-01-walkthrough-v21-coverage-*.json (its MP4 stays in git history at commit 78847a2).
Re-running after the X cut is validated adds it without touching the history. A source folder with only the main cut
(the November 2 update leaves the X cut alone) keeps the current X cut and its record after checking their hashes.

  python export_walkthrough_v2.py --source data/eval_runs/demo-walkthrough-v21-results-20261102
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_walkthrough_v2 import OUTPUT  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'docs/demo/assets'
PUBLIC = ROOT / 'web/public/demo'
GIF_SECONDS = 20.0
SCRIPTS = ('narration_v2.py', 'music_bed.py', 'screencast_recorder.py', 'capture_walkthrough_v2.py', 'render_walkthrough_v2.py',
           'render_demo.py', 'validate_walkthrough_v2.py', 'export_walkthrough_v2.py', 'validate_media_v2.py', 'select_voice_v2.py',
           'whisper_transcribe.py')
V2_HISTORY = {'manifest': 'historical/2026-10-30-walkthrough-v2-manifest.json',
              'validation': 'historical/2026-10-30-walkthrough-v2-validation.json'}
X_ASSETS = ('nocap-x.mp4', 'nocap-x.en.srt', 'nocap-x.en.vtt')
# The first v2.1 export (commit 121a6cb), replaced on October 31 by the same cuts with an updated end card.
V21_HISTORY = {'manifest': 'historical/2026-10-31-walkthrough-v21-manifest.json',
               'validation': 'historical/2026-10-31-walkthrough-v21-validation.json'}
V21_FIRST = {'receipts-demo.mp4': '364b7773da0751b605f0707f2f29017c9e6f989e7b9ce2861c41dffad00e5011',
             'nocap-x.mp4': 'f001ecd0f910d1aa20c617bbe52650918f4d81da1c6ec9723045c66789a5ce0a'}
END_CARD_UPDATE = {
    'date': '2026-10-31',
    'change': ('The end card of both cuts ("end" and "end-x") gained one line, "Now covering 499 S&P 500 companies". '
               'Every other end-card line is kept; the spacing was rebalanced and no text size changed.'),
    'reason': ('The footage was recorded on October 30, when NoCap covered fewer companies. On October 31 the evidence grew '
               'to 499 of the 500 S&P 500 companies (index members that day; Exxon Mobil is not covered), so the line '
               'states current coverage.'),
    'approval': 'Owner-approved, for both cuts',
    'unchanged': ('Footage, edit timeline, narration, music, captions and every other card. Rendered with the same '
                  'pipeline: with the previous end card it reproduced both previous MP4s byte for byte.'),
    'previous_video_sha256': V21_FIRST,
    'comparison_with_previous': {
        'receipts-demo.mp4': {
            'end_card_starts_s': 160.254, 'first_frame_with_new_card': 4007,
            'decoded_frames_identical': '0-3985 (0.00-159.40 s)',
            'reencoded_before_card': ('3986-4006 (159.44-160.24 s): the keyframe group that contains the first card frame; '
                                      'x264 looks ahead into the new card. Same source pixels; PSNR >= 62.4 dB against the '
                                      'previous export, under 0.1% of samples off by more than 2 levels'),
            'audio': 'AAC packets and decoded PCM identical'},
        'nocap-x.mp4': {
            'end_card_starts_s': 45.072, 'first_frame_with_new_card': 1127,
            'decoded_frames_identical': '0-1083 (0.00-43.32 s)',
            'reencoded_before_card': ('1084-1126 (43.36-45.04 s): the keyframe group that contains the first card frame. '
                                      'Same source pixels; PSNR >= 56.9 dB, under 0.12% of samples off by more than 2 levels'),
            'audio': 'AAC packets and decoded PCM identical'},
        'captions_and_plan': 'SRT, VTT and the render plan (timeline and cue times) byte-identical in both cuts',
        'receipts-demo.gif': ('Unchanged: its 20 seconds (16.56-36.56 s) decode identically, so the export kept it rather than '
                              're-cutting it with a palette shifted by the new end card')},
}
# The October 31 end-card export (commit 5d6f7ed), replaced on November 1 by the same cuts with the coverage line updated.
V21_ENDCARD_HISTORY = {'manifest': 'historical/2026-10-31-walkthrough-v21-endcard-manifest.json',
                       'validation': 'historical/2026-10-31-walkthrough-v21-endcard-validation.json'}
V21_ENDCARD = {'receipts-demo.mp4': '3dd8818760b8ce605757f48cfe3fbde1df5db72fd9a2af7c56dc903b59768cbd',
               'nocap-x.mp4': 'e5e20d5d312701e11501b706da68694f0e57c2d21ae0d3d032cd94434aeed688'}
COVERAGE_UPDATE = {
    'date': '2026-11-01',
    'change': ('The coverage line on the end card of both cuts ("end" and "end-x") now reads "Now covering all 500 S&P 500 '
               'companies" instead of "Now covering 499 S&P 500 companies". Same position, size, weight and color; every other '
               'end-card line is kept.'),
    'reason': ('Exxon Mobil, the one S&P 500 company not covered on October 31, joined the evidence on November 1 (snapshot '
               'committed in e61df17: 500 companies), so NoCap covers all 500 index members of October 31.'),
    'approval': 'Owner-approved (November 1), for both cuts',
    'unchanged': ('Footage, edit timeline, narration, music, captions and every other card. Rendered with the same pipeline: '
                  'with the October 31 end card it reproduced both October 31 MP4s byte for byte.'),
    'previous_video_sha256': V21_ENDCARD,
    'comparison_with_previous': {
        'receipts-demo.mp4': {
            'end_card_starts_s': 160.254, 'first_frame_with_new_card': 4007,
            'decoded_frames_identical': '0-3985 (0.00-159.40 s)',
            'reencoded_before_card': ('3986-4006 (159.44-160.24 s): the keyframe group that contains the first card frame; '
                                      'x264 looks ahead into the new card. Same source pixels; PSNR >= 63.7 dB against the '
                                      'October 31 export, under 0.07% of samples off by more than 2 levels'),
            'audio': 'AAC packets and decoded PCM identical'},
        'nocap-x.mp4': {
            'end_card_starts_s': 45.072, 'first_frame_with_new_card': 1127,
            'decoded_frames_identical': '0-1083 (0.00-43.32 s)',
            'reencoded_before_card': ('1084-1126 (43.36-45.04 s): the keyframe group that contains the first card frame. '
                                      'Same source pixels; PSNR >= 57.9 dB, under 0.09% of samples off by more than 2 levels'),
            'audio': 'AAC packets and decoded PCM identical'},
        'captions_and_plan': 'SRT, VTT and the render plan (timeline and cue times) byte-identical in both cuts',
        'receipts-demo.gif': ('Unchanged: its 20 seconds (16.56-36.56 s) decode identically, so the export kept it rather than '
                              're-cutting it with a palette shifted by the new end card')},
}
# The November 1 coverage-line walkthrough (commit 78847a2), replaced on November 2 by the same walkthrough with the
# comprehensive-test results card. The X cut has no results card, so it stays as exported on November 1.
V21_COVERAGE_HISTORY = {'manifest': 'historical/2026-11-01-walkthrough-v21-coverage-manifest.json',
                        'validation': 'historical/2026-11-01-walkthrough-v21-coverage-validation.json'}
V21_COVERAGE = {'receipts-demo.mp4': '9d5bb223148b3cc80d19ed5985433d8c501cd855212ff2bb8667825d91274897'}
RESULTS_UPDATE = {
    'date': '2026-11-02',
    'change': ('The walkthrough\'s results card ("results") and its line (c25) now show the comprehensive test of the November 2 '
               'round-8 release: "1,000 frozen test items from 400 S&P 500 companies"; 306/360 planted errors caught (none marked '
               '✓; the other 54 came back ?); 736/738 times a ✓ was right; 17/1,298 true figures flagged ✗; the October 29 '
               'held-out results (44/48, 1/47, 20/20) as one secondary line; caveat: one frozen run of the November 2 release, '
               'machine-made labels, details in eval/spot500_methods.md. The new line, "Across 1,000 test items, NoCap caught 306 '
               'of 360 planted errors and never marked one verified." (8.236 s), replaced "On held-out tests, NoCap caught 44 of '
               '48 planted errors, with one false alarm in 47 clean sentences." (8.524 s).'),
    'reason': ('The comprehensive test of the November 2 round-8 release is the current headline accuracy result (README.md '
               'Results; eval/spot500_methods.md, "Results of the one live run (November 2, 2026, after round 8)"). The card '
               'had shown only the October 29 held-out test, which stays as one secondary line.'),
    'approval': 'Owner-approved (November 2), for the walkthrough; the X cut has no results card and is not re-exported',
    'narration': ('c25 synthesized on 2026-11-02 with the same local Kokoro-82M files (SHA-256 unchanged), voice am_fenrir, speed '
                  '1.0; local Whisper small and medium both transcribed it as written. Over its span it measures -13.3 LUFS in the '
                  'export (the old line -13.5; its neighbors -13.6 and -13.5).'),
    'unchanged': ('Footage, every shot boundary, the other 25 lines and their times, the other four cards, the music bed and the '
                  'runtime (171.88 s): the results slot keeps its October 31 length, so the end card and its line stay put. The '
                  'audio was rendered with --keep-audio-from: the November 1 loudness measurements, and the November 1 narration '
                  'outside 150.280-160.029 s. Before this change the pipeline reproduced the November 1 MP4 byte for byte here; '
                  'after it, the old line still gives the same plan and the same AAC packets.'),
    'previous_video_sha256': V21_COVERAGE,
    'comparison_with_previous': {
        'receipts-demo.mp4': {
            'results_card_s': [150.23, 160.654],
            'changed_by_design': 'frames 3756-4016 (150.24-160.64 s): the results card, its fades and caption 25',
            'decoded_frames_identical': '0-3735 (0.00-149.40 s) and 4036-4296 (161.44-171.84 s), with the same H.264 packets',
            'reencoded_same_pixels': ('3736-3755 (149.44-150.20 s), the keyframe group before the card: PSNR >= 57.9 dB, under '
                                      '0.21% of samples off by more than 2 levels; 4017-4035 (160.68-161.40 s) of the end card: '
                                      'PSNR >= 52.8 dB, under 1.5% of samples off by more than 2 levels'),
            'audio': ('AAC packets identical up to 150.72 s (7,065 of 8,058) and for the last 42 (0.9 s). Before encoding, the '
                      'mix is bit-identical outside 150.729-162.631 s: the new line, then the music duck settling after it (at '
                      'most 0.0035, -49 dBFS, after the card). From 150.72 s the encoder chose different packets for the '
                      'unchanged end-card audio (difference 35 dB below the signal); integrated -14.0 LUFS, true peak -1.5 dBTP'),
            'captions': 'cue 25 only: new text and end 159.566 s instead of 159.854 s; 26 cues'},
        'plan': 'shots identical; cue c25 lasts 8.236 s instead of 8.524 s',
        'nocap-x.mp4': 'not re-exported: the X cut has no results card',
        'receipts-demo.gif': 'unchanged: its 20 seconds (16.56-36.56 s) decode identically'},
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gif_frames(video, start):
    """Per-frame MD5s of the 20 seconds the GIF is cut from, decoded with the GIF command's own seek."""
    return subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{start:.2f}', '-i', str(video), '-t', f'{GIF_SECONDS:.2f}',
                           '-map', '0:v', '-f', 'framemd5', '-'], capture_output=True, text=True, check=True).stdout


def load_cut(source, cut, capture):
    folder = source / cut
    validation = json.loads((folder / 'validation.json').read_text())
    manifest = json.loads((folder / 'manifest.json').read_text())
    video = folder / f'{OUTPUT[cut]}.mp4'
    assert validation['status'] == 'passed' and validation['cut'] == cut == manifest['cut']
    assert sha(video) == validation['video_sha256'] == manifest['video_sha256']
    assert sha(capture / 'master.mp4') == manifest['master_sha256']
    return folder, video, manifest, validation


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/eval_runs/demo-walkthrough-v21-endcard-20261101',
                        help='the folder passed to render_walkthrough_v2.py --out')
    parser.add_argument('--capture', type=Path, default=ROOT / 'data/eval_runs/demo-walkthrough-v2-20261030/take2',
                        help='the browser capture both cuts were edited from')
    args = parser.parse_args()
    source, capture = args.source.resolve(), args.capture.resolve()
    folder, video, manifest, validation = load_cut(source, 'main', capture)
    x = load_cut(source, 'x', capture) if (source / 'x' / 'validation.json').exists() else None

    # Keep the records of the export being replaced.
    previous = json.loads((ASSETS / 'manifest.json').read_text())
    history = ASSETS / 'historical'
    if previous['schema_version'] == 3:
        assert previous['version'] == 'v2 walkthrough', previous['version']
        for name in ('manifest', 'validation'):
            target = ASSETS / V2_HISTORY[name]
            if not target.exists():
                shutil.copy2(ASSETS / f'{name}.json', target)
        historical = dict(previous['historical_exports'])
        historical['v2_168s'] = {**V2_HISTORY, 'video_sha256': previous['asset_sha256']['receipts-demo.mp4'],
                                 'duration_s': previous['duration_s'], 'commit': '1300f84'}
    else:
        assert previous['schema_version'] == 4, previous['schema_version']
        historical = dict(previous['historical_exports'])
        if previous['asset_sha256']['receipts-demo.mp4'] == V21_FIRST['receipts-demo.mp4'] != sha(video):
            # The end-card update replaces the first v2.1 export: keep its records, as for v2.
            assert previous['asset_sha256']['nocap-x.mp4'] == V21_FIRST['nocap-x.mp4'] and x, 'export both cuts together'
            for name in ('manifest', 'validation'):
                target = ASSETS / V21_HISTORY[name]
                if not target.exists():
                    shutil.copy2(ASSETS / f'{name}.json', target)
            historical['v21_172s'] = {**V21_HISTORY, 'video_sha256': V21_FIRST['receipts-demo.mp4'],
                                      'duration_s': previous['duration_s'], 'commit': '121a6cb'}
            historical['v21_x_56s'] = {**V21_HISTORY, 'video_sha256': V21_FIRST['nocap-x.mp4'],
                                       'duration_s': previous['x_cut']['duration_s'], 'commit': '121a6cb'}
        elif previous['asset_sha256']['receipts-demo.mp4'] == V21_ENDCARD['receipts-demo.mp4'] != sha(video):
            # The November 1 coverage-line update replaces the October 31 end-card export: keep its records too.
            assert previous['asset_sha256']['nocap-x.mp4'] == V21_ENDCARD['nocap-x.mp4'] and x, 'export both cuts together'
            for name in ('manifest', 'validation'):
                target = ASSETS / V21_ENDCARD_HISTORY[name]
                if not target.exists():
                    shutil.copy2(ASSETS / f'{name}.json', target)
            historical['v21_endcard_172s'] = {**V21_ENDCARD_HISTORY, 'video_sha256': V21_ENDCARD['receipts-demo.mp4'],
                                              'duration_s': previous['duration_s'], 'commit': '5d6f7ed'}
            historical['v21_endcard_x_56s'] = {**V21_ENDCARD_HISTORY, 'video_sha256': V21_ENDCARD['nocap-x.mp4'],
                                               'duration_s': previous['x_cut']['duration_s'], 'commit': '5d6f7ed'}
        elif previous['asset_sha256']['receipts-demo.mp4'] == V21_COVERAGE['receipts-demo.mp4'] != sha(video):
            # The November 2 results-card update replaces the November 1 walkthrough only; the X cut stays current.
            assert not x, 'the November 2 update re-exports the walkthrough only'
            for name in ('manifest', 'validation'):
                target = ASSETS / V21_COVERAGE_HISTORY[name]
                if not target.exists():
                    shutil.copy2(ASSETS / f'{name}.json', target)
            historical['v21_coverage_172s'] = {**V21_COVERAGE_HISTORY, 'video_sha256': V21_COVERAGE['receipts-demo.mp4'],
                                               'duration_s': previous['duration_s'], 'commit': '78847a2'}
    for entry in historical.values():
        for key in ('manifest', 'validation'):
            assert (ASSETS / entry[key]).exists(), entry[key]
    assert history.is_dir()

    # README GIF: 20 continuous seconds of the Check scene at original speed (click Check, tiles, receipt).
    shots = {shot['name']: shot for shot in manifest['timeline']}
    start = round(shots['check']['start'] + 1.0, 2)
    end = start + GIF_SECONDS
    assert end <= shots['sources']['end'], 'GIF must stay inside the Check scene'
    gif = ASSETS / 'receipts-demo.gif'
    # palettegen reads its input to the end of the file, so a re-cut also depends on later scenes (the October 31
    # end-card line shifts its palette). Keep the current GIF when its 20 seconds decode identically in the new MP4.
    keep_gif = (gif.exists() and sha(gif) == previous['asset_sha256'].get('receipts-demo.gif')
                and previous.get('gif', {}).get('start_s') == start
                and gif_frames(ASSETS / 'receipts-demo.mp4', start) == gif_frames(video, start))

    # Without a newly validated X cut, keep the current one and its record, after checking that the files still match it.
    kept_x = None
    if not x and previous.get('x_cut'):
        kept_x = previous['x_cut']
        for name in X_ASSETS:
            assert sha(ASSETS / name) == previous['asset_sha256'][name], name

    for source_name, target_name in ((video.name, 'receipts-demo.mp4'), (f'{OUTPUT["main"]}.en.srt', 'receipts-demo.en.srt'),
                                     (f'{OUTPUT["main"]}.en.vtt', 'receipts-demo.en.vtt')):
        shutil.copy2(folder / source_name, ASSETS / target_name)
        if target_name.endswith(('.mp4', '.vtt')):
            shutil.copy2(folder / source_name, PUBLIC / target_name)
    if x:
        x_folder = x[0]
        for suffix, target_name in zip(('.mp4', '.en.srt', '.en.vtt'), X_ASSETS):
            shutil.copy2(x_folder / f'{OUTPUT["x"]}{suffix}', ASSETS / target_name)
            if target_name.endswith(('.mp4', '.vtt')):
                shutil.copy2(x_folder / f'{OUTPUT["x"]}{suffix}', PUBLIC / target_name)
    if (source / 'render' / 'card-stakes.png').exists():
        shutil.copy2(source / 'render' / 'card-stakes.png', PUBLIC / 'poster.png')
    if (source / 'render' / 'card-title-x.png').exists():
        shutil.copy2(source / 'render' / 'card-title-x.png', PUBLIC / 'poster-x.png')

    if not keep_gif:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.2f}', '-i', str(ASSETS / 'receipts-demo.mp4'), '-t', f'{GIF_SECONDS:.2f}',
                        '-vf', 'fps=10,scale=960:540:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a',
                        '-loop', '0', str(gif)], check=True)
    covered = [shot['name'] for shot in manifest['timeline'] if shot['end'] > start and shot['start'] < end]

    names = ('receipts-demo.mp4', 'receipts-demo.gif', 'receipts-demo.en.srt', 'receipts-demo.en.vtt') + (X_ASSETS if x or kept_x else ())
    record = {
        'schema_version': 4, 'product_brand': 'NoCap', 'version': 'v2.1 walkthrough',
        'source_render': str(source.relative_to(ROOT)), 'capture_take': str(capture.relative_to(ROOT)),
        'release_state': 'Repository export prepared; not deployed, published or submitted',
        'published': False,
        'end_card_update': (COVERAGE_UPDATE if 'v21_endcard_172s' in historical
                            else END_CARD_UPDATE if 'v21_172s' in historical else None),
        'earlier_end_card_updates': [END_CARD_UPDATE] if 'v21_endcard_172s' in historical else [],
        'results_card_update': RESULTS_UPDATE if 'v21_coverage_172s' in historical else None,
        'format': manifest['format'], 'product_pixels': manifest['product_pixels'],
        'duration_s': manifest['duration_s'], 'video_probe': manifest['video_probe'],
        'asset_sha256': {name: sha(ASSETS / name) for name in names},
        'public_copies': {name: sha(PUBLIC / name) for name in ('receipts-demo.mp4', 'receipts-demo.en.vtt')},
        'script_sha256': {name: sha(ROOT / 'docs/demo' / name) for name in SCRIPTS},
        'gif': {'source': 'receipts-demo.mp4', 'start_s': start, 'end_s': round(end, 2), 'duration_s': GIF_SECONDS, 'fps': 10,
                'resolution': [960, 540], 'speed': 1, 'audio': False, 'shots': covered,
                'content': 'Check click, verdict tiles and the growth receipt, at original speed with burned-in captions'},
        'narration': {key: value for key, value in manifest['narration'].items() if key != 'segments'},
        'narration_segments': manifest['narration']['segments'],
        'audio': manifest['audio'],
        'captions': manifest['captions'], 'timeline': manifest['timeline'], 'edit_overlays': manifest['edit_overlays'],
        'footage_commits': manifest['footage_commits'], 'capture': manifest['capture'], 'private_take': manifest['private_take'],
        'cards_sha256': manifest['cards_sha256'], 'master_sha256': manifest['master_sha256'],
        'source_validation': validation,
        'x_cut': ({'files': list(X_ASSETS), 'duration_s': x[2]['duration_s'], 'video_probe': x[2]['video_probe'],
                   'narration_segments': x[2]['narration']['segments'], 'captions': x[2]['captions'], 'caption_font_px': x[2]['caption_font_px'],
                   'timeline': x[2]['timeline'], 'edit_overlays': x[2]['edit_overlays'], 'audio': x[2]['audio'],
                   'cards_sha256': x[2]['cards_sha256'], 'source_validation': x[3]} if x else kept_x),
        'fresh_fix_evidence': '../fix-live-2026-10-29.json',
        'voice_selection': '../voice-selection-v2.json',
        'music_license': 'Original work synthesized by music_bed.py for this project (see ../narration-license.md)',
        'privacy_review': 'Only repository examples, public SEC filings and recorded public-document runs; no participant draft, secret, account page or terminal',
        'historical_exports': historical,
        'new_provider_calls': 0, 'new_provider_spend_usd': 0,
    }
    (ASSETS / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'assets': record['asset_sha256'], 'gif_bytes': gif.stat().st_size, 'gif_kept': keep_gif, 'gif_interval': [start, round(end, 2)],
                      'x_cut': 're-exported' if x else 'kept' if kept_x else None, 'historical_exports': sorted(historical)}, indent=2))


if __name__ == '__main__':
    main()
