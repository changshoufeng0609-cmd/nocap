#!/usr/bin/env python3
"""Capture real UI states from offline-verified checks and a recorded live Fix.

No provider calls are allowed: a local TestClient has failing provider factories,
and Playwright intercepts every check/fix/slide POST. Run from the repo root.
Requires the existing Python Playwright environment and a running web app.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def slide_provenance():
    """Keep historical model evidence separate from a branding-only display refresh."""
    source = json.loads((ROOT / 'docs/demo/slide-replay-provenance.json').read_text())
    current_html = (ROOT / 'examples/slides/nvda-draft.html').read_text()
    substitution = source['allowed_html_substitution']
    original_html = current_html.replace(substitution['after'], substitution['before'])
    assert hashlib.sha256(original_html.encode()).hexdigest() == source['original_html_sha256'], 'The slide changed beyond its approved branding'
    response_file = ROOT / source['recorded_response_path']
    assert hashlib.sha256(response_file.read_bytes()).hexdigest() == source['recorded_response_sha256']
    display_hash = hashlib.sha256((ROOT / 'examples/slides/nvda-draft.png').read_bytes()).hexdigest()
    return {**source, 'branded_display_input_sha256': display_hash,
            'input_relation': 'Branding-only display refresh; original live-input response replay. Financial values and layout unchanged.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:3111')
    parser.add_argument('--chromium', default=str(Path.home() / 'Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'))
    parser.add_argument('--output', type=Path, default=ROOT / 'data/eval_runs/demo_capture')
    parser.add_argument('--slide-only', action='store_true', help='Capture the validated slide flow with its recorded live response')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.slide_only:
        capture_slide(args)
        return
    from fastapi.testclient import TestClient
    from playwright.sync_api import expect, sync_playwright
    from receipts.api import Settings, create_app

    def forbidden():
        raise AssertionError('Provider calls are forbidden during demo capture')

    snapshot = ROOT / 'data/snapshot'
    settings = replace(Settings.from_env({}), data_dir=snapshot, api_cache_dir=None,
                       spark_cache_dir=None, spark_read_dirs=(snapshot / 'spark_cache',),
                       prebaked_dirs=(snapshot / 'api_cache',), daily_budget_usd=0)
    app = create_app(settings, client_factory=forbidden, wonder_client_factory=forbidden,
                     slide_client_factory=forbidden)
    fixtures: dict[tuple[str, str | None], str] = {}
    with TestClient(app) as client:
        examples = client.get('/api/examples').json()
        by_id = {e['id']: e for e in examples}
        for example_id, as_of in [('nvda-ai-story', None), ('q2-roundup-look-ahead', '2026-07-29T16:00:00Z'), ('q2-roundup-look-ahead', None)]:
            ex = by_id[example_id]
            response = client.post('/api/check', json={'text': ex['text'], 'context': ex['context'], 'as_of': as_of, 'example_id': example_id})
            assert response.status_code == 200
            assert 'event: error' not in response.text, response.text
            assert 'event: done' in response.text
            events = []
            for block in response.text.split('\n\n'):
                lines = block.splitlines()
                if len(lines) >= 2 and lines[0].startswith('event:'):
                    name, data = lines[0][6:].strip(), json.loads(lines[1][5:].strip())
                    if name == 'meta':
                        data['cached'] = True  # The browser receives this recorded offline response.
                    events.append((name, data))
            body = ''.join(f'event: {n}\ndata: {json.dumps(d)}\n\n' for n, d in events)
            fixtures[(ex['text'], as_of)] = body
            (args.output / f'{example_id}-{"cutoff" if as_of else "now"}.sse').write_text(body)

    fix_path = ROOT / 'data/eval_runs/fix_browser/live_events.json'
    fix_events = json.loads(fix_path.read_text())
    fix_body = ''.join(f'event: {n}\ndata: {json.dumps(d)}\n\n' for n, d in fix_events)
    fix_result = next(d for n, d in fix_events if n == 'result')
    assert fix_result['counts'] == {'verified': 9, 'wrong': 0, 'unverifiable': 2, 'approximate': 0}
    captures: list[str] = []
    errors: list[str] = []
    routed: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.chromium)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                      reduced_motion='reduce', color_scheme='light',
                                      permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def route_check(route):
            request = route.request.post_data_json
            body = fixtures.get((request['text'], request.get('as_of')))
            if body is None:
                errors.append('Unexpected check request; refused')
                route.abort()
                return
            routed.append('check:offline-verification-replay')
            route.fulfill(status=200, content_type='text/event-stream', body=body)

        def route_fix(route):
            request = route.request.post_data_json
            assert request['text'] == fix_result['original_text']
            assert request.get('as_of') is None
            routed.append('fix:recorded-live-response')
            route.fulfill(status=200, content_type='text/event-stream', body=fix_body)

        page.route('**/api/check', route_check)
        page.route('**/api/fix', route_fix)
        page.route('**/api/slide', lambda route: route.abort())
        page.goto(args.url)
        page.wait_for_load_state('networkidle')
        expect(page).to_have_title(re.compile(r'^NoCap\b'))
        assert not errors, errors

        def capture(name, locator=None):
            page.wait_for_timeout(250)
            path = args.output / f'{name}.png'
            if locator is None:
                page.screenshot(path=str(path))
            else:
                locator.screenshot(path=str(path))
            captures.append(name)

        expect(page.locator('textarea')).to_have_value(re.compile('NVIDIA'))
        capture('01-editor')
        page.get_by_role('button', name='Check', exact=True).click()
        expect(page.get_by_role('button', name='Fix with receipts', exact=True)).to_be_visible()
        summary = page.get_by_role('region', name='Check summary')
        expect(summary).to_contain_text(re.compile(r'7\s*verified'))
        capture('02-check')
        expect(page.get_by_role('region', name='Calculation from receipts')).to_be_visible()
        capture('03-growth-receipt', page.locator('#receipt-panel'))
        page.locator('[data-mark-id="c8"]').click()
        expect(page.locator('#receipt-panel')).to_contain_text('$2.46')
        capture('04-eps-receipt', page.locator('#receipt-panel'))
        capture('04b-eps-context')
        page.get_by_role('button', name='Fix with receipts', exact=True).click()
        expect(page.get_by_test_id('fix-result')).to_be_visible()
        capture('05-fix', page.get_by_role('region', name='Fix with receipts', exact=True))
        capture('06-correction-receipts', page.get_by_role('region', name='Correction receipts', exact=True))
        capture('07-all-rechecked', page.get_by_role('region', name='Every claim after correction', exact=True))
        page.get_by_role('button', name='Copy with receipts', exact=True).click()
        assert 'Receipts for corrections' in page.evaluate('navigator.clipboard.readText()')
        page.wait_for_timeout(5500)  # Let the real clipboard confirmation disappear before the next shot.
        page.get_by_role('button', name='Edit draft', exact=True).click()
        page.get_by_role('combobox').click()
        page.get_by_role('option', name='June-quarter roundup, as of July 29', exact=False).click()
        page.get_by_role('button', name='Check', exact=True).click()
        expect(summary).to_contain_text(re.compile(r'2\s*not public yet'))
        expect(summary).to_contain_text(re.compile(r'1\s*wrong'))
        page.locator('[data-mark-id="c8"]').click()
        expect(page.locator('#receipt-panel')).to_contain_text('Not public until')
        capture('08-time-cutoff')
        capture('09-time-receipt', page.locator('#receipt-panel'))
        page.get_by_role('button', name='Now', exact=True).click()
        page.get_by_role('button', name='Check', exact=True).click()
        expect(summary).to_contain_text(re.compile(r'8\s*verified'))
        capture('10-time-now')
        page.get_by_role('button', name='Edit draft', exact=True).click()
        page.get_by_role('combobox').click()
        page.get_by_role('option', name='NVIDIA Q2 FY2027 earnings story', exact=False).click()
        page.get_by_role('button', name='Check', exact=True).click()
        expect(summary).to_contain_text(re.compile(r'7\s*verified'))
        page.locator('[data-mark-id="c10"]').click()
        capture('11-guidance', page.locator('#receipt-panel'))
        context.close()
        browser.close()
    assert not errors, errors
    provenance = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  'source_worktree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)),
                  'source_url': args.url,
                  'product_brand': 'NoCap',
                  'capture_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'new_model_spend_usd': 0, 'provider_calls_forbidden': True,
                  'check_mode': 'real offline verification replay using bundled extraction caches',
                  'fix_mode': 'recorded successful live response from 2026-10-27',
                  'fix_record_sha256': hashlib.sha256(fix_path.read_bytes()).hexdigest(),
                  'screenshot_sha256': {name: hashlib.sha256((args.output / f'{name}.png').read_bytes()).hexdigest() for name in captures},
                  'captures': captures, 'routed_requests': routed, 'browser_errors': errors}
    (args.output / 'capture-record.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(provenance, indent=2))


def capture_slide(args):
    from playwright.sync_api import expect, sync_playwright
    input_file = ROOT / 'examples/slides/nvda-draft.png'
    response_file = ROOT / 'data/eval_runs/slide_live/nvda-draft-events.json'
    provenance = slide_provenance()
    events = json.loads(response_file.read_text())
    result = next(data for name, data in events if name == 'done')
    assert result['counts'] == {'verified': 2, 'wrong': 2, 'unverifiable': 0, 'approximate': 0}
    body = ''.join(f'event: {name}\ndata: {json.dumps(data)}\n\n' for name, data in events)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.chromium)
        page = browser.new_page(viewport={'width': 1440, 'height': 1050}, reduced_motion='reduce', color_scheme='light')
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            request = route.request
            if request.method == 'GET':
                route.continue_()  # Local health and examples cannot call a model.
            elif urlparse(request.url).path == '/api/slide':
                assert request.post_data_json['context'] == 'NVIDIA, Q2 FY2027'
                assert request.post_data_json['as_of'] is None
                assert base64.b64decode(request.post_data_json['image_data_url'].split(',', 1)[1]) == input_file.read_bytes()
                route.fulfill(status=200, content_type='text/event-stream', body=body)
            else:
                errors.append('Unexpected model-capable request refused')
                route.abort()

        page.route('**/api/**', intercept)
        page.goto(args.url)
        page.wait_for_load_state('networkidle')
        expect(page).to_have_title(re.compile(r'^NoCap\b'))
        page.get_by_role('tab', name='Slide', exact=True).click()
        page.locator('#slide-file').set_input_files(str(input_file))
        page.locator('#slide-context').fill('NVIDIA, Q2 FY2027')
        page.get_by_role('button', name='Check slide', exact=True).click()
        expect(page.get_by_text('4 extracted readings checked', exact=True)).to_be_visible()
        page.get_by_role('checkbox').check()
        page.locator('[data-slide-claim="c2"]').click()
        expect(page.locator('#slide-receipt-title')).to_be_visible()
        page.wait_for_timeout(250)
        page.screenshot(path=str(args.output / '12-slide.png'), full_page=True)
        browser.close()
    assert not errors, errors
    record = {'new_model_spend_usd': 0, 'model_post_requests_intercepted': True,
              'source_url': args.url,
              'product_brand': 'NoCap',
              'mode': 'actual NoCap UI; branding-only display image; recorded response for the original live input',
              'browser_errors': errors, **provenance,
              'response_sha256': hashlib.sha256(response_file.read_bytes()).hexdigest()}
    (args.output / 'slide-capture-record.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
