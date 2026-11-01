#!/usr/bin/env python3
"""Edit the v2.1 walkthrough and its X cut from the v2 browser capture: camera moves, cards, captions, narration, music.

v2.1 is a re-edit of the October 30 v2 capture; nothing is re-recorded. Product pixels come only from the browser
master recorded by capture_walkthrough_v2.py. The renderer crops and scales that footage and adds clearly separate
edit-layer elements, each listed in the manifest under `edit_overlays`:
- short scene labels in a corner;
- on the real SEC slide, an outline and spotlight around the two regions the product labels "Bar Chart · SAM";
- full-frame text cards: the cold-open line, the disclosure, the four-backends card, results and the end card.
Narration is generated locally with Kokoro-82M from narration_v2.py; the music bed is synthesized locally by
music_bed.py. Nothing leaves the machine; the capture folder is only read.

  --cut main   the walkthrough (about 2:40; a recorded Private-check shot is added with --private-take)
  --cut x      a 45-60 s cut for X that opens on a red ✗
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import music_bed  # noqa: E402
from narration_v2 import CUES, OPTIONAL, SPEED, VERSION, VOICE, X_CUES  # noqa: E402
from render_demo import local_narrator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TTS = ROOT / 'data/eval_runs/narration-replacement-20261029'
VOICES_SHA256 = 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'
MODEL_SHA256 = 'beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a'
W, H, PRODUCT_H, BAND_H, FPS = 1920, 1080, 960, 120, 25
VIEW_W, VIEW_H, SCALE = 1280, 640, 2            # CSS viewport recorded at 2x
MAX_ZOOM = 2.4
DARK, PANEL, LINE = (15, 27, 22), (23, 38, 31), (44, 62, 53)
CREAM, MUTED, GREEN, RED, BLUE = (243, 239, 230), (163, 178, 168), (86, 196, 138), (240, 106, 92), (139, 170, 255)
AMBER = (255, 190, 70)                          # edit-layer highlight; not a product verdict color in these scenes
LIVE_URL, REPO_URL = 'receipts-nocap.vercel.app', 'github.com/changshoufeng0609-cmd/nocap'
OUTPUT = {'main': 'nocap-walkthrough-v2.1', 'x': 'nocap-walkthrough-x'}
DURATION_LIMITS = {'main': (150.0, 179.5), 'x': (45.0, 60.0)}
BED_LUFS = -26.0                                # music bed level in the mix (integrated, ducked under narration)
LIMIT_DBFS = -2.5                               # sample-peak ceiling before the final linear loudness pass
DIM = 0.45                                      # spotlight: darkening outside the SAM regions
SAM_LABEL = 'SAM 3.1 · two bar-chart regions'


# --------------------------------------------------------------------------- fonts (the product's own web fonts)
def _font_files():
    found = {}
    for path in sorted(glob.glob(str(ROOT / 'web/.next/static/media/*.p.*.woff2'))) + sorted(glob.glob(str(ROOT / 'web/.next/static/media/*.woff2'))):
        family, style = ImageFont.truetype(path, 10).getname()
        found.setdefault((family, style), path)
    return found


FONT_FILES = _font_files()


def font(kind, size, weight=400, opsz=None):
    family = {'sans': ('Geist', 'Regular'), 'mono': ('Geist Mono', 'Regular'), 'serif': ('Newsreader 16pt', 'Regular'),
              'serif-italic': ('Newsreader 16pt', 'Italic')}[kind]
    path = FONT_FILES.get(family)
    assert path, f'Build the web app first: {family} font not found in web/.next/static/media'
    f = ImageFont.truetype(path, size)
    axes = f.get_variation_axes()
    values = [weight if axis['name'] == b'Weight' else (opsz or min(72, max(6, size // 2))) for axis in axes]
    f.set_variation_by_axes(values)
    return f


# --------------------------------------------------------------------------- drawing helpers (2x supersampled)
class Canvas:
    def __init__(self, size=(W, PRODUCT_H), background=DARK, ss=2):
        self.ss = ss
        self.image = Image.new('RGBA', (size[0] * ss, size[1] * ss), background + (255,) if len(background) == 3 else background)
        self.draw = ImageDraw.Draw(self.image)

    def f(self, kind, size, weight=400, opsz=None):
        return font(kind, size * self.ss, weight, opsz)

    def text(self, xy, text, kind='sans', size=32, weight=400, fill=CREAM, anchor='la', opsz=None, spacing=0):
        fnt = self.f(kind, size, weight, opsz)
        x, y = xy
        if spacing:
            # letter-spaced caps labels
            cursor = x * self.ss
            if anchor[0] == 'm':
                total = sum(self.draw.textlength(ch, font=fnt) for ch in text) + spacing * self.ss * (len(text) - 1)
                cursor -= total / 2
            for ch in text:
                self.draw.text((cursor, y * self.ss), ch, font=fnt, fill=fill, anchor='l' + anchor[1])
                cursor += self.draw.textlength(ch, font=fnt) + spacing * self.ss
            return
        self.draw.text((x * self.ss, y * self.ss), text, font=fnt, fill=fill, anchor=anchor)

    def width(self, text, kind='sans', size=32, weight=400, opsz=None):
        return self.draw.textlength(text, font=self.f(kind, size, weight, opsz)) / self.ss

    def wrap(self, text, width, kind='sans', size=32, weight=400):
        lines, line = [], ''
        for word in text.split():
            candidate = f'{line} {word}'.strip()
            if self.width(candidate, kind, size, weight) > width and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        return lines + ([line] if line else [])

    def rect(self, box, radius=0, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = (v * self.ss for v in box)
        self.draw.rounded_rectangle((x0, y0, x1, y1), radius=radius * self.ss, fill=fill, outline=outline, width=width * self.ss)

    def line(self, points, fill, width):
        self.draw.line([(x * self.ss, y * self.ss) for x, y in points], fill=fill, width=width * self.ss, joint='curve')

    def arrow(self, start, end, fill, width=3, head=12):
        (x0, y0), (x1, y1) = start, end
        self.line([start, end], fill, width)
        length = max(1e-6, ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        self.line([(x1 - head * ux - head * .6 * uy, y1 - head * uy + head * .6 * ux), (x1, y1),
                   (x1 - head * ux + head * .6 * uy, y1 - head * uy - head * .6 * ux)], fill, width)

    def check(self, center, size, color, width=6):
        cx, cy = center
        self.line([(cx - size * .42, cy + size * .02), (cx - size * .12, cy + size * .3), (cx + size * .42, cy - size * .3)], color, width)

    def cross(self, center, size, color, width=6):
        cx, cy = center
        self.line([(cx - size * .3, cy - size * .3), (cx + size * .3, cy + size * .3)], color, width)
        self.line([(cx - size * .3, cy + size * .3), (cx + size * .3, cy - size * .3)], color, width)

    def logo(self, xy, size):
        """The NoCap app icon (web/app/icon.svg): a receipt with a green check on a dark tile."""
        x, y = xy
        k = size / 32
        self.rect((x, y, x + size, y + size), radius=7 * k, fill=(31, 29, 26), outline=(70, 66, 60), width=1)
        receipt = [(9, 6.5), (23, 6.5), (23, 25.5), (20.67, 23.9), (18.33, 25.5), (16, 23.9), (13.67, 25.5), (11.33, 23.9), (9, 25.5)]
        self.draw.polygon([((x + px * k) * self.ss, (y + py * k) * self.ss) for px, py in receipt], fill=(251, 250, 247))
        self.line([(x + 12.3 * k, y + 15.6 * k), (x + 14.9 * k, y + 18.2 * k), (x + 19.7 * k, y + 13.0 * k)], (26, 127, 78), max(1, round(2.3 * k)))

    def result(self):
        image = self.image.resize((self.image.width // self.ss, self.image.height // self.ss), Image.LANCZOS)
        return image


# --------------------------------------------------------------------------- cards
def card_stakes():
    c = Canvas()
    c.text((W / 2, 392), 'One wrong number.', 'serif', 118, 500, CREAM, 'ms', 72)
    c.text((W / 2, 540), 'One public correction.', 'serif-italic', 118, 500, RED, 'ms', 72)
    c.rect((W / 2 - 90, 610, W / 2 + 90, 614), fill=LINE)
    c.text((W / 2, 676), 'Financial journalists and analysts check every figure before it runs.', 'sans', 32, 400, MUTED, 'ms')
    return c.result()


def card_title(private=False):
    c = Canvas()
    title_w = c.width('NoCap', 'serif', 128, 600, 72)
    x0 = (W - (title_w + 132)) / 2
    c.logo((x0, 136), 104)
    c.text((x0 + 132, 234), 'NoCap', 'serif', 128, 600, CREAM, 'ls', 72)
    c.text((W / 2, 336), 'Checks every financial number in a draft against SEC filings.', 'sans', 42, 400, CREAM, 'ms')
    c.rect((300, 400, W - 300, 872), radius=22, fill=PANEL, outline=LINE, width=2)
    c.text((W / 2, 456), 'HOW THIS VIDEO WAS MADE', 'sans', 22, 600, GREEN, 'ms', spacing=3)
    replayed = 'Fix, Slide, Ask and Private: recorded live runs replayed' if private else 'Fix, Slide and Ask: recorded live runs replayed, waits shortened'
    # The Private take was recorded on Oct 31 (Taipei time); the rest of the footage on Oct 30.
    items = [f'The real NoCap interface, recorded locally on {"Oct 30–31" if private else "Oct 30"}, 2026',
             'Check: cached model output for saved examples',
             replayed,
             'The “Live checks are open” notice shows the judging-window setting',
             'Voice: synthetic (Kokoro-82M), not a person',
             'Labels, highlights and original music added in editing']
    for i, text in enumerate(items):
        y = 524 + i * 56
        assert c.width(text, 'sans', 32, 400) <= W - 300 - 40 - 456, text
        c.rect((420, y - 17, 432, y - 5), radius=6, fill=GREEN)
        c.text((456, y), text, 'sans', 32, 400, CREAM, 'ls')
    return c.result()


def card_title_x():
    c = Canvas()
    title_w = c.width('NoCap', 'serif', 150, 600, 72)
    x0 = (W - (title_w + 150)) / 2
    c.logo((x0, 250), 120)
    c.text((x0 + 150, 362), 'NoCap', 'serif', 150, 600, CREAM, 'ls', 72)
    c.text((W / 2, 492), 'Checks every financial number in a draft against SEC filings.', 'sans', 46, 400, CREAM, 'ms')
    c.rect((W / 2 - 90, 548, W / 2 + 90, 552), fill=LINE)
    c.text((W / 2, 626), 'Built with Meta’s Muse Spark, Muse Wonder and SAM 3.1', 'sans', 36, 400, MUTED, 'ms')
    c.text((W / 2, 716), 'The real interface · recorded runs replayed · synthetic voice', 'sans', 28, 400, MUTED, 'ms')
    return c.result()


BACKENDS = [('Muse Spark 1.3', 'Meta Model API', 'Default extractor', CREAM),
            ('Muse Spark Open', 'Fireworks', '27/27 gold claims found', GREEN),
            ('Muse Spark Open', 'Baseten', '27/27 gold claims found', GREEN),
            ('Muse Glimmer', 'our own Modal H100', 'Private check: unpublished drafts', CREAM)]


def card_backends():
    c = Canvas()
    c.text((150, 118), 'OPEN WEIGHTS AND HOSTED MODELS', 'sans', 22, 600, GREEN, 'ls', spacing=3)
    c.text((150, 196), 'Same verifier, four backends', 'serif', 64, 500, CREAM, 'ls', 60)
    row_x0, row_x1, verifier = 150, 1210, (1330, 300, 1770, 690)
    for i, (model, host, result, color) in enumerate(BACKENDS):
        y0 = 248 + i * 104
        c.rect((row_x0, y0, row_x1, y0 + 88), radius=16, fill=PANEL, outline=LINE, width=2)
        c.text((row_x0 + 34, y0 + 40), model, 'sans', 34, 600, CREAM, 'ls')
        c.text((row_x0 + 34, y0 + 74), host, 'sans', 26, 400, MUTED, 'ls')
        c.text((row_x1 - 34, y0 + 56), result, 'sans', 32, 600, color, 'rs')
        c.arrow((row_x1 + 8, y0 + 44), (verifier[0] - 10, (verifier[1] + verifier[3]) / 2 + (i - 1.5) * 46), MUTED, 3, 13)
    vx0, vy0, vx1, vy1 = verifier
    c.rect(verifier, radius=22, fill=(14, 60, 40), outline=GREEN, width=3)
    c.check(((vx0 + vx1) / 2, vy0 + 110), 70, GREEN, 9)
    c.text(((vx0 + vx1) / 2, vy0 + 232), 'Same code', 'serif', 50, 600, CREAM, 'ms', 60)
    c.text(((vx0 + vx1) / 2, vy0 + 290), 'verifier', 'serif', 50, 600, CREAM, 'ms', 60)
    c.text(((vx0 + vx1) / 2, vy0 + 346), 'checks every claim', 'sans', 26, 400, MUTED, 'ms')
    c.text(((vx0 + vx1) / 2, vy0 + 380), 'against SEC filings', 'sans', 26, 400, MUTED, 'ms')
    c.rect((150, 712, W - 150, 812), radius=18, fill=PANEL, outline=LINE, width=2)
    c.text((190, 776), '200 closed-book answers from four Meta models:', 'sans', 34, 500, CREAM, 'ls')
    c.text((W - 190, 776), 'the gate let 0 wrong numbers through', 'sans', 34, 600, GREEN, 'rs')
    notes = ('Gold set: 27 claims in 10 NVIDIA passages; on each host one extra claim stayed ?. Q&A: a development benchmark '
             '(held-out split not used), post-run analysis; the gate accepted 0–34% of each model’s answers.')
    for j, line in enumerate(c.wrap(notes, W - 300, 'sans', 24)):
        c.text((150, 866 + j * 34), line, 'sans', 24, 400, MUTED, 'ls')
    return c.result()


def card_results():
    c = Canvas()
    c.text((180, 132), 'RESULTS · README.md', 'sans', 24, 600, GREEN, 'ls', spacing=3)
    c.text((180, 214), 'Measured on held-out tests', 'serif', 64, 500, CREAM, 'ls', 60)
    rows = [('44/48', GREEN, 'planted errors caught', 'real earnings-release sentences; the 4 misses came back ?, never ✓'),
            ('1/47', CREAM, 'false alarm on unmodified sentences', 'same set; 22 of 110 true figures left ? instead of guessed'),
            ('20/20', GREEN, 'planted errors caught in trap drafts', 'seven earnings stories written by Muse Wonder; 0 of 52 true figures flagged')]
    for i, (big, color, main, sub) in enumerate(rows):
        y = 340 + i * 148
        c.rect((180, y - 62, W - 180, y + 70), radius=18, fill=PANEL, outline=LINE, width=2)
        c.text((230, y + 34), big, 'serif', 96, 600, color, 'ls', 72)
        c.text((640, y - 6), main, 'sans', 38, 500, CREAM, 'ls')
        if sub.endswith(' ✓'):                      # the web fonts have no ✓ glyph, so draw the mark as a vector
            sub = sub[:-2]
            c.check((640 + c.width(sub + ' ', 'sans', 28, 400) + 11, y + 32), 24, GREEN, 3)
        c.text((640, y + 42), sub, 'sans', 28, 400, MUTED, 'ls')
        tag_w = c.width('HELD-OUT', 'sans', 20, 600) + 3 * 7 + 40
        c.rect((W - 220 - tag_w, y - 44, W - 220, y - 8), radius=18, fill=(14, 60, 40), outline=GREEN, width=2)
        c.text((W - 220 - tag_w / 2, y - 19), 'HELD-OUT', 'sans', 20, 600, GREEN, 'ms', spacing=3)
    notes = ('Frozen source 61e1055, run once on Oct 29 and not rerun after later fixes. Small, correlated sets; '
             'labels are machine-assisted, with no independent human review yet.')
    for j, line in enumerate(c.wrap(notes, W - 360, 'sans', 24)):
        c.text((180, 806 + j * 34), line, 'sans', 24, 400, MUTED, 'ls')
    return c.result()


# Added to both end cards on Oct 31: the footage was recorded on Oct 30, when NoCap covered fewer companies; on Oct 31
# the evidence grew to 499 of the 500 S&P 500 companies (index members that day), and on Nov 1 Exxon Mobil joined, so
# the card states current coverage. Oct 31 export: "Now covering 499 S&P 500 companies".
COVERAGE = 'Now covering all 500 S&P 500 companies'
# End-card baselines relative to the logo's top edge. The X card also carries two disclosure lines, so it starts higher
# and its lower block is a little tighter; no text size changed when the coverage line was added.
END_LAYOUT = {False: {'top': 84, 'coverage': 268, 'url': 390, 'repo': 584, 'rule': 634, 'models': 702, 'credit': 766},
              True: {'top': 56, 'coverage': 266, 'url': 382, 'repo': 566, 'rule': 612, 'models': 674, 'credit': 734,
                     'disclosure': 806}}


def card_end(x=False):
    c = Canvas()
    L = END_LAYOUT[x]
    top = L['top']
    title_w = c.width('NoCap', 'serif', 120, 600, 72)
    x0 = (W - (title_w + 124)) / 2
    c.logo((x0, top), 98)
    c.text((x0 + 124, top + 92), 'NoCap', 'serif', 120, 600, CREAM, 'ls', 72)
    c.text((W / 2, top + 182), 'Every financial number, with a receipt from the filing.', 'sans', 36, 400, MUTED, 'ms')
    c.text((W / 2, top + L['coverage']), COVERAGE, 'sans', 46, 600, GREEN, 'ms')
    url = top + L['url']
    c.text((W / 2, url), LIVE_URL, 'sans', 72, 600, CREAM, 'ms')
    pill = 'Live checks open through Nov 17'
    pw = c.width(pill, 'sans', 32, 600) + 70
    c.rect((W / 2 - pw / 2, url + 38, W / 2 + pw / 2, url + 98), radius=30, fill=(14, 60, 40), outline=GREEN, width=2)
    c.text((W / 2, url + 80), pill, 'sans', 32, 600, CREAM, 'ms')
    c.text((W / 2, top + L['repo']), REPO_URL, 'mono', 40, 500, CREAM, 'ms')
    c.rect((W / 2 - 90, top + L['rule'], W / 2 + 90, top + L['rule'] + 3), fill=LINE)
    c.text((W / 2, top + L['models']), 'Muse Spark 1.3 · Muse Wonder 1.0 · SAM 3.1 · Muse Spark Open · Muse Glimmer', 'sans', 30, 400, MUTED, 'ms')
    c.text((W / 2, top + L['credit']), 'Built during the Meta Global AI Developer Hackathon · Robert Chang · Team NoCap', 'sans', 30, 400, CREAM, 'ms')
    if x:
        d = top + L['disclosure']
        c.text((W / 2, d), 'The real interface, recorded locally: saved examples and recorded live runs replayed.', 'sans', 26, 400, MUTED, 'ms')
        c.text((W / 2, d + 38), 'Synthetic voice (Kokoro-82M); labels, highlights and original music added in editing.', 'sans', 26, 400, MUTED, 'ms')
    return c.result()


def cards_for(cut, private):
    if cut == 'x':
        return {'title-x': card_title_x, 'end-x': lambda: card_end(True)}
    return {'stakes': card_stakes, 'title': lambda: card_title(private), 'backends': card_backends,
            'results': card_results, 'end': card_end}


def chip_image(text, dot=GREEN, scale=1.0):
    size = round(27 * scale)
    height = round(64 * scale)
    c = Canvas((1400, height + 12), (0, 0, 0, 0))
    width = c.width(text, 'sans', size, 500) + 76 * scale
    c.rect((0, 0, width, height), radius=height / 2, fill=(15, 27, 22, 228), outline=(86, 196, 138, 255), width=2)
    d = 12 * scale
    c.rect((26 * scale, height / 2 - d / 2, 26 * scale + d, height / 2 + d / 2), radius=d / 2, fill=dot + (255,))
    c.text((52 * scale, height / 2 + 10 * scale), text, 'sans', size, 500, CREAM + (255,), 'ls')
    image = c.result()
    return image.crop((0, 0, int(width) + 4, height + 4))


def caption_band(text, size=34):
    c = Canvas((W, BAND_H), DARK)
    if text:
        lines = c.wrap(text, 1760 if size <= 34 else 1820, 'sans', size, 500)
        assert len(lines) <= 2, text
        if size <= 34:
            y, step = (52 if len(lines) == 1 else 34), 44
            for line in lines:
                c.text((W / 2, y + 12), line, 'sans', size, 500, CREAM, 'ms')
                y += step
        else:
            baselines = [74] if len(lines) == 1 else [48, 100]
            for line, baseline in zip(lines, baselines):
                c.text((W / 2, baseline), line, 'sans', size, 500, CREAM, 'ms')
    return np.asarray(c.result().convert('RGB'))


# --------------------------------------------------------------------------- narration
def synthesize(work, cache, cue_ids):
    """Kokoro speech per cue. Identical (voice, speed, text) audio is reused from `cache` (the v2 render folder).
    The pinned model files are hash-checked before the first line that has to be synthesized; reused lines are
    identified by their own SHA-256 in the manifest, so a render that reuses every line does not need the model."""
    voices = TTS / 'voices-v1.0.bin'
    narrator, speech = None, {}
    for cue_id, _, caption, spoken in CUES:
        if cue_id not in cue_ids:
            continue
        key = hashlib.sha256(f'{VOICE}|{SPEED}|{spoken}'.encode()).hexdigest()[:12]
        found = sorted(work.glob(f'voice-*-{key}.wav')) + (sorted(cache.glob(f'voice-*-{key}.wav')) if cache and cache.exists() else [])
        origin = 'reused from the v2 render' if found and found[0].parent != work else 'synthesized for v2.1'
        if found:
            path = found[0]
        else:
            path = work / f'voice-{cue_id}-{key}.wav'
            if narrator is None:
                assert hashlib.sha256(voices.read_bytes()).hexdigest() == VOICES_SHA256
                assert hashlib.sha256((TTS / 'kokoro-v1.0.onnx').read_bytes()).hexdigest() == MODEL_SHA256
                narrator = local_narrator(TTS / 'kokoro-v1.0.onnx', voices)
            phonemes = narrator.tokenizer.phonemize(spoken, lang='en-us')
            samples, rate = narrator.create(phonemes, voice=VOICE, speed=SPEED, lang='en-us', is_phonemes=True)
            partial = path.with_name(path.name + '.part')       # renamed only once complete (a full disk once left an 80-byte file)
            sf.write(partial, samples, rate, subtype='FLOAT', format='WAV')
            partial.replace(path)
            (work / f'phonemes-{cue_id}.txt').write_text(phonemes)
        samples, rate = sf.read(path, dtype='float32')
        assert rate == 24000 and len(samples) > rate // 2, f'{path} is empty or truncated; delete it and re-run'
        # Kokoro pads each utterance with near-silence; trim it so cue timing follows the speech itself.
        loud = np.flatnonzero(np.abs(samples) > 0.01)
        start, end = max(0, loud[0] - 240), min(len(samples), loud[-1] + 960)
        speech[cue_id] = {'samples': samples[start:end], 'seconds': (end - start) / rate, 'path': path, 'origin': origin,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'caption': caption, 'spoken': spoken}
    return speech


# --------------------------------------------------------------------------- camera framing
def frame(boxes, names, pad=22, zoom=None, center=None, min_w=None):
    """A 2:1 crop (x, y, w, h in CSS pixels) around the union of named boxes, clamped to the viewport."""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for name in names:
        x, y, w, h = boxes[name]
        xs0.append(x); ys0.append(y); xs1.append(x + w); ys1.append(y + h)
    x0, y0, x1, y1 = min(xs0) - pad, min(ys0) - pad, max(xs1) + pad, max(ys1) + pad
    cx, cy = center or ((x0 + x1) / 2, (y0 + y1) / 2)
    width = max(x1 - x0, (y1 - y0) * 2, VIEW_W / MAX_ZOOM, min_w or 0)
    if zoom:
        width = VIEW_W / zoom
    width = min(width, VIEW_W)
    height = width / 2
    x = min(max(cx - width / 2, 0), VIEW_W - width)
    y = min(max(cy - height / 2, 0), VIEW_H - height)
    return (x, y, width, height)


WIDE = (0, 0, VIEW_W, VIEW_H)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def camera_at(keys, t):
    """keys: [(start_s, rect, move_s)]; each key moves from the previous rect over move_s seconds."""
    rect = keys[0][1]
    for start, target, move in keys[1:]:
        if t <= start:
            break
        k = ease((t - start) / move) if move > 0 else 1.0
        rect = tuple(a + (b - a) * k for a, b in zip(rect, target))
    return rect


# --------------------------------------------------------------------------- timeline
class Timeline:
    def __init__(self, marks, actions, speech):
        self.M, self.actions, self.speech = marks, actions, speech
        self.shots, self.said = [], {}

    def say(self, cue, at):
        self.said[cue] = at
        return at + self.speech[cue]['seconds']

    def end_of(self, cue):
        return self.said[cue] + self.speech[cue]['seconds']

    def footage(self, name, src, start, end, cams, limit=None, label=None, fade=0.0, corner='top-left', master=None, overlays=None):
        labels = [{'text': label, 'corner': corner, 'start': 0.0, 'end': 4.0, 'dot': GREEN}] if label else []
        shot = {'kind': 'footage', 'name': name, 'src': src, 'start': start, 'end': end, 'cams': cams, 'fade': fade,
                'labels': labels, 'overlays': overlays or [], 'master': master}
        if limit is not None:
            assert src + (end - start) <= limit + 1e-6, (name, round(src + (end - start), 3), round(limit, 3))
        self.shots.append(shot)
        return shot

    def card(self, name, start, end, fade=0.0):
        self.shots.append({'kind': 'card', 'name': name, 'start': start, 'end': end, 'fade': fade})

    def action_time(self, kind, target, after):
        return next(a['t'] for a in self.actions if a['action'] == kind and a['target'].startswith(target) and a['t'] >= after)


def sam_highlight(shot, marks, start, end):
    """Edit-layer outline and spotlight on the regions the product labels 'Bar Chart · SAM'."""
    m = marks['slide-regions']
    names = [f'region-{i}' for i, label in enumerate(m['region_labels']) if label.endswith('SAM')]
    assert len(names) == 2, m['region_labels']
    shot['overlays'].append({'kind': 'sam-highlight', 'start': start, 'end': end, 'regions': names,
                             'boxes_css': [m['boxes'][n] for n in names], 'region_labels': [m['region_labels'][int(n[-1])] for n in names],
                             'boxes_from_mark': 'slide-regions', 'page_static_from_src_s': m['t'] - .3})
    shot['labels'].append({'text': SAM_LABEL, 'corner': 'bottom-left', 'start': start + .25, 'end': end, 'dot': AMBER})
    return names


def build_main(marks, actions, speech, private=None):
    """The walkthrough. Each shot is long enough for its narration plus a short hold."""
    T = Timeline(marks, actions, speech)
    M, say, end_of, footage, card = T.M, T.say, T.end_of, T.footage, T.card
    # 1. Cold open (about 11 s): the stakes, then both planted errors with their filed values.
    t = say('c01', .4) + .45
    card('stakes', 0.0, t)
    m, s = M['cold-both'], t - .45
    b = m['boxes']
    column = b['draft'][0] + b['draft'][2] / 2
    chip1 = frame(b, ['c2'], center=(column, b['c2'][1] + 60), min_w=760)
    chip2 = frame(b, ['c8'], center=(column, b['c8'][1] - 60), min_w=760)
    t = say('c02', t + .1) + .7
    footage('cold', m['t'] + .3, s, t, [(0, chip1, 0), (T.said['c02'] - s + 1.6, chip2, 1.0)], limit=M['tiles']['t'] - 1.0, fade=.45)
    # 2. Title and disclosure card: five seconds.
    s = t - .4
    card('title', s, s + 5.0, fade=.4)
    say('c03', s + .25)
    t = s + 5.0
    # 3. Check a saved example: wide shot, click Check, verdict tiles, receipts. The label sits top-right so the
    #    Check button stays visible while the page scrolls to it.
    m0, m1, s = M['draft-initial'], M['check-done'], t - .4
    say('c04', t + .25)
    src = m0['t'] + .6
    t = s + (m1['t'] + 1.0 - src)
    button = frame(M['draft-scrolled']['boxes'], ['editor', 'check'], pad=30, min_w=1180)
    footage('check', src, s, t, [(0, WIDE, 0), (M['draft-scrolled']['t'] - src - .2, button, 1.2)], limit=M['check-settled']['t'],
            label='Muse Spark 1.3 extracts the claims · code decides each verdict', fade=.4, corner='top-right')
    t = max(t, end_of('c04') + .2)
    m, s = M['tiles'], t
    t = say('c05', s + .2) + .9
    footage('tiles', m['t'] + .1, s, t, [(0, frame(m['boxes'], ['summary'], pad=26, min_w=780), 0)], limit=M['receipt-growth']['t'] - 1.0)
    m, s = M['receipt-growth'], t
    b = m['boxes']
    line = frame(b, ['c2'], center=(M['cold-both']['boxes']['draft'][0] + M['cold-both']['boxes']['draft'][2] / 2, b['c2'][1] + 20), min_w=760)
    receipt = frame(b, ['receipt-dl', 'calc'], pad=18)
    say('c06', s + .2)
    t = say('c07', end_of('c06') + .35) + 1.6
    footage('receipt', m['t'] + .1, s, t, [(0, line, 0), (T.said['c07'] - s - .35, receipt, .9)], limit=M['receipt-sources']['t'] - 1.0)
    m, s = M['receipt-sources'], t
    b = m['boxes']
    t = say('c08', s + .2) + .9
    sources = frame(b, ['why'], pad=18, center=(b['why'][0] + b['why'][2] / 2 - 120, b['why'][1] + 150), min_w=740)
    footage('sources', m['t'] + .1, s, t, [(0, sources, 0)], limit=M['receipt-eps']['t'] - 2.0)
    m, s = M['receipt-eps'], t
    b = m['boxes']
    start_src = T.action_time('click-start', '$2.22', M['receipt-sources']['t']) + .35
    eps_line = frame(b, ['c8'], center=(M['cold-both']['boxes']['draft'][0] + M['cold-both']['boxes']['draft'][2] / 2, b['c8'][1] - 40), min_w=760)
    eps_receipt = frame(b, ['receipt-dl', 'why'], pad=18)
    t = say('c09', s + .25) + 1.4
    footage('eps', start_src, s, t, [(0, eps_line, 0), (T.said['c09'] - s + 2.3, eps_receipt, .9)], limit=M['fix-ready']['t'] - 1.3)
    # 4. Fix with receipts (the recorded October 29 response).
    m0, m1, s = M['fix-ready'], M['fix-result'], t - .35
    say('c10', t + .2)
    t = say('c11', max(end_of('c10') + .35, m1['t'] - m0['t'] + s + 1.0)) + 1.5
    card_box = frame(m0['boxes'], ['fix-card'], pad=24)
    b = m1['boxes']
    result = frame(b, ['counts'], center=(VIEW_W / 2, b['counts'][1] + 205), zoom=1.15)
    counts = frame(b, ['counts'], center=(b['counts'][0] + 300, b['counts'][1] + 22), zoom=1.9)
    footage('fix', m0['t'] - .2, s, t, [(0, card_box, 0), (m1['t'] - (m0['t'] - .2) - .9, result, .9),
                                        (T.said['c11'] - s - .3, counts, 1.0)],
            limit=M['fix-receipts']['t'] - 1.0, label='Muse Wonder 1.0 proposes · code constrains and rechecks', fade=.35)
    m, s = M['fix-receipts'], t
    t = say('c12', s + .2) + 1.3
    footage('fix-receipts', m['t'] + .1, s, t,
            [(0, frame(m['boxes'], ['receipts'], center=(m['boxes']['receipts'][0] + 480, m['boxes']['receipts'][1] + 160), zoom=1.3), 0)],
            limit=M['cutoff-loaded']['t'] - 6.0)
    # 5. Time travel.
    m, s = M['cutoff-loaded'], t - .35
    start_src = T.action_time('click-start', 'June-quarter roundup', M['fix-receipts']['t']) - 1.4
    end = max(say('c13', t + .2) + .3, s + (m['t'] - start_src) + 1.5)
    footage('cutoff', start_src, s, end, [(0, frame(m['boxes'], ['picker', 'asof'], pad=40, min_w=820), 0)], limit=m['t'] + 4.0,
            label='Time travel: availability follows EDGAR acceptance time', fade=.35)
    m, s = M['cutoff-tiles'], end
    say('c14', s + .2)
    t = s + 2.2
    footage('cutoff-tiles', m['t'] + .1, s, t, [(0, frame(m['boxes'], ['summary'], pad=26, min_w=780), 0)], limit=m['t'] + 3.5)
    m, s = M['cutoff-receipt'], t
    b = m['boxes']
    t = max(end_of('c14') + 1.8, s + 4.0)
    footage('cutoff-receipt', m['t'] - .1, s, t, [(0, frame(b, ['c8', 'c9'], pad=30, min_w=760), 0), (1.6, frame(b, ['header', 'lookahead', 'receipt-dl'], pad=16), .9)],
            limit=m['t'] + 9.0)
    # 6. A real SEC slide in one continuous take: the run summary, the real click on "Show chart/table regions
    #    (2 SAM, 2 Spark)", the page scrolling to the slide, the edit-layer highlight on SAM's two bar charts, then
    #    the GAAP bars. The page is static from the slide-regions mark until the cursor heads for reading 10.
    m0, m1, m2, s = M['slide-tab'], M['slide-summary'], M['slide-regions'], t - .35
    say('c15', t + .1)
    src = m0['t'] - .3
    say('c16', max(end_of('c15') + .2, s + (m1['t'] - src) + .3))
    toggled = T.action_time('click-done', 'Show chart/table regions', m1['t'])
    b = m2['boxes']
    status = frame(m1['boxes'], ['status'], pad=24, min_w=860)
    image = frame(b, ['image'], pad=6)
    gaap = frame(b, [f'reading-{i}' for i in range(1, 11)], pad=40, min_w=640)
    to_image = toggled - src + .35                  # the page starts scrolling to the slide 0.4 s after the click
    highlight = max(to_image + 1.1, m2['t'] - src) + .1
    say('c17', max(end_of('c16') + .35, s + highlight - .9))
    push = T.end_of('c17') - s + .15
    t = say('c18', T.end_of('c17') + .45) + 1.0
    limit = T.action_time('click-start', 'Reading 10', m2['t']) + .4
    shot = footage('slide', src, s, t, [(0, WIDE, 0), (m1['t'] - src - .1, status, .8), (to_image, image, 1.1), (push, gaap, 1.2)],
                   limit=limit, label='SAM 3.1 finds the chart regions · Muse Spark 1.3 reads the labels', fade=.35, corner='bottom-right')
    sam_highlight(shot, M, highlight, push + .5)
    m, s = M['slide-receipt'], t
    t = say('c19', s + .25) + 2.0
    panel = m['boxes']['panel']
    # Selecting a reading focuses its receipt title, which scrolls to the top of the viewport.
    footage('slide-receipt', m['t'] + .1, s, t, [(0, frame(m['boxes'], ['panel'], center=(panel[0] + panel[2] / 2 - 60, 205), zoom=1.75), 0)],
            limit=m['t'] + 4.5)
    # 7. Ask: exact quotes from the whole 10-K in one request.
    m0, m1, s = M['ask-mode'], M['ask-result'], t - .35
    src = m0['t'] - 1.4
    t = say('c20', t + .2) + .4
    footage('ask', src, s, t, [(0, frame(m0['boxes'], ['filing', 'filing-meta'], pad=40, min_w=820), 0)], limit=M['ask-question']['t'],
            label='Muse Spark 1.3 · one request reads the whole 10-K', fade=.35)
    s = t
    t = say('c21', s + .2) + 1.1
    usage = m1['boxes']['usage']
    whole = frame(m1['boxes'], ['filing', 'usage'], pad=26)
    footage('ask-result', m1['t'] + .1, s, t, [(0, whole, 0),
                                               (1.6, frame(m1['boxes'], ['usage'], center=(usage[0] + 290, usage[1] - 20), zoom=2.1), 1.0),
                                               (4.0, frame(m1['boxes'], ['usage'], center=(usage[0] + usage[2] - 290, usage[1] - 20), zoom=2.1), 1.5),
                                               (6.5, whole, 1.0)],
            limit=M['ask-quotes']['t'] - .9)
    m, s = M['ask-quotes'], t
    t = say('c22', s + .2) + 1.5
    footage('ask-quotes', m['t'] + .1, s, t, [(0, frame(m['boxes'], ['quote-1', 'quote-2'], pad=24), 0)], limit=m['t'] + 6.0)
    # 8. One card: the same verifier behind four extraction backends (replaces v2's benchmark page and Private card).
    s = t - .4
    t = say('c23', t + .2) + .7
    card('backends', s, t, fade=.4)
    # 9. Optional: the recorded Private check (Muse Glimmer on Modal), replayed by the product. The shot opens on the
    #    Private form card, which keeps the half-visible status line above it out of frame, then frames the result
    #    after the page has scrolled to it. The label sits top-right, clear of the draft field that fills on the click.
    if private:
        P = private['marks']
        s = t - .35
        src = P['private-start']['t'] - .2
        say('c24', t + .2)
        t = max(end_of('c24') + .8, s + 4.5)
        form = frame(P['private-start']['boxes'], ['form'], pad=12)
        result = frame(P['private-result']['boxes'], ['result'], pad=24)
        footage('private', src, s, t, [(0, form, 0), (P['private-result']['t'] - src - .3, result, .9)],
                limit=P['private-end']['t'], master=private['master'],
                label='Private check · Muse Glimmer on our own Modal GPU', fade=.35, corner='top-right')
    # 10. Results card (held-out only).
    s = t - .4
    t = say('c25', t + .1) + 1.4
    card('results', s, t, fade=.4)
    # 11. Close.
    s = t - .4
    t = say('c26', t + .15) + 2.4
    card('end', s, t, fade=.4)
    return T.shots, T.said, round(t * FPS) / FPS


def build_x(marks, actions, speech):
    """The X cut: a red ✗ from the first frame, the receipt, Fix, SAM on a real slide, the end card."""
    T = Timeline(marks, actions, speech)
    M, say, end_of, footage, card = T.M, T.say, T.end_of, T.footage, T.card
    m = M['cold-both']
    b = m['boxes']
    column = b['draft'][0] + b['draft'][2] / 2
    chip1 = frame(b, ['c2'], center=(column, b['c2'][1] + 60), min_w=760)
    chip2 = frame(b, ['c8'], center=(column, b['c8'][1] - 60), min_w=760)
    say('c01', .15)
    say('c02', end_of('c01') + .25)
    cold_end = M['tiles']['t'] - 1.0 - (m['t'] + .3)
    footage('cold', m['t'] + .3, 0.0, cold_end, [(0, chip1, 0), (T.said['c02'] + 1.0, chip2, 1.0)], limit=M['tiles']['t'] - 1.0)
    s = cold_end
    t = max(end_of('c02') + .5, s + 2.5)
    footage('tiles', M['tiles']['t'] + .1, s, t, [(0, frame(M['tiles']['boxes'], ['summary'], pad=26, min_w=780), 0)],
            limit=M['receipt-growth']['t'] - 1.0)
    s = t - .35
    say('c03', t + .1)
    t = end_of('c03') + .5
    card('title-x', s, t, fade=.35)
    m, s = M['receipt-growth'], t - .35
    b = m['boxes']
    line = frame(b, ['c2'], center=(M['cold-both']['boxes']['draft'][0] + M['cold-both']['boxes']['draft'][2] / 2, b['c2'][1] + 20), min_w=760)
    receipt = frame(b, ['receipt-dl', 'calc'], pad=18)
    say('c06', t + .1)
    t = say('c07', end_of('c06') + .3) + 1.0
    footage('receipt', m['t'] + .1, s, t, [(0, line, 0), (T.said['c07'] - s - .35, receipt, .9)], limit=M['receipt-sources']['t'] - 1.0, fade=.35)
    m0, m1, s = M['fix-ready'], M['fix-result'], t - .35
    say('c10', t + .15)
    t = say('c11', max(end_of('c10') + .3, m1['t'] - m0['t'] + s + 1.0)) + 1.0
    b = m1['boxes']
    footage('fix', m0['t'] - .2, s, t, [(0, frame(m0['boxes'], ['fix-card'], pad=24), 0),
                                        (m1['t'] - (m0['t'] - .2) - .9, frame(b, ['counts'], center=(VIEW_W / 2, b['counts'][1] + 205), zoom=1.15), .9),
                                        (T.said['c11'] - s - .3, frame(b, ['counts'], center=(b['counts'][0] + 300, b['counts'][1] + 22), zoom=1.9), 1.0)],
            limit=M['fix-receipts']['t'] - 1.0, label='Muse Wonder 1.0 proposes · code constrains and rechecks', fade=.35)
    m, s = M['slide-regions'], t - .35
    src = m['t'] + .05
    say('c17', t + .5)
    t = end_of('c17') + .8
    shot = footage('slide', src, s, t, [(0, frame(m['boxes'], ['image'], pad=6), 0)], limit=M['slide-receipt']['t'] - 2.0, fade=.35)
    sam_highlight(shot, M, .45, t - s + .5)
    s = t - .4
    t = say('c26', t + .15) + 2.0
    card('end-x', s, t, fade=.4)
    return T.shots, T.said, round(t * FPS) / FPS


# --------------------------------------------------------------------------- rendering
class Decoder:
    def __init__(self, master, start, frames):
        self.size = (VIEW_W * SCALE, VIEW_H * SCALE)
        self.frames = frames
        self.proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', f'{start:.4f}', '-i', str(master), '-frames:v', str(frames),
                                      '-vf', 'scale=in_color_matrix=bt709:in_range=tv', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                     stdout=subprocess.PIPE)
        self.last = None

    def read(self):
        need = self.size[0] * self.size[1] * 3
        data = self.proc.stdout.read(need)
        if len(data) == need:
            self.last = Image.frombuffer('RGB', self.size, data, 'raw', 'RGB', 0, 1)
        assert self.last is not None, 'master ended before the shot'
        return self.last

    def close(self):
        self.proc.stdout.close()
        self.proc.wait()


def perimeter(rect, progress):
    """Points along a rectangle's outline from its top-left corner, clockwise, up to `progress` (0-1)."""
    x0, y0, x1, y1 = rect
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    total = 2 * ((x1 - x0) + (y1 - y0))
    remaining = total * progress
    points = [corners[0]]
    for a, b in zip(corners, corners[1:]):
        seg = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if remaining >= seg:
            points.append(b)
            remaining -= seg
        else:
            k = remaining / seg if seg else 0
            points.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
            break
    return points


def draw_overlays(array, shot, local, cam):
    for overlay in shot['overlays']:
        if overlay['kind'] != 'sam-highlight':
            continue
        a = min(ease((local - overlay['start']) / .45), 1 - ease((local - (overlay['end'] - .5)) / .5))
        if a <= 0:
            continue
        x, y, w, h = cam
        sx, sy = W / w, PRODUCT_H / h
        rects = [((bx - x) * sx - 5, (by - y) * sy - 5, (bx + bw - x) * sx + 5, (by + bh - y) * sy + 5) for bx, by, bw, bh in overlay['boxes_css']]
        mask = Image.new('L', (W, PRODUCT_H), 0)
        md = ImageDraw.Draw(mask)
        for r in rects:
            md.rounded_rectangle(r, radius=10, fill=255)
        mask = np.asarray(mask.filter(ImageFilter.GaussianBlur(5)), dtype=np.float32)[..., None] / 255
        array *= 1 - DIM * a * (1 - mask)
        layer = Image.new('RGBA', (W, PRODUCT_H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for i, r in enumerate(rects):
            p = min(max((local - overlay['start'] - .15 - .35 * i) / .8, 0.0), 1.0)
            if p <= 0:
                continue
            points = perimeter(r, p)
            ld.line(points, fill=DARK + (235,), width=10, joint='curve')
            ld.line(points, fill=AMBER + (255,), width=5, joint='curve')
        lay = np.asarray(layer, dtype=np.float32)
        alpha = lay[..., 3:4] / 255 * a
        array[:] = array * (1 - alpha) + lay[..., :3] * alpha


def draw_labels(array, shot, local, chips):
    for label in shot['labels']:
        if not (label['start'] <= local < label['end']):
            continue
        alpha = min(ease((local - label['start']) / .35), 1 - ease((local - (label['end'] - .4)) / .35))
        if alpha <= 0:
            continue
        chip = chips[(label['text'], label['dot'])]
        ch, cw = chip.shape[:2]
        top = 28 if label['corner'].startswith('top') else PRODUCT_H - 28 - ch
        left = 32 if label['corner'].endswith('left') else W - 32 - cw
        region = array[top:top + ch, left:left + cw]
        a = chip[:, :, 3:4] * alpha
        region[:] = region * (1 - a) + chip[:, :, :3] * a


def render_shot_frame(shot, t, decoders, master, cards, chips):
    if shot['kind'] == 'card':
        return cards[shot['name']].copy()
    key = id(shot)
    if key not in decoders:
        frames = int(round((shot['end'] - shot['start']) * FPS)) + 2
        decoders[key] = Decoder(shot.get('master') or master, shot['src'], frames)
    image = decoders[key].read()
    local = t - shot['start']
    x, y, w, h = camera_at(shot['cams'], local)
    product = image.resize((W, PRODUCT_H), Image.LANCZOS, box=(x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE))
    array = np.asarray(product, dtype=np.float32).copy()
    draw_overlays(array, shot, local, (x, y, w, h))
    draw_labels(array, shot, local, chips)
    return array


def stamp(seconds, dot=False):
    ms = round(seconds * 1000)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{"." if dot else ","}{ms % 1000:03}'


# --------------------------------------------------------------------------- audio (in memory; ffmpeg through pipes)
def ffmpeg_pcm(args, samples, rate):
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    return subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-f', 'f32le', '-ar', str(rate), '-ac', str(channels), '-i', '-'] + args,
                          input=np.ascontiguousarray(samples, dtype='<f4').tobytes(), capture_output=True, check=True)


def loudness(samples, rate):
    report = ffmpeg_pcm(['-af', 'ebur128=peak=true', '-f', 'null', '-'], samples, rate).stderr.decode()
    summary = report[report.rfind('Summary:'):]
    return {'integrated_lufs': float(re.search(r'I:\s+(-?[\d.]+) LUFS', summary).group(1)),
            'loudness_range_lu': float(re.search(r'LRA:\s+(-?[\d.]+) LU', summary).group(1)),
            'true_peak_dbtp': float(re.search(r'Peak:\s+(-?[\d.]+) dBFS', summary).group(1))}


def loudnorm_json(text):
    return json.loads(text[text.rfind('{'):text.rfind('}') + 1])


def two_pass(target, samples, rate, tp=-1.5):
    first = loudnorm_json(ffmpeg_pcm(['-af', f'loudnorm=I={target}:TP={tp}:LRA=11:print_format=json', '-f', 'null', '-'], samples, rate).stderr.decode())
    second = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={first['input_i']}:measured_TP={first['input_tp']}:"
              f"measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}:offset={first['target_offset']}:linear=true:print_format=json")
    return first, second


def build_audio(said, speech, order, duration, work, out_name):
    rate = 24000
    voice = np.zeros(int(duration * rate) + rate, dtype=np.float32)
    for cue in order:
        samples = speech[cue]['samples']
        offset = int(round(said[cue] * rate))
        voice[offset:offset + len(samples)] += samples
    voice = voice[:int(round(duration * rate))]
    # Narration: the v2 chain (two-pass loudnorm to -14 LUFS, -1.5 dBTP), upsampled to 48 kHz stereo. Speech peaks sit
    # about 22 dB above its loudness, so a linear gain to -14 LUFS would break the -1.5 dBTP ceiling and loudnorm uses
    # its dynamic mode instead, as it did for v2. The manifest records the mode; the final mix pass below must be linear.
    first, second = two_pass(-14, voice, rate)
    run = ffmpeg_pcm(['-af', second + ',aresample=48000,pan=stereo|c0=c0|c1=c0', '-f', 'f32le', '-ar', '48000', '-ac', '2', '-'], voice, rate)
    narration_pass = loudnorm_json(run.stderr.decode())
    n = int(round(duration * music_bed.SR))
    narration = np.frombuffer(run.stdout, dtype='<f4').reshape(-1, 2)[:n]
    narration = np.pad(narration, ((0, n - len(narration)), (0, 0)))
    narration_level = loudness(narration, music_bed.SR)
    # Music: an original bed, ducked under each narration line, set to BED_LUFS by measurement.
    bed, bed_info = music_bed.synthesize(duration)
    spans = [(said[c], said[c] + speech[c]['seconds']) for c in order]
    duck = music_bed.duck_gains(spans, duration)
    ducked = bed * duck[:, None]
    raw_level = loudness(ducked, music_bed.SR)['integrated_lufs']
    gain_db = BED_LUFS - raw_level
    bed_mix = ducked * np.float32(10 ** (gain_db / 20))
    bed_level = loudness(bed_mix, music_bed.SR)
    open_level = loudness(bed * np.float32(10 ** (gain_db / 20)), music_bed.SR)['integrated_lufs']
    mix = narration + bed_mix
    # Speech and music peaks can coincide above the -1.5 dBTP ceiling. A lookahead limiter catches those few peaks so the
    # final pass can be a plain linear gain; loudnorm's dynamic mode would instead lift the music in every pause.
    ceiling = 10 ** (LIMIT_DBFS / 20)
    over_s = float(np.count_nonzero(np.abs(mix).max(axis=1) > ceiling)) / music_bed.SR
    peak_before = float(20 * np.log10(np.abs(mix).max()))
    run = ffmpeg_pcm(['-af', f'alimiter=limit={ceiling:.5f}:attack=5:release=50:level=false:latency=true',
                      '-f', 'f32le', '-ar', str(music_bed.SR), '-ac', '2', '-'], mix, music_bed.SR)
    limited = np.frombuffer(run.stdout, dtype='<f4').reshape(-1, 2)
    assert abs(len(limited) - n) <= 2, (len(limited), n)
    mix = np.pad(limited[:n], ((0, max(0, n - len(limited))), (0, 0)))
    # Final mix: two-pass loudnorm (linear) to -14 LUFS, then AAC; the video encode copies this stream.
    first_mix, second_mix = two_pass(-14, mix, music_bed.SR)
    m4a = work / f'{out_name}.m4a'
    run = ffmpeg_pcm(['-af', second_mix + ',aresample=48000', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-y', str(m4a)], mix, music_bed.SR)
    final = loudnorm_json(run.stderr.decode())
    assert final['normalization_type'] == 'linear', final
    applied = float(final['output_i']) - float(first_mix['input_i'])
    audio = {
        'narration': {'chain': ('two-pass loudnorm I=-14 LUFS, TP=-1.5 dBTP, as in v2 (linear requested; loudnorm applies its dynamic '
                                'mode when a linear gain would exceed the true-peak ceiling); 48 kHz stereo'),
                      'normalization_type': narration_pass['normalization_type'], 'loudnorm_first_pass': first,
                      'loudnorm_output': narration_pass, 'measured_after_normalization': narration_level},
        'music': {**bed_info, 'license': 'Original work made for this project (see narration-license.md)',
                  'duck': {**music_bed.DUCK, 'spans': len(spans)},
                  'target_lufs_in_mix': BED_LUFS, 'gain_db': round(gain_db, 2),
                  'measured_in_mix': bed_level, 'undocked_level_lufs': open_level,
                  'level_in_export_lufs': round(bed_level['integrated_lufs'] + applied, 2),
                  'pcm_sha256': hashlib.sha256(np.ascontiguousarray(bed, dtype='<f4').tobytes()).hexdigest()},
        'mix': {'chain': (f'narration + ducked music; lookahead limiter at {LIMIT_DBFS} dBFS (5 ms attack, 50 ms release, delay '
                          'compensated); two-pass loudnorm I=-14 LUFS, TP=-1.5 dBTP, linear; AAC 192 kbit/s 48 kHz stereo'),
                'limiter': {'ceiling_dbfs': LIMIT_DBFS, 'sample_peak_before_dbfs': round(peak_before, 2),
                            'seconds_of_samples_over_ceiling_before': round(over_s, 4)},
                'loudnorm_first_pass': first_mix, 'loudnorm_output': final, 'gain_applied_db': round(applied, 2)},
    }
    return m4a, audio


# --------------------------------------------------------------------------- main
def load_private(path):
    path = Path(path).resolve()
    capture = json.loads((path / 'capture.json').read_text())
    assert capture['status'] == 'captured' and not capture['page_errors'] and not capture['blocked_requests'] and not capture['refused_requests']
    assert capture['provider_calls'] == 0 and capture['new_provider_spend_usd'] == 0 and capture.get('shot') == 'private'
    posts = [item for item in capture['api_requests'] if item['method'] == 'POST']
    assert len(posts) == 1 and posts[0]['path'] == '/api/private/check' and posts[0]['cached'], posts
    marks = {mark['name']: mark for mark in capture['marks']}
    assert {'private-start', 'private-result', 'private-end'} <= set(marks), sorted(marks)
    return {'path': path, 'capture': capture, 'marks': marks, 'master': path / 'master.mp4'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, default=ROOT / 'data/eval_runs/demo-walkthrough-v2-20261030/take2',
                        help='v2 capture folder (master.mp4, capture.json); only read')
    parser.add_argument('--out', type=Path, required=True,
                        help='output folder for this render (render/ and main/ or x/ are written inside it); must be outside --source')
    parser.add_argument('--cut', choices=('main', 'x'), default='main')
    parser.add_argument('--voice-cache', type=Path, help='read-only folder of earlier Kokoro cue files (default: SOURCE/render)')
    parser.add_argument('--private-take', type=Path, help='optional capture folder with a recorded Private-check replay')
    parser.add_argument('--plan-only', action='store_true', help='Print the timeline without rendering video')
    parser.add_argument('--cards-only', action='store_true', help='Write the text cards as PNG files for review')
    args = parser.parse_args()
    out, source = args.out.resolve(), args.source.resolve()
    if out == source or source in out.parents:
        parser.error('--out must be outside --source: the capture folder is only read')
    work = out / 'render'
    work.mkdir(parents=True, exist_ok=True)
    private = load_private(args.private_take) if args.private_take and args.cut == 'main' else None
    cards = {name: make().convert('RGB') for name, make in cards_for(args.cut, bool(private)).items()}
    for name, image in cards.items():
        image.save(work / f'card-{name}.png')
    if args.cards_only:
        return
    capture = json.loads((args.source / 'capture.json').read_text())
    assert capture['status'] == 'captured' and not capture['page_errors'] and not capture['blocked_requests'] and not capture['refused_requests']
    marks = {mark['name']: mark for mark in capture['marks']}
    cue_ids = set(X_CUES) if args.cut == 'x' else {c[0] for c in CUES if c[0] not in OPTIONAL or (c[0] == 'c24' and private)}
    speech = synthesize(work, args.voice_cache or args.source / 'render', cue_ids)
    if args.cut == 'x':
        shots, said, duration = build_x(marks, capture['actions'], speech)
    else:
        shots, said, duration = build_main(marks, capture['actions'], speech, private)
    # Captions follow the speech; SRT/VTT use the same times.
    order = sorted(said, key=said.get)
    captions = []
    for i, cue in enumerate(order):
        start = max(0.0, said[cue] - .05)
        end = said[cue] + speech[cue]['seconds'] + .6
        if i + 1 < len(order):
            end = min(end, said[order[i + 1]] - .05)
        captions.append({'cue': cue, 'start': round(start, 3), 'end': round(min(end, duration), 3), 'caption': speech[cue]['caption']})

    def shot_record(shot):
        record = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in shot.items() if k not in ('cams', 'labels', 'overlays', 'master')}
        record['camera_keys'] = [[round(k[0], 3), [round(v, 1) for v in k[1]], k[2]] for k in shot.get('cams', [])]
        if shot.get('master'):
            record['master'] = 'private take'
        return record

    plan = {'version': VERSION, 'cut': args.cut, 'duration_s': duration, 'shots': [shot_record(s) for s in shots],
            'cues': {cue: {'start_s': round(said[cue], 3), 'seconds': round(speech[cue]['seconds'], 3)} for cue in order}}
    final_dir = out / args.cut
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    print(json.dumps({'cut': args.cut, 'duration_s': duration, 'shots': len(shots), 'cues': len(order),
                      'speech_s': round(sum(speech[c]['seconds'] for c in order), 2)}), flush=True)
    low, high = DURATION_LIMITS[args.cut]
    assert low <= duration <= high, duration
    if args.plan_only:
        for shot in plan['shots']:
            print(f"{shot['start']:7.2f}-{shot['end']:7.2f}  {shot['kind']:<8} {shot['name']:<16} src={shot.get('src')}")
        for cue in order:
            print(f"{said[cue]:7.2f} +{speech[cue]['seconds']:5.2f}  {cue}  {speech[cue]['caption'][:90]}")
        return
    name = OUTPUT[args.cut]
    m4a, audio = build_audio(said, speech, order, duration, work, name)
    caption_size = 44 if args.cut == 'x' else 34
    bands = {item['cue']: caption_band(item['caption'], caption_size) for item in captions}
    blank = caption_band('', caption_size)
    chip_scale = 1.25 if args.cut == 'x' else 1.0
    chips = {}
    for shot in shots:
        for label in shot.get('labels', []):
            key = (label['text'], label['dot'])
            if key not in chips:
                chips[key] = np.asarray(chip_image(label['text'], label['dot'], chip_scale), dtype=np.float32) / np.array([1, 1, 1, 255], dtype=np.float32)
    card_arrays = {name_: np.asarray(image, dtype=np.float32) for name_, image in cards.items()}
    video = final_dir / f'{name}.mp4'
    encoder = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
                                '-i', str(m4a), '-map', '0:v', '-map', '1:a',
                                '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
                                '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-profile:v', 'high', '-g', '50',
                                '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
                                '-c:a', 'copy', '-t', f'{duration:.3f}', '-movflags', '+faststart', str(video)],
                               stdin=subprocess.PIPE)
    master = args.source / 'master.mp4'
    decoders = {}
    total = int(round(duration * FPS))
    caption_index = 0
    for n in range(total):
        t = n / FPS
        active = [shot for shot in shots if shot['start'] <= t < shot['end']]
        assert active, t
        array = render_shot_frame(active[0], t, decoders, master, card_arrays, chips)
        if len(active) > 1:
            incoming = active[1]
            alpha = ease((t - incoming['start']) / max(incoming['fade'], 1 / FPS))
            other = render_shot_frame(incoming, t, decoders, master, card_arrays, chips)
            array = array * (1 - alpha) + other * alpha
        for shot in shots:                      # release decoders of finished shots
            if shot['end'] <= t and id(shot) in decoders:
                decoders.pop(id(shot)).close()
        while caption_index < len(captions) and captions[caption_index]['end'] <= t:
            caption_index += 1
        current = captions[caption_index] if caption_index < len(captions) and captions[caption_index]['start'] <= t else None
        band = bands[current['cue']] if current else blank
        frame_rgb = np.vstack([np.clip(array, 0, 255).astype(np.uint8), band])
        encoder.stdin.write(frame_rgb.tobytes())
        if n % 250 == 0:
            print(f'frame {n}/{total}', flush=True)
    encoder.stdin.close()
    assert encoder.wait() == 0
    for decoder in decoders.values():
        decoder.close()
    m4a.unlink()                                # the MP4 carries the same AAC stream; keep the disk footprint small
    srt = [f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["caption"]}\n' for i, c in enumerate(captions, 1)]
    vtt = ['WEBVTT\n'] + [f'{stamp(c["start"], True)} --> {stamp(c["end"], True)}\n{c["caption"]}\n' for c in captions]
    (final_dir / f'{name}.en.srt').write_text('\n'.join(srt))
    (final_dir / f'{name}.en.vtt').write_text('\n'.join(vtt))
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                'format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels',
                                                '-of', 'json', str(video)], text=True))
    overlays = []
    for shot in shots:
        for label in shot.get('labels', []):
            overlays.append({'kind': 'scene label', 'shot': shot['name'], 'text': label['text'], 'corner': label['corner'],
                             'start_s': round(shot['start'] + label['start'], 2), 'end_s': round(shot['start'] + min(label['end'], shot['end'] - shot['start']), 2)})
        for overlay in shot.get('overlays', []):
            overlays.append({'kind': 'SAM region highlight (edit overlay, not product UI)', 'shot': shot['name'],
                             'start_s': round(shot['start'] + overlay['start'], 2),
                             'end_s': round(shot['start'] + min(overlay['end'], shot['end'] - shot['start']), 2),
                             'what': 'Amber outline and a 45% spotlight dim around the two regions the product itself labels '
                                     '"Bar Chart · SAM"; boxes are the DOM boxes logged at the slide-regions mark, after the page stopped scrolling',
                             'regions': overlay['regions'], 'region_labels_in_product': overlay['region_labels'],
                             'boxes_css': overlay['boxes_css']})
    manifest = {
        'version': VERSION, 'cut': args.cut,
        'format': ('v2.1 re-edit of the v2 capture' + (' plus one recorded Private take' if private else '') +
                   ': recorded browser footage with camera moves and cuts, text cards, edit-layer labels and highlight, '
                   'burned-in captions, local synthetic narration and an original music bed'),
        'product_pixels': ('Cropped and scaled from the recorded browser master' + ('s (the v2 capture, and the Private take for the '
                           '"private" shot)' if private else ' only') + '; edit-layer overlays are listed under edit_overlays; '
                           'cards are separate full-frame text graphics'),
        'video_probe': probe, 'video_sha256': hashlib.sha256(video.read_bytes()).hexdigest(),
        'master_sha256': hashlib.sha256(master.read_bytes()).hexdigest(),
        'capture': {key: capture[key] for key in ('harness', 'source_commit', 'viewport_css', 'device_scale_factor', 'health_substitution',
                                                   'fix_source', 'api_requests', 'blocked_requests', 'refused_requests', 'page_errors',
                                                   'provider_calls', 'new_provider_spend_usd', 'recorder', 'timeline_duration_s')},
        'footage_commits': {'walkthrough_capture': capture['source_commit'],
                            'private_take': private['capture']['source_commit'] if private else None},
        'private_take': ({'path': str(private['path'].relative_to(ROOT)), 'master_sha256': hashlib.sha256(private['master'].read_bytes()).hexdigest(),
                          'capture': {k: private['capture'].get(k) for k in ('harness', 'shot', 'source_commit', 'source_tree_changes',
                                                                               'private_recording', 'private_status_as_served',
                                                                               'api_health_as_served', 'health_substitution', 'api_requests',
                                                                               'blocked_requests', 'refused_requests', 'provider_calls',
                                                                               'new_provider_spend_usd', 'page_errors', 'recorder',
                                                                               'timeline_duration_s')}}
                         if private else None),
        'narration': {'model': 'hexgrad/Kokoro-82M v1.0', 'voice': VOICE, 'speed': SPEED, 'runtime': 'kokoro-onnx 0.6.1',
                      'execution': 'local CPU', 'model_license': 'Apache-2.0', 'runtime_license': 'MIT',
                      'model_sha256': MODEL_SHA256, 'voices_sha256': VOICES_SHA256, 'new_provider_calls': 0,
                      'loudness_target': 'two-pass loudnorm, I=-14 LUFS, TP=-1.5 dBTP', 'loudnorm_first_pass': audio['narration']['loudnorm_first_pass'],
                      'segments': [{'cue': cue, 'start_s': round(said[cue], 3), 'seconds': round(speech[cue]['seconds'], 3),
                                    'caption': speech[cue]['caption'], 'spoken_text': speech[cue]['spoken'], 'audio_origin': speech[cue]['origin'],
                                    'speech_sha256': speech[cue]['sha256']} for cue in order],
                      'human_listening_approval': False},
        'audio': audio,
        'captions': captions, 'caption_font_px': caption_size, 'timeline': plan['shots'], 'duration_s': duration,
        'edit_overlays': overlays,
        'cards_sha256': {card_name: hashlib.sha256((work / f'card-{card_name}.png').read_bytes()).hexdigest() for card_name in cards},
        'new_provider_calls': 0, 'new_provider_spend_usd': 0, 'published': False,
    }
    (final_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'video': str(video), 'duration': probe['format']['duration'], 'bytes': probe['format']['size'],
                      'music_in_export_lufs': audio['music']['level_in_export_lufs']}), flush=True)


if __name__ == '__main__':
    main()
