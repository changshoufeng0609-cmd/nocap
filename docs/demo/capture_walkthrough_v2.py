#!/usr/bin/env python3
"""Record the v2 walkthrough from the real NoCap interface and the real snapshot-only API.

Start the built frontend on port 3106 and the API on port 8106 with live requests disabled
(recording-checklist.md has the exact commands). The browser reaches the API only through this harness:

* Saved examples and the two recorded real-document samples (the Bank of America slide and the Meta 10-K
  question) replay through the real API at $0. The API itself refuses any other draft, slide or question.
* Fix replays the exact recorded October 29 response, as the v1 harness did. No new model call is made.
* /api/health comes from the real API with two fields set to the owner-approved judging-window values
  (live_requests_paused=false, live_until=2026-11-17T16:00:00Z), so the UI shows the state judges will see
  and the Fix button is enabled for the recorded replay. The API process itself stays paused.
* Every external request and every API call outside the allowlist is blocked and logged.

Chrome's compositor frames are encoded at 2560 x 1280 (1280 x 640 CSS pixels at 2x) so the renderer can
zoom into receipts without blurring the text. Marks and element boxes are logged for the renderer.

`--shot private` records one short take for v2.1 instead: the Private tab's "Replay a recorded Private check",
which the real API answers from the committed recording of a live Modal Glimmer run (no model call). It
forwards only that exact sample request, requires the replayed (cached) response, and leaves /api/health as
served (paused); the Private tab does not read it. Marks: private-start, private-result, private-end.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from screencast_recorder import ScreencastRecorder  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VIEWPORT = {'width': 1280, 'height': 640}
SCALE = 2
LIVE_UNTIL = '2026-11-17T16:00:00Z'      # docs/deploy.md, "Judging window (approved 2026-10-30)"
GET_ALLOW = {'/api/health', '/api/examples', '/api/ask/options', '/api/grounding/filings', '/api/private/status'}
POST_FORWARD = {'/api/check', '/api/slide', '/api/grounding'}
FIX_EVENTS = ROOT / 'data/eval_runs/demo-fix-current-20261029/events.json'
FIX_EVIDENCE = ROOT / 'docs/demo/fix-live-2026-10-29.json'   # records the response hash of the authorized live Fix run
# Seconds to hold each framing. Each is longer than the narration it carries, so the renderer can cut freely.
HOLD = {'initial': 3.0, 'cold': 7.5, 'tiles': 6.5, 'receipt': 13.5, 'sources': 6.5, 'eps': 8.0,
        'fix-result': 8.5, 'fix-receipts': 3.5, 'cutoff-loaded': 4.0, 'cutoff-tiles': 3.5, 'cutoff-receipt': 9.0,
        'slide-summary': 5.0, 'slide-regions': 13.0, 'slide-receipt': 4.5, 'ask-mode': 5.0, 'ask-result': 10.0,
        'ask-quotes': 6.0, 'bench-cards': 10.0, 'bench-question': 6.5, 'private-start': 0.8, 'private-result': 10.0}
PRIVATE_SAMPLE = 'nvda-q2-fy2027-private'      # web/lib/samples.json; its live run is in data/snapshot/recordings/private
PRIVATE_RECORDINGS = ROOT / 'data/snapshot/recordings/private'

OVERLAY = r"""(() => {
  const install = () => {
    if (document.querySelector('#demo-cursor')) return;
    const style = document.createElement('style');
    style.textContent = `html{scroll-behavior:smooth}
    #demo-cursor{position:fixed;left:640px;top:330px;width:28px;height:34px;z-index:2147483647;pointer-events:none;filter:drop-shadow(0 1px 2px #0008);transform:translate(-2px,-2px);transition:transform .08s}
    #demo-cursor.down{filter:drop-shadow(0 0 8px #16a774);transform:translate(-2px,-2px) scale(.86)}`;
    document.head.appendChild(style);
    const c = document.createElement('div'); c.id = 'demo-cursor';
    c.innerHTML = '<svg width="28" height="34" viewBox="0 0 28 34"><path d="M3 2 L3 26 L10 20 L16 32 L21 29 L15 17 L25 17 Z" fill="white" stroke="#153e2e" stroke-width="2" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    const saved = window.__demoCursor || [640, 330];
    c.style.left = saved[0] + 'px'; c.style.top = saved[1] + 'px';
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; window.__demoCursor = [e.clientX, e.clientY]; }, true);
    addEventListener('mousedown', () => c.classList.add('down'), true);
    addEventListener('mouseup', () => c.classList.remove('down'), true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})()"""

# name -> [css selector, text the element's text must start with (or null), index among matches]
BOX_JS = r"""(specs) => {
  const out = {};
  for (const [name, [selector, text, index]] of Object.entries(specs)) {
    let items = [...document.querySelectorAll(selector)].filter(el => el.getClientRects().length);
    if (text) items = items.filter(el => el.textContent.trim().startsWith(text));
    const el = items[index ?? 0];
    if (!el) { out[name] = null; continue; }
    const r = el.getBoundingClientRect();
    out[name] = [Math.round(r.x * 10) / 10, Math.round(r.y * 10) / 10, Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10];
  }
  return out;
}"""


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def sse(events):
    return ''.join(f'event: {kind}\ndata: {json.dumps(data)}\n\n' for kind, data in events)


def preflight(api):
    health = json.load(urlopen(api + '/api/health', timeout=5))
    assert health['live_requests_paused'] is True, 'The API must run with RECEIPTS_LIVE_REQUESTS_ENABLED=false'
    assert health['draft_cache_policy'] == 'snapshot_only', 'The API must run with RECEIPTS_PERSIST_DRAFT_CACHES=false'
    assert health['facts'] == 11554 and len(health['companies']) == 11, health
    examples = {item['id']: item for item in json.load(urlopen(api + '/api/examples', timeout=5))}
    filings = json.load(urlopen(api + '/api/grounding/filings', timeout=5))
    return health, examples, filings


def preflight_private(api):
    health = json.load(urlopen(api + '/api/health', timeout=5))
    assert health['live_requests_paused'] is True, 'The API must run with RECEIPTS_LIVE_REQUESTS_ENABLED=false'
    assert health['draft_cache_policy'] == 'snapshot_only', 'The API must run with RECEIPTS_PERSIST_DRAFT_CACHES=false'
    status = json.load(urlopen(api + '/api/private/status', timeout=5))
    assert any(item['sample_id'] == PRIVATE_SAMPLE for item in status['recorded_samples']), status
    sample = next(item for item in json.loads((ROOT / 'web/lib/samples.json').read_text())['private'] if item['id'] == PRIVATE_SAMPLE)
    records = [path for path in sorted(PRIVATE_RECORDINGS.glob('*.json'))
               if json.loads(path.read_text())['request'].get('sample_id') == PRIVATE_SAMPLE]
    assert len(records) == 1, records
    record = json.loads(records[0].read_text())
    assert {k: record['request'][k] for k in ('text', 'context', 'as_of')} == {k: sample[k] for k in ('text', 'context', 'as_of')}
    return health, status, sample, {'path': str(records[0].relative_to(ROOT)), 'sha256': digest(records[0].read_bytes()),
                                    'recorded_utc': record['recorded_utc'], 'recorded_at_commit': record['source_commit'],
                                    'request_wall_s': record['runtime'].get('request_wall_s')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3106')
    parser.add_argument('--api', default='http://127.0.0.1:8106')
    parser.add_argument('--shot', choices=('walkthrough', 'private'), default='walkthrough')
    parser.add_argument('--output', type=Path, help='default: the v2 take2 folder, or demo-private-take-20261031 for --shot private')
    parser.add_argument('--quick', action='store_true', help='Selector rehearsal: short holds, no video')
    args = parser.parse_args()
    private_shot = args.shot == 'private'
    args.output = args.output or ROOT / ('data/eval_runs/demo-private-take-20261031' if private_shot
                                         else 'data/eval_runs/demo-walkthrough-v2-20261030/take2')
    args.output.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright, expect

    log = {'harness': 'capture_walkthrough_v2.py', 'shot': args.shot, 'provider_calls': 0, 'new_provider_spend_usd': 0,
           'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
           'viewport_css': VIEWPORT, 'device_scale_factor': SCALE,
           'api_requests': [], 'blocked_requests': [], 'refused_requests': [], 'page_errors': [], 'marks': [], 'actions': []}
    if private_shot:
        health, private_status, private_sample, recording = preflight_private(args.api)
        # The product code must be the committed tree; only demo media under web/public/demo may differ.
        changes = subprocess.check_output(['git', 'status', '--porcelain', '--', 'web', 'receipts', 'data/snapshot'], cwd=ROOT, text=True).split('\n')
        changes = [line for line in changes if line.strip()]
        assert all(line[3:].startswith('web/public/demo/') for line in changes), changes
        log.update(api_health_as_served=health, health_substitution=None, private_status_as_served=private_status,
                   private_recording=recording, source_tree_changes=changes)
        fix_body = nvda = None
    else:
        health, examples, filings = preflight(args.api)
        nvda = examples['nvda-ai-story']
        assert digest(FIX_EVENTS.read_bytes()) == json.loads(FIX_EVIDENCE.read_text())['response_sha256'], 'Fix response differs from the recorded run'
        fix_events = json.loads(FIX_EVENTS.read_text())
        fix_result = next(data for kind, data in fix_events if kind == 'result')
        assert fix_result['original_text'] == nvda['text']
        assert fix_result['counts'] == {'verified': 9, 'wrong': 0, 'unverifiable': 2, 'approximate': 0}
        fix_body = sse(fix_events)
        log.update(api_health_as_served=health,
                   health_substitution={'live_requests_paused': False, 'live_until': LIVE_UNTIL,
                                        'reason': 'Shows the owner-approved judging-window state (docs/deploy.md); the API process stayed paused'},
                   fix_source={'path': str(FIX_EVENTS.relative_to(ROOT)), 'sha256': digest(FIX_EVENTS.read_bytes())})
    origin = urlsplit(args.url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=SCALE, color_scheme='light',
                                      permissions=['clipboard-read', 'clipboard-write'], service_workers='block')
        context.add_init_script(OVERLAY)

        def guard(route, request):
            parsed = urlsplit(request.url)
            if parsed.netloc != origin.netloc:
                log['blocked_requests'].append({'method': request.method, 'url': request.url[:200], 'reason': 'external'})
                return route.abort()
            path, method = parsed.path, request.method
            if not path.startswith('/api/'):
                if method not in ('GET', 'HEAD'):
                    log['blocked_requests'].append({'method': method, 'path': path, 'reason': 'non-GET page request'})
                    return route.abort()
                return route.continue_()
            target = args.api + path + (f'?{parsed.query}' if parsed.query else '')
            entry = {'method': method, 'path': path, 'time_s': round(recorder.now(), 3) if recorder else None}
            if method == 'GET' and path in GET_ALLOW:
                response = route.fetch(url=target)
                body = response.body()
                assert response.status == 200, (path, response.status)
                entry['mode'] = 'real API'
                if path == '/api/health' and not private_shot:
                    data = json.loads(body)
                    assert data['live_requests_paused'] is True
                    data.update(live_requests_paused=False, live_until=LIVE_UNTIL)
                    body = json.dumps(data).encode()
                    entry['mode'] = 'real API; judging-window values substituted'
                entry.update(status=200, response_sha256=digest(body))
                log['api_requests'].append(entry)
                return route.fulfill(status=200, content_type='application/json', body=body)
            if method == 'POST' and path == '/api/private/check' and private_shot:
                # Only the exact public sample may pass; the real API must answer it from the recording (cached).
                payload = request.post_data_json
                if payload != {key: private_sample[key] for key in ('text', 'context', 'as_of')}:
                    log['blocked_requests'].append({'method': method, 'path': path, 'reason': 'not the recorded Private sample'})
                    return route.abort()
                response = route.fetch(url=target)
                body = response.body()
                text = body.decode('utf-8', 'replace')
                cached = bool(re.search(r'"cached":\s*true', text))
                complete = response.status == 200 and 'event: error' not in text and 'event: done' in text
                entry.update(mode='recorded live Modal Glimmer run, replayed by the real API', status=response.status, cached=cached,
                             input_sha256=digest(json.dumps(payload, sort_keys=True)), response_sha256=digest(body), response_bytes=len(body))
                (log['api_requests'] if complete and cached else log['refused_requests']).append(entry)
                return route.fulfill(response=response, body=body)
            if method == 'POST' and path == '/api/fix' and not private_shot:
                payload = request.post_data_json
                assert payload['text'] == nvda['text'] and payload.get('as_of') is None, 'Only the recorded Fix input is allowed'
                entry.update(mode='recorded October 29 live Fix response (Muse Wonder 1.0 + full Spark recheck)',
                             status=200, input_sha256=digest(json.dumps(payload, sort_keys=True)), response_sha256=digest(fix_body))
                log['api_requests'].append(entry)
                return route.fulfill(status=200, content_type='text/event-stream', body=fix_body)
            if method == 'POST' and path in POST_FORWARD and not private_shot:
                payload = request.post_data_json
                response = route.fetch(url=target)
                body = response.body()
                text = body.decode('utf-8', 'replace')
                cached = bool(re.search(r'"cached":\s*true', text))
                complete = response.status == 200 and 'event: error' not in text and ('event: done' in text or 'event: result' in text)
                entry.update(mode='real snapshot-only API replay', status=response.status, cached=cached,
                             input_sha256=digest(json.dumps(payload, sort_keys=True)), response_sha256=digest(body),
                             response_bytes=len(body))
                (log['api_requests'] if complete and cached else log['refused_requests']).append(entry)
                return route.fulfill(response=response, body=body)
            log['blocked_requests'].append({'method': method, 'path': path, 'reason': 'not allowlisted'})
            return route.abort()

        recorder = None
        context.route('**/*', guard)
        page = context.new_page()
        page.on('pageerror', lambda error: log['page_errors'].append(str(error)))
        page.set_default_timeout(20000)
        page.goto(args.url, wait_until='networkidle')
        expect(page.get_by_role('heading', name='NoCap', exact=True)).to_be_visible()
        if not private_shot:
            expect(page.get_by_text(re.compile('Live checks are open until'))).to_be_visible()
            editor = page.locator('textarea#draft')
            expect(editor).to_have_value(nvda['text'])
        page.wait_for_timeout(800)
        if not args.quick:
            recorder = ScreencastRecorder(context, page, args.output / 'master.mp4', (VIEWPORT['width'] * SCALE, VIEWPORT['height'] * SCALE))
        clock = time.monotonic()
        cursor = [640.0, 330.0]

        def now():
            return round(recorder.now(), 3) if recorder else round(time.monotonic() - clock, 3)

        def pause(seconds):
            page.wait_for_timeout(round(seconds * (120 if args.quick else 1000)))

        def boxes(specs):
            return page.evaluate(BOX_JS, specs) if specs else {}

        def mark(name, specs=None):
            entry = {'name': name, 't': now(), 'scroll_y': page.evaluate('window.scrollY'), 'url_path': urlsplit(page.url).path,
                     'boxes': boxes(specs)}
            missing = [key for key, value in entry['boxes'].items() if value is None]
            assert not missing, (name, missing)
            log['marks'].append(entry)
            print(f'{entry["t"]:7.2f}  {name}', flush=True)
            return entry

        def hold(name):
            pause(HOLD[name])

        def glide(target, duration=.7):
            start = cursor.copy()
            steps = max(8, round(duration * 30))
            for i in range(1, steps + 1):
                t = i / steps
                t = t * t * (3 - 2 * t)
                page.mouse.move(start[0] + (target[0] - start[0]) * t, start[1] + (target[1] - start[1]) * t)
                pause(duration / steps)
            cursor[:] = target

        def hover(locator, duration=.7, offset=(0.5, 0.5)):
            locator.scroll_into_view_if_needed()
            box = locator.bounding_box()
            assert box, locator
            glide([box['x'] + box['width'] * offset[0], box['y'] + box['height'] * offset[1]], duration)

        def click(locator, duration=.7):
            label = (locator.get_attribute('aria-label') or locator.inner_text() or '')[:80]
            log['actions'].append({'action': 'click-start', 'target': label, 't': now()})
            hover(locator, duration)
            # Playwright performs the click itself (stability and hit-target checks) at the point the cursor reached.
            locator.click(delay=110)
            pause(.3)
            log['actions'].append({'action': 'click-done', 'target': label, 't': now()})

        def scroll_to(y, settle=.9):
            page.evaluate('y => window.scrollTo({top: Math.max(0, y), behavior: "smooth"})', y)
            pause(settle)

        def absolute_top(selector):
            return page.evaluate('s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.top + scrollY, r.bottom + scrollY]; }', selector)

        summary = page.get_by_role('region', name='Check summary')
        panel = page.locator('#receipt-panel')

        def walkthrough_take():
            # 1. Draft: the NVIDIA example is loaded by default; the green notice shows the judging window.
            mark('draft-initial', {'banner': ['[role=status]', 'Live checks are open', 0], 'tabs': ['[role=tablist]', None, 0],
                                   'picker': ['#example-picker', None, 0], 'editor': ['textarea#draft', None, 0]})
            hold('initial')
            button_bottom = page.evaluate('() => { const b = [...document.querySelectorAll("button")].find(x => x.textContent.trim() === "Check"); const r = b.getBoundingClientRect(); return r.bottom + scrollY; }')
            scroll_to(button_bottom - VIEWPORT['height'] + 18)
            check = page.get_by_role('button', name='Check', exact=True)
            mark('draft-scrolled', {'editor': ['textarea#draft', None, 0], 'check': ['button', 'Check', 0], 'picker': ['#example-picker', None, 0]})
            hover(check, 1.0)
            pause(.4)
            click(check, .2)
            mark('check-click')
            expect(summary).to_contain_text(re.compile(r'7\s*Verified'))
            mark('check-done')
            pause(1.3)
            mark('check-settled', {'summary': ['section[aria-label="Check summary"]', None, 0], 'c2': ['[data-mark-id="c2"]', None, 0],
                                   'c8': ['[data-mark-id="c8"]', None, 0], 'panel': ['#receipt-panel', None, 0]})

            # Cold open framing: both wrong figures in view, with the receipt panel beside them.
            c2 = absolute_top('[data-mark-id="c2"]')
            c8 = absolute_top('[data-mark-id="c8"]')
            scroll_to((c2[0] + c8[1]) / 2 - VIEWPORT['height'] / 2 + 10)
            glide([1000, 620], .6)
            mark('cold-both', {'c2': ['[data-mark-id="c2"]', None, 0], 'c8': ['[data-mark-id="c8"]', None, 0],
                               'draft': ['[data-testid="highlighted-draft"]', None, 0], 'panel': ['#receipt-panel', None, 0],
                               'receipt-dl': ['#receipt-panel dl', None, 0]})
            hold('cold')

            # Verdict tiles.
            scroll_to(absolute_top('section[aria-label="Check summary"]')[0] - 70)
            mark('tiles', {'summary': ['section[aria-label="Check summary"]', None, 0], 'tiles': ['ul[aria-label="Verdict counts"]', None, 0]})
            hold('tiles')

            # Growth receipt: claimed 18%, filed 105.85%, and the calculation.
            scroll_to(absolute_top('[data-mark-id="c2"]')[0] - 150)
            mark('receipt-growth', {'c2': ['[data-mark-id="c2"]', None, 0], 'panel': ['#receipt-panel', None, 0],
                                    'header': ['#receipt-panel header', None, 0], 'receipt-dl': ['#receipt-panel dl', None, 0],
                                    'calc': ['#receipt-panel dl + *', None, 0]})
            hover(panel.locator('dl dd').nth(1), 1.0, offset=(.93, .55))   # point beside the filed value, not over it
            hold('receipt')

            # Sources: why, and the first filing with accession and EDGAR acceptance time.
            glide([560, 610], .7)
            panel.evaluate('el => { const f = el.querySelector("figure"); el.scrollTo({top: f.offsetTop - 12, behavior: "smooth"}); }')
            pause(.9)
            mark('receipt-sources', {'panel': ['#receipt-panel', None, 0], 'why': ['#receipt-panel figure', None, 0],
                                     'first-receipt': ['#receipt-panel section[aria-label="Receipts"] li', None, 0]})
            hold('sources')

            # EPS basis mismatch.
            eps = page.locator('[data-mark-id="c8"]').first
            click(eps, .9)
            expect(panel).to_contain_text('$2.46')
            glide([1000, 620], .6)
            panel.evaluate('el => el.scrollTo({top: 0, behavior: "instant"})')
            pause(.5)
            mark('receipt-eps', {'c8': ['[data-mark-id="c8"]', None, 0], 'panel': ['#receipt-panel', None, 0],
                                 'header': ['#receipt-panel header', None, 0], 'receipt-dl': ['#receipt-panel dl', None, 0],
                                 'why': ['#receipt-panel figure', None, 0]})
            hold('eps')

            # 2. Fix with receipts (recorded October 29 response).
            fix_card = 'section[aria-label="Fix with receipts"]'
            scroll_to(absolute_top(fix_card)[0] - 260, 1.0)
            fix_button = page.get_by_role('button', name='Fix with receipts', exact=True)
            mark('fix-ready', {'fix-card': [fix_card, None, 0], 'fix-button': ['button', 'Fix with receipts', 0]})
            hover(fix_button, .9)
            pause(.3)
            click(fix_button, .2)
            mark('fix-click')
            result = page.get_by_test_id('fix-result')
            expect(result).to_be_visible()
            mark('fix-done')
            pause(.6)
            scroll_to(absolute_top('[data-testid="fix-result"]')[0] - 8, 1.0)
            mark('fix-result', {'result': ['[data-testid="fix-result"]', None, 0], 'counts': ['ul[aria-label="Re-check verdict counts"]', None, 0],
                                'original': ['section[aria-label="Original draft before corrections"]', None, 0],
                                'revised': ['section[aria-label="Revised draft"]', None, 0]})
            glide([1240, 610], .8)
            hold('fix-result')
            scroll_to(absolute_top('section[aria-label="Correction receipts"]')[0] - 20, 1.0)
            mark('fix-receipts', {'receipts': ['section[aria-label="Correction receipts"]', None, 0]})
            hold('fix-receipts')

            # 3. Time travel: the June-quarter roundup at its saved cutoff.
            scroll_to(0, 1.2)
            edit = page.get_by_role('button', name='Edit draft', exact=True)
            click(edit, .9)
            pause(.4)
            scroll_to(0, .6)
            click(page.get_by_role('combobox', name='Example', exact=True), .8)
            roundup = page.get_by_role('option', name='June-quarter roundup, as of July 29', exact=False)
            expect(roundup).to_be_visible()
            page.wait_for_timeout(450)             # let the list finish opening before aiming at an option
            pause(.3)
            click(roundup, .6)
            pause(.6)
            expect(page.locator('textarea#draft')).to_have_value(examples['q2-roundup-look-ahead']['text'])
            mark('cutoff-loaded', {'picker': ['#example-picker', None, 0], 'asof': ['input[type="datetime-local"]', None, 0]})
            hold('cutoff-loaded')
            button_bottom = page.evaluate('() => { const b = [...document.querySelectorAll("button")].find(x => x.textContent.trim() === "Check"); const r = b.getBoundingClientRect(); return r.bottom + scrollY; }')
            scroll_to(button_bottom - VIEWPORT['height'] + 18, .9)
            click(page.get_by_role('button', name='Check', exact=True), .8)
            expect(summary).to_contain_text(re.compile(r'2\s*Not public yet', re.I))
            mark('cutoff-done')
            pause(1.2)
            scroll_to(absolute_top('section[aria-label="Check summary"]')[0] - 70)
            mark('cutoff-tiles', {'summary': ['section[aria-label="Check summary"]', None, 0], 'tiles': ['ul[aria-label="Verdict counts"]', None, 0]})
            hold('cutoff-tiles')
            msft = page.locator('[data-mark-id="c8"]').first
            scroll_to(absolute_top('[data-mark-id="c8"]')[0] - 260, 1.0)
            click(msft, .9)
            expect(panel).to_contain_text('Not public until')
            glide([1000, 620], .6)
            mark('cutoff-receipt', {'c8': ['[data-mark-id="c8"]', None, 0], 'c9': ['[data-mark-id="c9"]', None, 0],
                                    'panel': ['#receipt-panel', None, 0], 'header': ['#receipt-panel header', None, 0],
                                    'lookahead': ['#receipt-panel p', 'Not public until', 0], 'receipt-dl': ['#receipt-panel dl', None, 0]})
            hold('cutoff-receipt')

            # 4. A real SEC slide (recorded live run, replayed by the real API).
            scroll_to(0, 1.0)
            click(page.get_by_role('tab', name='Slide', exact=True), .8)
            pause(.8)
            sample = page.get_by_role('button', name='Try a real SEC slide', exact=True)
            mark('slide-tab', {'sample': ['button', 'Try a real SEC slide', 0]})
            click(sample, .9)
            expect(page.get_by_text('32 extracted readings checked', exact=True)).to_be_visible()
            mark('slide-done')
            pause(.6)
            status_top = page.evaluate('() => { const el = [...document.querySelectorAll("[role=status]")].find(x => x.textContent.includes("extracted readings checked")); return el.getBoundingClientRect().top + scrollY; }')
            scroll_to(status_top - 40, 1.0)
            mark('slide-summary', {'status': ['[role=status]', '32 extracted readings checked', 0]})
            hold('slide-summary')
            regions = page.get_by_text(re.compile(r'Show chart/table regions'))
            click(regions, .9)
            pause(.4)
            image_top = page.evaluate('() => { const img = document.querySelector("img[alt^=\'Uploaded slide\']"); return img.getBoundingClientRect().top + scrollY; }')
            scroll_to(image_top - 14, 1.0)
            specs = {'image': ["img[alt^='Uploaded slide']", None, 0]}
            for index in range(1, 33):
                specs[f'reading-{index}'] = [f'button[data-slide-claim="c{index}"]', None, 0]
            region_specs = {f'region-{i}': ['div.pointer-events-none.absolute.border-dashed', None, i] for i in range(4)}
            entry = mark('slide-regions', {**specs, **region_specs, 'list': ['aside#slide-receipt-panel', None, 0]})
            entry['region_labels'] = page.evaluate('() => [...document.querySelectorAll("div.pointer-events-none.absolute.border-dashed")].map(d => d.textContent.trim())')
            entry['reading_labels'] = page.evaluate('() => [...document.querySelectorAll("button[data-slide-claim]")].map(b => [b.dataset.slideClaim, b.getAttribute("aria-label")])')
            glide([300, 420], .8)
            hold('slide-regions')
            reading = page.locator('button[data-slide-claim="c10"]')
            click(reading, .9)
            expect(page.locator('#slide-receipt-title')).to_be_visible()
            pause(.8)
            mark('slide-receipt', {'panel': ['aside#slide-receipt-panel', None, 0], 'reading': ['button[data-slide-claim="c10"]', None, 0]})
            hold('slide-receipt')

            # 5. Ask: exact quotes from Meta's complete 10-K (recorded live run, replayed by the real API).
            scroll_to(0, 1.0)
            click(page.get_by_role('tab', name='Ask', exact=True), .8)
            pause(.6)
            click(page.get_by_text('Filing quotes', exact=True), .8)
            pause(.5)
            mark('ask-mode', {'filing': ['#ask-filing', None, 0], 'filing-meta': ['p', 'Accepted 2026-01-29', 0]})
            hold('ask-mode')
            recorded = page.get_by_role('button', name=re.compile('What does Meta plan to spend'))
            click(recorded, .8)
            pause(.5)
            mark('ask-question', {'question': ['#ask-question', None, 0]})
            find = page.get_by_role('button', name='Find exact quotes', exact=True)
            click(find, .8)
            glide([1240, 610], .6)
            heading = page.get_by_role('heading', name='Exact filing quotations', exact=True)
            expect(heading).to_be_visible()
            mark('ask-done')
            pause(.5)
            scroll_to(absolute_top('section[aria-label="Exact filing quotations"]')[0] - 12, 1.1)
            mark('ask-result', {'result': ['section[aria-label="Exact filing quotations"]', None, 0],
                                'usage': ['span', 'Muse Spark read the complete filing', 0],
                                'filing': ['section[aria-label="Exact filing quotations"] p', 'META', 0],
                                'quote-1': ['blockquote', None, 0]})
            hold('ask-result')
            scroll_to(absolute_top('blockquote')[0] - 40, 1.0)
            mark('ask-quotes', {'quote-1': ['blockquote', None, 0], 'quote-2': ['blockquote', None, 1]})
            hold('ask-quotes')

            # 6. Open weights on the real benchmarks page (recorded Spark Open and Glimmer answers).
            scroll_to(0, 1.0)
            click(page.get_by_role('link', name='Benchmarks', exact=True), .9)
            expect(page.get_by_role('heading', name='What reaches the answer?')).to_be_visible()
            pause(1.0)
            mark('bench-top')
            cards_top = page.evaluate('() => { const h = [...document.querySelectorAll("article h3")].find(x => x.textContent.trim() === "Muse Spark Open"); return h.closest("article").getBoundingClientRect().top + scrollY; }')
            scroll_to(cards_top - 30, 1.2)
            mark('bench-cards', {'spark-open': ['article', 'Muse Spark Open', 0], 'glimmer': ['article', 'Muse Glimmer', 0]})
            glide([1240, 220], .8)
            hold('bench-cards')
            question = page.locator('details summary').filter(has_text="What was Amazon's GAAP net income for Q4 FY2023")
            scroll_to(page.evaluate('() => { const s = [...document.querySelectorAll("details summary")].find(x => x.textContent.includes("Amazon\'s GAAP net income for Q4 FY2023")); return s.getBoundingClientRect().top + scrollY; }') - 60, 1.2)
            click(question, .9)
            pause(.6)
            answers_bottom = page.evaluate('() => { const a = [...document.querySelectorAll("details[open] article")].find(x => x.textContent.trim().startsWith("Muse Spark Open")); return a.getBoundingClientRect().bottom + scrollY; }')
            scroll_to(answers_bottom - VIEWPORT['height'] + 24, 1.0)
            mark('bench-question', {'details': ['details[open]', None, 0],
                                    'reference': ['details[open] div.rounded-lg', None, 0],
                                    'spark-open-answer': ['details[open] article', 'Muse Spark Open', 0],
                                    'glimmer-answer': ['details[open] article', 'Muse Glimmer', 0]})
            hold('bench-question')
            mark('end')
            log['status'] = 'captured'

        def private_take():
            # One take on the Private tab: the real API replays the recorded live Modal Glimmer run of the sample.
            click(page.get_by_role('tab', name='Private', exact=True), .8)
            replay = page.get_by_role('button', name='Replay a recorded Private check', exact=True)
            expect(replay).to_be_enabled()
            form = 'section[aria-label="Private draft"]'
            results = 'section[aria-label="Private check results"]'
            scroll_to(absolute_top(form)[0] - 12, 1.0)
            glide([1000, 560], .6)
            mark('private-start', {'form': [form, None, 0], 'draft': [f'{form} textarea', None, 0],
                                   'replay': ['button', 'Replay a recorded Private check', 0]})
            hold('private-start')
            click(replay, .9)
            expect(page.locator(results)).to_contain_text('Recorded Private check · Muse Glimmer · 2 claims')
            expect(page.locator(f'{form} textarea')).to_have_value(private_sample['text'])
            mark('private-done')
            pause(.7)
            glide([1180, 330], .6)               # off the content column, so it never covers a verdict
            scroll_to(absolute_top(results)[1] - VIEWPORT['height'] + 16, 1.0)
            mark('private-result', {'result': [results, None, 0], 'title': [f'{results} h3', None, 0],
                                    'replay': ['button', 'Replay a recorded Private check', 0]})
            hold('private-result')
            mark('private-end')
            log['status'] = 'captured'

        try:
            (private_take if private_shot else walkthrough_take)()
        finally:
            log['timeline_duration_s'] = now()
            if recorder:
                log['recorder'] = recorder.stop()
            context.close()
            browser.close()
            (args.output / 'capture.json').write_text(json.dumps(log, indent=2) + '\n')
    assert not log['page_errors'], log['page_errors']
    assert not log['blocked_requests'], log['blocked_requests']
    assert not log['refused_requests'], log['refused_requests']
    if private_shot:
        replays = [item for item in log['api_requests'] if item['method'] == 'POST']
        assert len(replays) == 1 and replays[0]['path'] == '/api/private/check' and replays[0]['cached'], replays
    print(json.dumps({'status': log.get('status'), 'duration_s': log['timeline_duration_s'], 'requests': len(log['api_requests']),
                      'output': str(args.output)}, indent=2))


if __name__ == '__main__':
    main()
