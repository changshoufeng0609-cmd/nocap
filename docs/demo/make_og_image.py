#!/usr/bin/env python3
"""Build the site's link-preview card (web/app/opengraph-image.png, twitter-image.png) from a real v2 video frame.

    .venv/bin/python docs/demo/make_og_image.py

The product pixels are cropped from the committed walkthrough at 30.5 s (the NVIDIA draft with its 18% → 105.85%
correction); only the title and the bottom band are drawn here. No model call and no network access.
"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
VIDEO = ROOT / "docs/demo/assets/receipts-demo.mp4"
OUT = [ROOT / "web/app/opengraph-image.png", ROOT / "web/app/twitter-image.png"]
FRAME_S = 30.5
CROP = (120, 180, 1740, 530)  # the draft's first paragraph in the 1920 × 1080 frame, above the caption band
W, H = 1200, 630
PAPER, INK, MUTED, BAND, BAND_INK = "#fbfaf7", "#141312", "#5f5b55", "#10241f", "#f3f1ec"
SERIF = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
SANS = "/System/Library/Fonts/HelveticaNeue.ttc"


def frame() -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "frame.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(FRAME_S), "-i", str(VIDEO), "-frames:v", "1", str(png)],
                       check=True)
        return Image.open(png).convert("RGB").copy()


def main() -> int:
    card = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(card)
    draw.text((56, 44), "NoCap", font=ImageFont.truetype(SERIF, 64), fill=INK)
    draw.text((58, 124), "Every financial number in a draft, checked against SEC filings.",
              font=ImageFont.truetype(SANS, 30), fill=MUTED)
    shot = frame().crop(CROP)
    shot = shot.resize((W - 112, round(shot.height * (W - 112) / shot.width)), Image.LANCZOS)
    top = 196
    card.paste(shot, (56, top))
    draw.rectangle((56, top, 56 + shot.width - 1, top + shot.height - 1), outline="#d9d4cb", width=2)
    draw.text((58, top + shot.height + 22), "The draft says 18% from a year ago. The filings say 105.85%.",
              font=ImageFont.truetype(SANS, 30), fill=INK)
    draw.rectangle((0, H - 92, W, H), fill=BAND)
    draw.text((56, H - 92 + 28), "receipts-nocap.vercel.app  ·  Muse Spark, Muse Wonder and SAM 3.1 propose; code decides",
              font=ImageFont.truetype(SANS, 26), fill=BAND_INK)
    for path in OUT:
        card.save(path, optimize=True)
        print(f"written: {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
