#!/usr/bin/env python3
"""Regenerate the repository's social preview image.

    uv run docs/social_preview.py

Writes docs/social-preview.png, 1280 x 640 px: the repository name, a
two-line pitch, and five of the labels the package generates, drawn from
the same fixture documents as docs/examples/render.py so the picture
tracks the generator. The labels are drawn as vectors straight onto the
page and the page is rasterised at twice the size and halved, so the QR
codes and MACs stay crisp.

GitHub has no API for the social preview: upload the file by hand in the
repository's Settings, under "Social preview". Needs pdftoppm
(poppler-utils) and the DejaVu monospace the labels use. The headline is
set in Bitstream Vera, which reportlab ships, so nothing outside the
dependency set is used; the PNG is byte-stable for a given set of fonts
and poppler version.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))

import conftest  # noqa: E402
from rpi_hwid import labels  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

OUT = HERE / "social-preview.png"
# GitHub's recommended size. The page is already 2:1; link cards crop it to
# about 1.91:1 and show it at half size, so everything that matters stays
# inside SAFE and the type is large.
W, H = 1280, 640
SUPERSAMPLE = 2           # rasterise at 2x and halve
SAFE = (40, 70, W - 40, H - 70)   # left, top, right, bottom: nothing outside
MARGIN = 60               # the text's left edge and the headline's top
GAP = 28                  # least clearance between stickers, and to the text

NAME = "rpi-hwid"
NAME_SIZE = 108
PITCH = ("Raspberry Pi hardware identity", "and print-ready labels")
PITCH_SIZE = 32
PITCH_TOP, PITCH_LEADING = 196, 44

BACKGROUND = HexColor("#dfe3e8")
INK = HexColor("#1f2328")
PITCH_INK = HexColor("#333333")
EDGE = HexColor("#8b9096")
SHADOW = HexColor("#b9bec6")

# (kind, title substring, x, y, degrees): which fixture label goes where, its
# top-left corner in pixels from the page's top-left, and its tilt. Two on
# the top row beside the text, three along the bottom.
SCALE = 1.6               # 63.5 x 38.1 mm label -> 288 x 173 px
STICKERS = [
    ("netv2", "netv2-grove", 612, 88, -3.0),
    ("rpi", "Pi 5 4 GB", 938, 126, 2.5),
    ("rpi", "Zero", 150, 336, 1.5),
    ("usb", "AX88179", 510, 356, -2.0),
    ("arty", "arty-hawk", 870, 372, -2.5),
]


def corners(x, y, angle):
    """The four page corners of a sticker placed by `draw_sticker`."""
    w, h = labels.LABEL_W * SCALE, labels.LABEL_H * SCALE
    a = math.radians(angle)
    cos, sin = math.cos(a), math.sin(a)
    # (dx, dy) in the label's own frame, y downwards; the page's y grows
    # downwards too, so a positive angle tilts the right edge upwards.
    return [(x + dx * cos + dy * sin, y - dx * sin + dy * cos)
            for dx, dy in ((0, 0), (w, 0), (w, h), (0, h))]


def find(titles, kind, needle):
    hits = [(draw, data) for k, title, draw, data in titles if k == kind and needle in title]
    if len(hits) != 1:
        raise SystemExit(f"{len(hits)} fixture labels match {kind} {needle!r}, want one")
    return hits[0]


def draw_sticker(c, draw, data, x, y, angle):
    c.saveState()
    c.translate(x, H - y)
    c.rotate(angle)
    c.scale(SCALE, SCALE)
    lab = labels.Label(c, 0, -labels.LABEL_H)
    c.setFillColor(SHADOW)
    c.rect(2 / SCALE, -labels.LABEL_H - 3 / SCALE, labels.LABEL_W, labels.LABEL_H,
           stroke=0, fill=1)
    c.setFillColor(HexColor("#ffffff"))
    c.setStrokeColor(EDGE)
    c.setLineWidth(1 / SCALE)
    c.rect(0, -labels.LABEL_H, labels.LABEL_W, labels.LABEL_H, stroke=1, fill=1)
    draw(lab, data)
    c.restoreState()


def check_layout():
    """Every sticker inside SAFE, and the text clear of the stickers
    beside it."""
    text_bottom = PITCH_TOP + PITCH_LEADING * (len(PITCH) - 1) + PITCH_SIZE
    text_limit = W
    for _kind, needle, x, y, angle in STICKERS:
        xs, ys = zip(*corners(x, y, angle), strict=True)
        if min(xs) < SAFE[0] or min(ys) < SAFE[1] or max(xs) > SAFE[2] or max(ys) > SAFE[3]:
            raise SystemExit(f"sticker {needle!r} leaves the safe area {SAFE}")
        if min(ys) < text_bottom:
            text_limit = min(text_limit, min(xs) - GAP)
    for line in PITCH:
        if MARGIN + pdfmetrics.stringWidth(line, "Vera", PITCH_SIZE) > text_limit:
            raise SystemExit(f"pitch line {line!r} runs into the stickers")
    if MARGIN + pdfmetrics.stringWidth(NAME, "VeraBd", NAME_SIZE) > text_limit:
        raise SystemExit("the headline runs into the stickers")


def main() -> None:
    pdfmetrics.registerFont(TTFont("VeraBd", "VeraBd.ttf"))
    pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    labels.register_fonts()
    if labels.MONO == "Courier-Bold":
        raise SystemExit("the DejaVu Sans Mono font the labels use is not installed "
                         "(fonts-dejavu-core); refusing to render with Courier")
    docs = {name: ProbeDocument.from_dict(name, raw) for name, raw in conftest.RAW.items()}
    titles = list(labels.all_labels(docs, {"fpga", "rpi", "usb"}))
    check_layout()

    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        pdf = Path(tmp) / "social-preview.pdf"
        c = canvas.Canvas(str(pdf), pagesize=(W, H))
        c.setTitle("rpi-hwid social preview")
        c.setFillColor(BACKGROUND)
        c.rect(0, 0, W, H, stroke=0, fill=1)

        # Cap-height tops at MARGIN and PITCH_TOP; 0.72 em is the cap height.
        c.setFillColor(INK)
        c.setFont("VeraBd", NAME_SIZE)
        c.drawString(MARGIN, H - MARGIN - NAME_SIZE * 0.72, NAME)
        c.setFillColor(PITCH_INK)
        c.setFont("Vera", PITCH_SIZE)
        for i, line in enumerate(PITCH):
            c.drawString(MARGIN, H - PITCH_TOP - i * PITCH_LEADING - PITCH_SIZE * 0.72, line)

        for kind, needle, x, y, angle in STICKERS:
            draw, data = find(titles, kind, needle)
            draw_sticker(c, draw, data, x, y, angle)
        c.showPage()
        c.save()

        subprocess.run(["pdftoppm", "-r", str(72 * SUPERSAMPLE), "-png", "-singlefile",
                        str(pdf), str(Path(tmp) / "page")], check=True)
        page = Image.open(Path(tmp) / "page.png").convert("RGB")
        if page.size != (W * SUPERSAMPLE, H * SUPERSAMPLE):
            raise SystemExit(f"pdftoppm rendered {page.size}, not {W * SUPERSAMPLE} x "
                             f"{H * SUPERSAMPLE}")
        page.resize((W, H), Image.LANCZOS).save(OUT, optimize=True)
    print(OUT, f"{W}x{H}")


if __name__ == "__main__":
    main()
