#!/usr/bin/env python3
"""Record actual NoCap browser interactions; all inference is cached/recorded.

Start the existing production Next app separately. This script intercepts every
API request, derives Check/GET responses from the real offline API, and permits
only exact historical Fix/Slide/Ask inputs. No provider or remote network calls.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sse(events):
    return ''.join(f'event: {kind}\ndata: {json.dumps(data)}\n\n' for kind, data in events)


def fixtures(fix_response=None):
    from fastapi.testclient import TestClient
    from receipts.api import Settings, create_app

    def forbidden(*args, **kwargs):
        raise AssertionError('Provider calls forbidden in walkthrough capture')

    snap = ROOT / 'data/snapshot'
    settings = replace(Settings.from_env({}), data_dir=snap, api_cache_dir=None,
                       spark_cache_dir=None, spark_read_dirs=(snap / 'spark_cache',),
                       prebaked_dirs=(snap / 'api_cache',), daily_budget_usd=0,
                       rate_limit_checks=0, live_requests_enabled=True,
                       spend_database_url=None, require_shared_budget=False)
    app = create_app(settings, client_factory=forbidden, wonder_client_factory=forbidden,
                     slide_client_factory=forbidden)
    reads, checks = {}, {}
    with TestClient(app) as client:
        for path in ('health', 'examples', 'ask/options', 'grounding/filings', 'private/status'):
            response = client.get('/api/' + path)
            assert response.status_code == 200
            reads['/api/' + path] = response.json()
        assert reads['/api/health']['facts'] == 11554
        assert len(reads['/api/health']['companies']) == 11
        examples = {item['id']: item for item in reads['/api/examples']}
        for name, cutoff in [('nvda-ai-story', None), ('q2-roundup-look-ahead', '2026-07-29T16:00:00Z'), ('q2-roundup-look-ahead', None)]:
            item = examples[name]
            body = {'text': item['text'], 'context': item['context'], 'as_of': cutoff, 'example_id': name}
            response = client.post('/api/check', json=body)
            assert response.status_code == 200 and 'event: done' in response.text and 'event: error' not in response.text
            checks[(item['text'], cutoff)] = response.text
    paths = {'fix': fix_response or ROOT / 'data/eval_runs/demo-fix-current-20261029/events.json',
             'slide': ROOT / 'data/eval_runs/final_ui/live/slide-events.json',
             'ask': ROOT / 'data/eval_runs/final_ui/live/ask-events.json'}
    records = {key: json.loads(path.read_text()) for key, path in paths.items()}
    fix = next(data for kind, data in records['fix'] if kind == 'result')
    ask = next(data for kind, data in records['ask'] if kind == 'result')
    assert fix['original_text'] == examples['nvda-ai-story']['text']
    assert fix['counts'] == {'verified': 9, 'wrong': 0, 'unverifiable': 2, 'approximate': 0}
    return reads, checks, records, examples, ask, {key: {'path': str(path.relative_to(ROOT)), 'sha256': digest(path.read_bytes())} for key, path in paths.items()}


OVERLAY = r"""() => {
 const style = document.createElement('style');
 style.textContent = `html{scroll-behavior:smooth}
 #demo-cursor{position:fixed;left:900px;top:400px;width:30px;height:36px;z-index:2147483647;pointer-events:none;filter:drop-shadow(0 1px 2px #0008);transform:translate(-2px,-2px)}
 #demo-cursor.down{filter:drop-shadow(0 0 9px #16a774);transform:translate(-2px,-2px) scale(.87)}
 #demo-disclosure{position:fixed;right:24px;top:12px;z-index:2147483646;pointer-events:none;border:1px solid #b5cbbf;border-radius:7px;background:#f6faf7ee;color:#234a38;font:600 12px/1.4 Arial;padding:7px 11px;letter-spacing:.3px}
 #demo-end{position:fixed;bottom:120px;left:50%;transform:translateX(-50%);z-index:2147483645;border:1px solid #b5cbbf;border-radius:10px;background:#f6faf7f5;padding:16px 26px;color:#153e2e;font:600 18px/1.7 Arial;text-align:center;pointer-events:none;display:none}`;
 document.head.appendChild(style);
 const c=document.createElement('div');c.id='demo-cursor';c.innerHTML='<svg width="28" height="34" viewBox="0 0 28 34"><path d="M3 2 L3 26 L10 20 L16 32 L21 29 L15 17 L25 17 Z" fill="white" stroke="#153e2e" stroke-width="2" stroke-linejoin="round"/></svg>';document.body.appendChild(c);
 const d=document.createElement('div');d.id='demo-disclosure';d.textContent='LOCAL PRODUCT RECORDING · CACHED EXTRACTION';document.body.appendChild(d);
 const e=document.createElement('div');e.id='demo-end';e.innerHTML='NoCap · Robert Chang<br>receipts-nocap.vercel.app<br>github.com/changshoufeng0609-cmd/nocap';document.body.appendChild(e);
 addEventListener('mousemove',event=>{c.style.left=event.clientX+'px';c.style.top=event.clientY+'px'});
 addEventListener('mousedown',()=>c.classList.add('down'));addEventListener('mouseup',()=>c.classList.remove('down'));
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3106')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/eval_runs/demo-walkthrough-20261029/take3')
    parser.add_argument('--inspect', action='store_true')
    parser.add_argument('--quick', action='store_true', help='Short rehearsal for selector and layout checks')
    parser.add_argument('--fix-response', type=Path, help='Exact successful Fix events recorded by an authorized live run')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright, expect
    reads, checks, records, examples, ask, sources = fixtures(args.fix_response.resolve() if args.fix_response else None)
    image = ROOT / 'examples/slides/nvda-draft.png'
    image_hash = digest(image.read_bytes())
    assert image_hash == '7e93576180457003522abf1c46a4cfb4889869d476cc9a735d72d81545fa7c7c'
    log = {'provider_calls': 0, 'new_provider_spend_usd': 0, 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
           'mode': 'actual browser video; offline Check and exact historical model-response replay', 'sources': sources,
           'image_sha256': image_hash, 'api_requests': [], 'blocked_requests': [], 'page_errors': [], 'scenes': [], 'actions': []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        options = {'viewport': {'width': 1600, 'height': 780}, 'device_scale_factor': 1.2, 'color_scheme': 'light',
                   'permissions': ['clipboard-read', 'clipboard-write'], 'service_workers': 'block'}
        if not args.inspect:
            options.update(record_video_dir=str(args.output / 'raw'), record_video_size={'width': 1600, 'height': 780})
        context = browser.new_context(**options)
        origin = urlsplit(args.url)

        def guard(route, request):
            parsed = urlsplit(request.url)
            if parsed.netloc != origin.netloc:
                log['blocked_requests'].append({'method': request.method, 'path': parsed.path, 'reason': 'external'})
                return route.abort()
            if parsed.path.startswith('/api/'):
                if request.method == 'GET' and parsed.path in reads:
                    return route.fulfill(status=200, content_type='application/json', body=json.dumps(reads[parsed.path]))
                payload = request.post_data_json if request.method == 'POST' else None
                body, mode = None, None
                if parsed.path == '/api/check' and payload:
                    body = checks.get((payload['text'], payload.get('as_of')))
                    assert payload.get('context') in {item['context'] for item in examples.values()}
                    mode = 'real offline verification with bundled extraction cache'
                elif parsed.path == '/api/fix' and payload:
                    assert payload['text'] == examples['nvda-ai-story']['text'] and payload.get('as_of') is None
                    body, mode = sse(records['fix']), 'recorded Wonder + Spark Fix response'
                elif parsed.path == '/api/slide' and payload:
                    assert digest(base64.b64decode(payload['image_data_url'].split(',', 1)[1])) == image_hash
                    assert payload.get('context') == 'NVIDIA, Q2 FY2027' and payload.get('as_of') is None
                    body, mode = sse(records['slide']), 'recorded live response for these exact branded image bytes'
                elif parsed.path == '/api/ask' and payload:
                    assert payload == {'question': ask['question'], 'as_of': None}
                    body, mode = sse(records['ask']), 'recorded Spark Ask response'
                if body is not None:
                    log['api_requests'].append({'path': parsed.path, 'mode': mode, 'input_sha256': digest(json.dumps(payload, sort_keys=True).encode()), 'response_sha256': digest(body.encode())})
                    return route.fulfill(status=200, content_type='text/event-stream', body=body)
                log['blocked_requests'].append({'method': request.method, 'path': parsed.path, 'reason': 'not allowlisted'})
                return route.abort()
            if request.method not in ('GET', 'HEAD'):
                return route.abort()
            route.continue_()

        context.route('**/*', guard)
        born = time.monotonic()
        page = context.new_page()
        page.on('pageerror', lambda error: log['page_errors'].append(str(error)))
        page.set_default_timeout(15000)
        page.goto(args.url, wait_until='networkidle')
        expect(page.get_by_role('heading', name='NoCap', exact=True)).to_be_visible()
        page.evaluate(OVERLAY)
        if args.inspect:
            page.screenshot(path=str(args.output / 'inspect.png'), full_page=True)
            (args.output / 'inspect.txt').write_text(page.locator('body').inner_text())
            print(json.dumps({'inspect': str(args.output / 'inspect.png'), 'buttons': page.get_by_role('button').all_text_contents(), 'textareas': page.locator('textarea').count()}))
            context.close(); browser.close(); return

        # A removable setup slate gives the renderer an exact visual sync point.
        # It is excluded from the final video; action timing starts after removal.
        page.evaluate('''() => {const el=document.createElement('div');el.id='demo-sync';el.style='position:fixed;inset:0;background:#ff00ff;z-index:2147483647';document.body.appendChild(el)}''')
        page.wait_for_timeout(600)
        page.evaluate('''() => {
          document.querySelector('#demo-sync').remove();
          window.__demoStart=performance.now();window.__demoModes=[];
          new MutationObserver(changes=>changes.forEach(change=>{
            const el=change.target;
            if(el.getAttribute('role')==='tab'&&el.getAttribute('aria-selected')==='true')
              window.__demoModes.push({mode:el.textContent,time_s:(performance.now()-window.__demoStart)/1000});
          })).observe(document.body,{subtree:true,attributes:true,attributeFilter:['aria-selected']});
        }''')
        start = time.monotonic()
        log['sync_marker'] = 'magenta setup slate; trim through its final frame'
        log['timeline_offset_s'] = start - born
        cursor = [960, 500]

        def pause(seconds):
            page.wait_for_timeout(round(seconds * (160 if args.quick else 1000)))

        def until(seconds):
            if args.quick:
                return
            remaining = seconds - (time.monotonic() - start)
            if remaining > 0:
                pause(remaining)

        def scene(name, label):
            log['scenes'].append({'name': name, 'start_s': round(time.monotonic() - start, 3)})
            page.evaluate('label => document.querySelector("#demo-disclosure").textContent=label', label)
            print(name, flush=True)

        def hover(locator, duration=.65):
            locator.scroll_into_view_if_needed()
            pause(.35)
            box = locator.bounding_box()
            assert box
            target = [box['x'] + box['width'] / 2, box['y'] + box['height'] / 2]
            initial = cursor.copy()
            for i in range(1, 13):
                t = i / 12; t = t*t*(3-2*t)
                point = [initial[j] + (target[j]-initial[j])*t for j in (0, 1)]
                page.mouse.move(*point); pause(duration / 12)
            cursor[:] = target

        def click(locator):
            name = locator.get_attribute('aria-label') or locator.inner_text()[:70]
            log['actions'].append({'action':'click-start', 'target':name, 'time_s':round(time.monotonic()-start,3)})
            hover(locator)
            page.mouse.down(); pause(.12); page.mouse.up(); pause(.35)
            log['actions'].append({'action':'click-done', 'target':name, 'time_s':round(time.monotonic()-start,3)})

        def shot(name):
            log['actions'].append({'action':'frame', 'target':name, 'time_s':round(time.monotonic()-start,3)})
            page.screenshot(path=str(args.output / f'{name}.png'))

        try:
            scene('paste-and-check', 'CACHED EXTRACTION · ACTUAL LOCAL PRODUCT RECORDING')
            editor = page.locator('textarea').first
            click(editor); editor.fill(''); pause(1)
            page.evaluate('text => navigator.clipboard.writeText(text)', examples['nvda-ai-story']['text'])
            editor.press('Meta+V')
            expect(editor).to_have_value(examples['nvda-ai-story']['text'])
            until(8)
            click(page.get_by_role('button', name='Check', exact=True))
            summary = page.get_by_role('region', name='Check summary')
            expect(summary).to_contain_text(re.compile(r'7\s*verified'))
            shot('01-check')
            until(18)
            scene('growth-receipt', 'CACHED EXAMPLE · MUSE SPARK EXTRACTION + DETERMINISTIC VERDICTS')
            click(page.locator('[data-mark-id="c2"]').first)
            panel = page.locator('#receipt-panel')
            expect(panel).to_contain_text('105.85')
            hover(panel.get_by_text('105.85%', exact=False).first)
            shot('02-growth')
            until(35)
            scene('eps-receipt', 'CACHED EXAMPLE · CLAIMED VALUE VS FILED GAAP VALUE')
            click(page.locator('[data-mark-id="c8"]').first)
            expect(panel).to_contain_text('$2.46')
            hover(panel.get_by_text('$2.46', exact=False).first)
            shot('03-eps')
            until(50)
            scene('fix-and-recheck', 'RECORDED LIVE RESPONSE · MUSE WONDER + CACHED EXTRACTION')
            click(page.get_by_role('button', name='Fix with receipts', exact=True))
            result = page.get_by_test_id('fix-result')
            expect(result).to_be_visible()
            result.scroll_into_view_if_needed(); pause(.6)
            shot('04-fix')
            until(66)
            corrections = page.get_by_role('region', name='Correction receipts', exact=True)
            hover(corrections); shot('05-corrections')
            until(78)
            rechecked = page.get_by_role('region', name='Every claim after correction', exact=True)
            hover(rechecked); shot('06-rechecked')
            until(88)
            click(page.get_by_role('button', name='Copy with receipts', exact=True))
            assert 'Receipts for corrections' in page.evaluate('navigator.clipboard.readText()')
            until(94)
            scene('historical-cutoff', 'CACHED EXTRACTION · VERDICTS RECOMPUTED FOR THE CUTOFF')
            click(page.get_by_role('button', name='Edit draft', exact=True))
            click(page.get_by_role('combobox', name='Example', exact=True))
            click(page.get_by_role('option', name='June-quarter roundup, as of July 29', exact=False))
            click(page.get_by_role('button', name='Check', exact=True))
            expect(summary).to_contain_text(re.compile(r'2\s*not public yet'))
            click(page.locator('[data-mark-id="c8"]').first)
            expect(panel).to_contain_text('Not public until')
            shot('07-cutoff')
            until(110)
            click(page.get_by_role('button', name='Now', exact=True))
            click(page.get_by_role('button', name='Check', exact=True))
            expect(summary).to_contain_text(re.compile(r'8\s*verified'))
            shot('08-now')
            until(121)
            scene('slide-upload', 'RECORDED RESPONSE · SAM 3.1 + MUSE SPARK · SAME IMAGE BYTES')
            click(page.get_by_role('tab', name='Slide', exact=True))
            hover(page.get_by_label('Slide image', exact=True))
            page.get_by_label('Slide image', exact=True).set_input_files(image)
            field = page.locator('#slide-context')
            click(field); field.fill('NVIDIA, Q2 FY2027')
            pause(1)
            click(page.get_by_role('button', name='Check slide', exact=True))
            expect(page.get_by_text('4 extracted readings checked', exact=True)).to_be_visible()
            shot('09-slide')
            overlay = page.locator('button[data-slide-claim="c2"]')
            until(133)
            click(overlay)
            expect(page.locator('#slide-receipt-title')).to_be_visible()
            pause(.8)
            shot('10-slide-receipt')
            until(147)
            scene('ask-with-sources', 'RECORDED RESPONSE · MUSE SPARK PLAN + CODE-VERIFIED ANSWER')
            click(page.get_by_role('tab', name='Ask', exact=True))
            question = page.get_by_label('Your question', exact=True)
            click(question); question.fill(ask['question'])
            click(page.get_by_role('button', name='Ask with receipts', exact=True))
            expect(page.get_by_role('heading', name='Answer with receipts', exact=True)).to_be_visible()
            hover(page.get_by_role('region', name='Answer with receipts', exact=True))
            pause(.8)
            shot('11-ask')
            until(156)
            click(page.get_by_role('tab', name='Draft', exact=True))
            page.evaluate('window.scrollTo({top:0,behavior:"smooth"})')
            scene('closing', '11 COMPANIES · PRE-INGESTED SEC EVIDENCE · RESEARCH, NOT INVESTMENT ADVICE')
            page.evaluate('document.querySelector("#demo-end").style.display="block"')
            until(165)
            shot('12-end')
            log['status'] = 'captured'
        finally:
            log['timeline_duration_s'] = round(time.monotonic() - start, 3)
            log['mode_changes'] = page.evaluate('window.__demoModes')
            video = page.video
            context.close()
            if video:
                video.save_as(str(args.output / 'walkthrough-raw.webm'))
            browser.close()
            (args.output / 'capture.json').write_text(json.dumps(log, indent=2) + '\n')
    assert not log['page_errors'] and not log['blocked_requests'], log
    assert log['timeline_duration_s'] < 178, log['timeline_duration_s']
    print(json.dumps({'status': log['status'], 'duration_s': log['timeline_duration_s'], 'output': str(args.output)}))


if __name__ == '__main__':
    main()
