#!/usr/bin/env python3
"""Regenerate the repository's social preview image.

    uv run docs/social_preview.py

Writes docs/social-preview.png, 1280 x 640 px: the repository name, its
one-line description, and a handful of the labels the package generates,
drawn from the same fixture documents as docs/examples/render.py so the
picture tracks the generator. The labels are drawn as vectors straight
onto the page and the page is rasterised at twice the size and halved, so
the QR codes and MACs stay crisp.

GitHub has no API for the social preview: upload the file by hand in the
repository's Settings, under "Social preview". Needs pdftoppm
(poppler-utils). The headline is set in Bitstream Vera, which reportlab
ships, so nothing outside the dependency set is used.
"""

from __future__ import annotations

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
W, H = 1280, 640          # GitHub's recommended size; shown cropped to 2:1
SUPERSAMPLE = 2           # rasterise at 2x and halve

NAME = "rpi-hwid"
PITCH = ("Raspberry Pi hardware identity: what a Pi wears, what powers it, "
         "which FPGA board it hosts, and print-ready labels for all of it.")

BACKGROUND = HexColor("#f3f4f6")
INK = HexColor("#1f2328")
MUTED = HexColor("#57606a")
EDGE = HexColor("#9a9fa6")
SHADOW = HexColor("#d7dadf")

# (kind, title substring, x, y, degrees): which fixture label goes where, its
# top-left corner in pixels from the page's top-left, and its tilt.
SCALE = 1.5               # 63.5 x 38.1 mm label -> 270 x 162 px
STICKERS = [
    ("rpi", "Pi 5 1 GB", 60, 392, 1.5),
    ("netv2", "netv2-grove", 634, 46, -3.0),
    ("rpi", "Pi 5 4 GB", 926, 76, 2.5),
    ("rpi", "Zero", 606, 236, 2.0),
    ("arty", "arty-hawk", 944, 262, -2.5),
    ("usb", "AX88179", 790, 436, -1.5),
]


def wrap(text, font, size, max_w):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and pdfmetrics.stringWidth(trial, font, size) > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    return lines


def draw_sticker(c, index, titles, x, y, angle):
    _kind, _title, draw, data = titles[index]
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


def main() -> None:
    pdfmetrics.registerFont(TTFont("VeraBd", "VeraBd.ttf"))
    pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
    labels.register_fonts()
    docs = {name: ProbeDocument.from_dict(name, raw) for name, raw in conftest.RAW.items()}
    titles = list(labels.all_labels(docs, {"fpga", "rpi", "usb"}))

    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        pdf = Path(tmp) / "social-preview.pdf"
        c = canvas.Canvas(str(pdf), pagesize=(W, H))
        c.setTitle("rpi-hwid social preview")
        c.setFillColor(BACKGROUND)
        c.rect(0, 0, W, H, stroke=0, fill=1)

        # The headline, cap-height top 84 px down, 60 px in.
        c.setFillColor(INK)
        c.setFont("VeraBd", 108)
        c.drawString(60, H - 84 - 108 * 0.72, NAME)

        c.setFillColor(MUTED)
        c.setFont("Vera", 24)
        y = H - 222
        for line in wrap(PITCH, "Vera", 24, 540):
            c.drawString(62, y, line)
            y -= 35

        for kind, needle, x, ly, angle in STICKERS:
            index = next(i for i, (k, t, _d, _r) in enumerate(titles)
                         if k == kind and needle in t)
            draw_sticker(c, index, titles, x, ly, angle)
        c.showPage()
        c.save()

        subprocess.run(["pdftoppm", "-r", str(72 * SUPERSAMPLE), "-png", "-singlefile",
                        str(pdf), str(Path(tmp) / "page")], check=True)
        page = Image.open(Path(tmp) / "page.png").convert("RGB")
        assert page.size == (W * SUPERSAMPLE, H * SUPERSAMPLE), page.size
        page.resize((W, H), Image.LANCZOS).save(OUT, optimize=True)
    print(OUT, f"{W}x{H}")


if __name__ == "__main__":
    main()
