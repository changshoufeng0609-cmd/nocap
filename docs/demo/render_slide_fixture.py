#!/usr/bin/env python3
"""Render the NoCap branding refresh; validate financial pixels against the original live input."""
import hashlib
import io
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[2]
source = root / 'examples/slides/nvda-draft.html'
output = source.with_suffix('.png')
provenance = json.loads((root / 'docs/demo/slide-replay-provenance.json').read_text())
replacement = provenance['allowed_html_substitution']
assert hashlib.sha256(source.read_text().replace(replacement['after'], replacement['before']).encode()).hexdigest() == provenance['original_html_sha256']
original = subprocess.check_output(['git', 'show', f"{provenance['original_live_input_git_commit']}:{provenance['original_live_input_path']}"], cwd=root)
assert hashlib.sha256(original).hexdigest() == provenance['original_live_input_sha256']
chrome = Path.home() / 'Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(chrome))
    page = browser.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=1)
    page.goto(source.as_uri())
    page.wait_for_load_state('networkidle')
    page.screenshot(path=str(output))
    browser.close()
assert output.stat().st_size < 2 * 1024 * 1024
before, after = Image.open(io.BytesIO(original)).convert('RGB'), Image.open(output).convert('RGB')
assert before.size == after.size == (1440, 900)
assert ImageChops.difference(before.crop((0, 0, 1440, 700)), after.crop((0, 0, 1440, 700))).getbbox() is None, 'Financial content or layout changed'
changed = ImageChops.difference(before, after).getbbox()
assert changed and changed[0] >= 1000 and changed[1] >= 700 and changed[3] < 780, changed
record = {**provenance, 'branded_display_input_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
          'changed_pixel_bounds': list(changed), 'financial_pixels_unchanged': True,
          'new_model_spend_usd': 0}
record_path = root / 'data/eval_runs/demo_capture/fixture-brand-refresh.json'
record_path.parent.mkdir(parents=True, exist_ok=True)
record_path.write_text(json.dumps(record, indent=2) + '\n')
print(f'{output.relative_to(root)}: {output.stat().st_size} bytes')
