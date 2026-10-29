#!/usr/bin/env python3
"""Verify a local walkthrough artifact without API calls or publication."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    source = args.source
    manifest = json.loads((source/'manifest.json').read_text())
    capture = manifest['capture']
    video = source/'nocap-walkthrough-review.mp4'
    assert sha(video) == manifest['video_sha256']
    assert capture['status'] == 'captured'
    assert capture['provider_calls'] == capture['new_provider_spend_usd'] == 0
    assert not capture['blocked_requests'] and not capture['page_errors']
    assert Counter(item['path'] for item in capture['api_requests']) == {
        '/api/check':3, '/api/fix':1, '/api/slide':1, '/api/ask':1}
    streams = manifest['video_probe']['streams']
    video_stream = next(stream for stream in streams if stream['codec_type']=='video')
    assert (video_stream['width'],video_stream['height'],video_stream['codec_name']) == (1920,1080,'h264')
    assert next(stream for stream in streams if stream['codec_type']=='audio')['codec_name']=='aac'
    duration = float(manifest['video_probe']['format']['duration'])
    assert 0 < duration < 180
    segments = manifest['narration']['segments']
    assert segments[0]['start_s']==0 and segments[-1]['end_s']==duration
    for index, segment in enumerate(segments):
        assert segment['start_s'] < segment['end_s']
        if index:
            assert segments[index-1]['end_s']==segment['start_s']
    def parsed_cues(path):
        pattern = r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})\n([^\n]+)'
        result=[]
        for match in re.finditer(pattern,path.read_text()):
            fields=[int(value) for value in match.groups()[:8]]
            start=fields[0]*3600+fields[1]*60+fields[2]+fields[3]/1000
            end=fields[4]*3600+fields[5]*60+fields[6]+fields[7]/1000
            result.append((start,end,match.group(9)))
        return result
    expected=[(segment['start_s'],segment['end_s'],segment['caption']) for segment in segments]
    assert parsed_cues(source/'nocap-walkthrough.en.srt')==expected
    assert parsed_cues(source/'nocap-walkthrough.en.vtt')==expected
    asks = [change for change in capture['mode_changes'] if change['mode']=='Ask']
    assert len(asks)==1
    ask_start=asks[0]['time_s']
    ask_end=next(change['time_s'] for change in capture['mode_changes'] if change['time_s']>ask_start)
    assert 0 < ask_end-ask_start <= 10, ask_end-ask_start
    subprocess.run(['ffmpeg','-v','error','-i',str(video),'-f','null','-'],check=True,capture_output=True)
    levels = subprocess.run(['ffmpeg','-hide_banner','-i',str(video),'-af','volumedetect','-vn','-sn','-f','null','-'],check=True,capture_output=True,text=True).stderr
    mean = float(re.search(r'mean_volume: (-?[\d.]+) dB', levels).group(1))
    peak = float(re.search(r'max_volume: (-?[\d.]+) dB', levels).group(1))
    assert -25 < mean < -10 and peak < 0
    result = {
        'status':'passed','video_sha256':sha(video),'video_bytes':video.stat().st_size,
        'duration_s':duration,'resolution':[1920,1080],'full_decode':'passed',
        'caption_count':len(segments),'caption_bounds':'passed',
        'srt_sha256':sha(source/'nocap-walkthrough.en.srt'),
        'vtt_sha256':sha(source/'nocap-walkthrough.en.vtt'),
        'ask_tab_visible_s':round(ask_end-ask_start,3),
        'mean_volume_db':mean,'peak_volume_db':peak,
        'provider_calls_during_capture':0,'new_spend_during_capture_usd':0,
        'browser_page_errors':0,'unapproved_or_external_browser_requests':0,
        'product_footage':'continuous browser recording; no internal cuts',
        'human_listening_or_editorial_approval':False,'published':False,
    }
    (source/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
