#!/usr/bin/env python3
"""Copy a validated local walkthrough into repository assets; never deploy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT/'docs/demo/assets'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'data/eval_runs/demo-walkthrough-20261029/take3')
    args=parser.parse_args()
    source=args.source.resolve()
    validation=json.loads((source/'validation.json').read_text())
    assert validation['status']=='passed'
    assert sha(source/'nocap-walkthrough-review.mp4')==validation['video_sha256']
    history=ASSETS/'historical'
    history.mkdir(exist_ok=True)
    for name in ('manifest.json','validation.json'):
        old=history/f'2026-10-29-screenshot-{name}'
        if not old.exists():
            shutil.copy2(ASSETS/name,old)
    public=ROOT/'web/public/demo'
    for source_name,target_name in (
        ('nocap-walkthrough-review.mp4','receipts-demo.mp4'),
        ('nocap-walkthrough.en.srt','receipts-demo.en.srt'),
        ('nocap-walkthrough.en.vtt','receipts-demo.en.vtt')):
        shutil.copy2(source/source_name,ASSETS/target_name)
        if target_name.endswith(('.mp4','.vtt')):
            shutil.copy2(source/source_name,public/target_name)
    gif=ASSETS/'receipts-demo.gif'
    subprocess.run(['ffmpeg','-v','error','-y','-ss','8','-i',str(ASSETS/'receipts-demo.mp4'),'-t','20',
        '-vf','fps=10,scale=960:540:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a',
        '-loop','0',str(gif)],check=True)
    manifest=json.loads((source/'manifest.json').read_text())
    manifest.update({
        'schema_version':2,'product_brand':'NoCap','voice':'Kokoro-82M af_heart, synthetic narration',
        'source_take':str(source.relative_to(ROOT)),
        'release_state':'Repository export prepared; deployment acceptance recorded separately',
        'published':False,
        'asset_sha256':{name:sha(ASSETS/name) for name in ('receipts-demo.mp4','receipts-demo.gif','receipts-demo.en.srt','receipts-demo.en.vtt')},
        'script_sha256':{name:sha(ROOT/'docs/demo'/name) for name in ('capture_walkthrough.py','render_walkthrough.py','validate_walkthrough.py','export_walkthrough.py')},
        'gif':{'source':'receipts-demo.mp4','start_s':8,'end_s':28,'duration_s':20,'fps':10,'resolution':[960,540],
               'speed':1,'audio':False,'internal_cuts':False,'content':'Actual Check click followed by the growth receipt'},
        'source_validation':validation,
        'privacy_review':'Only repository synthetic examples and public SEC facts; no participant draft, secret or account page',
        'historical_exports':{
            'manifest':'historical/2026-10-29-screenshot-manifest.json',
            'validation':'historical/2026-10-29-screenshot-validation.json',
            'scope':'Superseded 179.937663-second screenshot montage with licensed Kokoro narration'},
    })
    fix_dir=ROOT/'data/eval_runs/demo-fix-current-20261029'
    live=json.loads((fix_dir/'result.json').read_text())
    events=json.loads((fix_dir/'events.json').read_text())
    result=next(value for kind,value in events if kind=='result')
    request=json.loads((ROOT/'data/eval_runs/demo-walkthrough-20261029/fix-request.json').read_text())
    request_digest=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    actual=next(row['input_sha256'] for row in manifest['capture']['api_requests'] if row['path']=='/api/fix')
    assert actual==request_digest
    summary={
        'scope':'One seeded NVIDIA draft; not a correction quality benchmark',
        'live_run':live,'browser_recording':'Exact response replay; not a live browser-to-provider test',
        'input':'examples/nvda_q2_fy2027.txt','input_text_sha256':hashlib.sha256(request['text'].encode()).hexdigest(),
        'context':request['context'],'as_of':request['as_of'],
        'normalized_request_sha256':request_digest,'browser_request_exact_match':True,
        'response_sha256':sha(fix_dir/'events.json'),'response_source':'data/eval_runs/demo-fix-current-20261029/events.json',
        'counts':result['counts'],'coverage':result['coverage'],'original_coverage':result['original_coverage'],
        'accepted_numeric_changes':[{'from':'18%','to':'105.9%'},{'from':'$2.22','to':'$2.46'}],
        'recording_provider_calls':0,'recording_new_provider_spend_usd':0,
        'privacy':'Synthetic repository example only; no unpublished participant draft or credentials',
    }
    (ROOT/'docs/demo/fix-live-2026-10-29.json').write_text(json.dumps(summary,indent=2)+'\n')
    manifest['fresh_fix_evidence']='../fix-live-2026-10-29.json'
    (ASSETS/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'assets':manifest['asset_sha256'],'gif_bytes':gif.stat().st_size},indent=2))


if __name__=='__main__':
    main()
