#!/usr/bin/env python3
"""Edit the v2.1 walkthrough (16:9) and its vertical X & Threads cut (9:16) from the v2 browser capture.

v2.1 is a re-edit of the October 30 v2 capture; nothing is re-recorded. Product pixels come only from the browser
master recorded by capture_walkthrough_v2.py. The renderer crops and scales that footage and adds clearly separate
edit-layer elements, each listed in the manifest under `edit_overlays`:
- short scene labels in a corner;
- focus spotlights and callout outlines around active panels and claims so adjacent columns never show half-sliced text;
- on the real SEC slide, an outline and spotlight around the two regions the product labels "Bar Chart · SAM";
- time-animated full-frame text cards: the dramatic cold-open stakes card, disclosure, four-backends card, results and end card.
Narration is generated locally with Kokoro-82M from narration_v2.py; the music bed is synthesized locally by
music_bed.py. Nothing leaves the machine; the capture folder is only read.

  --cut main   the walkthrough (1920x1080 16:9, about 2:52; a recorded Private-check shot is added with --private-take)
  --cut x      a 45-60 s vertical cut for X and Threads (1080x1920 9:16) that opens on the planted errors
  --keep-audio-from MANIFEST   when a line's text changes, keep the rest of a previous export's audio as it was
"""
from __future__ import annotations

import argparse
from functools import lru_cache
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
from narration_v2 import CUES, LATER_LINES, OPTIONAL, SPEED, VERSION, VOICE, X_CUES  # noqa: E402
from render_demo import local_narrator  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TTS = ROOT / 'data/eval_runs/narration-replacement-20261029'
VOICES_SHA256 = 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d'
MODEL_SHA256 = 'beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a'
W, H, PRODUCT_H, BAND_H, FPS = 1920, 1080, 960, 120, 25
W_X, H_X, PRODUCT_H_X, BAND_H_X = 1080, 1920, 1620, 300
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
DIM = 0.48                                      # spotlight: darkening outside the focused regions
SAM_LABEL = 'SAM 3.1 · two bar-chart regions'
UNSHARP = ImageFilter.UnsharpMask(radius=1.15, percent=65, threshold=2)
# Length of the October 31 results line (c25, held-out results) after trimming: 204,578 samples at 24 kHz, 8.524 s. The
# results slot is sized by it, so replacing the line moves nothing after the card.
RESULTS_LINE_S = 204578 / 24000


# --------------------------------------------------------------------------- fonts (the product's own web fonts)
def _font_files():
    found = {}
    # Sort candidates by size descending so the full primary character bundle is picked first
    candidates = sorted(glob.glob(str(ROOT / 'web/.next/static/media/*.woff2')), key=lambda p: Path(p).stat().st_size, reverse=True)
    for path in candidates:
        family, style = ImageFont.truetype(path, 10).getname()
        found.setdefault((family, style), path)
    return found


FONT_FILES = _font_files()


@lru_cache(maxsize=256)
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


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def ease_out(t):
    t = min(max(t, 0.0), 1.0)
    return 1.0 - (1.0 - t) ** 3


def lerp_color(c0, c1, k):
    k = min(max(k, 0.0), 1.0)
    return tuple(int(round(a + (b - a) * k)) for a, b in zip(c0, c1))


# --------------------------------------------------------------------------- drawing helpers (2x supersampled)
class Canvas:
    def __init__(self, size=(W, PRODUCT_H), background=DARK, ss=2, grid=False):
        self.ss = ss
        self.size = size
        self.image = Image.new('RGBA', (size[0] * ss, size[1] * ss), background + (255,) if len(background) == 3 else background)
        self.draw = ImageDraw.Draw(self.image)
        if grid:
            self.draw_grid()

    def draw_grid(self, step=80, color=(22, 37, 30, 255)):
        w, h = self.size
        for gx in range(0, w, step):
            self.draw.line([(gx * self.ss, 0), (gx * self.ss, h * self.ss)], fill=color, width=1)
        for gy in range(0, h, step):
            self.draw.line([(0, gy * self.ss), (w * self.ss, gy * self.ss)], fill=color, width=1)

    def f(self, kind, size, weight=400, opsz=None):
        return font(kind, max(8, int(round(size * self.ss))), weight, opsz)

    def text(self, xy, text, kind='sans', size=32, weight=400, fill=CREAM, anchor='la', opsz=None, spacing=0):
        fnt = self.f(kind, size, weight, opsz)
        x, y = xy
        if spacing:
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

    def marked(self, xy, text, kind='sans', size=32, weight=400, fill=CREAM):
        """Left-aligned text on a baseline. The web fonts have no ✓ or ✗ glyph, so those are drawn as vector marks in the
        product's verdict colors."""
        x, y = xy
        for part in re.split('([✓✗])', text):
            if part in ('✓', '✗'):
                m = size * .82
                center = (x + m * .55, y - size * .34)
                if part == '✓':
                    self.check(center, m, GREEN, max(3, round(size / 9)))
                else:
                    self.cross(center, m * 1.15, RED, max(3, round(size / 9)))
                x += m * 1.1
            elif part:
                self.text((x, y), part, kind, size, weight, fill, 'ls')
                x += self.width(part, kind, size, weight)
        return x

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
        if x1 <= x0 or y1 <= y0:
            return
        self.draw.rounded_rectangle((x0, y0, x1, y1), radius=radius * self.ss, fill=fill, outline=outline, width=max(1, int(round(width * self.ss))) if outline else 0)

    def line(self, points, fill, width):
        self.draw.line([(x * self.ss, y * self.ss) for x, y in points], fill=fill, width=max(1, int(round(width * self.ss))), joint='curve')

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
        if self.ss == 1:
            return self.image
        return self.image.resize((self.image.width // self.ss, self.image.height // self.ss), Image.LANCZOS)


# --------------------------------------------------------------------------- animated cards
def card_stakes(local_t=3.5, ss=2):
    """Dramatic cold-open card: kinetic headlines + live red-pen audit preview of the two planted errors."""
    c = Canvas(grid=True, ss=ss)
    # Top editorial kicker pill
    k_top = ease_out(local_t / 0.45)
    c.rect((W / 2 - 430, 64, W / 2 + 430, 108), radius=22, fill=(20, 35, 28), outline=LINE, width=1.5)
    c.rect((W / 2 - 402, 80, W / 2 - 390, 92), radius=6, fill=RED)
    c.text((W / 2 + 12, 94), 'SEC EDGAR DETERMINISTIC VERIFICATION · META AI HACKATHON', 'sans', 19, 600, MUTED, 'ms', spacing=2)

    # Hero lines with kinetic reveal
    k1 = ease_out((local_t - 0.05) / 0.5)
    y1 = 236 + (1.0 - k1) * 20
    c.text((W / 2, y1), 'One wrong number.', 'serif', 102, 500, lerp_color(DARK, CREAM, k1), 'ms', 72)

    k2 = ease_out((local_t - 0.3) / 0.55)
    y2 = 358 + (1.0 - k2) * 20
    c.text((W / 2, y2), 'One public correction.', 'serif-italic', 102, 500, lerp_color(DARK, RED, k2), 'ms', 72)

    # Sweeping red editorial underline
    k_line = ease_out((local_t - 0.65) / 0.65)
    if k_line > 0:
        lw = 480 * k_line
        c.rect((W / 2 - lw, 384, W / 2 + lw, 389), radius=2, fill=RED)

    # Live "Red-Pen Audit" preview card showing the two planted errors being caught in real time
    k_card = ease_out((local_t - 0.45) / 0.55)
    if k_card > 0:
        cy0 = 432 + (1.0 - k_card) * 18
        cy1 = cy0 + 362
        c.rect((190, cy0, W - 190, cy1), radius=20, fill=PANEL, outline=LINE, width=2)
        c.rect((190, cy0, W - 190, cy0 + 54), radius=20, fill=(19, 32, 26), outline=LINE, width=1)
        c.text((230, cy0 + 35), 'AI-DRAFTED EARNINGS STORY · NVIDIA Q2 FY2027', 'mono', 20, 600, MUTED, 'ls')
        c.text((W - 230, cy0 + 35), 'FILED SEC 10-Q RECEIPT', 'mono', 20, 600, GREEN, 'rs')

        # Row 1: 18% -> 105.85%
        r1_y = cy0 + 132
        c.text((230, r1_y), '“Data Center revenue grew ', 'serif', 36, 400, CREAM, 'ls')
        x_val1 = 230 + c.width('“Data Center revenue grew ', 'serif', 36, 400)
        w_val1 = c.width('18%', 'mono', 38, 700)
        c.rect((x_val1 - 8, r1_y - 36, x_val1 + w_val1 + 8, r1_y + 10), radius=8, fill=(58, 24, 24))
        c.text((x_val1, r1_y), '18%', 'mono', 38, 700, RED, 'ls')
        c.text((x_val1 + w_val1 + 12, r1_y), ' from a year ago…”', 'serif', 36, 400, CREAM, 'ls')
        strike1 = ease_out((local_t - 0.95) / 0.45)
        if strike1 > 0:
            c.line([(x_val1 - 4, r1_y - 13), (x_val1 - 4 + (w_val1 + 8) * strike1, r1_y - 13)], RED, 4)
        stamp1 = ease_out((local_t - 1.15) / 0.45)
        if stamp1 > 0:
            bx0, bx1 = 1052 + (1.0 - stamp1) * 24, W - 202
            c.rect((bx0, r1_y - 40, bx1, r1_y + 14), radius=12, fill=(58, 24, 24), outline=RED, width=2.5)
            c.cross((bx0 + 28, r1_y - 13), 22, RED, 4)
            c.text((bx0 + 52, r1_y - 4), 'Filed YoY: 105.85%  (18% is QoQ)', 'mono', 25, 600, CREAM, 'ls')

        c.rect((230, cy0 + 178, W - 230, cy0 + 180), fill=LINE)

        # Row 2: $2.22 -> $2.46
        r2_y = cy0 + 250
        c.text((230, r2_y), '“GAAP diluted EPS reached ', 'serif', 36, 400, CREAM, 'ls')
        x_val2 = 230 + c.width('“GAAP diluted EPS reached ', 'serif', 36, 400)
        w_val2 = c.width('$2.22', 'mono', 38, 700)
        c.rect((x_val2 - 8, r2_y - 36, x_val2 + w_val2 + 8, r2_y + 10), radius=8, fill=(58, 24, 24))
        c.text((x_val2, r2_y), '$2.22', 'mono', 38, 700, RED, 'ls')
        c.text((x_val2 + w_val2 + 12, r2_y), ' for the quarter.”', 'serif', 36, 400, CREAM, 'ls')
        strike2 = ease_out((local_t - 1.45) / 0.45)
        if strike2 > 0:
            c.line([(x_val2 - 4, r2_y - 13), (x_val2 - 4 + (w_val2 + 8) * strike2, r2_y - 13)], RED, 4)
        stamp2 = ease_out((local_t - 1.65) / 0.45)
        if stamp2 > 0:
            bx0, bx1 = 1052 + (1.0 - stamp2) * 24, W - 202
            c.rect((bx0, r2_y - 40, bx1, r2_y + 14), radius=12, fill=(58, 24, 24), outline=RED, width=2.5)
            c.cross((bx0 + 28, r2_y - 13), 22, RED, 4)
            c.text((bx0 + 52, r2_y - 4), 'Filed GAAP: $2.46  ($2.22 is Non-GAAP)', 'mono', 25, 600, CREAM, 'ls')

        # Footer note inside card
        c.text((W / 2, cy0 + 328), 'Every number verified deterministically against SEC EDGAR filings · Zero guessing', 'sans', 25, 500, GREEN, 'ms')

    c.text((W / 2, 876), 'Financial journalists and analysts check every figure before it runs.', 'sans', 32, 400, MUTED, 'ms')
    return c.result()


def draw_verification_seal(c, center, radius, local_t=0.0, color=GREEN):
    """Refined circular medallion seal: double rings, gear ticks, and a bold checkmark."""
    cx, cy = center
    # Outer ring
    c.draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(16, 36, 26), outline=color, width=3)
    c.draw.ellipse((cx - radius + 8, cy - radius + 8, cx + radius - 8, cy + radius - 8), outline=lerp_color(DARK, color, 0.55), width=1)
    # Rotating micro tick marks around the rim
    ticks = 16
    rot = local_t * 0.4
    for i in range(ticks):
        ang = rot + i * (2 * np.pi / ticks)
        r0 = radius - 6
        r1 = radius - 2
        p0 = (cx + r0 * np.cos(ang), cy + r0 * np.sin(ang))
        p1 = (cx + r1 * np.cos(ang), cy + r1 * np.sin(ang))
        c.line([p0, p1], color, 1.5)
    # Centered checkmark
    c.check((cx, cy + 2), radius * 0.55, color, width=4)


def draw_3d_verifier_core(c, center, radius, theta, phi=0.42, color=GREEN):
    """React Three Fiber (R3F)-style 3D projective wireframe octahedron + nested inner cube + orbital ring,
    depth-sorted back-to-front with perspective foreshortening and glowing vertex nodes."""
    cx, cy = center
    ct, st = float(np.cos(theta)), float(np.sin(theta))
    cp, sp = float(np.cos(phi)), float(np.sin(phi))
    fov = 3.2

    def project(pt):
        x, y, z = pt
        x1 = x * ct + z * st
        z1 = -x * st + z * ct
        y2 = y * cp - z1 * sp
        z2 = y * sp + z1 * cp
        s = fov / (fov + z2)
        return (cx + x1 * radius * s, cy + y2 * radius * s, z2, s)

    # Equatorial 3D orbital ring
    ring_pts = [project((1.22 * np.cos(a), 0.0, 1.22 * np.sin(a))) for a in np.linspace(0, 2 * np.pi, 29)]
    for p0, p1 in zip(ring_pts[:-1], ring_pts[1:]):
        z_avg = 0.5 * (p0[2] + p1[2])
        col_r = lerp_color((28, 56, 44), color, 0.35 if z_avg > 0 else 0.75)
        c.line([(p0[0], p0[1]), (p1[0], p1[1])], col_r, 1.4 if z_avg > 0 else 2.2)

    # Outer 3D octahedron (6 vertices, 12 edges) + nested inner cube (8 vertices, 12 edges)
    oct_v = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    oct_e = [(0, 2), (0, 3), (0, 4), (0, 5), (1, 2), (1, 3), (1, 4), (1, 5), (2, 4), (4, 3), (3, 5), (5, 2)]
    s_in = 0.44
    ct2, st2 = float(np.cos(-theta * 1.35)), float(np.sin(-theta * 1.35))
    cube_v = []
    for dx in (-s_in, s_in):
        for dy in (-s_in, s_in):
            for dz in (-s_in, s_in):
                cube_v.append((dx * ct2 + dz * st2, dy, -dx * st2 + dz * ct2))
    cube_e = [(i, j) for i in range(8) for j in range(i + 1, 8) if sum(abs(a - b) > 1e-4 for a, b in zip(cube_v[i], cube_v[j])) == 1]

    p_oct = [project(v) for v in oct_v]
    p_cube = [project(v) for v in cube_v]
    edges = []
    for i, j in oct_e:
        edges.append((0.5 * (p_oct[i][2] + p_oct[j][2]), p_oct[i], p_oct[j], 'oct'))
    for i, j in cube_e:
        edges.append((0.5 * (p_cube[i][2] + p_cube[j][2]), p_cube[i], p_cube[j], 'cube'))
    # Sort back-to-front (largest z2 is furthest away)
    edges.sort(key=lambda item: item[0], reverse=True)
    for z_avg, p0, p1, kind in edges:
        front = z_avg < 0.0
        if kind == 'cube':
            edge_col = lerp_color((32, 68, 52), BLUE if front else GREEN, 0.85 if front else 0.45)
            w = 2.0 if front else 1.2
        else:
            edge_col = color if front else lerp_color(DARK, color, 0.42)
            w = 3.2 if front else 1.7
        c.line([(p0[0], p0[1]), (p1[0], p1[1])], edge_col, w)

    for px, py, z2, sc in p_oct:
        r_node = (4.8 if z2 < 0 else 3.2) * sc
        n_col = CREAM if z2 < -0.15 else color
        c.rect((px - r_node, py - r_node, px + r_node, py + r_node), radius=r_node, fill=n_col)


def card_title(private=False, local_t=3.5, ss=2):
    c = Canvas(grid=True, ss=ss)
    k_head = ease_out(local_t / 0.5)
    title_w = c.width('NoCap', 'serif', 120, 600, 72)
    x0 = (W - (title_w + 128)) / 2
    y_off = (1.0 - k_head) * 14
    c.logo((x0, 76 + y_off), 98)
    c.text((x0 + 128, 168 + y_off), 'NoCap', 'serif', 120, 600, CREAM, 'ls', 72)
    c.text((W / 2, 252 + y_off), 'Checks every financial number in a draft against SEC filings.', 'sans', 40, 400, CREAM, 'ms')
    # 3D R3F-style Verifier Core emblem in top right
    draw_verification_seal(c, (1640, 150), 48, local_t=local_t, color=GREEN)
    c.text((1640, 218), 'DETERMINISTIC VERIFIER', 'mono', 14, 600, GREEN, 'ms', spacing=2)

    # Meta model badges strip — each with its semantic role color and active border
    models = [('Muse Spark 1.3', 'Claims & 10-K Grounding', GREEN),
              ('Muse Wonder 1.0', 'Fix with Receipts', GREEN),
              ('SAM 3.1', 'Slide Chart Regions', AMBER),
              ('Muse Glimmer', 'Private Modal GPU', BLUE)]
    pill_w, gap = 365, 22
    start_x = (W - (len(models) * pill_w + (len(models) - 1) * gap)) / 2
    for idx, (m_name, m_role, m_col) in enumerate(models):
        km = ease_out((local_t - 0.16 - idx * 0.08) / 0.45)
        if km <= 0:
            continue
        px = start_x + idx * (pill_w + gap)
        py = 286 + (1.0 - km) * 12
        c.rect((px, py, px + pill_w, py + 92), radius=16, fill=(19, 34, 27), outline=m_col, width=2)
        c.rect((px, py, px + 8, py + 92), radius=4, fill=m_col)
        c.rect((px + 22, py + 24, px + 32, py + 34), radius=5, fill=m_col)
        c.text((px + 44, py + 40), m_name, 'sans', 26, 600, CREAM, 'ls')
        c.text((px + 44, py + 72), m_role, 'sans', 20, 400, MUTED, 'ls')

    c.rect((280, 412, W - 280, 848), radius=20, fill=PANEL, outline=LINE, width=2)
    c.text((W / 2, 456), 'HOW THIS VIDEO WAS MADE', 'sans', 20, 600, GREEN, 'ms', spacing=3)
    replayed = 'Fix, Slide, Ask and Private: recorded live runs replayed' if private else 'Fix, Slide and Ask: recorded live runs replayed, waits shortened'
    items = [f'The real NoCap interface, recorded locally on {"Oct 30–31" if private else "Oct 30"}, 2026',
             'Check: cached model output for saved examples',
             replayed,
             'The “Live checks are open” notice shows the judging-window setting',
             'Voice: synthetic (Kokoro-82M), not a person',
             'Labels, highlights and original music added in editing']
    for i, text in enumerate(items):
        ki = ease_out((local_t - 0.35 - i * 0.1) / 0.45)
        if ki <= 0:
            continue
        y = 514 + i * 48 + (1.0 - ki) * 8
        c.rect((360, y - 14, 370, y - 4), radius=5, fill=lerp_color(DARK, GREEN, ki))
        c.text((396, y), text, 'sans', 27, 400, lerp_color(DARK, CREAM, ki), 'ls')
    return c.result()


def card_title_x(local_t=2.5, ss=2):
    """Vertical 9:16 title & 3D AI stack reveal card for X and Threads (1080x1620 product area)."""
    c = Canvas((W_X, PRODUCT_H_X), grid=True, ss=ss)
    title_w = c.width('NoCap', 'serif', 124, 600, 72)
    x0 = 88
    c.logo((x0, 96), 104)
    c.text((x0 + 128, 192), 'NoCap', 'serif', 124, 600, CREAM, 'ls', 72)
    # Verification seal in top right
    draw_verification_seal(c, (W_X - 170, 160), 52, local_t=local_t, color=GREEN)
    c.text((W_X - 170, 236), 'SEC VERIFIED', 'mono', 15, 600, GREEN, 'ms', spacing=2)

    c.text((W_X / 2, 314), 'Checks every financial number in a draft', 'sans', 40, 500, CREAM, 'ms')
    c.text((W_X / 2, 366), 'against SEC EDGAR filings.', 'sans', 40, 600, GREEN, 'ms')

    # Model stack cards with 3D stream dots into the bottom Deterministic Gate
    models = [
        ('Muse Spark 1.3', 'Extracts claims & reads full 10-K filings in one request', GREEN),
        ('Muse Wonder 1.0', 'Proposes corrected figures; code constrains every digit', GREEN),
        ('SAM 3.1', 'Finds bar-chart & table regions on real SEC slides', AMBER),
        ('Muse Spark Open & Glimmer', 'Open-weights parity & Private check on Modal H100', BLUE),
    ]
    for i, (m_title, m_desc, m_col) in enumerate(models):
        ki = ease_out((local_t - 0.16 - i * 0.10) / 0.45)
        y0 = 434 + i * 180 + (1.0 - ki) * 16
        c.rect((68, y0, W_X - 68, y0 + 154), radius=20, fill=PANEL, outline=m_col if ki > 0.7 else LINE, width=2)
        c.rect((68, y0, 80, y0 + 154), radius=6, fill=m_col)
        c.text((112, y0 + 58), m_title, 'sans', 36, 600, CREAM, 'ls')
        c.text((112, y0 + 112), m_desc, 'sans', 26, 400, MUTED, 'ls')

    c.rect((68, 1188, W_X - 68, 1536), radius=22, fill=(17, 36, 28), outline=GREEN, width=2.5)
    c.text((W_X / 2, 1252), 'THE LLM PROPOSES · DETERMINISTIC CODE DECIDES', 'mono', 23, 700, GREEN, 'ms', spacing=2)
    c.text((W_X / 2, 1322), 'Real NoCap interface · Recorded live runs replayed', 'sans', 31, 500, CREAM, 'ms')
    c.text((W_X / 2, 1378), 'Synthetic voice (Kokoro-82M) · Zero guessing on ambiguity', 'sans', 27, 400, MUTED, 'ms')
    c.rect((128, 1412, W_X - 128, 1414), fill=LINE)
    c.text((W_X / 2, 1478), LIVE_URL, 'mono', 34, 600, GREEN, 'ms')
    return c.result()


BACKENDS = [('Muse Spark 1.3', 'Meta Model API', 'Default extractor', CREAM),
            ('Muse Spark Open', 'Fireworks', '27/27 gold claims found', GREEN),
            ('Muse Spark Open', 'Baseten', '27/27 gold claims found', GREEN),
            ('Muse Glimmer', 'our own Modal H100', 'Private check: unpublished drafts', BLUE)]


def card_backends(local_t=4.0, ss=2):
    c = Canvas(grid=True, ss=ss)
    c.text((150, 118), 'OPEN WEIGHTS AND HOSTED MODELS · 3D AI PIPELINE', 'sans', 22, 600, GREEN, 'ls', spacing=3)
    c.text((150, 196), 'Same verifier, four backends', 'serif', 64, 500, CREAM, 'ls', 60)
    row_x0, row_x1, verifier = 150, 1190, (1330, 236, 1770, 678)
    vx0, vy0, vx1, vy1 = verifier
    for i, (model, host, result, color) in enumerate(BACKENDS):
        ki = ease_out((local_t - 0.15 - i * 0.14) / 0.5)
        y0 = 248 + i * 104 + (1.0 - ki) * 14
        outline_col = color if color in (GREEN, BLUE) and ki > 0.8 else LINE
        c.rect((row_x0, y0, row_x1, y0 + 88), radius=16, fill=PANEL, outline=outline_col, width=2)
        c.rect((row_x0, y0 + 18, row_x0 + 6, y0 + 70), radius=3, fill=color)
        c.text((row_x0 + 34, y0 + 40), model, 'sans', 34, 600, CREAM, 'ls')
        c.text((row_x0 + 34, y0 + 74), host, 'sans', 26, 400, MUTED, 'ls')
        c.text((row_x1 - 34, y0 + 56), result, 'sans', 32, 600, color, 'rs')
        p_start = (row_x1 + 8, y0 + 44)
        p_end = (verifier[0] - 10, (verifier[1] + verifier[3]) / 2 + (i - 1.5) * 46)
        c.arrow(p_start, p_end, GREEN if ki > 0.7 else MUTED, 3, 13)
        # 3D volumetric particle stream traveling along each backend channel into the 3D Verifier Gate
        p_col = color if color in (GREEN, BLUE) else GREEN
        for p_idx in range(3):
            pulse = ((local_t * 0.85 + i * 0.21 + p_idx * 0.33) % 1.0)
            px = p_start[0] + (p_end[0] - p_start[0]) * pulse
            py = p_start[1] + (p_end[1] - p_start[1]) * pulse
            pr = 6.0 if p_idx == 0 else 4.0
            c.rect((px - pr, py - pr, px + pr, py + pr), radius=pr, fill=p_col)

    c.rect(verifier, radius=22, fill=(14, 52, 35), outline=GREEN, width=3)
    # React Three Fiber-style 3D rotating Verifier Crystal Core inside the gate card
    draw_3d_verifier_core(c, ((vx0 + vx1) / 2, vy0 + 114), 68, theta=local_t * 1.15 + 0.4, phi=0.38, color=GREEN)
    c.text(((vx0 + vx1) / 2, vy0 + 244), 'Deterministic', 'serif', 44, 600, CREAM, 'ms', 60)
    c.text(((vx0 + vx1) / 2, vy0 + 296), 'Verifier Gate', 'serif', 44, 600, CREAM, 'ms', 60)
    c.text(((vx0 + vx1) / 2, vy0 + 354), 'checks every claim', 'sans', 25, 400, MUTED, 'ms')
    c.text(((vx0 + vx1) / 2, vy0 + 388), 'against SEC filings', 'sans', 25, 400, MUTED, 'ms')

    kb = ease_out((local_t - 0.85) / 0.55)
    c.rect((150, 712, W - 150, 812), radius=18, fill=PANEL, outline=GREEN if kb > 0.7 else LINE, width=2)
    c.text((190, 776), '200 closed-book answers from four Meta models:', 'sans', 34, 500, CREAM, 'ls')
    c.text((W - 190, 776), 'the gate let 0 wrong numbers through', 'sans', 34, 600, GREEN, 'rs')
    notes = ('Gold set: 27 claims in 10 NVIDIA passages; on each host one extra claim stayed ?. Q&A: a development benchmark '
             '(held-out split not used), post-run analysis; the gate accepted 0–34% of each model’s answers.')
    for j, line in enumerate(c.wrap(notes, W - 300, 'sans', 24)):
        c.text((150, 866 + j * 34), line, 'sans', 24, 400, MUTED, 'ls')
    return c.result()


RESULTS_TITLE = '1,000 frozen test items from 400 S&P 500 companies'
RESULTS_ROWS = [('306/360', GREEN, 'planted errors caught', 'none marked ✓; the other 54 came back ?', 306 / 360),
                ('736/738', GREEN, 'times a ✓ was right', '99.7%; the other 2 landed on figures labeled guidance', 736 / 738),
                ('17/1,298', CREAM, 'true figures flagged ✗', '1.3%; 736 verified and 533 left ? instead of guessed', 1.0 - 17 / 1298)]
RESULTS_EARLIER = ('Earlier held-out test, run once on Oct 29:',
                   '44/48 planted errors caught, 1/47 false alarm, 20/20 in trap drafts')
RESULTS_NOTES = ['One frozen run of the November 2 release, not rerun. Labels are machine-made, with no human review yet.',
                 'Details: eval/spot500_methods.md']


def card_results(local_t=5.0, ss=2):
    c = Canvas(grid=True, ss=ss)
    c.text((180, 126), 'RESULTS · README.md', 'sans', 24, 600, GREEN, 'ls', spacing=3)
    assert c.width(RESULTS_TITLE, 'serif', 58, 500, 60) <= W - 360, RESULTS_TITLE
    c.text((180, 204), RESULTS_TITLE, 'serif', 58, 500, CREAM, 'ls', 60)
    for i, (big, color, main_txt, sub, ratio) in enumerate(RESULTS_ROWS):
        kr = ease_out((local_t - 0.18 - i * 0.18) / 0.55)
        y = 322 + i * 142 + (1.0 - kr) * 14
        c.rect((180, y - 62, W - 180, y + 66), radius=18, fill=PANEL, outline=LINE, width=2)
        assert c.width(big, 'serif', 86, 600, 72) <= 650 - 222 - 30, big
        c.text((222, y + 26), big, 'serif', 86, 600, color, 'ls', 72)
        c.marked((650, y - 10), main_txt, 'sans', 38, 500, CREAM)
        assert c.marked((650, y + 36), sub, 'sans', 28, 400, MUTED) <= W - 180 - 40, sub
        # Full-card bottom accuracy/precision bar inside each row card
        bar_k = ease_out((local_t - 0.45 - i * 0.22) / 1.1)
        bar_x0, bar_x1 = 222, W - 220
        c.rect((bar_x0, y + 51, bar_x1, y + 58), radius=3, fill=(15, 25, 20))
        if bar_k > 0:
            fill_w = (bar_x1 - bar_x0) * ratio * bar_k
            c.rect((bar_x0, y + 51, bar_x0 + max(8, fill_w), y + 58), radius=3, fill=GREEN if color == GREEN else BLUE)

    label, numbers = RESULTS_EARLIER
    x = 180 + c.width(label + ' ', 'sans', 26, 400)
    assert x + c.width(numbers, 'sans', 26, 500) <= W - 180, numbers
    c.text((180, 744), label, 'sans', 26, 400, MUTED, 'ls')
    c.text((x, 744), numbers, 'sans', 26, 500, CREAM, 'ls')
    for j, line in enumerate(RESULTS_NOTES):
        assert c.width(line, 'sans', 24) <= W - 360, line
        c.text((180, 802 + j * 34), line, 'sans', 24, 400, MUTED, 'ls')
    return c.result()


COVERAGE = 'Now covering all 500 S&P 500 companies'
END_LAYOUT = {False: {'top': 84, 'coverage': 268, 'url': 390, 'repo': 584, 'rule': 634, 'models': 702, 'credit': 766},
              True: {'top': 56, 'coverage': 266, 'url': 382, 'repo': 566, 'rule': 612, 'models': 674, 'credit': 734,
                     'disclosure': 806}}


def card_end(x=False, local_t=4.0, ss=2):
    if x:
        return card_end_x(local_t=local_t, ss=ss)
    c = Canvas(grid=True, ss=ss)
    L = END_LAYOUT[False]
    top = L['top']
    title_w = c.width('NoCap', 'serif', 120, 600, 72)
    x0 = (W - (title_w + 124)) / 2
    c.logo((x0, top - 16), 98)
    c.text((x0 + 124, top + 76), 'NoCap', 'serif', 120, 600, CREAM, 'ls', 72)
    c.text((W / 2, top + 162), 'Every financial number, with a receipt from the filing.', 'sans', 36, 400, MUTED, 'ms')

    # Coverage highlight pill
    cov_w = c.width(COVERAGE, 'sans', 42, 600) + 84
    c.rect((W / 2 - cov_w / 2, top + 206, W / 2 + cov_w / 2, top + 278), radius=18, fill=(14, 60, 40), outline=GREEN, width=2)
    c.text((W / 2, top + 255), COVERAGE, 'sans', 42, 600, GREEN, 'ms')

    # Structured editorial card for Live URL + Repo + Meta Models
    c.rect((220, top + 308, W - 220, top + 804), radius=24, fill=PANEL, outline=LINE, width=2)
    c.text((W / 2, top + 356), 'TRY IT LIVE IN YOUR BROWSER', 'mono', 20, 600, GREEN, 'ms', spacing=3)
    c.text((W / 2, top + 432), LIVE_URL, 'sans', 68, 600, CREAM, 'ms')
    pill = 'Live checks open through Nov 17'
    pw = c.width(pill, 'sans', 30, 600) + 70
    c.rect((W / 2 - pw / 2, top + 462, W / 2 + pw / 2, top + 518), radius=28, fill=(14, 60, 40), outline=GREEN, width=2)
    c.text((W / 2, top + 501), pill, 'sans', 30, 600, CREAM, 'ms')

    c.text((W / 2, top + 592), REPO_URL, 'mono', 38, 500, CREAM, 'ms')
    c.rect((320, top + 634, W - 320, top + 636), fill=LINE)
    c.text((W / 2, top + 694), 'Muse Spark 1.3 · Muse Wonder 1.0 · SAM 3.1 · Muse Spark Open · Muse Glimmer', 'sans', 29, 500, GREEN, 'ms')
    c.text((W / 2, top + 754), 'Built during the Meta Global AI Developer Hackathon · Robert Chang · Team NoCap', 'sans', 28, 400, CREAM, 'ms')
    return c.result()


def card_end_x(local_t=4.0, ss=2):
    """Vertical 9:16 end card for X and Threads (1080x1620 product area)."""
    c = Canvas((W_X, PRODUCT_H_X), grid=True, ss=ss)
    draw_verification_seal(c, (W_X - 156, 178), 52, local_t=local_t, color=GREEN)
    title_w = c.width('NoCap', 'serif', 128, 600, 72)
    x0 = 76
    c.logo((x0, 108), 108)
    c.text((x0 + 132, 208), 'NoCap', 'serif', 128, 600, CREAM, 'ls', 72)
    c.text((76, 304), 'Every financial number, with a receipt', 'sans', 38, 400, MUTED, 'ls')
    c.text((76, 354), 'from the SEC filing.', 'sans', 38, 400, MUTED, 'ls')

    # Coverage & S&P 500 Benchmark highlight card
    c.rect((68, 412, W_X - 68, 584), radius=22, fill=(14, 60, 40), outline=GREEN, width=2.5)
    c.text((W_X / 2, 476), COVERAGE, 'sans', 42, 600, GREEN, 'ms')
    c.rect((128, 502, W_X - 128, 504), fill=(28, 92, 62))
    c.text((W_X / 2, 552), '306 / 360 errors caught  ·  736 / 738 (99.7%) verified precision', 'sans', 26, 600, CREAM, 'ms')

    # URL & Live badge card
    c.rect((68, 616, W_X - 68, 884), radius=24, fill=PANEL, outline=LINE, width=2)
    c.text((W_X / 2, 674), 'TRY IT LIVE IN YOUR BROWSER', 'mono', 22, 600, GREEN, 'ms', spacing=3)
    c.text((W_X / 2, 748), LIVE_URL, 'sans', 62, 600, CREAM, 'ms')
    pill = 'Live checks open through Nov 17'
    pw = c.width(pill, 'sans', 32, 600) + 76
    c.rect((W_X / 2 - pw / 2, 796, W_X / 2 + pw / 2, 856), radius=30, fill=(14, 60, 40), outline=GREEN, width=2)
    c.text((W_X / 2, 838), pill, 'sans', 32, 600, CREAM, 'ms')

    # Repo & Models card
    c.rect((68, 916, W_X - 68, 1256), radius=22, fill=PANEL, outline=LINE, width=2)
    c.text((W_X / 2, 980), REPO_URL, 'mono', 33, 500, CREAM, 'ms')
    c.rect((W_X / 2 - 120, 1018, W_X / 2 + 120, 1021), fill=LINE)
    c.text((W_X / 2, 1076), 'Muse Spark 1.3 · Muse Wonder 1.0 · SAM 3.1', 'sans', 32, 500, GREEN, 'ms')
    c.text((W_X / 2, 1130), 'Muse Spark Open · Muse Glimmer (Modal H100)', 'sans', 30, 400, MUTED, 'ms')
    c.text((W_X / 2, 1206), 'Meta Global AI Developer Hackathon · Robert Chang · Team NoCap', 'sans', 26, 500, CREAM, 'ms')

    # Disclosure footer
    c.rect((68, 1292, W_X - 68, 1528), radius=18, fill=(19, 32, 26), outline=LINE, width=1.5)
    c.text((W_X / 2, 1354), 'DISCLOSURE', 'mono', 20, 600, MUTED, 'ms', spacing=3)
    c.text((W_X / 2, 1408), 'The real interface, recorded locally: saved examples', 'sans', 26, 400, MUTED, 'ms')
    c.text((W_X / 2, 1446), 'and recorded live runs replayed. Synthetic voice (Kokoro-82M);', 'sans', 26, 400, MUTED, 'ms')
    c.text((W_X / 2, 1484), 'labels, highlights and original music added in editing.', 'sans', 26, 400, MUTED, 'ms')
    return c.result()


def cards_for(cut, private):
    if cut == 'x':
        return {'title-x': card_title_x, 'end-x': card_end_x}
    return {'stakes': card_stakes, 'title': lambda local_t=3.5, ss=2: card_title(private, local_t=local_t, ss=ss),
            'backends': card_backends, 'results': card_results, 'end': lambda local_t=4.0, ss=2: card_end(False, local_t=local_t, ss=ss)}


def chip_image(text, dot=GREEN, scale=1.0):
    size = round(27 * scale)
    height = round(64 * scale)
    c = Canvas((1400, height + 12), (0, 0, 0, 0))
    width = c.width(text, 'sans', size, 500) + 76 * scale
    c.rect((0, 0, width, height), radius=height / 2, fill=(15, 27, 22, 238), outline=(86, 196, 138, 255), width=2)
    d = 12 * scale
    c.rect((26 * scale, height / 2 - d / 2, 26 * scale + d, height / 2 + d / 2), radius=d / 2, fill=dot + (255,))
    c.text((52 * scale, height / 2 + 10 * scale), text, 'sans', size, 500, CREAM + (255,), 'ls')
    image = c.result()
    return image.crop((0, 0, int(width) + 4, height + 4))


def caption_band(text, size=34, cut='main'):
    if cut == 'x':
        c = Canvas((W_X, BAND_H_X), DARK, grid=True)
        c.rect((36, 16, W_X - 36, BAND_H_X - 28), radius=24, fill=PANEL, outline=LINE, width=2)
        if text:
            lines = c.wrap(text, W_X - 144, 'sans', size, 600)
            assert len(lines) <= 3, text
            step = 54
            total_h = (len(lines) - 1) * step
            y = (BAND_H_X - 12) / 2 - total_h / 2 + 12
            for line in lines:
                c.text((W_X / 2, y), line, 'sans', size, 600, CREAM, 'ms')
                y += step
        return np.asarray(c.result().convert('RGB'))

    c = Canvas((W, BAND_H), DARK, grid=True)
    c.rect((112, 8, W - 112, BAND_H - 10), radius=18, fill=PANEL, outline=LINE, width=2)
    if text:
        lines = c.wrap(text, 1610 if size <= 34 else 1640, 'sans', size, 500)
        assert len(lines) <= 2, text
        if size <= 34:
            y, step = (54 if len(lines) == 1 else 36), 42
            for line in lines:
                c.text((W / 2, y + 12), line, 'sans', size, 500, CREAM, 'ms')
                y += step
        else:
            baselines = [74] if len(lines) == 1 else [48, 98]
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
        origin = 'reused from the v2 render' if found and found[0].parent != work else LATER_LINES.get(cue_id, 'synthesized for v2.1')
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

    def footage(self, name, src, start, end, cams, limit=None, label=None, fade=0.0, corner='top-left', master=None, overlays=None,
                v_panels=None, v_meta=None, v_meta_before=None, switch_s=None, v_connector=None):
        labels = [{'text': label, 'corner': corner, 'start': 0.0, 'end': 4.0, 'dot': GREEN}] if label else []
        shot = {'kind': 'footage', 'name': name, 'src': src, 'start': start, 'end': end, 'cams': cams, 'fade': fade,
                'labels': labels, 'overlays': overlays or [], 'master': master, 'v_panels': v_panels,
                'v_meta': v_meta, 'v_meta_before': v_meta_before, 'switch_s': switch_s, 'v_connector': v_connector}
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


def focus_spotlight(shot, start, end, panel_css, callout_css=None, color=GREEN, what=''):
    """Screen-Studio style focus spotlight: blurs and dims adjacent columns outside `panel_css` so half-sliced text
    from neighboring columns never competes with the active card, and optionally draws an animated outline around `callout_css`."""
    boxes = list(panel_css) if isinstance(panel_css[0], (list, tuple)) else [panel_css]
    shot['overlays'].append({
        'kind': 'focus-spotlight',
        'start': start,
        'end': end,
        'boxes_css': boxes,
        'callout_css': callout_css,
        'color': color,
        'what': what or 'Soft spotlight dim/blur outside active panel and callout outline around highlighted element',
    })


def click_ripple(shot, at_local, cx, cy, color=GREEN, tag='', what=''):
    """Draw an expanding concentric click ripple and floating action tag at CSS coordinates (cx, cy)."""
    shot['overlays'].append({
        'kind': 'click-ripple',
        'start': max(0.0, at_local - 0.12),
        'end': at_local + 0.85,
        'boxes_css': [[cx - 18, cy - 18, 36, 36]],
        'center_css': [cx, cy],
        'color': color,
        'tag': tag,
        'what': what or f'Expanding concentric click ripple and action callout ({tag}) at cursor interaction point',
    })


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
    chip1 = frame(b, ['c2'], center=(column, 195), min_w=760)
    chip2 = frame(b, ['c8'], center=(column, 430), min_w=760)
    t = say('c02', t + .1) + .7
    move_cold = T.said['c02'] - s + 1.6
    shot_cold = footage('cold', m['t'] + .3, s, t, [(0, chip1, 0), (move_cold, chip2, 1.0)], limit=M['tiles']['t'] - 1.0, fade=.45)
    focus_spotlight(shot_cold, 0.0, move_cold + 0.3, [86, 20, 700, 360],
                    callout_css=[b['c2'][0] - 46, b['c2'][1] - 5, b['c2'][2] + 54, b['c2'][3] + 10], color=RED,
                    what='Focus spotlight on left draft column and red callout around 18% planted error')
    focus_spotlight(shot_cold, move_cold + 0.3, t - s, [86, 260, 700, 355],
                    callout_css=[b['c8'][0] - 64, b['c8'][1] - 5, b['c8'][2] + 72, b['c8'][3] + 10], color=RED,
                    what='Focus spotlight on left draft column and red callout around $2.22 planted error')
    # 2. Title and disclosure card: five seconds.
    s = t - .4
    card('title', s, s + 5.0, fade=.4)
    say('c03', s + .25)
    t = s + 5.0
    # 3. Check a saved example: wide shot, click Check, verdict tiles, receipts.
    m0, m1, s = M['draft-initial'], M['check-done'], t - .4
    say('c04', t + .25)
    src = m0['t'] + .6
    t = s + (m1['t'] + 1.0 - src)
    button = (60, 48, 1160, 580)
    shot_check = footage('check', src, s, t, [(0, WIDE, 0), (M['draft-scrolled']['t'] - src - .2, button, 1.2)], limit=M['check-settled']['t'],
                         label='Muse Spark 1.3 extracts the claims · code decides each verdict', fade=.4, corner='top-left')
    focus_spotlight(shot_check, M['draft-scrolled']['t'] - src + 0.4, t - s, [85, 50, 702, 578],
                    callout_css=[643, 582, 120, 44], color=GREEN,
                    what='Focus spotlight on draft editor and Check button click')
    click_ripple(shot_check, M['check-click']['t'] - src - 0.15, 703, 604, color=GREEN, tag='CLICK · Check',
                 what='Click ripple on Check button')
    t = max(t, end_of('c04') + .2)
    m, s = M['tiles'], t
    t = say('c05', s + .2) + .9
    shot_tiles = footage('tiles', m['t'] + .1, s, t, [(0, frame(m['boxes'], ['summary'], pad=20, center=(436, 175), min_w=700), 0)],
                         limit=M['receipt-growth']['t'] - 1.0)
    focus_spotlight(shot_tiles, 0.0, t - s, [85, 58, 702, 280],
                    callout_css=[m['boxes']['tiles'][0] - 6, m['boxes']['tiles'][1] - 6, m['boxes']['tiles'][2] + 12, m['boxes']['tiles'][3] + 12],
                    color=GREEN, what='Focus spotlight on summary strip and verdict count tiles')
    m, s = M['receipt-growth'], t
    b = m['boxes']
    line = frame(b, ['c2'], center=(M['cold-both']['boxes']['draft'][0] + M['cold-both']['boxes']['draft'][2] / 2, 195), min_w=760)
    receipt = frame(b, ['header', 'receipt-dl', 'calc'], pad=22, center=(1008, 302), min_w=960)
    say('c06', s + .2)
    t = say('c07', end_of('c06') + .35) + 1.6
    move_rec = T.said['c07'] - s - .35
    shot_rec = footage('receipt', m['t'] + .1, s, t, [(0, line, 0), (move_rec, receipt, .9)], limit=M['receipt-sources']['t'] - 1.0)
    focus_spotlight(shot_rec, 0.0, move_rec + 0.3, [86, 20, 700, 580],
                    callout_css=[b['c2'][0] - 46, b['c2'][1] - 5, b['c2'][2] + 54, b['c2'][3] + 10], color=RED,
                    what='Focus spotlight on draft claim 18%')
    focus_spotlight(shot_rec, move_rec + 0.3, t - s, [816, 24, 384, 520],
                    callout_css=[b['calc'][0] - 4, b['calc'][1] - 4, b['calc'][2] + 8, b['calc'][3] + 8], color=AMBER,
                    what='Focus spotlight on right receipt panel and 105.85% calculation table')
    m, s = M['receipt-sources'], t
    b = m['boxes']
    t = say('c08', s + .2) + .9
    sources = frame(b, ['why', 'first-receipt'], pad=26, center=(1008, 232), min_w=888)
    shot_src = footage('sources', m['t'] + .1, s, t, [(0, sources, 0)], limit=M['receipt-eps']['t'] - 2.0)
    focus_spotlight(shot_src, 0.0, t - s, [816, 24, 384, 418],
                    callout_css=[b['first-receipt'][0] - 4, b['first-receipt'][1] - 4, b['first-receipt'][2] + 8, b['first-receipt'][3] + 8],
                    color=GREEN, what='Focus spotlight on receipt filing citation and EDGAR acceptance timestamp')
    m, s = M['receipt-eps'], t
    b = m['boxes']
    start_src = T.action_time('click-start', '$2.22', M['receipt-sources']['t']) + .15
    eps_line = frame(b, ['c8'], center=(M['cold-both']['boxes']['draft'][0] + M['cold-both']['boxes']['draft'][2] / 2, 435), min_w=760)
    eps_receipt = (160, 58, 1120, 560)
    t = say('c09', s + .25) + 1.4
    move_eps = T.said['c09'] - s + 2.3
    shot_eps = footage('eps', start_src, s, t, [(0, eps_line, 0), (move_eps, eps_receipt, .9)], limit=M['fix-ready']['t'] - 1.3)
    focus_spotlight(shot_eps, 0.0, move_eps + 0.3, [86, 250, 700, 360],
                    callout_css=[b['c8'][0] - 64, b['c8'][1] - 5, b['c8'][2] + 72, b['c8'][3] + 10], color=RED,
                    what='Focus spotlight on $2.22 diluted EPS claim click')
    click_ripple(shot_eps, 1.05, 292, 530, color=RED, tag='CLICK · $2.22 EPS',
                 what='Click ripple on $2.22 claim chip')
    focus_spotlight(shot_eps, move_eps + 0.3, t - s, [816, 62, 384, 542],
                    callout_css=[b['receipt-dl'][0] - 4, b['receipt-dl'][1] - 4, b['receipt-dl'][2] + 8, b['receipt-dl'][3] + 8], color=RED,
                    what='Focus spotlight on GAAP $2.46 vs non-GAAP $2.22 receipt panel and SEC 8-K accession')
    # 4. Fix with receipts (the recorded October 29 response).
    m0, m1, s = M['fix-ready'], M['fix-result'], t - .35
    say('c10', t + .2)
    t = say('c11', max(end_of('c10') + .35, m1['t'] - m0['t'] + s + 1.0)) + 1.5
    card_box = frame(m0['boxes'], ['fix-card'], pad=24)
    b = m1['boxes']
    result = frame(b, ['counts'], center=(VIEW_W / 2, 290), min_w=1150)
    counts = frame(b, ['counts'], center=(VIEW_W / 2, 250), min_w=1130)
    move_fix = T.said['c11'] - s - .3
    shot_fix = footage('fix', m0['t'] - .2, s, t, [(0, card_box, 0), (m1['t'] - (m0['t'] - .2) - .9, result, .9),
                                                    (move_fix, counts, 1.0)],
                       limit=M['fix-receipts']['t'] - 1.0, label='Muse Wonder 1.0 proposes · code constrains and rechecks', fade=.35)
    focus_spotlight(shot_fix, 0.0, 2.8, [84, 358, 1112, 140],
                    callout_css=[1009, 406, 164, 44], color=GREEN,
                    what='Focus spotlight on Fix with receipts card and button click')
    click_ripple(shot_fix, 2.1, 1091, 428, color=GREEN, tag='CLICK · Fix with receipts',
                 what='Click ripple on Fix with receipts button')
    focus_spotlight(shot_fix, move_fix, t - s, [85, 8, 1110, 560],
                    callout_css=[b['counts'][0] - 8, b['counts'][1] - 4, b['counts'][2] + 16, b['counts'][3] + 10], color=GREEN,
                    what='Full-width view of Fix result with emerald callout around 9 verified, 0 wrong summary')
    m, s = M['fix-receipts'], t
    t = say('c12', s + .2) + 1.3
    shot_fix_rec = footage('fix-receipts', m['t'] + .1, s, t,
                           [(0, (70, 10, 1140, 570), 0)],
                           limit=M['cutoff-loaded']['t'] - 6.0)
    focus_spotlight(shot_fix_rec, 0.0, t - s, [85, 16, 1110, 462], color=GREEN,
                    what='Full-width view of both correction receipts without slicing third row')
    # 5. Time travel.
    m, s = M['cutoff-loaded'], t - .35
    start_src = T.action_time('click-start', 'June-quarter roundup', M['fix-receipts']['t']) - 1.4
    end = max(say('c13', t + .2) + .3, s + (m['t'] - start_src) + 1.5)
    shot_cutoff = footage('cutoff', start_src, s, end, [(0, frame(m['boxes'], ['picker', 'asof'], pad=40, min_w=820), 0)], limit=m['t'] + 4.0,
                          label='Time travel: availability follows EDGAR acceptance time', fade=.35)
    focus_spotlight(shot_cutoff, 3.1, end - s, [85, 336, 702, 108],
                    callout_css=[449, 374, 258, 48], color=AMBER,
                    what='Focus spotlight on example picker and as-of cutoff timestamp')
    click_ripple(shot_cutoff, 2.2, 274, 398, color=AMBER, tag='SELECT · June-quarter',
                 what='Click ripple on June-quarter roundup example selection')
    m, s = M['cutoff-tiles'], end
    say('c14', s + .2)
    t = s + 2.2
    shot_ctiles = footage('cutoff-tiles', m['t'] + .1, s, t, [(0, frame(m['boxes'], ['summary'], pad=26, center=(436, 185), min_w=780), 0)],
                          limit=m['t'] + 3.5)
    focus_spotlight(shot_ctiles, 0.0, t - s, [85, 58, 702, 250],
                    callout_css=[m['boxes']['tiles'][0] - 6, m['boxes']['tiles'][1] - 6, m['boxes']['tiles'][2] + 12, m['boxes']['tiles'][3] + 12],
                    color=AMBER, what='Focus spotlight on time-travel summary tiles')
    m, s = M['cutoff-receipt'], t
    b = m['boxes']
    t = max(end_of('c14') + 1.8, s + 4.0)
    crec_src = T.action_time('click-start', '$90.0 billion', M['cutoff-tiles']['t']) + 0.25
    shot_crec = footage('cutoff-receipt', crec_src, s, t,
                        [(0, frame(b, ['c8', 'c9'], pad=40, center=(436, 260), min_w=740), 0),
                         (1.8, (160, 42, 1120, 560), .9)],
                        limit=m['t'] + 9.0)
    focus_spotlight(shot_crec, 0.0, 1.9, [86, 40, 700, 540],
                    callout_css=[b['c8'][0] - 8, b['c8'][1] - 6, (b['c9'][0] + b['c9'][2]) - b['c8'][0] + 16, b['c8'][3] + 12],
                    color=AMBER, what='Focus spotlight on not-public-yet claims in draft as cursor clicks $90.0 billion')
    click_ripple(shot_crec, 0.95, 240, 270, color=AMBER, tag='CLICK · $90.0 billion',
                 what='Click ripple on $90.0 billion not-public-yet chip')
    focus_spotlight(shot_crec, 1.9, t - s, [816, 44, 384, 515],
                    callout_css=[822, 182, 368, 88],
                    color=AMBER, what='Focus spotlight on right receipt panel showing full EDGAR lookahead card and filed figure')
    # 6. A real SEC slide in one continuous take.
    m0, m1, m2, s = M['slide-tab'], M['slide-summary'], M['slide-regions'], t - .35
    say('c15', t + .1)
    src = m0['t'] - .3
    say('c16', max(end_of('c15') + .2, s + (m1['t'] - src) + .3))
    toggled = T.action_time('click-done', 'Show chart/table regions', m1['t'])
    b = m2['boxes']
    slide_upload = (60, 40, 1160, 580)
    status = (24, 28, 792, 396)
    image = (68, 8, 1144, 572)
    gaap = (80, 8, 1120, 560)
    to_image = toggled - src + .35                  # the page starts scrolling to the slide 0.4 s after the click
    highlight = max(to_image + 1.1, m2['t'] - src) + .1
    say('c17', max(end_of('c16') + .35, s + highlight - .9))
    push = T.end_of('c17') - s + .15
    t = say('c18', T.end_of('c17') + .45) + 1.0
    limit = T.action_time('click-done', 'Reading 10', m2['t']) + .1
    shot = footage('slide', src, s, t, [(0, slide_upload, 0), (m1['t'] - src - .1, status, .8), (to_image, image, 1.1), (push, gaap, 1.2)],
                   limit=limit, label='SAM 3.1 finds the chart regions · Muse Spark 1.3 reads the labels', fade=.35, corner='bottom-left')
    focus_spotlight(shot, 0.2, 2.3, [85, 210, 702, 305],
                    callout_css=[342, 338, 164, 44], color=AMBER,
                    what='Focus spotlight on Try a real SEC slide button click')
    click_ripple(shot, 1.25, 424, 360, color=AMBER, tag='CLICK · Try a real SEC slide',
                 what='Click ripple on Try a real SEC slide button')
    focus_spotlight(shot, 4.2, to_image - 0.1, [85, 36, 708, 206],
                    callout_css=None, color=AMBER,
                    what='Focus spotlight on 32 extracted readings summary')
    click_ripple(shot, 9.8, 630, 169, color=AMBER, tag='CLICK · Show chart regions',
                 what='Click ripple on Show chart/table regions toggle')
    sam_highlight(shot, M, highlight, push + .5)
    focus_spotlight(shot, push + 0.4, t - s, [[156, 72, 276, 210], [820, 28, 372, 415]],
                    callout_css=[180, 176, 242, 34], color=GREEN,
                    what='Dual focus spotlight on verified GAAP bar chart and right Verified readings list')
    click_ripple(shot, 27.6, 401, 190, color=GREEN, tag='CLICK · Reading 10 ($16.0B)',
                 what='Click ripple on Reading 10 ($16.0) bar label')
    m, s = M['slide-receipt'], t
    t = say('c19', s + .25) + 2.0
    shot_srec = footage('slide-receipt', m['t'] + .1, s, t,
                        [(0, frame(m['boxes'], ['panel'], center=(1008, 300), min_w=980), 0)],
                        limit=m['t'] + 4.5)
    focus_spotlight(shot_srec, 0.0, t - s, [816, 48, 384, 505], color=GREEN,
                    what='Focus spotlight on Reading 10 receipt panel so full citation is visible without left-column bleed')
    # 7. Ask: exact quotes from the whole 10-K in one request.
    m0, m1, s = M['ask-mode'], M['ask-result'], t - .35
    src = T.action_time('click-start', 'What does Meta plan', m0['t']) - 1.15
    t = say('c20', t + .2) + .4
    shot_ask = footage('ask', src, s, t, [(0, (60, 8, 1160, 580), 0)], limit=M['ask-done']['t'],
                       label='Muse Spark 1.3 · one request reads the whole 10-K', fade=.35)
    focus_spotlight(shot_ask, 0.0, t - s, [148, 12, 656, 560],
                    callout_css=[167, 460, 168, 46], color=GREEN,
                    what='Focus spotlight on Ask form, question selection, and Find exact quotes button click')
    click_ripple(shot_ask, 1.85, 340, 330, color=GREEN, tag='SELECT · Meta CapEx Question',
                 what='Click ripple on recorded Meta CapEx question')
    click_ripple(shot_ask, 4.05, 248, 482, color=GREEN, tag='CLICK · Find exact quotes',
                 what='Click ripple on Find exact quotes button')
    s = t
    t = say('c21', s + .2) + 1.1
    usage = m1['boxes']['usage']
    whole = frame(m1['boxes'], ['filing', 'usage', 'quote-1'], pad=32, center=(640, 245), min_w=1040)
    zoom_usage = frame(m1['boxes'], ['filing', 'usage', 'quote-1'], pad=24, center=(640, 220), min_w=990)
    shot_ask_res = footage('ask-result', m1['t'] + .1, s, t,
                           [(0, whole, 0), (1.4, zoom_usage, 0.9), (6.4, whole, 0.9)],
                           limit=M['ask-quotes']['t'] - .9)
    focus_spotlight(shot_ask_res, 1.1, 6.8, [148, 10, 984, 500],
                    callout_css=[usage[0] - 8, usage[1] - 2, usage[2] + 16, usage[3] + 8], color=GREEN,
                    what='Full-width view of Ask result with callout outline around 101,135 input tokens usage line')
    m, s = M['ask-quotes'], t
    t = say('c22', s + .2) + 1.5
    shot_quotes = footage('ask-quotes', m['t'] + .1, s, t,
                          [(0, frame(m['boxes'], ['quote-1', 'quote-2'], pad=32, center=(640, 435), min_w=1050), 0)],
                          limit=m['t'] + 6.0)
    focus_spotlight(shot_quotes, 0.0, t - s, [152, 320, 976, 232], color=GREEN,
                    what='Focus spotlight on verbatim 10-K quotes')
    # 8. One card: the same verifier behind four extraction backends.
    s = t - .4
    t = say('c23', t + .2) + .7
    card('backends', s, t, fade=.4)
    # 9. Optional: the recorded Private check (Muse Glimmer on Modal), replayed by the product.
    if private:
        P = private['marks']
        s = t - .35
        src = P['private-start']['t'] - .2
        say('c24', t + .2)
        t = max(end_of('c24') + .8, s + 4.5)
        form = frame(P['private-start']['boxes'], ['form'], pad=12)
        result = frame(P['private-result']['boxes'], ['result'], pad=20, center=(640, 325), min_w=1000)
        shot_priv = footage('private', src, s, t, [(0, form, 0), (P['private-result']['t'] - src - .3, result, .9)],
                            limit=P['private-end']['t'], master=private['master'],
                            label='Private check · Muse Glimmer on our own Modal GPU', fade=.35, corner='top-right')
        focus_spotlight(shot_priv, 0.0, 2.9, [212, 35, 856, 498],
                        callout_css=[235, 399, 267, 44], color=BLUE,
                        what='Focus spotlight on Private draft form and Replay button click')
        click_ripple(shot_priv, 1.85, 368, 421, color=BLUE, tag='CLICK · Replay Private check',
                     what='Click ripple on Replay a recorded Private check button')
        focus_spotlight(shot_priv, P['private-result']['t'] - src, t - s, [212, 168, 856, 460],
                        callout_css=[P['private-result']['boxes']['result'][0] - 6, P['private-result']['boxes']['result'][1] - 4,
                                     P['private-result']['boxes']['result'][2] + 12, P['private-result']['boxes']['result'][3] + 8],
                        color=BLUE, what='Focus spotlight on Modal Glimmer Private check results')
    # 10. Results card.
    s = t - .4
    assert say('c25', t + .1) <= t + .1 + RESULTS_LINE_S + .4, 'the results line no longer fits the results slot'
    t = t + .1 + RESULTS_LINE_S + 1.4
    card('results', s, t, fade=.4)
    # 11. Close.
    s = t - .4
    t = say('c26', t + .15) + 2.4
    card('end', s, t, fade=.4)
    return T.shots, T.said, round(t * FPS) / FPS


def build_x(marks, actions, speech):
    """The vertical 9:16 X & Threads cut (1080x1920): 5-Beat Mobile Story (Trap -> Check -> 10-Q Math -> Fix -> SAM 3.1)."""
    T = Timeline(marks, actions, speech)
    M, say, end_of, footage, card = T.M, T.say, T.end_of, T.footage, T.card
    m = M['cold-both']
    b = m['boxes']
    column = b['draft'][0] + b['draft'][2] / 2
    chip1 = frame(b, ['c2'], center=(column, 195), min_w=760)
    chip2 = frame(b, ['c8'], center=(column, 430), min_w=760)
    say('c01', .15)
    say('c02', end_of('c01') + .25)
    cold_end = M['tiles']['t'] - 1.0 - (m['t'] + .3)
    cold_switch = T.said['c02'] + 0.65
    footage('cold', m['t'] + .3, 0.0, cold_end, [(0, chip1, 0), (T.said['c02'] + 1.0, chip2, 1.0)], limit=M['tiles']['t'] - 1.0,
            switch_s=cold_switch,
            v_meta=('Muse Spark 1.3 · Deterministic Verifier', 'BEAT 1/5 · ERROR #2: “$2.22 EPS” IS NON-GAAP', RED, 0),
            v_meta_before=('Muse Spark 1.3 · Deterministic Verifier', 'BEAT 1/5 · ERROR #1: “18% YoY” IS ACTUALLY QoQ', RED, 0),
            v_connector='FLAGGED AGAINST SEC 10-Q FILING',
            v_panels=[
                {'box': (84, 380, 704, 155), 'box_before': (84, 25, 704, 165), 'switch_s': cold_switch,
                 'title': 'ERROR #2 IN DRAFT · “$2.22 DILUTED EPS” (NON-GAAP)',
                 'title_before': 'ERROR #1 IN DRAFT · “18% FROM A YEAR AGO” (QoQ vs YoY)',
                 'role': 'DRAFT CLAIM #2', 'role_before': 'DRAFT CLAIM #1',
                 'callout': (b['c8'][0] - 64, b['c8'][1] - 5, b['c8'][2] + 72, b['c8'][3] + 10),
                 'callout_before': (b['c2'][0] - 46, b['c2'][1] - 5, b['c2'][2] + 54, b['c2'][3] + 10)},
                {'box': (820, 24, 372, 380), 'title': 'LIVE SEC EDGAR RECEIPT · CLICK ANY FIGURE', 'role': 'SEC 10-Q RECEIPT'},
            ])
    s = cold_end
    t = max(end_of('c02') + .5, s + 2.5)
    tiles_src = M['draft-scrolled']['t'] + 1.3
    tiles_switch = 1.25
    shot_tiles_x = footage('tiles', tiles_src, s, t, [(0, frame(M['tiles']['boxes'], ['summary'], pad=26, center=(436, 185), min_w=780), 0)],
                           limit=M['receipt-growth']['t'] - 1.0,
                           switch_s=tiles_switch,
                           v_meta=('Muse Spark 1.3 · Deterministic Verifier', 'BEAT 2/5 · VERDICT SUMMARY: 7 VERIFIED, 2 WRONG', GREEN, 1),
                           v_meta_before=('Muse Spark 1.3 · Deterministic Verifier', 'BEAT 2/5 · PASTE DRAFT & CLICK CHECK', GREEN, 1),
                           v_connector='DETERMINISTIC VERIFIER GATE',
                           v_panels=[
                               {'box': (84, 150, 704, 165), 'box_before': (84, 55, 704, 575), 'switch_s': tiles_switch,
                                'title': 'VERDICT SUMMARY · 7 VERIFIED, 2 WRONG, 2 ?',
                                'title_before': 'NVIDIA Q2 DRAFT EDITOR · CLICK CHECK BUTTON',
                                'role': 'VERDICT GATE', 'role_before': 'DRAFT INPUT',
                                'callout': (M['tiles']['boxes']['tiles'][0] - 4, M['tiles']['boxes']['tiles'][1] - 4,
                                            M['tiles']['boxes']['tiles'][2] + 8, M['tiles']['boxes']['tiles'][3] + 8)},
                               {'box': (820, 24, 372, 420), 'box_before': (820, 35, 372, 280), 'switch_s': tiles_switch,
                                'title': 'ACTIVE FILING RECEIPT · 18% vs 105.85%',
                                'title_before': 'HOW A DRAFT GETS ITS RECEIPTS · 01 EXTRACT · 02 VERIFY',
                                'role': 'SEC 10-Q RECEIPT', 'role_before': 'PIPELINE FLOW'},
                           ])
    click_ripple(shot_tiles_x, 0.85, 703, 604, color=GREEN, tag='CLICK · Check',
                 what='Click ripple on Check button in 9:16 cut')
    s = t - .35
    say('c03', t + .1)
    t = end_of('c03') + .5
    card('title-x', s, t, fade=.35)
    m, s = M['receipt-growth'], t - .35
    b = m['boxes']
    line = frame(b, ['c2'], center=(M['cold-both']['boxes']['draft'][0] + M['cold-both']['boxes']['draft'][2] / 2, 195), min_w=760)
    receipt = frame(b, ['header', 'receipt-dl', 'calc'], pad=22, center=(1008, 302), min_w=960)
    say('c06', t + .1)
    t = say('c07', end_of('c06') + .3) + 1.0
    footage('receipt', m['t'] + .1, s, t, [(0, line, 0), (T.said['c07'] - s - .35, receipt, .9)], limit=M['receipt-sources']['t'] - 1.0, fade=.35,
            v_meta=('Muse Spark 1.3 · SEC 10-Q Receipt', 'BEAT 3/5 · REVENUE GROWTH RECEIPT (18% vs 105.85%)', AMBER, 2),
            v_connector='EXACT SEC 10-Q TABLE CALCULATION',
            v_panels=[
                {'box': (84, 40, 704, 195), 'title': 'CLAIM IN DRAFT · “18% FROM A YEAR AGO”', 'role': 'DRAFT CLAIM',
                 'callout': (b['c2'][0] - 46, b['c2'][1] - 5, b['c2'][2] + 54, b['c2'][3] + 10)},
                {'box': (820, 64, 372, 476), 'title': 'SEC 10-Q RECEIPT · EXACT TABLE MATH (105.85%)', 'role': 'SEC 10-Q PROOF',
                 'callout': (b['calc'][0] - 4, b['calc'][1] - 4, b['calc'][2] + 8, b['calc'][3] + 8)},
            ])
    # Split Fix into stationary click take (fix-click) and stationary result take (fix) to avoid mid-scroll frames
    m0, m1, s = M['fix-ready'], M['fix-result'], t - .35
    say('c10', t + .15)
    t = say('c11', max(end_of('c10') + .3, m1['t'] - m0['t'] + s + 1.0)) + 1.0
    b = m1['boxes']
    fix_click_end = s + 3.05
    shot_fix_click = footage('fix-click', m0['t'] - .2, s, fix_click_end, [(0, frame(m0['boxes'], ['fix-card'], pad=24), 0)],
                             limit=M['fix-result']['t'] - 1.4, fade=.35,
                             v_meta=('Muse Wonder 1.0 · Fix with Receipts', 'BEAT 4/5 · CLICK “FIX WITH RECEIPTS” BUTTON', AMBER, 3),
                             v_connector='PROPOSE REWRITE FROM VERIFIED RECEIPTS',
                             v_panels=[
                                 {'box': (84, 355, 1110, 145), 'title': 'ONE-CLICK FIX · CLICK “FIX WITH RECEIPTS”', 'role': 'MUSE WONDER 1.0',
                                  'callout': (1009, 406, 164, 44)},
                                 {'box': (84, 52, 704, 290), 'title': 'ORIGINAL DRAFT · 18% AND $2.22 FLAGGED FOR CORRECTION', 'role': 'DRAFT BEFORE FIX'},
                             ])
    click_ripple(shot_fix_click, 2.05, 1091, 428, color=GREEN, tag='CLICK · Fix with receipts',
                 what='Click ripple on Fix with receipts button in 9:16 cut')
    footage('fix', m1['t'] + .05, fix_click_end - .35, t,
            [(0, frame(b, ['counts'], center=(VIEW_W / 2, 250), min_w=1130), 0)],
            limit=M['fix-receipts']['t'] - 1.0, label='Muse Wonder 1.0 proposes · code constrains and rechecks', fade=.35,
            v_meta=('Muse Wonder 1.0 · Fix with Receipts', 'BEAT 4/5 · RECHECKED: 9 VERIFIED, 0 WRONG', GREEN, 3),
            v_connector='RECHECKED BY CODE · ZERO WRONG CLAIMS',
            v_panels=[
                {'box': (84, 12, 1110, 250), 'title': 'RECHECKED SUMMARY · 9 VERIFIED · 0 WRONG', 'role': 'VERIFIED RESULT',
                 'callout': (b['counts'][0] - 8, b['counts'][1] - 4, b['counts'][2] + 16, b['counts'][3] + 10)},
                {'box': (636, 260, 558, 360), 'title': 'REVISED DRAFT · CORRECTED FIGURES WITH RECEIPTS', 'role': 'REVISED DRAFT'},
            ])
    m, s = M['slide-regions'], t - .35
    src = m['t'] + .05
    say('c17', t + .5)
    t = end_of('c17') + .8
    shot = footage('slide', src, s, t, [(0, frame(m['boxes'], ['image'], pad=16, center=(440, 275), min_w=1060), 0)],
                   limit=M['slide-receipt']['t'] - 2.0, fade=.35,
                   v_meta=('SAM 3.1 + Muse Spark 1.3', 'BEAT 5/5 · REAL SEC SLIDE · 2 SAM BAR CHARTS', AMBER, 4),
                   v_connector='SAM 3.1 REGIONS :: VERIFIED READINGS',
                   v_panels=[
                       {'box': (96, 10, 686, 530), 'title': 'BANK OF AMERICA SEC SLIDE · SAM 3.1 REGIONS', 'role': 'SAM 3.1 VISION', 'sam': True},
                       {'box': (820, 12, 372, 335), 'title': 'EXTRACTED READINGS · 5 GAAP VERIFIED, 27 ?', 'role': 'VERIFIED READINGS'},
                   ])
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
        self.proc = subprocess.Popen(['ffmpeg', '-v', 'fatal', '-ss', f'{start:.4f}', '-i', str(master), '-frames:v', str(frames),
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


def fast_blur_darken(array, dim=0.52):
    """Fast bokeh blur + studio darkening for out-of-focus background columns."""
    h, w = array.shape[:2]
    img = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))
    small = img.resize((max(1, w // 4), max(1, h // 4)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(3.5))
    blurred = np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)
    tint = np.array(DARK, dtype=np.float32)
    return blurred * (1.0 - dim) + tint * dim


def _draw_click_tag(ld, px, py, tag, col, p, out_w, out_h):
    """Draw a floating high-contrast action badge next to an active cursor click ripple."""
    if not tag:
        return
    a_tag = min(ease(p / 0.18), 1.0 - ease((p - 0.72) / 0.28))
    if a_tag <= 0.02:
        return
    fnt = font('mono', 18, 600)
    tw = ld.textlength(tag, font=fnt)
    pw, ph = int(round(tw + 48)), 36
    # Prefer placing pill above-left of the click if near right edge, otherwise above-right
    tx0 = px - pw - 18 if px + pw + 28 > out_w else px + 18
    ty0 = py - ph - 14 if py - ph - 18 > 12 else py + 16
    tx0 = min(max(12, int(round(tx0))), out_w - pw - 12)
    ty0 = min(max(12, int(round(ty0))), out_h - ph - 12)
    alpha_bg = int(round(244 * a_tag))
    alpha_fg = int(round(255 * a_tag))
    ld.rounded_rectangle((tx0, ty0, tx0 + pw, ty0 + ph), radius=18, fill=(14, 25, 20, alpha_bg), outline=col + (alpha_fg,), width=2)
    ld.ellipse((tx0 + 14, ty0 + 13, tx0 + 24, ty0 + 23), fill=col + (alpha_fg,))
    ld.text((tx0 + 32, ty0 + 24), tag, font=fnt, fill=CREAM + (alpha_fg,), anchor='ls')


def draw_overlays(array, shot, local, cam):
    out_h, out_w = array.shape[:2]
    blurred_bg = None
    x, y, w, h = cam
    sx, sy = out_w / w, out_h / h
    for overlay in shot['overlays']:
        kind = overlay['kind']
        if kind == 'click-ripple':
            if not (overlay['start'] <= local <= overlay['end']):
                continue
            p = (local - overlay['start']) / max(0.01, overlay['end'] - overlay['start'])
            cx, cy = overlay['center_css']
            px, py = (cx - x) * sx, (cy - y) * sy
            col = overlay.get('color', GREEN)
            layer = Image.new('RGBA', (out_w, out_h), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            r1 = 10 + 44 * ease_out(p)
            a1 = int(round(245 * (1.0 - p)))
            ld.ellipse((px - r1, py - r1, px + r1, py + r1), outline=col + (a1,), width=4)
            if p > 0.10:
                p2 = (p - 0.10) / 0.90
                r2 = 6 + 28 * ease_out(p2)
                a2 = int(round(215 * (1.0 - p2)))
                ld.ellipse((px - r2, py - r2, px + r2, py + r2), outline=CREAM + (a2,), width=3)
            a_dot = int(round(255 * (1.0 - p * 0.7)))
            ld.ellipse((px - 5, py - 5, px + 5, py + 5), fill=col + (a_dot,))
            _draw_click_tag(ld, px, py, overlay.get('tag', ''), col, p, out_w, out_h)
            lay = np.asarray(layer, dtype=np.float32)
            alpha = lay[..., 3:4] / 255.0
            array[:] = array * (1.0 - alpha) + lay[..., :3] * alpha
            continue
        if kind not in ('sam-highlight', 'focus-spotlight'):
            continue
        a = min(ease((local - overlay['start']) / .35), 1 - ease((local - (overlay['end'] - .35)) / .35))
        if a <= 0:
            continue
        rects = [((bx - x) * sx - 5, (by - y) * sy - 5, (bx + bw - x) * sx + 5, (by + bh - y) * sy + 5) for bx, by, bw, bh in overlay['boxes_css']]
        mask = Image.new('L', (out_w, out_h), 0)
        md = ImageDraw.Draw(mask)
        for r in rects:
            md.rounded_rectangle(r, radius=14, fill=255)
        mask_arr = np.asarray(mask.filter(ImageFilter.GaussianBlur(6)), dtype=np.float32)[..., None] / 255.0

        if kind == 'focus-spotlight':
            if blurred_bg is None:
                blurred_bg = fast_blur_darken(array, dim=0.55)
            blend = a * (1.0 - mask_arr)
            array[:] = array * (1.0 - blend) + blurred_bg * blend
            layer = Image.new('RGBA', (out_w, out_h), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            for r in rects:
                ld.rounded_rectangle(r, radius=14, outline=LINE + (210,), width=2)
            if overlay.get('callout_css'):
                cx, cy, cw, ch = overlay['callout_css']
                cr = ((cx - x) * sx - 4, (cy - y) * sy - 4, (cx + cw - x) * sx + 4, (cy + ch - y) * sy + 4)
                p = min(max((local - overlay['start'] - .1) / .55, 0.0), 1.0)
                if p > 0:
                    pts = perimeter(cr, p)
                    col = overlay.get('color', GREEN)
                    ld.line(pts, fill=DARK + (235,), width=9, joint='curve')
                    ld.line(pts, fill=col + (255,), width=4, joint='curve')
            lay = np.asarray(layer, dtype=np.float32)
            alpha = lay[..., 3:4] / 255.0 * a
            array[:] = array * (1.0 - alpha) + lay[..., :3] * alpha
        else:
            array *= 1 - DIM * a * (1 - mask_arr)
            layer = Image.new('RGBA', (out_w, out_h), (0, 0, 0, 0))
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
    out_h, out_w = array.shape[:2]
    for label in shot['labels']:
        if not (label['start'] <= local < label['end']):
            continue
        alpha = min(ease((local - label['start']) / .35), 1 - ease((local - (label['end'] - .4)) / .35))
        if alpha <= 0:
            continue
        chip = chips[(label['text'], label['dot'])]
        ch, cw = chip.shape[:2]
        top = 20 if label['corner'].startswith('top') else out_h - 20 - ch
        left = 24 if label['corner'].endswith('left') else out_w - 24 - cw
        region = array[top:top + ch, left:left + cw]
        a = chip[:, :, 3:4] * alpha
        region[:] = region * (1 - a) + chip[:, :, :3] * a


# --------------------------------------------------------------------------- 16:9 Cinema Studio Stage (floating macOS window + workflow HUD)
WIN_X0, WIN_Y0 = 112, 100
WIN_W, WIN_H = 1696, 848                        # exact 2.0:1 aspect ratio matching 1280x640 capture viewport
STUDIO_STEPS = ['01 Check', '02 Receipts', '03 Fix', '04 Cutoff', '05 Slide', '06 Ask 10-K', '07 Private']
STUDIO_META = {
    'cold':           (0, 'Muse Spark 1.3 · Verifier',      'check · nvidia-q2-fy2027 (planted errors)', 'AUDIT · 2 Planted Errors Flagged', RED),
    'check':          (0, 'Muse Spark 1.3 · Extractor',     'check · nvidia-q2-fy2027',                  'ACTION · Paste Draft & Click Check', GREEN),
    'tiles':          (0, 'Muse Spark 1.3 · Verifier Gate', 'check · verdict-summary (7 verified · 2 wrong · 2 ?)', 'RESULT · 11 Claims Classified', GREEN),
    'receipt':        (1, 'Muse Spark 1.3 · SEC 10-Q Math', 'receipt · data-center-yoy (18% vs 105.85%)','INSPECT · 18% QoQ vs 105.85% YoY', AMBER),
    'sources':        (1, 'SEC EDGAR · Provenance',         'receipt · edgar-accession-provenance',      'PROVENANCE · SEC Filing Citation', GREEN),
    'eps':            (1, 'Muse Spark 1.3 · SEC 8-K',       'receipt · gaap-diluted-eps ($2.22 vs $2.46)','ACTION · Click $2.22 EPS Chip', RED),
    'fix':            (2, 'Muse Wonder 1.0 · Fix',          'fix · one-click-fix-with-receipts',         'ACTION · Click Fix with receipts', GREEN),
    'fix-receipts':   (2, 'Muse Wonder 1.0 · Digit Gate',   'fix · rechecked-9-verified-0-wrong',        'VERIFIED · Only Filed Digits Changed', GREEN),
    'cutoff':         (3, 'SEC EDGAR · Time Travel',        'check · june-quarter-roundup (Jul 29)',     'SELECT · July 29 Cutoff Timestamp', AMBER),
    'cutoff-tiles':   (3, 'SEC EDGAR · Anti-Lookahead',     'check · cutoff-summary (3 not public yet)', 'GATE · 3 Future Filings Held Back', AMBER),
    'cutoff-receipt': (3, 'SEC EDGAR · Acceptance Gate',    'receipt · apple-q3-10q-aug-01-lookahead',   'ACTION · Click $90.0B Chip', AMBER),
    'slide':          (4, 'SAM 3.1 + Muse Spark 1.3',       'slide · bofa-q2-2025-earnings-slide',       'ACTION · Try SEC Slide & Regions', AMBER),
    'slide-receipt':  (4, 'SAM 3.1 + Muse Spark 1.3',       'slide · reading-10-net-income ($16.0B)',    'VERIFIED · Slide Bar Matches 10-Q', GREEN),
    'ask':            (5, 'Muse Spark 1.3 · Full 10-K',     'ask · meta-fy2025-10k-grounding',           'ACTION · Select Question & Ask 10-K', GREEN),
    'ask-result':     (5, 'Muse Spark 1.3 · 101K Tokens',   'ask · single-request-full-10k-read',        'RESULT · 101,135 Tokens in 1 Request', GREEN),
    'ask-quotes':     (5, 'Muse Spark 1.3 · Exact Quotes',  'ask · exact-substring-verified-quotes',     'VERIFIED · Exact Substring Match', GREEN),
    'private':        (6, 'Muse Glimmer · Modal H100',      'private · modal-h100-unpublished-draft',    'ACTION · Replay Private GPU Check', BLUE),
}
_STUDIO_STAGE_CACHE = {}
_WIN_MASK = None


def studio_stage_16x9(shot_name):
    """Render and cache the 1920x960 Cinema Studio Stage backdrop, top 7-step interactive workflow stepper HUD,
    and macOS floating browser window chrome for `shot_name`."""
    global _WIN_MASK
    if _WIN_MASK is None:
        m = Image.new('L', (WIN_W, WIN_H), 0)
        md = ImageDraw.Draw(m)
        md.rounded_rectangle((0, -20, WIN_W - 1, WIN_H - 1), radius=14, fill=255)
        _WIN_MASK = np.asarray(m, dtype=np.float32)[..., None] / 255.0

    meta = STUDIO_META.get(shot_name, (0, 'Muse Spark 1.3', 'receipts-nocap.vercel.app', 'LIVE · Interactive Walkthrough', GREEN))
    if meta not in _STUDIO_STAGE_CACHE:
        step_idx, model_badge, win_title, action_badge, accent = meta
        c = Canvas((W, PRODUCT_H), DARK, ss=2, grid=True)
        # Top brand mark (left)
        c.logo((112, 14), 34)
        c.text((154, 41), 'NoCap', 'serif', 29, 600, CREAM, 'ls', 48)
        # Top 7-step interactive workflow stepper (center)
        sx = 256
        for i, step_label in enumerate(STUDIO_STEPS):
            sw = c.width(step_label, 'sans', 15, 600) + (38 if i <= step_idx else 26)
            if i < step_idx:
                c.rect((sx, 15, sx + sw, 47), radius=16, fill=(18, 42, 32), outline=(50, 108, 78), width=1.5)
                c.check((sx + 16, 31), 10, GREEN, 2.5)
                c.text((sx + 27, 37), step_label, 'sans', 15, 600, GREEN, 'ls')
            elif i == step_idx:
                c.rect((sx, 14, sx + sw, 48), radius=17, fill=(24, 46, 36), outline=accent, width=2.2)
                c.rect((sx + 12, 27, sx + 20, 35), radius=4, fill=accent)
                c.text((sx + 27, 37), step_label, 'sans', 15, 700, CREAM, 'ls')
            else:
                c.rect((sx, 15, sx + sw, 47), radius=16, fill=(17, 29, 23), outline=(36, 52, 44), width=1)
                c.text((sx + 13, 37), step_label, 'sans', 15, 500, MUTED, 'ls')
            sx += sw + 8
        # Active Meta AI Model pill (top right)
        mw = c.width(model_badge, 'sans', 16, 600) + 44
        c.rect((1808 - mw, 14, 1808, 48), radius=17, fill=PANEL, outline=accent, width=2)
        c.rect((1808 - mw + 14, 27, 1808 - mw + 22, 35), radius=4, fill=accent)
        c.text((1808 - mw + 30, 37), model_badge, 'sans', 16, 600, CREAM, 'ls')

        # Floating macOS Browser Window outer shadow & frame
        c.rect((WIN_X0 - 6, 58, WIN_X0 + WIN_W + 6, WIN_Y0 + WIN_H + 6), radius=20, fill=(8, 15, 12))
        c.rect((WIN_X0 - 2, 60, WIN_X0 + WIN_W + 2, WIN_Y0 + WIN_H + 2), radius=16, fill=(20, 33, 27), outline=LINE, width=2)
        # macOS Window Chrome Title Bar (y = 62 .. 100)
        for idx_dot, dot_col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            dx = WIN_X0 + 22 + idx_dot * 20
            c.rect((dx - 6, 75, dx + 6, 87), radius=6, fill=dot_col)
        # Centered URL / Route Omnibox
        url_txt = f'{LIVE_URL}  /  {win_title}'
        aw = c.width(action_badge, 'mono', 14, 600) + 42
        ax0 = WIN_X0 + WIN_W - 14 - aw
        max_uw = max(480, (ax0 - 28) - (WIN_X0 + 100))
        uw = min(max_uw, max(520, c.width(url_txt, 'mono', 15, 500) + 64))
        ux0 = W / 2 - uw / 2 - 40
        ux0 = min(ux0, ax0 - 24 - uw)
        c.rect((ux0, 68, ux0 + uw, 94), radius=8, fill=(13, 23, 19), outline=(38, 55, 47), width=1.2)
        c.rect((ux0 + 14, 77, ux0 + 22, 85), radius=4, fill=GREEN)
        c.text((ux0 + uw / 2 + 6, 86), url_txt, 'mono', 15, 500, MUTED, 'ms')
        # Right Live Action Status Badge inside title bar
        c.rect((ax0, 68, WIN_X0 + WIN_W - 14, 94), radius=13, fill=(16, 30, 24), outline=accent, width=1.5)
        c.rect((ax0 + 12, 77, ax0 + 20, 85), radius=4, fill=accent)
        c.text((ax0 + 27, 86), action_badge, 'mono', 14, 600, CREAM, 'ls')
        c.line([(WIN_X0, WIN_Y0), (WIN_X0 + WIN_W, WIN_Y0)], LINE, 1.5)
        _STUDIO_STAGE_CACHE[meta] = np.asarray(c.result().convert('RGB'), dtype=np.float32)
    return _STUDIO_STAGE_CACHE[meta].copy(), _WIN_MASK


_V_HEADER_CACHE = {}
_V_BAR_CACHE = {}
_V_CONN_CACHE = {}
V_BEATS = ['01 TRAP', '02 CHECK', '03 MATH', '04 FIX', '05 SLIDE']


def vertical_header(model_badge, scene_title, accent, beat_idx=0):
    key = (model_badge, scene_title, accent, beat_idx)
    if key not in _V_HEADER_CACHE:
        c = Canvas((W_X, 156), DARK, ss=2, grid=True)
        c.logo((36, 16), 48)
        c.text((96, 52), 'NoCap', 'serif', 44, 600, CREAM, 'ls', 56)
        c.text((246, 50), '·  SEC Filing Verifier', 'sans', 20, 500, MUTED, 'ls')
        # Right model pill
        pw = c.width(model_badge, 'sans', 19, 600) + 48
        c.rect((W_X - 36 - pw, 18, W_X - 36, 60), radius=21, fill=PANEL, outline=accent, width=1.8)
        c.rect((W_X - 36 - pw + 14, 34, W_X - 36 - pw + 24, 44), radius=5, fill=accent)
        c.text((W_X - 36 - pw + 34, 45), model_badge, 'sans', 19, 600, CREAM, 'ls')

        # Streamlined 2-tier HUD: 5 beat pills combined with current action title
        strip_x0, strip_w = 36, W_X - 72
        pill_gap = 10
        pill_w = (strip_w - pill_gap * 4) / 5
        for i, b_lbl in enumerate(V_BEATS):
            px0 = strip_x0 + i * (pill_w + pill_gap)
            px1 = px0 + pill_w
            if i < beat_idx:
                c.rect((px0, 72, px1, 108), radius=10, fill=(14, 42, 30), outline=(38, 118, 78), width=1.5)
                tw = c.width(b_lbl, 'sans', 16, 600)
                dx = (px0 + px1) / 2 - (tw + 14) / 2
                c.check((dx + 4, 90), 10, GREEN, 2.5)
                c.text((dx + 16, 95), b_lbl, 'sans', 16, 600, GREEN, 'ls')
            elif i == beat_idx:
                c.rect((px0, 72, px1, 108), radius=10, fill=(24, 42, 34), outline=accent, width=2.0)
                tw = c.width(b_lbl, 'sans', 16, 700)
                dx = (px0 + px1) / 2 - (tw + 14) / 2
                c.rect((dx, 85, dx + 8, 93), radius=4, fill=accent)
                c.text((dx + 15, 95), b_lbl, 'sans', 16, 700, CREAM, 'ls')
            else:
                c.rect((px0, 72, px1, 108), radius=10, fill=(15, 25, 20), outline=LINE, width=1.0)
                c.text(((px0 + px1) / 2, 95), b_lbl, 'sans', 16, 500, MUTED, 'ms')

        # Action title line under the beat pills
        c.rect((36, 116, W_X - 36, 148), radius=8, fill=(14, 25, 20), outline=LINE, width=1.2)
        c.rect((36, 116, 44, 148), radius=3, fill=accent)
        c.text((58, 137), scene_title, 'mono', 18, 600, CREAM, 'ls', spacing=1)
        _V_HEADER_CACHE[key] = np.asarray(c.result().convert('RGB'), dtype=np.float32)
    return _V_HEADER_CACHE[key]


def render_vertical_footage(image, shot, local):
    """Render a 1080x1620 vertical studio frame for X and Threads by stacking the active browser cards vertically."""
    canvas = np.zeros((PRODUCT_H_X, W_X, 3), dtype=np.float32)
    canvas[:] = np.array(DARK, dtype=np.float32)
    v_meta = shot.get('v_meta')
    if shot.get('v_meta_before') and shot.get('switch_s') is not None and local < shot['switch_s']:
        v_meta = shot['v_meta_before']
    if v_meta and len(v_meta) == 4:
        model_badge, scene_title, accent, beat_idx = v_meta
    elif v_meta:
        model_badge, scene_title, accent = v_meta
        beat_idx = 0
    else:
        model_badge, scene_title, accent, beat_idx = ('Muse Spark 1.3', 'NOCAP · SEC FILING VERIFIER', GREEN, 0)
    header = vertical_header(model_badge, scene_title, accent, beat_idx)
    canvas[:156, :] = header

    panels = shot.get('v_panels') or [{'box': camera_at(shot['cams'], local), 'title': 'LIVE INTERFACE'}]
    avail_y0, avail_y1 = 162, PRODUCT_H_X - 14
    gap = 36
    card_w = W_X - 72  # 1008px wide

    # Compute target boxes for each stacked panel (with clean transition at switch_s after page scroll settles)
    resolved = []
    for p in panels:
        bx, by, bw, bh = p['box']
        p_title = p.get('title', '')
        p_role = p.get('role', '')
        callout = p.get('callout')
        c_start = 0.15
        if 'box_before' in p and 'switch_s' in p:
            if local < p['switch_s'] + 0.12:
                p_title = p.get('title_before', p_title)
                p_role = p.get('role_before', p_role)
                callout = p.get('callout_before', callout)
            else:
                if 'callout_before' in p:
                    c_start = p['switch_s'] + 0.18
            k = ease((local - p['switch_s']) / 0.22)
            bx0, by0, bw0, bh0 = p['box_before']
            bx = bx0 + (bx - bx0) * k
            by = by0 + (by - by0) * k
            bw = bw0 + (bw - bw0) * k
            bh = bh0 + (bh - bh0) * k
        resolved.append((bx, by, bw, bh, p_title, p_role, p.get('sam', False), callout, c_start))

    # Scale widths to card_w, and cap total height to fit avail_y1 - avail_y0
    title_h = 46
    raw_heights = [int(round(card_w * (bh / bw))) for _, _, bw, bh, _, _, _, _, _ in resolved]
    max_content_h = (avail_y1 - avail_y0) - len(resolved) * (title_h + 12) - (len(resolved) - 1) * gap
    scale_f = min(1.0, max_content_h / max(1, sum(raw_heights)))
    heights = [max(160, int(round(rh * scale_f))) for rh in raw_heights]
    total_stack_h = sum(h + title_h for h in heights) + (len(resolved) - 1) * gap
    cur_y = avail_y0 + max(0, (avail_y1 - avail_y0 - total_stack_h) // 2)

    for p_idx, ((bx, by, bw, bh, p_title, p_role, is_sam, callout, c_start), ch) in enumerate(zip(resolved, heights)):
        cw = int(round(ch * (bw / bh)))
        cw = min(card_w, cw)
        cx0 = (W_X - cw) // 2
        # Card header bar (macOS/Studio style, cached)
        bar_key = (cw, title_h, p_title, p_role, accent)
        if bar_key not in _V_BAR_CACHE:
            bar = Canvas((cw, title_h), PANEL, ss=2)
            bar.rect((0, 0, cw, title_h), radius=12, fill=PANEL, outline=LINE, width=2)
            bar.rect((14, 17, 24, 27), radius=5, fill=accent)
            max_tw = cw - 50 - (bar.width(p_role, 'mono', 14, 600) + 40 if p_role else 0)
            disp_title = p_title
            while bar.width(disp_title, 'mono', 18, 600) > max_tw and len(disp_title) > 8:
                disp_title = disp_title[:-2] + '…'
            bar.text((34, 30), disp_title, 'mono', 18, 600, CREAM, 'ls')
            if p_role:
                rw = bar.width(p_role, 'mono', 14, 600) + 28
                bar.rect((cw - rw - 12, 9, cw - 12, 37), radius=9, fill=(16, 30, 24), outline=accent, width=1.5)
                bar.text((cw - rw / 2 - 12, 28), p_role, 'mono', 14, 600, accent, 'ms')
            _V_BAR_CACHE[bar_key] = np.asarray(bar.result().convert('RGB'), dtype=np.float32)
        canvas[cur_y:cur_y + title_h, cx0:cx0 + cw] = _V_BAR_CACHE[bar_key]
        cur_y += title_h

        # Crop and sharpen from 2x master with boundary clamping
        crop_x0 = max(0, min(VIEW_W * SCALE - 2, int(round(bx * SCALE))))
        crop_y0 = max(0, min(VIEW_H * SCALE - 2, int(round(by * SCALE))))
        crop_x1 = max(crop_x0 + 2, min(VIEW_W * SCALE, int(round((bx + bw) * SCALE))))
        crop_y1 = max(crop_y0 + 2, min(VIEW_H * SCALE, int(round((by + bh) * SCALE))))
        crop_box = (crop_x0, crop_y0, crop_x1, crop_y1)
        sub_img = image.resize((cw, ch), Image.LANCZOS, box=crop_box).filter(UNSHARP)
        sub_arr = np.asarray(sub_img, dtype=np.float32).copy()
        sx, sy = cw / bw, ch / bh

        # Draw animated callout outline if specified on this vertical panel
        if callout is not None:
            ccx, ccy, ccw, cch = callout
            if bx - 12 <= ccx and ccx + ccw <= bx + bw + 12 and by - 12 <= ccy and ccy + cch <= by + bh + 12:
                cr = ((ccx - bx) * sx - 4, (ccy - by) * sy - 4, (ccx + ccw - bx) * sx + 4, (ccy + cch - by) * sy + 4)
                p_c = min(max((local - c_start) / 0.55, 0.0), 1.0)
                if p_c > 0:
                    layer = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
                    ld = ImageDraw.Draw(layer)
                    pts = perimeter(cr, p_c)
                    ld.line(pts, fill=DARK + (235,), width=9, joint='curve')
                    ld.line(pts, fill=accent + (255,), width=4, joint='curve')
                    lay = np.asarray(layer, dtype=np.float32)
                    alpha = lay[..., 3:4] / 255.0
                    sub_arr[:] = sub_arr * (1.0 - alpha) + lay[..., :3] * alpha

        # Draw any active click ripple + action tag inside this panel
        for overlay in shot['overlays']:
            if overlay['kind'] == 'click-ripple' and overlay['start'] <= local <= overlay['end']:
                rcx, rcy = overlay['center_css']
                if bx <= rcx <= bx + bw and by <= rcy <= by + bh:
                    p = (local - overlay['start']) / max(0.01, overlay['end'] - overlay['start'])
                    px, py = (rcx - bx) * sx, (rcy - by) * sy
                    col = overlay.get('color', GREEN)
                    layer = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
                    ld = ImageDraw.Draw(layer)
                    r1 = 8 + 38 * ease_out(p)
                    a1 = int(round(245 * (1.0 - p)))
                    ld.ellipse((px - r1, py - r1, px + r1, py + r1), outline=col + (a1,), width=4)
                    if p > 0.10:
                        p2 = (p - 0.10) / 0.90
                        r2 = 5 + 22 * ease_out(p2)
                        a2 = int(round(215 * (1.0 - p2)))
                        ld.ellipse((px - r2, py - r2, px + r2, py + r2), outline=CREAM + (a2,), width=3)
                    a_dot = int(round(255 * (1.0 - p * 0.7)))
                    ld.ellipse((px - 5, py - 5, px + 5, py + 5), fill=col + (a_dot,))
                    _draw_click_tag(ld, px, py, overlay.get('tag', ''), col, p, cw, ch)
                    lay = np.asarray(layer, dtype=np.float32)
                    alpha = lay[..., 3:4] / 255.0
                    sub_arr[:] = sub_arr * (1.0 - alpha) + lay[..., :3] * alpha

        # If this panel has SAM highlight, draw the animated amber outlines on it
        if is_sam:
            for overlay in shot['overlays']:
                if overlay['kind'] == 'sam-highlight':
                    a = min(ease((local - overlay['start']) / .45), 1 - ease((local - (overlay['end'] - .5)) / .5))
                    if a > 0:
                        rects = [((rx - bx) * sx - 4, (ry - by) * sy - 4, (rx + rw - bx) * sx + 4, (ry + rh - by) * sy + 4)
                                 for rx, ry, rw, rh in overlay['boxes_css']]
                        mask = Image.new('L', (cw, ch), 0)
                        md = ImageDraw.Draw(mask)
                        for r in rects:
                            md.rounded_rectangle(r, radius=8, fill=255)
                        mask_arr = np.asarray(mask.filter(ImageFilter.GaussianBlur(4)), dtype=np.float32)[..., None] / 255
                        sub_arr *= 1 - DIM * a * (1 - mask_arr)
                        layer = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
                        ld = ImageDraw.Draw(layer)
                        for i, r in enumerate(rects):
                            p_prog = min(max((local - overlay['start'] - .15 - .35 * i) / .8, 0.0), 1.0)
                            if p_prog > 0:
                                pts = perimeter(r, p_prog)
                                ld.line(pts, fill=DARK + (235,), width=8, joint='curve')
                                ld.line(pts, fill=AMBER + (255,), width=4, joint='curve')
                        lay = np.asarray(layer, dtype=np.float32)
                        alpha = lay[..., 3:4] / 255 * a
                        sub_arr[:] = sub_arr * (1 - alpha) + lay[..., :3] * alpha

        canvas[cur_y:cur_y + ch, cx0:cx0 + cw] = np.clip(sub_arr, 0, 255)
        # Subtle border around the product panel
        canvas[cur_y:cur_y + 2, cx0:cx0 + cw] = LINE
        canvas[cur_y + ch - 2:cur_y + ch, cx0:cx0 + cw] = LINE
        canvas[cur_y:cur_y + ch, cx0:cx0 + 2] = LINE
        canvas[cur_y:cur_y + ch, cx0 + cw - 2:cx0 + cw] = LINE

        # C2: First 2.5s high-contrast attention hook sticker on cold open
        if shot['name'] == 'cold' and p_idx == 0 and local < 2.5:
            banner_a = min(1.0, (2.5 - local) / 0.35)
            if banner_a > 0:
                bh_w, bh_h = 760, 42
                bx_l = (W_X - bh_w) // 2
                by_t = cur_y + 14
                layer = Image.new('RGBA', (bh_w, bh_h), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle((0, 0, bh_w, bh_h), radius=10, fill=(58, 20, 20, int(245 * banner_a)),
                                     outline=RED + (int(255 * banner_a),), width=2)
                ld.text((bh_w / 2, bh_h / 2 + 1), 'AI DRAFTED AN EARNINGS STORY · 2 PLANTED ERRORS',
                        font=font('mono', 17, 700), fill=CREAM + (int(255 * banner_a),), anchor='mm')
                bl_arr = np.asarray(layer, dtype=np.float32)
                alpha_bl = bl_arr[..., 3:4] / 255.0
                canvas[by_t:by_t + bh_h, bx_l:bx_l + bh_w] = (
                    canvas[by_t:by_t + bh_h, bx_l:bx_l + bh_w] * (1.0 - alpha_bl) + bl_arr[..., :3] * alpha_bl
                )

        # Draw vertical flow connector pill in the gap between panel 0 and panel 1
        if p_idx == 0 and len(resolved) > 1:
            conn_txt = shot.get('v_connector', 'VERIFIED AGAINST SEC EDGAR FILING')
            conn_key = (conn_txt, accent)
            if conn_key not in _V_CONN_CACHE:
                cc = Canvas((W_X, gap), DARK, ss=2)
                # Compute exact text width with letter spacing accounted for
                raw_tw = cc.width(conn_txt, 'mono', 15, 600) + len(conn_txt) * 1.5
                cpw = raw_tw + 96
                cc.line([(W_X / 2, 0), (W_X / 2, gap)], accent, 2)
                x_l, x_r = W_X / 2 - cpw / 2, W_X / 2 + cpw / 2
                cc.rect((x_l, 4, x_r, gap - 4), radius=14, fill=(16, 34, 26), outline=accent, width=1.6)
                cy_m = gap / 2
                # Chevrons well separated from text (18px inward from pill edge, >16px from text)
                cc.line([(x_l + 14, cy_m - 3), (x_l + 20, cy_m + 3), (x_l + 26, cy_m - 3)], accent, 2.2)
                cc.line([(x_r - 26, cy_m - 3), (x_r - 20, cy_m + 3), (x_r - 14, cy_m - 3)], accent, 2.2)
                cc.text((W_X / 2, cy_m + 5), conn_txt, 'mono', 15, 600, CREAM, 'ms', spacing=1)
                _V_CONN_CACHE[conn_key] = np.asarray(cc.result().convert('RGB'), dtype=np.float32)
            gy0 = cur_y + ch
            if gy0 + gap <= PRODUCT_H_X:
                canvas[gy0:gy0 + gap, :] = _V_CONN_CACHE[conn_key]
        cur_y += ch + gap

    return canvas


def _perspective_coeffs(src_pts, dst_pts):
    """8-parameter perspective transform coefficients mapping dst_pts -> src_pts for PIL Image.PERSPECTIVE."""
    matrix = []
    for (dx, dy), (sx, sy) in zip(dst_pts, src_pts):
        matrix.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        matrix.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
    return np.linalg.solve(np.asarray(matrix, dtype=np.float64), np.asarray(src_pts, dtype=np.float64).reshape(8)).tolist()


SHOTCRAFT_REVEALS = {'check', 'cutoff', 'slide', 'ask', 'private'}


def _apply_shotcraft_reveal(stage, shot_name, local):
    """video-shotcraft style 3D perspective window tilt-in reveal + diagonal specular glass sweep on workflow openings."""
    if shot_name not in SHOTCRAFT_REVEALS or local >= 0.72:
        return stage
    k = 1.0 - ease_out(local / 0.72)
    if k <= 0.01:
        return stage
    wx0, wy0, wx1, wy1 = WIN_X0 - 6, 56, WIN_X0 + WIN_W + 6, WIN_Y0 + WIN_H + 6
    ww, wh = wx1 - wx0, wy1 - wy0
    win_crop = Image.fromarray(np.clip(stage[wy0:wy1, wx0:wx1], 0, 255).astype(np.uint8))
    # Subtle 3D pitch + yaw trapezoid perspective tilt that settles smoothly to 0 deg
    dx_top = 26.0 * k
    dy_top = 16.0 * k
    yaw_asym = 8.0 * k
    src_pts = [(0, 0), (ww, 0), (ww, wh), (0, wh)]
    dst_pts = [(dx_top - yaw_asym, dy_top), (ww - dx_top - yaw_asym, dy_top * 0.65),
               (ww + yaw_asym * 0.4, wh - dy_top * 0.25), (-yaw_asym * 0.4, wh)]
    coeffs = _perspective_coeffs(src_pts, dst_pts)
    warped = np.asarray(win_crop.transform((ww, wh), Image.PERSPECTIVE, coeffs, Image.BICUBIC), dtype=np.float32)
    mask_img = Image.new('L', (ww, wh), 255).transform((ww, wh), Image.PERSPECTIVE, coeffs, Image.BILINEAR)
    mask_arr = np.asarray(mask_img, dtype=np.float32)[..., None] / 255.0
    # Diagonal specular glass light sweep across the window during the reveal
    sweep_x = ww * ease_out(local / 0.72)
    xg = np.arange(ww, dtype=np.float32)[None, :]
    yg = np.arange(wh, dtype=np.float32)[:, None]
    dist = np.abs((xg + yg * 0.35) - (sweep_x + wh * 0.18))
    sheen = np.clip(1.0 - dist / 95.0, 0.0, 1.0)[..., None] * (26.0 * k)
    warped = np.clip(warped + sheen, 0.0, 255.0)
    bg_patch = studio_stage_16x9(shot_name)[0][wy0:wy1, wx0:wx1]
    # Clear the flat window region to dark studio grid before compositing the 3D tilted window
    dark_grid = np.empty_like(bg_patch)
    dark_grid[:] = np.array(DARK, dtype=np.float32)
    stage[wy0:wy1, wx0:wx1] = dark_grid * (1.0 - mask_arr) + warped * mask_arr
    return stage


def render_shot_frame(shot, t, decoders, master, card_makers, card_cache, chips, cut='main'):
    local = max(0.0, t - shot['start'])
    if shot['kind'] == 'card':
        # Allow the 3D R3F pipeline card ('backends') to rotate continuously; other cards settle after 2.4s
        if shot['name'] == 'backends':
            anim_t = round(local * 12.5) / 12.5
            key = (shot['name'], int(round(local * 12.5)))
        else:
            anim_t = min(local, 2.4)
            key = (shot['name'], int(round(anim_t * FPS)))
        if key not in card_cache:
            card_cache[key] = np.asarray(card_makers[shot['name']](local_t=anim_t, ss=2).convert('RGB'), dtype=np.float32)
        return card_cache[key].copy()

    key = id(shot)
    if key not in decoders:
        frames = int(round((shot['end'] - shot['start']) * FPS)) + 2
        decoders[key] = Decoder(shot.get('master') or master, shot['src'], frames)
    image = decoders[key].read()
    if cut == 'x':
        return render_vertical_footage(image, shot, local)

    # 16:9 Cinema Studio Stage: composite the 2:1 viewport into the floating macOS Studio Window
    stage, win_mask = studio_stage_16x9(shot['name'])
    x, y, w, h = camera_at(shot['cams'], local)
    product = image.resize((WIN_W, WIN_H), Image.LANCZOS, box=(x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE))
    product = product.filter(UNSHARP)
    viewport = np.asarray(product, dtype=np.float32).copy()
    draw_overlays(viewport, shot, local, (x, y, w, h))
    draw_labels(viewport, shot, local, chips)
    region = stage[WIN_Y0:WIN_Y0 + WIN_H, WIN_X0:WIN_X0 + WIN_W]
    region[:] = region * (1.0 - win_mask) + viewport * win_mask
    return _apply_shotcraft_reveal(stage, shot['name'], local)


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


def two_pass(target, samples, rate, tp=-1.5, measured=None):
    """loudnorm's measuring pass and the second-pass filter built from it; `measured` reuses an earlier measuring pass."""
    first = measured or loudnorm_json(ffmpeg_pcm(['-af', f'loudnorm=I={target}:TP={tp}:LRA=11:print_format=json', '-f', 'null', '-'],
                                                 samples, rate).stderr.decode())
    second = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={first['input_i']}:measured_TP={first['input_tp']}:"
              f"measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}:offset={first['target_offset']}:linear=true:print_format=json")
    return first, second


def voice_track(said, order, duration, samples_of, rate=24000):
    voice = np.zeros(int(duration * rate) + rate, dtype=np.float32)
    for cue in order:
        samples = samples_of(cue)
        offset = int(round(said[cue] * rate))
        voice[offset:offset + len(samples)] += samples
    return voice[:int(round(duration * rate))]


def normalize_narration(voice, second, n, rate=24000):
    run = ffmpeg_pcm(['-af', second + ',aresample=48000,pan=stereo|c0=c0|c1=c0', '-f', 'f32le', '-ar', '48000', '-ac', '2', '-'], voice, rate)
    narration = np.frombuffer(run.stdout, dtype='<f4').reshape(-1, 2)[:n]
    return np.pad(narration, ((0, n - len(narration)), (0, 0))), loudnorm_json(run.stderr.decode())


def build_audio(said, speech, order, duration, work, out_name, keep=None):
    """Narration, ducked music, limiter and final loudness pass; returns the AAC file and the audio record."""
    rate = 24000
    n = int(round(duration * music_bed.SR))
    voice = voice_track(said, order, duration, lambda cue: speech[cue]['samples'], rate)
    first, second = two_pass(-14, voice, rate, measured=keep and keep['audio']['narration']['loudnorm_first_pass'])
    narration, narration_pass = normalize_narration(voice, second, n, rate)
    splice = None
    if keep and keep['speech']:
        previous = voice_track(said, order, duration, lambda cue: keep['speech'].get(cue, speech[cue]['samples']), rate)
        before, _ = normalize_narration(previous, second, n, rate)
        replaced = [cue for cue in order if cue in keep['speech']]
        i0, i1 = order.index(replaced[0]), order.index(replaced[-1])
        assert i0 > 0 and i1 + 1 < len(order), replaced
        end_of = lambda cue: said[cue] + max(speech[cue]['seconds'], len(keep['speech'].get(cue, ())) / rate)  # noqa: E731
        cut_a = (end_of(order[i0 - 1]) + said[replaced[0]]) / 2
        cut_b = (end_of(replaced[-1]) + said[order[i1 + 1]]) / 2
        ia, ib = int(round(cut_a * music_bed.SR)), int(round(cut_b * music_bed.SR))
        guard = music_bed.SR // 10
        for i in (ia, ib):
            assert not narration[i - guard:i + guard].any() and not before[i - guard:i + guard].any(), f'cut at {i / music_bed.SR:.3f} s is not silent'
        differ = np.flatnonzero((before != narration).any(axis=1))
        narration = np.concatenate([before[:ia], narration[ia:ib], before[ib:]])
        splice = {'replaced_cues': replaced, 'new_narration_from_s': round(ia / music_bed.SR, 3), 'new_narration_to_s': round(ib / music_bed.SR, 3),
                  'outside': "the previous export's narration, normalized with the same parameters",
                  'unspliced_tracks_differ_from_to_s': [round(float(differ[0]) / music_bed.SR, 3), round(float(differ[-1]) / music_bed.SR, 3)]}
    narration_level = loudness(narration, music_bed.SR)
    bed, bed_info = music_bed.synthesize(duration)
    spans = [(said[c], said[c] + speech[c]['seconds']) for c in order]
    duck = music_bed.duck_gains(spans, duration)
    ducked = bed * duck[:, None]
    if keep:
        raw_level = keep['audio']['music'].get('raw_level_lufs', round(BED_LUFS - keep['audio']['music']['gain_db'], 1))
    else:
        raw_level = loudness(ducked, music_bed.SR)['integrated_lufs']
    gain_db = BED_LUFS - raw_level
    bed_mix = ducked * np.float32(10 ** (gain_db / 20))
    bed_level = loudness(bed_mix, music_bed.SR)
    open_level = loudness(bed * np.float32(10 ** (gain_db / 20)), music_bed.SR)['integrated_lufs']
    mix = narration + bed_mix
    ceiling = 10 ** (LIMIT_DBFS / 20)
    over_s = float(np.count_nonzero(np.abs(mix).max(axis=1) > ceiling)) / music_bed.SR
    peak_before = float(20 * np.log10(np.abs(mix).max()))
    run = ffmpeg_pcm(['-af', f'alimiter=limit={ceiling:.5f}:attack=5:release=50:level=false:latency=true',
                      '-f', 'f32le', '-ar', str(music_bed.SR), '-ac', '2', '-'], mix, music_bed.SR)
    limited = np.frombuffer(run.stdout, dtype='<f4').reshape(-1, 2)
    assert abs(len(limited) - n) <= 2, (len(limited), n)
    mix = np.pad(limited[:n], ((0, max(0, n - len(limited))), (0, 0)))
    first_mix, second_mix = two_pass(-14, mix, music_bed.SR, measured=keep and keep['audio']['mix']['loudnorm_first_pass'])
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
                  'target_lufs_in_mix': BED_LUFS, 'gain_db': round(gain_db, 2), 'raw_level_lufs': raw_level,
                  'measured_in_mix': bed_level, 'undocked_level_lufs': open_level,
                  'level_in_export_lufs': round(bed_level['integrated_lufs'] + applied, 2),
                  'pcm_sha256': hashlib.sha256(np.ascontiguousarray(bed, dtype='<f4').tobytes()).hexdigest()},
        'mix': {'chain': (f'narration + ducked music; lookahead limiter at {LIMIT_DBFS} dBFS (5 ms attack, 50 ms release, delay '
                          'compensated); two-pass loudnorm I=-14 LUFS, TP=-1.5 dBTP, linear; AAC 192 kbit/s 48 kHz stereo'),
                'limiter': {'ceiling_dbfs': LIMIT_DBFS, 'sample_peak_before_dbfs': round(peak_before, 2),
                            'seconds_of_samples_over_ceiling_before': round(over_s, 4)},
                'loudnorm_first_pass': first_mix, 'loudnorm_output': final, 'gain_applied_db': round(applied, 2)},
    }
    if keep:
        audio['measurements_reused'] = {
            'from_video_sha256': keep['video_sha256'],
            'what': ('the narration and final-mix loudnorm measuring passes and the music-bed level of the previous export, so '
                     'unchanged material gets exactly the gains it had'),
            'narration_splice': splice,
            'fresh_measurement_now': {'narration_loudnorm_first_pass': two_pass(-14, voice, rate)[0],
                                      'music_bed_raw_level_lufs': loudness(ducked, music_bed.SR)['integrated_lufs'],
                                      'mix_loudnorm_first_pass': two_pass(-14, mix, music_bed.SR)[0]}}
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


def load_keep(path, cut, said, speech, order, duration, voice_dirs):
    """The previous export's audio record and the previous speech of every line whose text changed (see build_audio)."""
    previous = json.loads(Path(path).read_text())
    if 'asset_sha256' in previous:               # docs/demo/assets/manifest.json, as written by export_walkthrough_v2.py
        record = previous['x_cut'] if cut == 'x' else previous
        segments, video_sha = record['narration_segments'], (previous['asset_sha256']['nocap-x.mp4' if cut == 'x' else 'receipts-demo.mp4'])
        old_duration = record['duration_s']
    else:                                        # a render folder's <cut>/manifest.json
        record, segments, video_sha, old_duration = previous, previous['narration']['segments'], previous['video_sha256'], previous['duration_s']
    assert old_duration == duration, ('the runtime changed, so the previous measurements do not apply', old_duration, duration)
    by_cue = {segment['cue']: segment for segment in segments}
    assert list(by_cue) == order, ('the lines or their order changed', list(by_cue), order)
    files = {}
    for folder in voice_dirs:
        for wav in sorted(Path(folder).glob('voice-*.wav')) if folder and Path(folder).exists() else []:
            files.setdefault(hashlib.sha256(wav.read_bytes()).hexdigest(), wav)
    replaced = {}
    for cue in order:
        old = by_cue[cue]
        assert round(said[cue], 3) == old['start_s'], (cue, 'moved', round(said[cue], 3), old['start_s'])
        if speech[cue]['sha256'] == old['speech_sha256']:
            continue
        assert old['speech_sha256'] in files, f'{cue}: the previous Kokoro file ({old["speech_sha256"][:12]}) is not in {voice_dirs}'
        samples, rate = sf.read(files[old['speech_sha256']], dtype='float32')
        loud = np.flatnonzero(np.abs(samples) > 0.01)              # the same trim as synthesize()
        samples = samples[max(0, loud[0] - 240):min(len(samples), loud[-1] + 960)]
        assert round(len(samples) / rate, 3) == old['seconds'], (cue, len(samples) / rate, old['seconds'])
        replaced[cue] = samples
    return {'audio': record['audio'], 'speech': replaced, 'video_sha256': video_sha}


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
    parser.add_argument('--keep-audio-from', type=Path,
                        help=('a previous export\'s manifest.json (e.g. docs/demo/assets/manifest.json): reuse its loudness measurements '
                              'and keep its narration outside the lines whose text changed (their previous Kokoro files must be in '
                              'render/ or the voice cache)'))
    args = parser.parse_args()
    out, source = args.out.resolve(), args.source.resolve()
    if out == source or source in out.parents:
        parser.error('--out must be outside --source: the capture folder is only read')
    work = out / 'render'
    work.mkdir(parents=True, exist_ok=True)
    private = load_private(args.private_take) if args.private_take and args.cut == 'main' else None
    card_makers = cards_for(args.cut, bool(private))
    cards = {name: make().convert('RGB') for name, make in card_makers.items()}
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
        record = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in shot.items()
                  if k not in ('cams', 'labels', 'overlays', 'master', 'v_panels', 'v_meta', 'v_meta_before', 'switch_s')}
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
    keep = load_keep(args.keep_audio_from, args.cut, said, speech, order, duration,
                     [work, args.voice_cache or args.source / 'render']) if args.keep_audio_from else None
    m4a, audio = build_audio(said, speech, order, duration, work, name, keep)
    caption_size = 38 if args.cut == 'x' else 34
    bands = {item['cue']: caption_band(item['caption'], caption_size, cut=args.cut) for item in captions}
    blank = caption_band('', caption_size, cut=args.cut)
    chip_scale = 1.15 if args.cut == 'x' else 0.90
    chips = {}
    for shot in shots:
        for label in shot.get('labels', []):
            key = (label['text'], label['dot'])
            if key not in chips:
                chips[key] = np.asarray(chip_image(label['text'], label['dot'], chip_scale), dtype=np.float32) / np.array([1, 1, 1, 255], dtype=np.float32)
    card_cache = {}
    out_w, out_h = (W_X, H_X) if args.cut == 'x' else (W, H)
    video = final_dir / f'{name}.mp4'
    encoder = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{out_w}x{out_h}', '-r', str(FPS), '-i', '-',
                                '-i', str(m4a), '-map', '0:v', '-map', '1:a',
                                '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
                                '-c:v', 'libx264', '-preset', 'slow', '-crf', '14', '-profile:v', 'high', '-g', '50',
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
        array = render_shot_frame(active[0], t, decoders, master, card_makers, card_cache, chips, cut=args.cut)
        if len(active) > 1:
            incoming = active[1]
            alpha = ease((t - incoming['start']) / max(incoming['fade'], 1 / FPS))
            other = render_shot_frame(incoming, t, decoders, master, card_makers, card_cache, chips, cut=args.cut)
            array = array * (1 - alpha) + other * alpha
        for shot in shots:                      # release decoders of finished shots
            if shot['end'] <= t and id(shot) in decoders:
                decoders.pop(id(shot)).close()
        while caption_index < len(captions) and captions[caption_index]['end'] <= t:
            caption_index += 1
        current = captions[caption_index] if caption_index < len(captions) and captions[caption_index]['start'] <= t else None
        band = (bands[current['cue']] if current else blank).copy()
        if args.cut == 'x':
            # 5-Beat Story Segmented Progress Bar (0-11.1s Trap | 11.1-19.3s Check | 19.3-29.0s 10-Q Math | 29.0-40.3s Fix | 40.3-56.3s SAM 3.1)
            bar_x0, bar_x1 = 50, W_X - 50
            bar_total_w = bar_x1 - bar_x0
            x_beats = [(0.0, 11.10, RED), (11.10, 19.30, GREEN), (19.30, 28.99, AMBER), (28.99, 40.30, GREEN), (40.30, duration, AMBER)]
            for b_idx, (b_s, b_e, b_col) in enumerate(x_beats):
                sx0 = bar_x0 + int(round(bar_total_w * (b_s / duration))) + (5 if b_idx > 0 else 0)
                sx1 = bar_x0 + int(round(bar_total_w * (b_e / duration))) - (5 if b_idx < 4 else 0)
                if sx1 <= sx0:
                    continue
                band[11:17, sx0:sx1] = LINE
                if t > b_s:
                    fill_f = min(1.0, (t - b_s) / max(0.01, b_e - b_s))
                    fw = int(round((sx1 - sx0) * fill_f))
                    if fw > 0:
                        band[11:17, sx0:sx0 + fw] = b_col if t < b_e else GREEN
        else:
            # Remotion-style 4-Act Segmented Timeline Bar (0-28.8s Stakes | 28.8-64.0s AI Solution | 64.0-150.2s Live Demo | 150.2-171.9s Results)
            bar_x0, bar_x1 = 128, W - 128
            bar_total_w = bar_x1 - bar_x0
            act_bounds = [(0.0, 28.78, RED), (28.78, 64.01, GREEN), (64.01, 150.23, AMBER), (150.23, duration, GREEN)]
            for a_idx, (a_s, a_e, a_col) in enumerate(act_bounds):
                sx0 = bar_x0 + int(round(bar_total_w * (a_s / duration))) + (4 if a_idx > 0 else 0)
                sx1 = bar_x0 + int(round(bar_total_w * (a_e / duration))) - (4 if a_idx < 3 else 0)
                if sx1 <= sx0:
                    continue
                band[7:12, sx0:sx1] = LINE
                if t > a_s:
                    fill_f = min(1.0, (t - a_s) / max(0.01, a_e - a_s))
                    fw = int(round((sx1 - sx0) * fill_f))
                    if fw > 0:
                        band[7:12, sx0:sx0 + fw] = a_col if t < a_e else GREEN
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
            if overlay['kind'] == 'sam-highlight':
                overlays.append({'kind': 'SAM region highlight (edit overlay, not product UI)', 'shot': shot['name'],
                                 'start_s': round(shot['start'] + overlay['start'], 2),
                                 'end_s': round(shot['start'] + min(overlay['end'], shot['end'] - shot['start']), 2),
                                 'what': 'Amber outline and a 48% spotlight dim around the two regions the product itself labels '
                                         '"Bar Chart · SAM"; boxes are the DOM boxes logged at the slide-regions mark, after the page stopped scrolling',
                                 'regions': overlay['regions'], 'region_labels_in_product': overlay['region_labels'],
                                 'boxes_css': overlay['boxes_css']})
            elif overlay['kind'] == 'focus-spotlight':
                overlays.append({'kind': 'Focus spotlight and callout outline (edit overlay, not product UI)', 'shot': shot['name'],
                                 'start_s': round(shot['start'] + overlay['start'], 2),
                                 'end_s': round(shot['start'] + min(overlay['end'], shot['end'] - shot['start']), 2),
                                 'what': overlay['what'], 'boxes_css': overlay['boxes_css'], 'callout_css': overlay.get('callout_css')})
            elif overlay['kind'] == 'click-ripple':
                overlays.append({'kind': 'Cursor click ripple (edit overlay, not product UI)', 'shot': shot['name'],
                                 'start_s': round(shot['start'] + overlay['start'], 2),
                                 'end_s': round(shot['start'] + min(overlay['end'], shot['end'] - shot['start']), 2),
                                 'what': overlay['what'], 'center_css': overlay['center_css']})
    manifest = {
        'version': VERSION, 'cut': args.cut,
        'format': ('v2.1 re-edit of the v2 capture' + (' plus one recorded Private take' if private else '') +
                   ': recorded browser footage with camera moves and cuts, kinetic text cards, edit-layer labels, focus spotlights and highlight, '
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
