#!/usr/bin/env python3
"""Spike C: find chart and table regions on an earnings slide with SAM 3.1, then
read the chart's numbers with Muse Spark 1.3 structured output.

Day-1 gate (plan.md, Spike C): the SAM 3.1 box covers the chart, and Muse Spark
reads at least 80% of the chart's values within rounding. If SAM finds nothing,
Muse Spark grounding boxes are the fallback.

Usage, from the repo root:
  .venv/bin/python spikes/c_slides.py --selftest                  # offline: mocked API, no key, no network
  .venv/bin/python spikes/c_slides.py --synthetic                 # live: known-values chart, prints accuracy
  .venv/bin/python spikes/c_slides.py --synthetic --no-labels     # live: same chart, values read off the axis
  .venv/bin/python spikes/c_slides.py --image slide.png           # live: a real slide image (PNG or JPG)
  .venv/bin/python spikes/c_slides.py --pdf ex99-2.pdf --page 7   # live: render one PDF page (1-based)

Outputs go to spikes/out/ (gitignored):
  c_overlay_<name>.png    boxes and labels, plus translucent masks when they can be decoded
  c_chart_<name>.json     Muse Spark structured output {chart_title, series[]} with provenance
  c_run_<name>.json       full run log: raw SAM output, detections, usage, latency, cost, accuracy
  c_crop_<name>_<i>.png   chart crops sent to Muse Spark
  c_<name>_chart.png, c_<name>_truth.json   the synthetic chart and its ground truth
  cache/                  model results keyed by a hash of the request payload (--no-cache skips reads)
  c_cost_log.jsonl        one line per billed call

API usage follows the Meta docs fetched on 2026-10-27: sam/overview, sam/segmenting,
sam/reading-segmentation, sam/client-libraries, media-segmentation,
image-understanding, structured-output, protocols/responses, reasoning,
pricing-rate-limits, error-handling, and the cookbook pages sam-api-basics,
perception-chart-analysis, and perception-grounding.

Masks: sam/reading-segmentation documents the mask token (H,W, a `~` or `!`
encoding marker, then a base85 payload) but not the codec itself: neither the
base85 alphabet nor the bit layout. This file parses boxes from the documented
grammar. It decodes masks only through Meta's official Python parser
(`pip install meta-sam-parser`, imported as meta_sam_parser) when that package
is installed. Without it, the overlay shows boxes only, and the raw payloads
stay in c_run_<name>.json and the cache so they can be decoded later at no cost.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "spikes" / "out"
CACHE_DIR = OUT_DIR / "cache"
COST_LOG = OUT_DIR / "c_cost_log.jsonl"

BASE_URL = "https://api.meta.ai/v1"
SAM_MODEL = "sam-3.1"
SPARK_MODEL = "muse-spark-1.3"
for _model in (SAM_MODEL, SPARK_MODEL):
    # Standard-mode IDs only: Meta may train on anything sent to -contributor IDs.
    assert "-contributor" not in _model, _model

# pricing-rate-limits (2026-10-27): SAM 3.1 image segmentation is $2.50 per 1,000 images.
# Muse Spark 1.3 Standard mode, per 1M tokens. Reasoning tokens bill as output.
SAM_USD_PER_IMAGE = 2.50 / 1000
SPARK_USD_PER_MTOK = {"input": 1.25, "cached_input": 0.15, "output": 4.25}

DEFAULT_CONCEPTS = ("bar chart", "line chart", "table")
# Accepted reasoning.effort values. "none" returns HTTP 400 on Muse Spark (reasoning doc).
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
RETRYABLE_STREAM_CODES = {"server_error", "server_shutting_down", "rate_limit_exceeded",
                          "service_overloaded", "backend_unavailable"}
ACCURACY_GATE = 0.80

# Overlay colors: the dataviz reference palette (validated: CVD dE 24.7, normal-vision dE 33.6).
# Secondary encoding: SAM boxes are solid, Spark grounding boxes are dashed, and every box has a text label.
SURFACE = (252, 252, 251)
INK = (11, 11, 11)
INK_2 = (82, 81, 78)
METHODS = {
    "sam-3.1": {"name": "SAM 3.1", "color": (42, 120, 214), "dash": None},
    "spark-grounding": {"name": "Muse Spark grounding", "color": (235, 104, 52), "dash": (16, 9)},
}


class SpikeError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# SAM 3.1 wire grammar (sam/reading-segmentation)
#   line   ::= "<" INT "f>" record ("," record)*
#   record ::= OBJECT_ID box mask
#   box    ::= "<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..[;c=..]|>"   corners inclusive, source pixels
#   mask   ::= "<|mask;x=0;y=0[;c=..];data=" H "," W "," ("~"|"!") PAYLOAD "|>"
# The mask raster is box-local: H = y2-y1+1 rows, W = x2-x1+1 columns (every documented example).
# ----------------------------------------------------------------------------

@dataclass
class SamMask:
    height: int
    width: int
    encoding: str  # "lossless" (~) or "one_bit" (!)
    payload: str   # base85 text after the marker; opaque here


@dataclass
class SamRecord:
    frame: int
    object_id: str
    x1: int
    y1: int
    x2: int
    y2: int
    frame_w: int
    frame_h: int
    confidence: float | None
    mask: SamMask


# One line per frame. Also split lines that arrive glued together without "\n".
_LINE_SPLIT = re.compile(r"\r?\n|(?<=\|>)(?=<\d+f>)")
_FRAME = re.compile(r"<(\d+)f>")
_RECORD = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;(.*?)\|>", re.S)


def _fields(spec: str) -> dict[str, str]:
    """Parse `k=v;k=v` by key, so a token reads the same with or without `c`."""
    out: dict[str, str] = {}
    for part in spec.split(";"):
        if part:
            key, sep, value = part.partition("=")
            if not sep:
                raise ValueError(f"field without '=': {part!r}")
            out[key] = value
    return out


def _parse_record(frame: int, m: re.Match[str]) -> SamRecord:
    box = _fields(m.group(2))
    x1, y1, x2, y2, w, h = (int(box[k]) for k in ("x1", "y1", "x2", "y2", "w", "h"))
    if not (0 <= x1 <= x2 < w and 0 <= y1 <= y2 < h):
        raise ValueError(f"box {x1},{y1},{x2},{y2} is outside the {w}x{h} frame")
    confidence = float(box["c"]) if "c" in box else None
    head, sep, data = m.group(3).partition("data=")
    if not sep:
        raise ValueError("mask token has no data= field")
    mask_fields = _fields(head)
    if confidence is None and "c" in mask_fields:
        confidence = float(mask_fields["c"])
    rows, cols, rest = data.split(",", 2)
    # Read only the first character after "H,W," as the marker; the payload may contain ~ or !.
    marker, payload = rest[:1], rest[1:]
    encoding = {"~": "lossless", "!": "one_bit"}.get(marker)
    if encoding is None:
        raise ValueError(f"unknown mask encoding marker {marker!r}")
    mask = SamMask(height=int(rows), width=int(cols), encoding=encoding, payload=payload)
    if mask.height <= 0 or mask.width <= 0:
        raise ValueError("mask dimensions must be positive")
    return SamRecord(frame, m.group(1), x1, y1, x2, y2, w, h, confidence, mask)


def parse_sam_output(text: str) -> tuple[list[SamRecord], list[str]]:
    """Parse SAM 3.1 output_text into records plus diagnostics. Empty text means zero matches."""
    records: list[SamRecord] = []
    diagnostics: list[str] = []
    for n, raw in enumerate(_LINE_SPLIT.split(text or ""), 1):
        line = raw.strip()
        if not line:
            continue
        fm = _FRAME.match(line)
        if not fm:
            diagnostics.append(f"line {n}: no <Nf> frame marker: {line[:60]!r}")
            continue
        frame, pos = int(fm.group(1)), fm.end()
        while pos < len(line):
            rm = _RECORD.match(line, pos)
            if not rm:
                diagnostics.append(f"line {n}: unparsed text at column {pos}: {line[pos:pos + 60]!r}")
                break
            try:
                records.append(_parse_record(frame, rm))
            except (KeyError, ValueError) as exc:
                diagnostics.append(f"line {n}: malformed record {rm.group(0)[:60]!r}: {exc}")
            pos = rm.end()
            if pos < len(line):
                if line[pos] != ",":
                    diagnostics.append(f"line {n}: expected ',' at column {pos}")
                    break
                pos += 1
    return records, diagnostics


def decode_masks_with_official_parser(output_text: str, media: str = "image") -> tuple[dict[str, np.ndarray], list[str]]:
    """Decode masks with Meta's official Python parser when it is installed.

    Returns ({object_id: H x W uint8 raster of 0/1}, notes). Never raises: on any
    problem the pipeline continues with boxes only. Uses only the package's public
    API (format factory, push/finish, decode_mask_to_raster). Not verified in this
    spike, because the package isn't installed in the project venv.
    """
    try:
        import meta_sam_parser as msp  # type: ignore[import-not-found]
    except ImportError:
        return {}, ["meta_sam_parser is not installed, so masks are not decoded "
                    "(pip install meta-sam-parser to decode them)"]
    try:
        fmt = msp.image_segmentation_format() if media == "image" else msp.video_segmentation_format()
        parser = fmt.create_parser()
        parser.push(output_text if output_text.endswith("\n") else output_text + "\n")
        result = parser.finish(msp.CompletedOutcome()).result
        rasters: dict[str, np.ndarray] = {}
        for rec in result.records:
            if rec.kind == "mask":
                flat = bytes(msp.decode_mask_to_raster(rec.mask))
                rasters[str(rec.object_id)] = (np.frombuffer(flat, dtype=np.uint8)
                                               .reshape(rec.mask.height, rec.mask.width).copy())
        notes = [f"meta_sam_parser {d.code}: {d.message}" for d in result.diagnostics]
        return rasters, notes
    except Exception as exc:  # optional path; never break the boxes-only pipeline
        return {}, [f"meta_sam_parser failed ({type(exc).__name__}: {exc}); boxes only"]


# ----------------------------------------------------------------------------
# Detections, crops, overlay
# ----------------------------------------------------------------------------

@dataclass
class Detection:
    method: str                      # "sam-3.1" or "spark-grounding"
    concept: str
    object_id: str
    box: tuple[int, int, int, int]   # inclusive x1, y1, x2, y2 in working-image pixels
    confidence: float | None = None
    mask: SamMask | None = None
    raster: np.ndarray | None = None  # box-local H x W of 0/1, only when decoded
    mask_source: str = "none"         # "meta_sam_parser", "selftest-placeholder", or "none"

    def area(self) -> int:
        x1, y1, x2, y2 = self.box
        return (x2 - x1 + 1) * (y2 - y1 + 1)

    def to_json(self, size: tuple[int, int]) -> dict[str, Any]:
        x1, y1, x2, y2 = self.box
        out: dict[str, Any] = {
            "method": self.method, "concept": self.concept, "object_id": self.object_id,
            "box_inclusive": [x1, y1, x2, y2], "box_half_open": [x1, y1, x2 + 1, y2 + 1],
            "confidence": self.confidence, "area_fraction": round(self.area() / (size[0] * size[1]), 4),
        }
        if self.mask is not None:
            out["mask"] = {"height": self.mask.height, "width": self.mask.width,
                           "encoding": self.mask.encoding, "payload_chars": len(self.mask.payload),
                           "decoded": self.raster is not None, "source": self.mask_source,
                           "foreground_px": int(self.raster.sum()) if self.raster is not None else None}
        return out


def box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix = min(a[2], b[2]) - max(a[0], b[0]) + 1
    iy = min(a[3], b[3]) - max(a[1], b[1]) + 1
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    area = lambda r: (r[2] - r[0] + 1) * (r[3] - r[1] + 1)  # noqa: E731
    return inter / (area(a) + area(b) - inter)


def box_coverage(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> float:
    """Fraction of `inner` that lies inside `outer`."""
    ix = min(inner[2], outer[2]) - max(inner[0], outer[0]) + 1
    iy = min(inner[3], outer[3]) - max(inner[1], outer[1]) + 1
    if ix <= 0 or iy <= 0:
        return 0.0
    return ix * iy / ((inner[2] - inner[0] + 1) * (inner[3] - inner[1] + 1))


def merge_bar_instances(dets: list[Detection], size: tuple[int, int], max_w_frac: float = 0.15,
                        min_n: int = 3) -> list[Detection]:
    """SAM 3.1 is an instance segmenter. On stacked bars it returns every column as its own "bar chart"
    (BofA slide 10: five ~146 px boxes; "chart" and "graph" matched nothing). Merge >= min_n narrow,
    row-aligned boxes of one concept into a single chart region. Wide boxes are whole charts and stay apart."""
    W = size[0]
    out: list[Detection] = []
    for key in sorted({(d.method, d.concept) for d in dets}):
        group = [d for d in dets if (d.method, d.concept) == key]
        narrow = sorted((d for d in group if d.box[2] - d.box[0] + 1 <= max_w_frac * W), key=lambda d: d.box[0])
        rows: list[list[Detection]] = []
        for d in narrow:
            for row in rows:
                top, bottom = min(r.box[1] for r in row), max(r.box[3] for r in row)
                overlap = min(bottom, d.box[3]) - max(top, d.box[1])
                if overlap >= 0.5 * min(bottom - top, d.box[3] - d.box[1]):
                    row.append(d)
                    break
            else:
                rows.append([d])
        merged_ids = set()
        for row in rows:
            if len(row) >= min_n:
                box = (min(r.box[0] for r in row), min(r.box[1] for r in row),
                       max(r.box[2] for r in row), max(r.box[3] for r in row))
                conf = max((r.confidence for r in row if r.confidence is not None), default=None)
                out.append(Detection(key[0], key[1], "+".join(r.object_id for r in row), box, conf))
                merged_ids |= {id(r) for r in row}
        out += [d for d in group if id(d) not in merged_ids]
    return out


def select_chart_regions(dets: list[Detection], size: tuple[int, int], max_crops: int) -> list[Detection]:
    """Chart regions worth cropping: per-bar instances merged, most confident (then largest) first,
    near-duplicates dropped."""
    min_area = 0.01 * size[0] * size[1]
    charts = [d for d in merge_bar_instances(dets, size) if "chart" in d.concept.lower() and d.area() >= min_area]
    charts.sort(key=lambda d: (d.confidence if d.confidence is not None else 0.0, d.area()), reverse=True)
    picked: list[Detection] = []
    for d in charts:
        if all(box_iou(d.box, p.box) < 0.7 for p in picked):
            picked.append(d)
        if len(picked) >= max_crops:
            break
    return picked


@dataclass
class Crop:
    det: Detection
    rect: tuple[int, int, int, int]  # half-open l, t, r, b in working pixels, padded
    path: Path
    data_url: str
    upscale: float = 1.0             # small crops are enlarged so tiny axis text stays legible


def make_crops(img: Image.Image, regions: list[Detection], name: str) -> list[Crop]:
    crops = []
    for i, d in enumerate(regions, 1):
        x1, y1, x2, y2 = d.box
        bw, bh = x2 - x1 + 1, y2 - y1 + 1
        # A SAM "bar chart" box can hug the plotted bars. On the live synthetic run it left out the title,
        # the tick labels, and the data label above the tallest bar. So pad more above (data labels) and
        # below (axis labels) than at the sides. The full slide is sent too, so titles are never lost.
        # Bottom padding also covers axis labels, which Tesla prints rotated (~65 px tall).
        px, top, bottom = max(12, round(0.06 * bw)), max(16, round(0.12 * bh)), max(24, round(0.16 * bh))
        rect = (max(0, x1 - px), max(0, y1 - top), min(img.width, x2 + 1 + px), min(img.height, y2 + 1 + bottom))
        crop = img.crop(rect)
        # A chart that fills a third of a 1365x1055 slide crops to ~450 px with tiny axis text.
        # Enlarge small crops (long side to >= 1024 px, at most 3x) so the zoom adds legibility.
        factor = min(3.0, 1024 / max(crop.size)) if max(crop.size) < 1024 else 1.0
        if factor > 1.0:
            crop = crop.resize((round(crop.width * factor), round(crop.height * factor)), Image.Resampling.LANCZOS)
        path = OUT_DIR / f"c_crop_{name}_{i}.png"
        crop.save(path)
        crops.append(Crop(d, rect, path, png_data_url(crop), round(factor, 3)))
    return crops


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        from matplotlib import font_manager
        props = font_manager.FontProperties(family="DejaVu Sans", weight="bold" if bold else "normal")
        return ImageFont.truetype(font_manager.findfont(props, fallback_to_default=True), size)
    except Exception:
        return ImageFont.load_default(size=size)


def _dashed_rect(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float],
                 color: tuple[int, ...], width: int, dash: tuple[int, int]) -> None:
    x1, y1, x2, y2 = box
    on, off = dash
    for ax, ay, bx, by in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        t = 0.0
        while t < length:
            t2 = min(t + on, length)
            draw.line([(ax + ux * t, ay + uy * t), (ax + ux * t2, ay + uy * t2)], fill=color, width=width)
            t += on + off


def _mask_edge(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1, constant_values=False)
    interior = p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return m & ~interior


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + ([cur] if cur else [])


def _place_chip(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str,
                color: tuple[int, int, int], font: Any, pad: int, size: tuple[int, int],
                placed: list[tuple[int, int, int, int]]) -> None:
    W, H = size
    x1, y1, _, _ = box
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    cw, ch = 3 * pad + th + tw, 2 * pad + th
    cx = min(max(0, x1), max(0, W - cw))
    candidates = [y1 - ch - 3] + [y1 + 3 + k * (ch + 3) for k in range(6)]
    overlaps = lambda a, c: not (a[2] <= c[0] or c[2] <= a[0] or a[3] <= c[1] or c[3] <= a[1])  # noqa: E731
    rect = (cx, max(0, y1 + 3), cx + cw, max(0, y1 + 3) + ch)
    for cy in candidates:
        cand = (cx, cy, cx + cw, cy + ch)
        if cy >= 0 and cy + ch <= H and not any(overlaps(cand, p) for p in placed):
            rect = cand
            break
    placed.append(rect)
    draw.rounded_rectangle(rect, radius=pad, fill=(*SURFACE, 235), outline=(*color, 255), width=max(1, pad // 3))
    draw.rectangle((rect[0] + pad, rect[1] + pad, rect[0] + pad + th, rect[1] + pad + th), fill=(*color, 255))
    # Text wears ink, never the series color; the swatch carries identity.
    draw.text((rect[0] + 2 * pad + th - l, rect[1] + pad - t), text, fill=(*INK, 255), font=font)


def render_overlay(img: Image.Image, dets: list[Detection], out_path: Path, *, title: str,
                   notes: list[str], crop_rects: list[tuple[int, int, int, int]]) -> Path:
    """Boxes, labels, and translucent masks on the working image, with a header legend and footer notes."""
    W, H = img.size
    s = max(0.8, W / 1200)
    lw = max(2, round(3 * s))
    pad = max(3, round(5 * s))
    font = _font(round(14 * s))
    font_b = _font(round(17 * s), bold=True)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for d in dets:
        if d.raster is None:
            continue
        color = METHODS[d.method]["color"]
        x1, y1, x2, y2 = d.box
        bw, bh = x2 - x1 + 1, y2 - y1 + 1
        m = d.raster.astype(bool)
        if m.shape != (bh, bw):  # box-local raster; stretch to the box if sizes ever differ
            m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((bw, bh), Image.Resampling.NEAREST)) > 127
        rgba = np.zeros((bh, bw, 4), dtype=np.uint8)
        rgba[m] = (*color, 89)                # 0.35 fill, the @meta-sam/graphics default
        rgba[_mask_edge(m)] = (*color, 204)   # 0.8 outline
        layer.alpha_composite(Image.fromarray(rgba, "RGBA"), dest=(x1, y1))
    out = Image.alpha_composite(img.convert("RGBA"), layer)
    draw = ImageDraw.Draw(out, "RGBA")
    for l, t, r, b in crop_rects:  # what Muse Spark saw, drawn under the boxes
        _dashed_rect(draw, (l, t, r - 1, b - 1), (*INK_2, 170), max(1, lw // 2), (3, 6))
    for d in dets:
        st = METHODS[d.method]
        if st["dash"] is None:
            draw.rectangle(d.box, outline=(*st["color"], 255), width=lw)
        else:
            _dashed_rect(draw, d.box, (*st["color"], 255), lw, st["dash"])
    placed: list[tuple[int, int, int, int]] = []
    for d in dets:
        st = METHODS[d.method]
        text = f"{st['name']} · {d.concept} #{d.object_id}"
        if d.confidence is not None:
            text += f" · c={d.confidence:.2f}"
        _place_chip(draw, d.box, text, st["color"], font, pad, (W, H), placed)

    # Header (title + legend) and footer (notes) sit outside the slide so nothing is covered.
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    line_h = round(24 * s)
    legend = [("sam-3.1", "SAM 3.1 (solid box, translucent mask when decoded)"),
              ("spark-grounding", "Muse Spark grounding (dashed box)")]
    if crop_rects:
        legend.append(("crop", "Crop sent to Muse Spark (dotted)"))
    rows: list[list[tuple[str, str]]] = [[]]
    x = 2 * pad
    for key, label in legend:
        need = line_h + pad + probe.textlength(label, font=font) + 3 * pad
        if rows[-1] and x + need > W - 2 * pad:
            rows.append([])
            x = 2 * pad
        rows[-1].append((key, label))
        x += need
    note_lines = [ln for n in notes for ln in _wrap(probe, n, font, W - 4 * pad)]
    header_h = 2 * pad + round(28 * s) + line_h * len(rows)
    footer_h = 2 * pad + line_h * max(1, len(note_lines))
    canvas = Image.new("RGB", (W, header_h + H + footer_h), SURFACE)
    canvas.paste(out.convert("RGB"), (0, header_h))
    cd = ImageDraw.Draw(canvas)
    cd.text((2 * pad, pad), title, fill=INK, font=font_b)
    y = pad + round(28 * s)
    for row in rows:
        x = 2 * pad
        for key, label in row:
            sw = round(line_h * 0.62)
            sy = y + (line_h - sw) // 2
            if key == "crop":
                _dashed_rect(cd, (x, sy, x + sw, sy + sw), INK_2, max(1, lw // 2), (3, 4))
            elif METHODS[key]["dash"] is None:
                cd.rectangle((x, sy, x + sw, sy + sw), outline=METHODS[key]["color"], width=lw)
            else:
                _dashed_rect(cd, (x, sy, x + sw, sy + sw), METHODS[key]["color"], lw, (6, 4))
            x += sw + pad
            cd.text((x, y), label, fill=INK, font=font)
            x += cd.textlength(label, font=font) + 3 * pad
        y += line_h
    for i, ln in enumerate(note_lines):
        cd.text((2 * pad, header_h + H + pad + i * line_h), ln, fill=INK_2, font=font)
    canvas.save(out_path)
    return out_path


# ----------------------------------------------------------------------------
# Model API calls
# ----------------------------------------------------------------------------

def api_key_from(environ: Any) -> str | None:
    key = str(environ.get("MODEL_API_KEY", "") or "").strip().strip('"').strip("'").strip()
    return key or None


def make_client() -> Any:
    """OpenAI SDK client for Meta Model API. Loads the repo's .env; never prints the key."""
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)
    key = api_key_from(os.environ)
    if not key:
        raise SystemExit(f"MODEL_API_KEY is not set. Add it to {REPO_ROOT / '.env'} (copy env.example to .env "
                         "and fill it in), then re-run. --selftest runs offline without a key.")
    from openai import OpenAI
    # The SDK doesn't read MODEL_API_KEY on its own, so pass it explicitly. The SDK retries 429/5xx.
    return OpenAI(base_url=BASE_URL, api_key=key, max_retries=3, timeout=180.0)


def png_data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class Cache:
    """Model results keyed by a hash of the full request payload (media included)."""

    def __init__(self, root: Path | None, read: bool = True):
        self.root, self.read = root, read

    def _path(self, kind: str, key: str) -> Path:
        assert self.root is not None
        return self.root / f"{kind}_{key[:32]}.json"

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        if self.root is None or not self.read or not self._path(kind, key).exists():
            return None
        try:
            return json.loads(self._path(kind, key).read_text())
        except json.JSONDecodeError:
            return None

    def put(self, kind: str, key: str, value: dict[str, Any]) -> None:
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(kind, key).with_suffix(".tmp")
        tmp.write_text(json.dumps(value))
        tmp.replace(self._path(kind, key))


def request_key(kwargs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(kwargs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def log_cost(enabled: bool, entry: dict[str, Any]) -> None:
    if enabled:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with COST_LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def _as_dict(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj if isinstance(obj, dict) else {"message": str(obj)}


def usage_dict(usage: Any) -> dict[str, Any]:
    raw = _as_dict(usage) or {}
    det_in = raw.get("input_tokens_details") or {}
    det_out = raw.get("output_tokens_details") or {}
    return {"input_tokens": raw.get("input_tokens"), "cached_tokens": det_in.get("cached_tokens"),
            "output_tokens": raw.get("output_tokens"), "reasoning_tokens": det_out.get("reasoning_tokens"),
            "total_tokens": raw.get("total_tokens"), "images_processed": raw.get("images_processed")}


def spark_cost_usd(u: dict[str, Any]) -> float:
    inp = u.get("input_tokens") or 0
    cached = min(u.get("cached_tokens") or 0, inp)
    out = u.get("output_tokens") or 0  # includes reasoning tokens, billed as output
    p = SPARK_USD_PER_MTOK
    return ((inp - cached) * p["input"] + cached * p["cached_input"] + out * p["output"]) / 1_000_000


def _stream_sam_once(client: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """One streamed SAM request. Accumulate output_text deltas; stop at a terminal event.

    Uses responses.create(stream=True) and raw events rather than the SDK's
    responses.stream() helper: that helper raises on lifecycle details it doesn't
    expect, and the live SAM stream finalizes the text lane with
    response.content_part.done only.
    """
    import openai
    t0 = time.monotonic()
    first_delta: float | None = None
    deltas: list[str] = []
    final, status, error = None, None, None
    events = client.responses.create(**kwargs)
    try:
        for ev in events:
            kind = getattr(ev, "type", "")
            if kind == "response.output_text.delta":
                if first_delta is None:
                    first_delta = time.monotonic() - t0
                deltas.append(getattr(ev, "delta", "") or "")
            elif kind in ("response.completed", "response.incomplete", "response.failed"):
                final, status = ev.response, kind.split(".", 1)[1]
                if kind == "response.failed":
                    error = _as_dict(getattr(final, "error", None))
                elif kind == "response.incomplete":
                    error = _as_dict(getattr(final, "incomplete_details", None))
            elif kind == "error":  # stream-interrupting event; code and message at the top level
                status = "error"
                error = {"code": getattr(ev, "code", None), "message": getattr(ev, "message", None)}
    except openai.APIError as exc:  # an error surfaced mid-stream
        status, error = "error", {"code": getattr(exc, "code", None) or "stream_error", "message": str(exc)}
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()
    text = "".join(deltas)
    final_text = (getattr(final, "output_text", "") or "") if final is not None else ""
    if not text and final_text:
        text = final_text  # complete result delivered without deltas
    code = (error or {}).get("code")
    return {"status": status or "eof", "output_text": text,
            "final_text_matches_deltas": (final_text == text) if final is not None else None,
            "usage": usage_dict(getattr(final, "usage", None)), "latency_s": round(time.monotonic() - t0, 3),
            "first_delta_s": round(first_delta, 3) if first_delta is not None else None,
            "error": error, "retryable": status in ("error", "eof") or code in RETRYABLE_STREAM_CODES,
            "response_id": getattr(final, "id", None)}


def sam_segment(client: Any, concept: str, data_url: str, *, cache: Cache, ledger: bool, run_name: str) -> dict[str, Any]:
    """One noun-phrase concept per request (sam/segmenting): input_text phrase + input_image."""
    kwargs = {
        "model": SAM_MODEL,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": data_url},
        ]}],
        "stream": True,
        # Metadata values are strings. one_bit = smallest mask payloads; include_confidence adds c.
        "metadata": {"mask_encoding": "one_bit", "include_confidence": "true"},
    }
    key = request_key(kwargs)
    hit = cache.get("sam", key)
    if hit is not None:
        return {**hit, "cached": True, "cost_usd": 0.0, "attempts": 0}
    attempts = 0
    while True:
        attempts += 1
        rec = _stream_sam_once(client, kwargs)
        if rec["status"] == "completed":
            break
        if attempts < 2 and rec["retryable"]:
            print(f"    SAM '{concept}' ended with {rec['status']} {rec['error']}; retrying once")
            time.sleep(1.0 + random.random())
            continue
        raise SpikeError(f"SAM 3.1 '{concept}' ended with status={rec['status']}: {rec['error']}")
    images = rec["usage"].get("images_processed")
    rec["billed_images"] = images if images is not None else 1
    rec["billed_images_source"] = "usage.images_processed" if images is not None else "assumed 1 per image request"
    rec["cost_usd"] = rec["billed_images"] * SAM_USD_PER_IMAGE
    rec["attempts"] = attempts
    rec["concept"] = concept
    cache.put("sam", key, rec)
    log_cost(ledger, {"ts": _now(), "run": run_name, "model": SAM_MODEL, "purpose": f"segment '{concept}'",
                      "billed_images": rec["billed_images"], "cost_usd": rec["cost_usd"], "latency_s": rec["latency_s"]})
    return {**rec, "cached": False}


def spark_structured(client: Any, *, content: list[dict[str, Any]], schema_name: str, schema: dict[str, Any],
                     effort: str, max_output_tokens: int, cache: Cache, ledger: bool, run_name: str,
                     purpose: str) -> dict[str, Any]:
    """Muse Spark 1.3 on the Responses API with text.format json_schema (strict)."""
    kwargs = {
        "model": SPARK_MODEL,
        "input": [{"type": "message", "role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        # Large inline media with store=true can 413 on persistence (error-handling); we never chain.
        "store": False,
    }
    key = request_key(kwargs)
    rec = cache.get("spark", key)
    cached = rec is not None
    if rec is None:
        t0 = time.monotonic()
        resp = client.responses.create(**kwargs)
        u = usage_dict(resp.usage)
        rec = {"purpose": purpose, "status": resp.status, "output_text": resp.output_text or "", "usage": u,
               "latency_s": round(time.monotonic() - t0, 3), "cost_usd": spark_cost_usd(u), "effort": effort,
               "response_id": resp.id, "incomplete_details": _as_dict(getattr(resp, "incomplete_details", None))}
        log_cost(ledger, {"ts": _now(), "run": run_name, "model": SPARK_MODEL, "purpose": purpose, "effort": effort,
                          "usage": u, "cost_usd": rec["cost_usd"], "latency_s": rec["latency_s"]})
        if rec["status"] == "completed":
            cache.put("spark", key, rec)
    if rec["status"] != "completed":
        raise SpikeError(f"Muse Spark {purpose}: status={rec['status']} {rec.get('incomplete_details')} "
                         "(raise --max-output-tokens if the reason is max_output_tokens)")
    try:
        parsed = json.loads(rec["output_text"])
    except json.JSONDecodeError as exc:
        raise SpikeError(f"Muse Spark {purpose}: output is not JSON ({exc}): {rec['output_text'][:200]!r}") from exc
    return {**rec, "parsed": parsed, "cached": cached, "cost_usd": 0.0 if cached else rec["cost_usd"]}


def _image_part(data_url: str, detail: str | None) -> dict[str, Any]:
    part: dict[str, Any] = {"type": "input_image", "image_url": data_url}
    if detail:
        part["detail"] = detail
    return part


# Box-mode grounding prompt from image-understanding ("Some grounding modes"). The only change:
# strict structured output needs an object root, so the array goes in a "results" field.
GROUNDING_PROMPT = (
    "You are an object grounding expert. Provide the bounding box coordinates of the objects in the image: "
    "{names}. Ensure the objects accurately match the request and do not miss any objects. For each object, "
    'answer in the format {{"object_name": "<name>", "bbox": [{{"x_min": <int>, "y_min": <int>, "x_max": <int>, '
    '"y_max": <int>}}]}}. The coordinates should be in the 0-1000 range. Return a JSON object whose "results" '
    "field is the array of results. If you cannot find an object, omit it from the results."
)
_BOX = {"type": "object",
        "properties": {k: {"type": "integer"} for k in ("x_min", "y_min", "x_max", "y_max")},
        "required": ["x_min", "y_min", "x_max", "y_max"], "additionalProperties": False}
GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"object_name": {"type": "string"}, "bbox": {"type": "array", "items": _BOX}},
        "required": ["object_name", "bbox"], "additionalProperties": False}}},
    "required": ["results"], "additionalProperties": False,
}


def to_pixels(v: float, size: int) -> int:
    """Normalized 0-1000 grid to pixels (image-understanding), clamped: the model may emit 1001 at an edge."""
    return int(min(max(round(float(v) * size / 1000), 0), size - 1))


def grounding_to_detections(parsed: dict[str, Any], size: tuple[int, int]) -> list[Detection]:
    W, H = size
    dets: list[Detection] = []
    for item in parsed.get("results", []):
        name = str(item.get("object_name", "")).strip().strip('"') or "object"
        for b in item.get("bbox", []):
            xs = sorted((to_pixels(b["x_min"], W), to_pixels(b["x_max"], W)))
            ys = sorted((to_pixels(b["y_min"], H), to_pixels(b["y_max"], H)))
            if xs[1] - xs[0] >= 2 and ys[1] - ys[0] >= 2:
                dets.append(Detection("spark-grounding", name, f"g{len(dets)}", (xs[0], ys[0], xs[1], ys[1])))
    return dets


VALUE_SOURCES = ("printed_label", "axis_estimate")

CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "chart_title": {"type": "string", "description": "The chart's title as printed, or an empty string."},
        "series": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Series name, e.g. 'Revenue'."},
                "period": {"type": "string", "description": "X-axis category exactly as printed, e.g. \"Q1'24\"."},
                "value": {"type": "number", "description": "Plotted value as a plain number in the chart's unit."},
                "unit": {"type": "string", "description": "Unit stated on the chart, e.g. '$M', '%', or ''."},
                # Design rule: only printed labels can be verified exactly; axis estimates are approximate.
                "source": {"type": "string", "enum": list(VALUE_SOURCES),
                           "description": "printed_label if the number is printed on the chart; "
                                          "axis_estimate if it was estimated from the axis scale."},
            },
            "required": ["label", "period", "value", "unit", "source"], "additionalProperties": False}},
    },
    "required": ["chart_title", "series"], "additionalProperties": False,
}

CHART_PROMPT = """You are reading the numbers plotted in the chart(s) on one slide from a company's earnings presentation, for a fact-checking tool. Describe only what is printed or plotted on the slide. Give no advice and no predictions.

{images}

Return chart_title (the chart's title as printed, or "" if it has none) and series: one item per plotted data point, meaning one series at one period.
- label: the series name from the legend, or from the chart title when the chart has a single series (for example "Revenue").
- period: the x-axis category exactly as printed (for example "Q1'24", "1Q25", "FY2023").
- value: the number as printed in the point's data label, written as a plain number with no thousands separators, currency symbols, or unit words. If a point has no data label, estimate its value from the axis scale.
- unit: the unit stated on the chart (for example "$M", "$B", "%"), or "" if none is stated.
- source: "printed_label" when the number is printed on the chart for that point; "axis_estimate" when you estimated it from the axis scale. Never mark an estimate as printed_label.

Rules:
- For stacked bars, report each printed segment and also the printed total of the stack, as its own series labeled "Total" plus the measure (for example "Total noninterest expense").
- If the legend, the title, or a footnote marks a number as adjusted, non-GAAP, or FTE, say so in its label (for example "Efficiency ratio (adjusted, non-GAAP)"), so GAAP and non-GAAP values are never mixed.
- Report each data point once, even if it appears in more than one image.
- Do not include numbers that are not plotted in a chart, such as totals or growth rates quoted in the slide's text.
- Leave out any data point you cannot read."""


def chart_content(data_url: str, crops: list[Crop], detail: str | None) -> list[dict[str, Any]]:
    lines = ["Image 1 is the full slide."]
    for i, c in enumerate(crops, 2):
        l, t, r, b = c.rect
        lines.append(f'Image {i} is a zoomed-in crop of the same slide: the region located as "{c.det.concept}" '
                     f"by {METHODS[c.det.method]['name']} (pixels x {l}-{r - 1}, y {t}-{b - 1} of image 1).")
    content = [{"type": "input_text", "text": CHART_PROMPT.format(images="\n".join(lines))},
               _image_part(data_url, detail)]
    content += [_image_part(c.data_url, detail) for c in crops]
    return content


# ----------------------------------------------------------------------------
# Synthetic chart (accuracy harness) and scoring
# ----------------------------------------------------------------------------

SYNTH_POINTS = [("Q1'24", 1084.7), ("Q2'24", 1126.3), ("Q3'24", 1098.5), ("Q4'24", 1212.9),
                ("Q1'25", 1267.4), ("Q2'25", 1239.8), ("Q3'25", 1331.6), ("Q4'25", 1402.2)]


def make_synthetic_chart(name: str, *, labels: bool = True) -> tuple[Path, Path, dict[str, Any]]:
    """A 1600x900 slide with an 8-quarter bar chart of known values, plus distractor numbers in a text panel.

    A test fixture that imitates an earnings slide, so every bar carries a data label
    the way real decks do (--no-labels removes them to test reading off the axis).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    W, H, DPI = 1600, 900, 100
    # Navy bars, like a typical deck, and distinct from the overlay's SAM blue so masks stay visible.
    ink, ink2, rule, navy = "#0b0b0b", "#52514e", "#c3c2b7", "#1f3a5f"
    periods = [p for p, _ in SYNTH_POINTS]
    values = [v for _, v in SYNTH_POINTS]
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor="#fcfcfb")
    fig.text(0.04, 0.925, "Example Corp", fontsize=26, fontweight="bold", color=ink)
    fig.text(0.04, 0.88, "Fourth quarter 2025 earnings presentation", fontsize=15, color=ink2)
    fig.add_artist(plt.Line2D([0.04, 0.96], [0.855, 0.855], color=rule, linewidth=1))
    ax = fig.add_axes((0.07, 0.14, 0.55, 0.6))
    ax.set_facecolor("#fcfcfb")
    ax.set_title("Example Corp revenue ($M)", loc="left", fontsize=18, fontweight="bold", color=ink,
                 pad=18, parse_math=False)
    x = list(range(len(values)))
    bars = ax.bar(x, values, width=0.62, color=navy, zorder=2)
    ax.set_xticks(x, periods, fontsize=14, color=ink)
    ax.set_ylim(0, max(values) * 1.15)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(rule)
    ax.tick_params(axis="x", length=0, pad=8)
    if labels:
        for bar, v in zip(bars, values):
            ax.annotate(f"{v:,.1f}", (bar.get_x() + bar.get_width() / 2, v), xytext=(0, 6),
                        textcoords="offset points", ha="center", va="bottom", fontsize=15, color=ink)
        ax.spines["left"].set_visible(False)
        ax.set_yticks([])
    else:
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0, labelsize=13, labelcolor=ink2)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.grid(axis="y", color="#e4e3df", linewidth=1, zorder=0)
    yoy = (values[7] / values[3] - 1) * 100
    fy25, fy24 = sum(values[4:]), sum(values[:4])
    fig.text(0.68, 0.70, "Highlights", fontsize=18, fontweight="bold", color=ink)
    bullets = [f"Q4'25 revenue of ${values[7]:,.1f}M, up {yoy:.1f}% YoY",
               f"FY2025 revenue of ${fy25:,.1f}M, up {(fy25 / fy24 - 1) * 100:.1f}%",
               "Operating margin of 21.4%, up 1.2 points",
               "All figures are fictional test data"]
    for i, text in enumerate(bullets):
        fig.text(0.68, 0.63 - i * 0.075, "•  " + text, fontsize=14, color=ink, parse_math=False, wrap=True)
    fig.text(0.04, 0.04, "Example Corp · synthetic slide for Receipts Spike C", fontsize=11, color=ink2)
    fig.text(0.96, 0.04, "7", fontsize=11, color=ink2, ha="right")

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    def to_px(bb: Any) -> list[int]:
        return [max(0, math.floor(bb.x0)), max(0, math.floor(H - bb.y1)),
                min(W - 1, math.ceil(bb.x1) - 1), min(H - 1, math.ceil(H - bb.y0) - 1)]

    from matplotlib.transforms import Bbox
    chart_box = to_px(ax.get_tightbbox(renderer))  # bars, ticks, data labels, and title
    plot_area = to_px(ax.get_window_extent(renderer))  # axes rectangle, including empty margins
    marks_box = to_px(Bbox.union([b.get_window_extent(renderer) for b in bars]))  # the plotted bars
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img_path = OUT_DIR / f"c_{name}_chart.png"
    fig.savefig(img_path, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    with Image.open(img_path) as im:
        if im.size != (W, H):
            raise SpikeError(f"synthetic chart is {im.size}, expected {(W, H)}")
    truth = {"title": "Example Corp revenue ($M)", "label": "Revenue", "unit": "$M", "decimals": 1,
             "labels_printed": labels, "image": img_path.name, "size": [W, H],
             "chart_box_px": chart_box, "plot_area_px": plot_area, "marks_box_px": marks_box,
             "points": [{"period": p, "canonical": canonical_period(p), "value": v} for p, v in SYNTH_POINTS],
             "distractors": {"yoy_pct": round(yoy, 1), "fy2025_total": round(fy25, 1), "operating_margin_pct": 21.4}}
    truth_path = OUT_DIR / f"c_{name}_truth.json"
    truth_path.write_text(json.dumps(truth, indent=2))
    return img_path, truth_path, truth


_PERIOD_PATTERNS = [
    (re.compile(r"^Q([1-4])\s*(?:FY|CY)?\s*'?\s*(\d{4}|\d{2})$"), False),  # Q1'24, Q1 2024, Q1 FY24, Q1-24
    (re.compile(r"^([1-4])Q\s*(?:FY|CY)?\s*'?\s*(\d{4}|\d{2})$"), False),  # 1Q24, 1Q 2024, 1QFY24
    (re.compile(r"^(?:FY|CY)?\s*'?(\d{4}|\d{2})\s*Q([1-4])$"), True),      # 2024 Q1, FY24 Q1
]


def canonical_period(text: str) -> str:
    t = str(text).upper().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[-_./]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for rx, year_first in _PERIOD_PATTERNS:
        m = rx.match(t)
        if m:
            q, y = (m.group(2), m.group(1)) if year_first else (m.group(1), m.group(2))
            year = int(y) + (2000 if len(y) == 2 else 0)
            return f"Q{q} {year}"
    m = re.match(r"^(?:FY|CY)\s*'?(\d{4}|\d{2})$", t)
    if m:
        y = m.group(1)
        return f"FY {int(y) + (2000 if len(y) == 2 else 0)}"
    return t


def unit_scale(unit: str) -> float | None:
    u = str(unit).lower().replace(" ", "")
    if re.search(r"billion|bn|\$b\b|^b$|usdb", u):
        return 1e9
    if re.search(r"million|\bmm\b|\$mm|\$m\b|^m$|usdm|\bmn\b", u):
        return 1e6
    if re.search(r"thousand|\$k\b|^k$|000s", u):
        return 1e3
    return None


def score_chart(series: list[dict[str, Any]], truth: dict[str, Any]) -> dict[str, Any]:
    """Share of known values read within +-1% or the chart's rounding (half the last printed digit)."""
    truth_scale = unit_scale(truth["unit"])
    half_step = 0.5 * 10 ** (-truth["decimals"])
    by_period: dict[str, list[dict[str, Any]]] = {}
    for item in series:
        by_period.setdefault(canonical_period(item.get("period", "")), []).append(item)
    rows = []
    for p in truth["points"]:
        cands = by_period.get(p["canonical"], [])
        best: tuple[dict[str, Any], float, float] | None = None
        for c in cands:
            try:
                v = float(c["value"])
            except (TypeError, ValueError):
                continue
            s = unit_scale(c.get("unit", ""))
            if truth_scale and s and s != truth_scale:
                v = v * s / truth_scale
            err = abs(v - p["value"])
            if best is None or err < best[1]:
                best = (c, err, v)
        tol = max(0.01 * abs(p["value"]), half_step)
        rows.append({"period": p["period"], "truth": p["value"],
                     "read": None if best is None else best[2], "read_raw": None if best is None else best[0],
                     "source": None if best is None else best[0].get("source"),
                     "abs_err": None if best is None else round(best[1], 6),
                     "rel_err_pct": None if best is None else round(100 * best[1] / abs(p["value"]), 3),
                     "within_tol": best is not None and best[1] <= tol + 1e-9,
                     "exact": best is not None and best[1] <= half_step + 1e-9,
                     "candidates": len(cands)})
    hits = sum(r["within_tol"] for r in rows)
    known = {p["canonical"] for p in truth["points"]}
    extras = [i for i in series if canonical_period(i.get("period", "")) not in known]
    acc = hits / len(rows) if rows else 0.0
    by_source: dict[str, dict[str, int]] = {}
    for r in rows:
        b = by_source.setdefault(r["source"] or "not_read", {"total": 0, "within_tol": 0, "exact": 0})
        b["total"] += 1
        b["within_tol"] += r["within_tol"]
        b["exact"] += r["exact"]
    return {"accuracy": round(acc, 4), "hits": hits, "total": len(rows), "by_source": by_source,
            "exact": sum(r["exact"] for r in rows), "extra_items": len(extras), "extras": extras,
            "duplicates": sum(max(0, r["candidates"] - 1) for r in rows),
            "gate": ACCURACY_GATE, "passes_gate": acc >= ACCURACY_GATE, "rows": rows,
            "rule": "match by canonical period; hit if |read - truth| <= max(1% of truth, half the last printed digit)"}


def check_chart_box(dets: list[Detection], truth: dict[str, Any]) -> dict[str, Any]:
    """Did a chart box cover the known chart? It must contain >= 95% of the plotted bars and have
    IoU >= 0.5 with the whole chart (title, data labels, and axis labels included)."""
    chart, marks = tuple(truth["chart_box_px"]), tuple(truth["marks_box_px"])
    rows = [{"method": d.method, "concept": d.concept, "object_id": d.object_id, "box": list(d.box),
             "iou_with_chart": round(box_iou(d.box, chart), 3),
             "marks_coverage": round(box_coverage(marks, d.box), 3),
             "chart_coverage": round(box_coverage(chart, d.box), 3)}
            for d in dets if "chart" in d.concept.lower()]
    best: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        mine = [r for r in rows if r["method"] == method]
        if mine:
            top = max(mine, key=lambda r: (r["marks_coverage"], r["iou_with_chart"]))
            best[method] = {**top, "boxed_chart": top["marks_coverage"] >= 0.95 and top["iou_with_chart"] >= 0.5}
    return {"truth_chart_box": list(chart), "truth_marks_box": list(marks), "detections": rows, "best": best}


# ----------------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------------

def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_working_image(args: argparse.Namespace) -> tuple[Image.Image, dict[str, Any]]:
    """The image every model sees. All boxes are in its pixels, and the overlay is drawn on it."""
    if args.pdf:
        import pymupdf
        if not args.pdf.is_file():
            raise SpikeError(f"no such PDF: {args.pdf}")
        with pymupdf.open(str(args.pdf)) as doc:
            if not 1 <= args.page <= len(doc):
                raise SpikeError(f"--page {args.page} is out of range: the PDF has {len(doc)} page(s)")
            page = doc[args.page - 1]
            zoom = args.max_side / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            info = {"kind": "pdf", "path": str(args.pdf), "page": args.page, "sha256": _sha256_file(args.pdf),
                    "page_points": [page.rect.width, page.rect.height], "zoom": round(zoom, 4)}
    else:
        path = args.image
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise SpikeError("--image takes a .png, .jpg, or .jpeg file")
        if not path.is_file():
            raise SpikeError(f"no such image: {path}")
        with Image.open(path) as raw:
            # SAM coordinates are in pixels as displayed, after EXIF rotation (sam/segmenting).
            oriented = ImageOps.exif_transpose(raw)
            original = oriented.size
            img = _to_rgb(oriented)
        scale = min(1.0, args.max_side / max(img.size))
        if scale < 1.0:
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
        info = {"kind": "image", "path": str(path), "sha256": _sha256_file(path),
                "original_size": list(original), "scale": round(scale, 4)}
    info["working_size"] = list(img.size)
    return img, info


def _mask_status(dets: list[Detection]) -> str:
    sam = [d for d in dets if d.method == "sam-3.1"]
    if not sam:
        return "no SAM 3.1 detections"
    sources = {d.mask_source for d in sam}
    if "meta_sam_parser" in sources:
        return f"masks decoded with meta_sam_parser ({sum(d.raster is not None for d in sam)}/{len(sam)})"
    if "selftest-placeholder" in sources:
        return "self-test placeholder masks (real payloads need meta_sam_parser to decode)"
    return "boxes only: mask payloads received but not decoded (pip install meta-sam-parser)"


def run_pipeline(client: Any, img: Image.Image, *, name: str, source: dict[str, Any], concepts: list[str],
                 grounding: str, effort: str, detail: str | None, max_crops: int, max_output_tokens: int,
                 cache: Cache, ledger: bool, truth: dict[str, Any] | None = None,
                 mask_fallback: Callable[[Image.Image, Detection], np.ndarray] | None = None) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    W, H = img.size
    data_url = png_data_url(img)
    run: dict[str, Any] = {
        "name": name, "started_at": _now(), "source": source,
        "models": {"segmentation": SAM_MODEL, "reading": SPARK_MODEL},
        "params": {"concepts": concepts, "grounding": grounding, "effort": effort, "detail": detail,
                   "max_crops": max_crops, "max_output_tokens": max_output_tokens},
        "sam": [], "grounding": None, "chart": None,
    }
    dets: list[Detection] = []
    notes: list[str] = []

    # Step 1: SAM 3.1, one noun phrase per request.
    print(f"[SAM 3.1] {W}x{H} image, {len(concepts)} concept(s), one request each")
    for concept in concepts:
        r = sam_segment(client, concept, data_url, cache=cache, ledger=ledger, run_name=name)
        records, diags = parse_sam_output(r["output_text"])
        rasters: dict[str, np.ndarray] = {}
        if records:
            rasters, mask_notes = decode_masks_with_official_parser(r["output_text"])
            notes += [n for n in mask_notes if n not in notes]
        for rec in records:
            box = (rec.x1, rec.y1, rec.x2, rec.y2)
            if (rec.frame_w, rec.frame_h) != (W, H):
                sx, sy = W / rec.frame_w, H / rec.frame_h
                diags.append(f"frame {rec.frame_w}x{rec.frame_h} differs from the sent {W}x{H}; box rescaled")
                box = (int(rec.x1 * sx), int(rec.y1 * sy), min(W - 1, int(rec.x2 * sx)), min(H - 1, int(rec.y2 * sy)))
            det = Detection("sam-3.1", concept, rec.object_id, box, rec.confidence, rec.mask)
            if rec.object_id in rasters:
                det.raster, det.mask_source = rasters[rec.object_id], "meta_sam_parser"
            elif mask_fallback is not None:
                det.raster, det.mask_source = mask_fallback(img, det), "selftest-placeholder"
            dets.append(det)
        run["sam"].append({
            "concept": concept, "status": r["status"], "matches": len(records), "attempts": r["attempts"],
            "latency_s": r["latency_s"], "first_delta_s": r["first_delta_s"], "usage": r["usage"],
            "billed_images": r.get("billed_images"), "billed_images_source": r.get("billed_images_source"),
            "cost_usd": r["cost_usd"], "cached": r["cached"], "response_id": r.get("response_id"),
            "final_text_matches_deltas": r.get("final_text_matches_deltas"), "diagnostics": diags,
            "output_text": r["output_text"]})
        boxes = ", ".join(f"#{x.object_id} {list(x.box)}" + (f" c={x.confidence:.2f}" if x.confidence is not None else "")
                          for x in dets if x.method == "sam-3.1" and x.concept == concept)
        print(f"  '{concept}': {len(records)} match(es) {boxes}  |  {r['latency_s']:.1f}s, "
              f"${r['cost_usd']:.4f}{' (cached)' if r['cached'] else ''}")
        for d in diags:
            print(f"    diagnostic: {d}")
    sam_found = any(d.method == "sam-3.1" for d in dets)

    # Step 3, fallback: Muse Spark grounding boxes. Runs before chart reading so its boxes can be cropped.
    if grounding == "always" or (grounding == "auto" and not sam_found):
        why = "SAM 3.1 found nothing" if not sam_found else "--grounding always"
        print(f"[Muse Spark grounding] {why}; effort={effort}")
        content = [{"type": "input_text", "text": GROUNDING_PROMPT.format(names=", ".join(f'"{c}"' for c in concepts))},
                   _image_part(data_url, detail)]
        g = spark_structured(client, content=content, schema_name="object_grounding", schema=GROUNDING_SCHEMA,
                             effort=effort, max_output_tokens=max_output_tokens, cache=cache, ledger=ledger,
                             run_name=name, purpose="grounding")
        g_dets = grounding_to_detections(g["parsed"], (W, H))
        dets += g_dets
        run["grounding"] = {k: g[k] for k in ("status", "latency_s", "usage", "cost_usd", "cached", "effort",
                                              "response_id", "parsed")}
        print(f"  {len(g_dets)} box(es): " + ", ".join(f"{d.concept} {list(d.box)}" for d in g_dets)
              + f"  |  {g['latency_s']:.1f}s, ${g['cost_usd']:.4f}{' (cached)' if g['cached'] else ''}")

    # Step 2: Muse Spark chart reading on the full slide plus chart crops (SAM first, grounding if none).
    regions = (select_chart_regions([d for d in dets if d.method == "sam-3.1"], (W, H), max_crops)
               or select_chart_regions([d for d in dets if d.method == "spark-grounding"], (W, H), max_crops))
    crops = make_crops(img, regions, name)
    print(f"[Muse Spark chart reading] full image + {len(crops)} chart crop(s); effort={effort}")
    c = spark_structured(client, content=chart_content(data_url, crops, detail), schema_name="chart_values",
                         schema=CHART_SCHEMA, effort=effort, max_output_tokens=max_output_tokens, cache=cache,
                         ledger=ledger, run_name=name, purpose="chart reading")
    chart = c["parsed"]
    series = chart.get("series", [])
    print(f"  {len(series)} value(s), title {chart.get('chart_title', '')!r}  |  {c['latency_s']:.1f}s, "
          f"${c['cost_usd']:.4f}{' (cached)' if c['cached'] else ''}, usage {c['usage']}")
    for item in series:
        print(f"    {item.get('label', '')!s:<28} {item.get('period', '')!s:<10} {item.get('value')!s:>12} "
              f"{item.get('unit', '')!s:<6} {item.get('source', '')}")
    chart_path = OUT_DIR / f"c_chart_{name}.json"
    chart_path.write_text(json.dumps({
        "source": source, "model": SPARK_MODEL, "effort": effort, "created_at": _now(),
        "rule": "only series items with source=printed_label can be verified exactly; axis_estimate is approximate",
        "regions_sent": [{"method": x.det.method, "concept": x.det.concept, "object_id": x.det.object_id,
                          "crop_half_open": list(x.rect), "file": x.path.name} for x in crops],
        "chart": chart}, indent=2))
    run["chart"] = {k: c[k] for k in ("status", "latency_s", "usage", "cost_usd", "cached", "effort", "response_id")}
    run["chart"]["n_values"] = len(series)
    run["chart"]["crops"] = [{"file": x.path.name, "rect_half_open": list(x.rect), "concept": x.det.concept,
                              "method": x.det.method, "upscale": x.upscale} for x in crops]

    if truth is not None:
        run["accuracy"] = score_chart(series, truth)
        run["box_check"] = check_chart_box(dets, truth)

    mask_status = _mask_status(dets)
    run["mask_status"] = mask_status
    footer = [f"Masks: {mask_status}.",
              f"Chart reading: {SPARK_MODEL}, effort={effort}, {len(series)} value(s) from the full slide + {len(crops)} crop(s)."]
    if "accuracy" in run:
        a = run["accuracy"]
        footer.append(f"Accuracy vs known values: {a['hits']}/{a['total']} within ±1% or rounding "
                      f"({a['exact']}/{a['total']} exact); gate {int(ACCURACY_GATE * 100)}%: "
                      f"{'PASS' if a['passes_gate'] else 'FAIL'}.")
    footer += [n for n in notes if "not installed" not in n]
    overlay = render_overlay(img, dets, OUT_DIR / f"c_overlay_{name}.png",
                             title=f"Spike C · {name} · {W}x{H}", notes=footer, crop_rects=[x.rect for x in crops])

    sam_usd = sum(s["cost_usd"] for s in run["sam"])
    spark_usd = (run["grounding"]["cost_usd"] if run["grounding"] else 0.0) + run["chart"]["cost_usd"]
    run["detections"] = [d.to_json((W, H)) for d in dets]
    run["notes"] = notes
    def live_s(rec: dict[str, Any] | None) -> float:  # cached calls cost no time on this run
        return 0.0 if not rec or rec.get("cached") else rec["latency_s"]

    run["totals"] = {"sam_usd": round(sam_usd, 6), "spark_usd": round(spark_usd, 6),
                     "total_usd": round(sam_usd + spark_usd, 6),
                     "sam_live_latency_s": round(sum(live_s(s) for s in run["sam"]), 3),
                     "spark_live_latency_s": round(live_s(run["grounding"]) + live_s(run["chart"]), 3)}
    run["outputs"] = {"overlay": str(overlay), "chart_json": str(chart_path),
                      "crops": [str(x.path) for x in crops], "run_json": str(OUT_DIR / f"c_run_{name}.json")}
    run["finished_at"] = _now()
    Path(run["outputs"]["run_json"]).write_text(json.dumps(run, indent=2, default=str))
    print_summary(run)
    return run


def run_sam_only(client: Any, img: Image.Image, *, name: str, concepts: list[str], cache: Cache, ledger: bool) -> None:
    """SAM 3.1 boxes and overlay only ($0.0025 per concept), for tuning noun phrases cheaply."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data_url = png_data_url(img)
    dets: list[Detection] = []
    total = 0.0
    for concept in concepts:
        r = sam_segment(client, concept, data_url, cache=cache, ledger=ledger, run_name=name)
        records, diags = parse_sam_output(r["output_text"])
        dets += [Detection("sam-3.1", concept, rec.object_id, (rec.x1, rec.y1, rec.x2, rec.y2), rec.confidence, rec.mask)
                 for rec in records]
        total += r["cost_usd"]
        print(f"  '{concept}': {len(records)} match(es) "
              + ", ".join(f"{list((x.x1, x.y1, x.x2, x.y2))} c={x.confidence}" for x in records)
              + f"  |  {r['latency_s']:.1f}s, ${r['cost_usd']:.4f}{' (cached)' if r['cached'] else ''}"
              + (f"  diagnostics: {diags}" if diags else ""))
    out = render_overlay(img, dets, OUT_DIR / f"c_overlay_{name}.png", title=f"Spike C · {name} · SAM only",
                         notes=[f"Masks: {_mask_status(dets)}.", f"Concepts: {', '.join(concepts)}."], crop_rects=[])
    print(f"SAM-only cost ${total:.4f}; overlay {out}")


def print_summary(run: dict[str, Any]) -> None:
    print(f"\n== Spike C summary: {run['name']} ==")
    for s in run["sam"]:
        print(f"SAM 3.1 '{s['concept']}': {s['matches']} match(es), {s['latency_s']:.1f}s"
              f"{' (cached)' if s['cached'] else ''}")
    print(f"Masks: {run['mask_status']}")
    if run["grounding"]:
        print(f"Muse Spark grounding: {len(run['grounding']['parsed'].get('results', []))} object(s), "
              f"{run['grounding']['latency_s']:.1f}s")
    if "box_check" in run:
        for method, b in run["box_check"]["best"].items():
            print(f"Chart box ({METHODS[method]['name']}): contains {b['marks_coverage']:.0%} of the plotted bars and "
                  f"{b['chart_coverage']:.0%} of the whole chart (title and axis labels included), IoU {b['iou_with_chart']:.2f} "
                  f"-> {'boxed the chart' if b['boxed_chart'] else 'MISSED'}")
        if not run["box_check"]["best"]:
            print("Chart box: no chart detection to compare")
    if "accuracy" in run:
        a = run["accuracy"]
        print(f"Chart reading accuracy: {a['hits']}/{a['total']} = {a['accuracy']:.0%} within ±1% or rounding; "
              f"exact {a['exact']}/{a['total']}; extra items {a['extra_items']}; "
              f"gate {int(ACCURACY_GATE * 100)}% -> {'PASS' if a['passes_gate'] else 'FAIL'}")
        print("   by source: " + "; ".join(f"{s} {b['exact']}/{b['total']} exact, {b['within_tol']}/{b['total']} within ±1%"
                                         for s, b in a["by_source"].items()))
        for r in a["rows"]:
            flag = "ok " if r["within_tol"] else "MISS"
            print(f"   {flag} {r['period']:<6} truth {r['truth']:>9,.1f}  read {r['read'] if r['read'] is not None else '-':>10}"
                  f"  err {r['rel_err_pct'] if r['rel_err_pct'] is not None else '-'}%")
    u = run["chart"]["usage"]
    print(f"Muse Spark chart reading: {run['chart']['latency_s']:.1f}s{' (cached result)' if run['chart']['cached'] else ''}, "
          f"effort={run['chart']['effort']}, input {u.get('input_tokens')} (cached {u.get('cached_tokens')}), "
          f"output {u.get('output_tokens')} (reasoning {u.get('reasoning_tokens')})")
    t = run["totals"]
    print(f"Cost this run: SAM ${t['sam_usd']:.4f} + Spark ${t['spark_usd']:.4f} = ${t['total_usd']:.4f}  "
          f"(live-call latency: SAM {t['sam_live_latency_s']:.1f}s, Spark {t['spark_live_latency_s']:.1f}s; cached calls are free)")
    for k, v in run["outputs"].items():
        print(f"  {k}: {v}")


# ----------------------------------------------------------------------------
# Offline self-test: real OpenAI SDK, mocked HTTP transport, documented response shapes
# ----------------------------------------------------------------------------

SELFTEST_KEY = "selftest-dummy-key"

# Verbatim from sam/reading-segmentation (image grammar: three matches, payloads elided).
DOC_IMAGE_THREE = ("<0f>0<|box;x1=353;y1=382;x2=453;y2=427;w=537;h=561|><|mask;x=0;y=0;data=46,101,~...|>,"
                   "1<|box;x1=260;y1=382;x2=340;y2=427;w=537;h=561|><|mask;x=0;y=0;data=46,81,~...|>,"
                   "2<|box;x1=516;y1=117;x2=536;y2=150;w=537;h=561|><|mask;x=0;y=0;data=34,21,~...|>")
# Verbatim from sam/reading-segmentation (video grammar, frame 0, real lossless payloads).
DOC_VIDEO_FRAME0 = ("<0f>0<|box;x1=211;y1=228;x2=270;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,60,"
                    "~!!!!M!0c[0o91w?q1!pIH4pPRVp2B3'7`e.ioeAf6-k/#Xd8%dX9x(|>,"
                    "1<|box;x1=155;y1=228;x2=202;y2=254;w=320;h=334|><|mask;x=0;y=0;data=27,48,"
                    "~!!!!J!0c[q=Pj_zs=*4C4(/./x#:/`S_GnD`=o3?X{emCgO$y@|>")
# Verbatim from sam/segmenting (include_confidence) and the box-prompt response with object 2 elided.
DOC_CONFIDENCE = ("<0f>1<|box;x1=444;y1=625;x2=679;y2=850;w=1800;h=1200;c=0.9609375|>"
                  "<|mask;x=0;y=0;c=0.9609375;data=226,236,!...|>")
DOC_PROMPTED_ELIDED = ("<0f>1<|box;x1=444;y1=625;x2=679;y2=850;w=1800;h=1200|><|mask;x=0;y=0;data=226,236,!...|>,"
                       "2<|box;...|><|mask;...|>")


def _sse(events: list[dict[str, Any]]) -> bytes:
    chunks = []
    for i, ev in enumerate(events):
        ev = {**ev, "sequence_number": i}
        chunks.append(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n")
    return "".join(chunks).encode()


def _mock_sam_sse(lines: list[str], resp_id: str, *, error_code: str | None = None) -> bytes:
    """Responses stream events for SAM: one output_text.delta per line, finalized by content_part.done."""
    base = {"id": resp_id, "object": "response", "created_at": 1761552000, "model": SAM_MODEL, "output": [],
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "status": "in_progress"}
    usage = {"input_tokens": 0, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 0,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 0, "images_processed": 1}
    evs: list[dict[str, Any]] = [{"type": "response.created", "response": base},
                                 {"type": "response.in_progress", "response": base}]
    if error_code:  # stream-interrupting error event (error-handling: Streaming errors)
        evs.append({"type": "error", "code": error_code, "message": "Server is shutting down. Please retry your request.",
                    "param": None})
        return _sse(evs)
    if not lines:  # zero matches: no deltas, empty output_text, then response.completed
        evs.append({"type": "response.completed", "response": {**base, "status": "completed", "usage": usage}})
        return _sse(evs)
    item_id = f"msg_{resp_id}"
    part = {"type": "output_text", "text": "".join(lines), "annotations": []}
    item = {"id": item_id, "type": "message", "role": "assistant", "status": "completed", "content": [part]}
    evs.append({"type": "response.output_item.added", "output_index": 0,
                "item": {**item, "status": "in_progress", "content": []}})
    evs.append({"type": "response.content_part.added", "item_id": item_id, "output_index": 0, "content_index": 0,
                "part": {**part, "text": ""}})
    for line in lines:
        evs.append({"type": "response.output_text.delta", "item_id": item_id, "output_index": 0,
                    "content_index": 0, "delta": line, "logprobs": []})
    evs.append({"type": "response.content_part.done", "item_id": item_id, "output_index": 0, "content_index": 0,
                "part": part})
    evs.append({"type": "response.output_item.done", "output_index": 0, "item": item})
    evs.append({"type": "response.completed", "response": {**base, "status": "completed", "output": [item], "usage": usage}})
    return _sse(evs)


def _mock_spark_body(payload: dict[str, Any], resp_id: str, *, input_tokens: int, cached: int,
                     output_tokens: int, reasoning: int) -> dict[str, Any]:
    """Shaped like the protocols/responses example response, plus the documented usage breakdown."""
    return {"id": resp_id, "object": "response", "created_at": 1761552000, "status": "completed",
            "model": SPARK_MODEL, "store": False, "temperature": 1.0, "top_p": 1.0, "max_output_tokens": 12000,
            "tools": [], "tool_choice": "auto", "parallel_tool_calls": True, "service_tier": "auto",
            "output": [{"id": f"rs_{resp_id}", "type": "reasoning", "summary": []},
                       {"id": f"msg_{resp_id}:456", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(payload), "annotations": []}]}],
            "usage": {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": cached},
                      "output_tokens": output_tokens, "output_tokens_details": {"reasoning_tokens": reasoning},
                      "total_tokens": input_tokens + output_tokens}}


class _MockMetaAPI:
    """Serves canned SAM SSE streams and Spark JSON to the real OpenAI SDK through httpx2.MockTransport."""

    def __init__(self, sam: dict[str, list[bytes]], spark: list[dict[str, Any]]):
        self.sam = {k: list(v) for k, v in sam.items()}
        self.spark = list(spark)
        self.requests: list[dict[str, Any]] = []

    def handler(self, request: Any) -> Any:
        import httpx2
        body = json.loads(request.content.decode("utf-8"))
        self.requests.append({"url": str(request.url), "body": body,
                              "auth_ok": request.headers.get("authorization") == f"Bearer {SELFTEST_KEY}"})
        if body.get("model") == SAM_MODEL:
            concept = body["input"][0]["content"][0]["text"]
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=self.sam[concept].pop(0))
        if body.get("model") == SPARK_MODEL and self.spark:
            return httpx2.Response(200, json=self.spark.pop(0))
        return httpx2.Response(404, json={"error": {"code": "model_not_found", "message": "The requested model was not found.",
                                                    "param": None, "type": "invalid_request_error"}})

    def client(self) -> Any:
        import httpx2
        import openai
        return openai.OpenAI(base_url=BASE_URL, api_key=SELFTEST_KEY, max_retries=0, timeout=30.0,
                             http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(self.handler)))


def _selftest_mask(img: Image.Image, det: Detection) -> np.ndarray:
    """Placeholder box-local raster for the overlay test (an inset rounded region), clearly not a real mask."""
    x1, y1, x2, y2 = det.box
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    inset = max(2, round(0.03 * min(bw, bh)))
    m = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(m).rounded_rectangle((inset, inset, bw - 1 - inset, bh - 1 - inset), radius=4 * inset, fill=1)
    return np.asarray(m, dtype=np.uint8)


def _strict_ok(schema: dict[str, Any], path: str = "$") -> list[str]:
    """Meta's strict subset: object root, additionalProperties false, required lists every property."""
    problems = []
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        if sorted(schema.get("required", [])) != sorted(props):
            problems.append(f"{path}: required must list every property")
        for k, v in props.items():
            problems += _strict_ok(v, f"{path}.{k}")
    if schema.get("type") == "array":
        problems += _strict_ok(schema.get("items", {}), f"{path}[]")
    for bad in ("allOf", "oneOf"):
        if bad in schema:
            problems.append(f"{path}: {bad} is not supported")
    return problems


def run_selftest() -> int:
    import inspect

    import openai

    results: list[bool] = []

    def check(name: str, ok: Any, detail: str = "") -> None:
        results.append(bool(ok))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))

    print("SELFTEST (offline). No network and no .env: the Model API is mocked at the HTTP layer, so the real")
    print("OpenAI SDK builds every request and parses every SSE stream and JSON body. Input is a SYNTHETIC chart.\n")

    print("[1] SAM 3.1 grammar parser on the documented examples")
    recs, diags = parse_sam_output(DOC_IMAGE_THREE)
    check("image example: 3 records, ids 0,1,2, no diagnostics", len(recs) == 3 and [r.object_id for r in recs] == ["0", "1", "2"] and not diags)
    check("image example: first box = (353,382)-(453,427) in a 537x561 frame",
          (recs[0].x1, recs[0].y1, recs[0].x2, recs[0].y2, recs[0].frame_w, recs[0].frame_h) == (353, 382, 453, 427, 537, 561))
    check("mask raster is box-local: H = y2-y1+1 and W = x2-x1+1 in every record",
          all((r.mask.height, r.mask.width) == (r.y2 - r.y1 + 1, r.x2 - r.x1 + 1) for r in recs),
          ", ".join(f"{r.mask.height}x{r.mask.width}" for r in recs))
    recs, diags = parse_sam_output(DOC_VIDEO_FRAME0)
    check("video example: 2 lossless masks 27x60 and 27x48 with intact payloads",
          [(r.mask.height, r.mask.width, r.mask.encoding) for r in recs] == [(27, 60, "lossless"), (27, 48, "lossless")]
          and recs[0].mask.payload.startswith("!!!!M!0c[") and recs[1].mask.payload.endswith("y@") and not diags)
    two_frames = DOC_VIDEO_FRAME0 + "\n" + DOC_VIDEO_FRAME0.replace("<0f>", "<2f>")
    recs_nl, _ = parse_sam_output(two_frames)
    recs_glued, _ = parse_sam_output(two_frames.replace("\n", ""))
    check("frames read from the <Nf> marker (0 and 2), with or without a newline between lines",
          [r.frame for r in recs_nl] == [0, 0, 2, 2] and [r.frame for r in recs_glued] == [0, 0, 2, 2])
    recs, diags = parse_sam_output(DOC_CONFIDENCE)
    check("confidence example: c=0.9609375, one_bit mask 226x236",
          len(recs) == 1 and recs[0].confidence == 0.9609375 and recs[0].mask.encoding == "one_bit"
          and (recs[0].mask.height, recs[0].mask.width) == (226, 236))
    recs, diags = parse_sam_output(DOC_PROMPTED_ELIDED)
    check("elided record is a diagnostic, not a crash", len(recs) == 1 and len(diags) == 1, diags[0] if diags else "")
    recs, diags = parse_sam_output("")
    check("empty output_text = zero matches, no diagnostics", recs == [] and diags == [])

    print("\n[2] helpers")
    check("period formats normalize", [canonical_period(p) for p in ("Q1'24", "Q1 2024", "1Q24", "Q1 FY24", "2024 Q1", "Q1-24", "Q1 ’24")]
          == ["Q1 2024"] * 7)
    check("unit scales", (unit_scale("$M"), unit_scale("USD billions"), unit_scale("%"), unit_scale("$ in millions"))
          == (1e6, 1e9, None, 1e6))
    check("0-1000 grid to pixels, clamped at 1001", (to_pixels(500, 1600), to_pixels(1001, 1600), to_pixels(0, 900)) == (800, 1599, 0))
    columns = [Detection("sam-3.1", "bar chart", str(i), b, 0.61) for i, b in enumerate(
        [(1113, 184, 1259, 352), (882, 199, 1027, 352), (420, 208, 564, 352), (188, 210, 334, 353), (651, 199, 796, 352)])]
    charts3 = [Detection("sam-3.1", "bar chart", str(i), b, 0.6) for i, b in enumerate(
        [(519, 339, 867, 622), (110, 326, 446, 637), (943, 326, 1299, 637)])]
    merged = merge_bar_instances(columns, (1365, 1055))
    check("five per-column SAM boxes (live BofA slide 10) merge into one chart region",
          len(merged) == 1 and merged[0].box == (188, 184, 1259, 353), f"{[m.box for m in merged]}")
    check("three wide side-by-side charts (live Tesla slide 23) stay separate",
          len(merge_bar_instances(charts3, (1365, 1055))) == 3)
    check("chart schema fits the strict subset", not _strict_ok(CHART_SCHEMA), "; ".join(_strict_ok(CHART_SCHEMA)))
    check("grounding schema fits the strict subset", not _strict_ok(GROUNDING_SCHEMA), "; ".join(_strict_ok(GROUNDING_SCHEMA)))
    check("missing or blank MODEL_API_KEY is detected",
          api_key_from({}) is None and api_key_from({"MODEL_API_KEY": ' "" '}) is None
          and api_key_from({"MODEL_API_KEY": " abc "}) == "abc")
    check("Standard-mode model IDs only", "-contributor" not in SAM_MODEL + SPARK_MODEL, f"{SAM_MODEL}, {SPARK_MODEL}")
    accepted = set(inspect.signature(openai.OpenAI(api_key=SELFTEST_KEY, base_url=BASE_URL).responses.create).parameters)
    check(f"openai {openai.__version__} responses.create accepts every field we send",
          {"model", "input", "stream", "metadata", "text", "reasoning", "max_output_tokens", "store"} <= accepted)

    print("\n[3] pipeline: SAM boxes the chart (mocked), Spark reads it (mocked)")
    img_path, truth_path, truth = make_synthetic_chart("selftest", labels=True)
    img = Image.open(img_path).convert("RGB")
    W, H = img.size
    x1, y1, x2, y2 = truth["chart_box_px"]
    sam_line = (f"<0f>0<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={W};h={H};c=0.93|>"
                f"<|mask;x=0;y=0;c=0.93;data={y2 - y1 + 1},{x2 - x1 + 1},!...|>\n")
    wrong = dict(zip([p for p, _ in SYNTH_POINTS], [v for _, v in SYNTH_POINTS]))
    series = [{"label": "Revenue", "period": p, "value": v, "unit": "$M", "source": "printed_label"}
              for p, v in wrong.items()]
    series[4]["period"] = "1Q25"                               # alternate period format, still a hit
    series[6]["value"] = round(series[6]["value"] * 1.03, 1)   # 3% off, a miss...
    series[6]["source"] = "axis_estimate"                      # ...that the model says it estimated
    series.append({"label": "Revenue", "period": "FY2025", "value": 5241.0, "unit": "$M",
                   "source": "printed_label"})                 # not plotted: an extra
    chart_payload = {"chart_title": "Example Corp revenue ($M)", "series": series}
    api = _MockMetaAPI(
        sam={"bar chart": [_mock_sam_sse([], "resp_sam_err", error_code="server_shutting_down"),
                           _mock_sam_sse([sam_line], "resp_sam_bar")],
             "line chart": [_mock_sam_sse([], "resp_sam_line")], "table": [_mock_sam_sse([], "resp_sam_table")]},
        spark=[_mock_spark_body(chart_payload, "resp_chart", input_tokens=3120, cached=0, output_tokens=690, reasoning=412)])
    run = run_pipeline(api.client(), img, name="selftest", source={"kind": "synthetic", "path": str(img_path)},
                       concepts=list(DEFAULT_CONCEPTS), grounding="auto", effort="low", detail=None, max_crops=3,
                       max_output_tokens=12000, cache=Cache(None), ledger=False, truth=truth, mask_fallback=_selftest_mask)
    print()
    sam_reqs = [r for r in api.requests if r["body"]["model"] == SAM_MODEL]
    spark_reqs = [r for r in api.requests if r["body"]["model"] == SPARK_MODEL]
    b = sam_reqs[0]["body"]
    content = b["input"][0]["content"]
    sent = Image.open(io.BytesIO(base64.b64decode(content[1]["image_url"].split(",", 1)[1])))
    check("SAM request = one user message: input_text noun phrase + input_image data URL, streamed",
          b["stream"] is True and b["input"][0]["role"] == "user" and content[0] == {"type": "input_text", "text": "bar chart"}
          and content[1]["type"] == "input_image" and content[1]["image_url"].startswith("data:image/png;base64,"))
    check("SAM metadata values are strings", b["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"})
    check("the image SAM saw is the working image", sent.size == (W, H), f"{sent.size}")
    check("POST /v1/responses with the Bearer key", all(r["url"].endswith("/v1/responses") and r["auth_ok"] for r in api.requests))
    check("mid-stream error event (server_shutting_down) retried once", len(sam_reqs) == 4 and run["sam"][0]["attempts"] == 2)
    check("one bar-chart box, exactly the mocked box; zero matches for line chart and table",
          [s["matches"] for s in run["sam"]] == [1, 0, 0] and run["detections"][0]["box_inclusive"] == [x1, y1, x2, y2])
    check("final response text equals the accumulated deltas", run["sam"][0]["final_text_matches_deltas"] is True)
    check("SAM cost from usage.images_processed: 3 x $0.0025", abs(run["totals"]["sam_usd"] - 0.0075) < 1e-12,
          f"${run['totals']['sam_usd']}")
    sb = spark_reqs[0]["body"]
    images_sent = [p for p in sb["input"][0]["content"] if p["type"] == "input_image"]
    fmt = sb["text"]["format"]
    check("Spark request: muse-spark-1.3, text.format json_schema strict, reasoning.effort=low, store=false",
          sb["model"] == SPARK_MODEL and fmt["type"] == "json_schema" and fmt["strict"] is True
          and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", fmt["name"]) is not None
          and sb["reasoning"] == {"effort": "low"} and sb["store"] is False)
    check("Spark got the full image plus the SAM crop", len(images_sent) == 2 and len(run["chart"]["crops"]) == 1)
    crop = Image.open(OUT_DIR / run["chart"]["crops"][0]["file"])
    l, t, r_, b_ = run["chart"]["crops"][0]["rect_half_open"]
    f = run["chart"]["crops"][0]["upscale"]
    check("crop = SAM box plus padding, inside the image, enlarged to a 1024 px long side",
          crop.size == (round((r_ - l) * f), round((b_ - t) * f)) and max(crop.size) == 1024 and l <= x1 and t <= y1
          and r_ > x2 and b_ > y2 and r_ <= W and b_ <= H, f"{crop.size}, upscale {f}")
    expected_spark = (3120 * 1.25 + 690 * 4.25) / 1e6
    check("Spark cost from usage at Standard prices", abs(run["chart"]["cost_usd"] - expected_spark) < 1e-12,
          f"${run['chart']['cost_usd']:.7f}")
    a = run["accuracy"]
    check("accuracy scorer: 7/8 within tolerance (one 3% miss), 1 extra item, alternate period format matched",
          (a["hits"], a["total"], a["extra_items"], a["passes_gate"]) == (7, 8, 1, True) and a["rows"][4]["within_tol"]
          and not a["rows"][6]["within_tol"])
    check("accuracy split by source: printed_label 7/7 exact, axis_estimate 0/1",
          a["by_source"] == {"printed_label": {"total": 7, "within_tol": 7, "exact": 7},
                             "axis_estimate": {"total": 1, "within_tol": 0, "exact": 0}}, json.dumps(a["by_source"]))
    check("box check: the mocked SAM box boxed the known chart", run["box_check"]["best"]["sam-3.1"]["boxed_chart"])
    check("grounding fallback not called when SAM found a chart", run["grounding"] is None and len(spark_reqs) == 1)
    ov = Image.open(run["outputs"]["overlay"])
    check("overlay PNG written: full slide width, header and footer added", ov.format == "PNG" and ov.size[0] == W and ov.size[1] > H,
          f"{run['outputs']['overlay']} {ov.size}")
    saved = json.loads(Path(run["outputs"]["chart_json"]).read_text())
    check("chart JSON saved with series[] of {label, period, value, unit, source}",
          all(set(i) == {"label", "period", "value", "unit", "source"} for i in saved["chart"]["series"]))
    item_schema = sb["text"]["format"]["schema"]["properties"]["series"]["items"]
    check("schema sent: source is a required enum of printed_label | axis_estimate",
          "source" in item_schema["required"] and item_schema["properties"]["source"]["enum"] == ["printed_label", "axis_estimate"])

    print("\n[4] pipeline: SAM finds nothing, so Muse Spark grounding boxes are used (mocked)")
    norm = [round(x1 * 1000 / W), round(y1 * 1000 / H), round(x2 * 1000 / W), round(y2 * 1000 / H)]
    grounding_payload = {"results": [{"object_name": "bar chart", "bbox": [
        {"x_min": norm[0], "y_min": norm[1], "x_max": norm[2], "y_max": norm[3]}]},
        {"object_name": "table", "bbox": [{"x_min": 655, "y_min": 260, "x_max": 1001, "y_max": 640}]}]}
    api2 = _MockMetaAPI(
        sam={c: [_mock_sam_sse([], f"resp_empty_{i}")] for i, c in enumerate(DEFAULT_CONCEPTS)},
        spark=[_mock_spark_body(grounding_payload, "resp_ground", input_tokens=1650, cached=0, output_tokens=310, reasoning=220),
               _mock_spark_body(chart_payload, "resp_chart2", input_tokens=3120, cached=1024, output_tokens=650, reasoning=400)])
    run2 = run_pipeline(api2.client(), img, name="selftest_fallback", source={"kind": "synthetic", "path": str(img_path)},
                        concepts=list(DEFAULT_CONCEPTS), grounding="auto", effort="low", detail=None, max_crops=3,
                        max_output_tokens=12000, cache=Cache(None), ledger=False, truth=truth)
    print()
    g_req = [r for r in api2.requests if r["body"]["model"] == SPARK_MODEL][0]["body"]
    g_text = g_req["input"][0]["content"][0]["text"]
    check("grounding prompt = the documented Box mode wording with the three concepts",
          g_text.startswith("You are an object grounding expert.") and '"bar chart", "line chart", "table"' in g_text
          and "0-1000 range" in g_text and g_req["text"]["format"]["name"] == "object_grounding")
    gd = [d for d in run2["detections"] if d["method"] == "spark-grounding"]
    gb = gd[0]["box_inclusive"] if gd else [0, 0, 0, 0]
    check("grounding boxes converted from 0-1000 to pixels (bar chart within 2 px of truth)",
          len(gd) == 2 and all(abs(p - q) <= 2 for p, q in zip(gb, (x1, y1, x2, y2))), f"{gb} vs {[x1, y1, x2, y2]}")
    check("x_max=1001 clamped to the right edge", gd[1]["box_inclusive"][2] == W - 1 if len(gd) > 1 else False)
    check("grounding chart box used for the crop, and each box labeled with its method",
          run2["chart"]["crops"][0]["method"] == "spark-grounding" and run2["box_check"]["best"]["spark-grounding"]["boxed_chart"])
    check("cached input tokens priced at $0.15/M",
          abs(run2["chart"]["cost_usd"] - ((3120 - 1024) * 1.25 + 1024 * 0.15 + 650 * 4.25) / 1e6) < 1e-12)
    check("fallback overlay PNG written", Path(run2["outputs"]["overlay"]).is_file(), run2["outputs"]["overlay"])

    print("\n[5] official mask decoder (optional)")
    try:
        import meta_sam_parser  # noqa: F401  # type: ignore[import-not-found]
        rasters, notes = decode_masks_with_official_parser(DOC_VIDEO_FRAME0, media="video")
        check("meta_sam_parser decodes the documented 27x60 and 27x48 masks",
              {k: v.shape for k, v in rasters.items()} == {"0": (27, 60), "1": (27, 48)}, "; ".join(notes))
    except ImportError:
        print("  SKIP  meta_sam_parser is not installed; masks will not be decoded live (boxes only)")

    passed = sum(results)
    print(f"\nSELFTEST {'PASSED' if passed == len(results) else 'FAILED'}: {passed}/{len(results)} checks")
    return 0 if passed == len(results) else 1


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Spike C: SAM 3.1 chart and table regions on a slide, then Muse Spark 1.3 chart reading.",
        epilog=__doc__.split("Outputs go to")[0].split("Usage, from the repo root:")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path, help="slide image, PNG or JPG")
    src.add_argument("--pdf", type=Path, help="PDF; renders --page with PyMuPDF")
    src.add_argument("--synthetic", action="store_true", help="live run on a generated chart with known values; reports accuracy")
    src.add_argument("--selftest", action="store_true", help="offline self-test with mocked API responses")
    p.add_argument("--page", type=int, default=1, help="1-based PDF page (default 1)")
    p.add_argument("--name", help="output name (default: from the input)")
    p.add_argument("--concepts", nargs="+", default=list(DEFAULT_CONCEPTS), metavar="PHRASE",
                   help='SAM noun phrases, one request each (default: "bar chart" "line chart" "table")')
    p.add_argument("--grounding", choices=("auto", "always", "never"), default="auto",
                   help="Muse Spark grounding boxes: auto = only when SAM finds nothing (default); always = compare both")
    p.add_argument("--effort", choices=EFFORTS, default="low",
                   help="Muse Spark reasoning.effort (default low; reasoning tokens bill as output)")
    p.add_argument("--detail", choices=("auto", "low", "high", "original"), default=None,
                   help="input_image detail for Muse Spark (default: not sent, which means auto)")
    p.add_argument("--max-crops", type=int, default=3, help="chart crops sent to Muse Spark (default 3)")
    p.add_argument("--max-side", type=int, default=2048, help="long side of the working image in pixels (default 2048)")
    p.add_argument("--max-output-tokens", type=int, default=12000, help="Muse Spark cap, reasoning included (default 12000)")
    p.add_argument("--no-labels", action="store_true", help="with --synthetic: no data labels, so values come off the axis")
    p.add_argument("--no-cache", action="store_true", help="don't read cached model results (still writes them)")
    p.add_argument("--sam-only", action="store_true", help="only SAM 3.1 boxes and the overlay; no Muse Spark calls")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        return run_selftest()  # never loads .env
    if args.no_labels and not args.synthetic:
        print("note: --no-labels only applies to --synthetic", file=sys.stderr)
    try:
        truth = None
        if args.synthetic:
            name = args.name or ("synthetic_nolabels" if args.no_labels else "synthetic")
            img_path, truth_path, truth = make_synthetic_chart(name, labels=not args.no_labels)
            print(f"Synthetic chart: {img_path} (ground truth: {truth_path})")
            img = Image.open(img_path).convert("RGB")
            source: dict[str, Any] = {"kind": "synthetic", "path": str(img_path), "truth": str(truth_path),
                                      "labels_printed": not args.no_labels, "working_size": list(img.size)}
        else:
            img, source = load_working_image(args)
            stem = (args.pdf or args.image).stem + (f"_p{args.page}" if args.pdf else "")
            name = args.name or stem
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        client = make_client()  # exits with a clear message if MODEL_API_KEY is missing
        if args.sam_only:
            run_sam_only(client, img, name=name, concepts=args.concepts,
                         cache=Cache(CACHE_DIR, read=not args.no_cache), ledger=True)
            return 0
        run_pipeline(client, img, name=name, source=source, concepts=args.concepts, grounding=args.grounding,
                     effort=args.effort, detail=args.detail, max_crops=args.max_crops,
                     max_output_tokens=args.max_output_tokens, cache=Cache(CACHE_DIR, read=not args.no_cache),
                     ledger=True, truth=truth)
        return 0
    except SpikeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        import openai
        if isinstance(exc, openai.APIStatusError):
            print(f"error: HTTP {exc.status_code} from {BASE_URL}: {exc.message}", file=sys.stderr)
            return 3
        if isinstance(exc, openai.APIConnectionError):
            print(f"error: could not reach {BASE_URL}: {exc}", file=sys.stderr)
            return 3
        raise


if __name__ == "__main__":
    sys.exit(main())
