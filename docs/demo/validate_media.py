#!/usr/bin/env python3
"""Validate current repository demo exports offline, without models or browsers."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ASSETS=ROOT/'docs/demo/assets'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_type,codec_name,width,height','-of','json',str(path)],text=True))


def cues(path):
    pattern=r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})\n([^\n]+)'
    result=[]
    for match in re.finditer(pattern,path.read_text()):
        fields=[int(value) for value in match.groups()[:8]]
        times=[fields[i]*3600+fields[i+1]*60+fields[i+2]+fields[i+3]/1000 for i in (0,4)]
        result.append((*times,match.group(9)))
    return result


manifest=json.loads((ASSETS/'manifest.json').read_text())
assert manifest['schema_version']==2 and manifest['product_brand']=='NoCap'
assert manifest['product_pixels']=='Recorded browser video only; no screenshot/card substitution'
video,gif=ASSETS/'receipts-demo.mp4',ASSETS/'receipts-demo.gif'
vp,gp=probe(video),probe(gif)
duration=float(vp['format']['duration'])
assert duration==165 and duration<180
assert abs(float(gp['format']['duration'])-20)<.05
assert gif.stat().st_size<=5*1024*1024
vs=next(s for s in vp['streams'] if s['codec_type']=='video')
gs=next(s for s in gp['streams'] if s['codec_type']=='video')
assert (vs['width'],vs['height'],vs['codec_name'])==(1920,1080,'h264')
assert (gs['width'],gs['height'],gs['codec_name'])==(960,540,'gif')
assert any(s['codec_type']=='audio' and s['codec_name']=='aac' for s in vp['streams'])
for path in (video,gif):
    subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],check=True,capture_output=True)
levels=subprocess.run(['ffmpeg','-hide_banner','-i',str(video),'-vn','-af','volumedetect','-f','null','-'],check=True,capture_output=True,text=True).stderr
mean=float(re.search(r'mean_volume: (-?[\d.]+) dB',levels).group(1))
peak=float(re.search(r'max_volume: (-?[\d.]+) dB',levels).group(1))
assert -25<mean<-10 and peak<0
narration=manifest['narration']
assert narration['execution']=='local CPU' and narration['new_provider_calls']==0
assert narration['model_license']=='Apache-2.0' and narration['runtime_license']=='MIT'
expected=[(item['start_s'],item['end_s'],item['caption']) for item in narration['segments']]
assert len(expected)==18 and expected[0][0]==0 and expected[-1][1]==duration
assert all(start<end for start,end,_ in expected)
assert all(expected[i-1][1]==expected[i][0] for i in range(1,len(expected)))
assert cues(ASSETS/'receipts-demo.en.srt')==cues(ASSETS/'receipts-demo.en.vtt')==expected
assert 'NoCap' in (ASSETS/'receipts-demo.en.srt').read_text()
hashes={name:sha(ASSETS/name) for name in manifest['asset_sha256']}
assert hashes==manifest['asset_sha256']
for name,expected_hash in manifest['script_sha256'].items():
    assert sha(ROOT/'docs/demo'/name)==expected_hash
for source in manifest['capture']['sources'].values():
    path=ROOT/source['path']
    if path.exists():
        assert sha(path)==source['sha256']
assert sha(ROOT/'examples/slides/nvda-draft.png')==manifest['capture']['image_sha256']
public=ROOT/'web/public/demo'
for name in ('receipts-demo.mp4','receipts-demo.en.vtt'):
    assert sha(public/name)==hashes[name]
assert manifest['capture']['provider_calls']==0 and not manifest['capture']['page_errors']
assert not manifest['capture']['blocked_requests']
assert 0<manifest['source_validation']['ask_tab_visible_s']<=10
fix=json.loads((ROOT/'docs/demo/fix-live-2026-10-29.json').read_text())
assert fix['response_sha256']==manifest['capture']['sources']['fix']['sha256']
assert fix['browser_request_exact_match'] and fix['live_run']['cost_usd']==.01108
record={
    'status':'passed','video_probe':vp,'gif_probe':gp,'full_video_decode':'passed','full_gif_decode':'passed',
    'asset_sha256':hashes,'caption_cues':18,'caption_timing':'SRT/VTT texts and times match narration; contiguous within 165 seconds',
    'audio_mean_db':mean,'audio_peak_db':peak,'ask_tab_visible_s':manifest['source_validation']['ask_tab_visible_s'],
    'product_footage':'Continuous actual browser recording; no internal cuts or screenshot substitution',
    'gif':manifest['gif'],'new_recording_provider_calls':0,'new_recording_provider_spend_usd':0,
    'fresh_fix_source_cost_usd':fix['live_run']['cost_usd'],
    'narration':{key:value for key,value in narration.items() if key!='segments'},
    'public_media':{'repository_mp4_and_vtt_copies_match':True,'deployed_new_export_acceptance':'Not performed by this export; recorded separately after deployment'},
    'review_scope':'Automated decode/hash/caption/audio checks plus agent contact-sheet/key-frame review; no human listening or editorial approval claimed',
    'privacy_review':manifest['privacy_review'],
}
(ASSETS/'validation.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
