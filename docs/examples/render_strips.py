#!/usr/bin/env python3
"""Regenerate the DYMO roll images the README shows.

    uv run docs/examples/render_strips.py

The documents are the ones render.py uses (tests/conftest.py, and the radios'
hosts in tests/sdr_fixtures), rendered with stock="dymo-99010": one 28 x 89 mm
sticker per page. Each page is turned a quarter clockwise so the strip reads
left to right, and the crop is the whole sticker at render.py's 200 dpi, with
the sticker's edge and the strip's box drawn (--outline) so the image shows
where the print lands. The micro pair uses render_micro.py's synthetic labels
(no fixture document carries one), at 400 dpi like its sticker because the
type is half the size. Needs pdftoppm (poppler-utils).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from reportlab.lib.colors import HexColor, black
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(HERE))

import render_micro  # noqa: E402

import conftest  # noqa: E402
import sdr_fixtures  # noqa: E402
from rpi_hwid import labels, micro, roll, strip  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

DPI = 200
MICRO_DPI = 400

# (kind, title substring) -> file name
EXAMPLES = [
    ("rpi", "Pi 5 4 GB", "strip-rpi5"),
    ("arty", "arty-hoopoe", "strip-arty"),
    ("usb", "AX88179", "strip-usb-asix"),
    ("tt", "TT06", "strip-tinytapeout"),
    ("sdr", "ADALM-Pluto", "strip-sdr-pluto"),
]


def pages(pdf: Path, dpi: int, scratch: str) -> list[Image.Image]:
    """The PDF's pages, each turned a quarter clockwise so a strip reads left
    to right."""
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), scratch + "/page"],
                   check=True)
    return [Image.open(p).rotate(-90, expand=True)
            for p in sorted(Path(scratch).glob("page*.png"))]


def main() -> None:
    docs = {n: ProbeDocument.from_dict(n, raw) for n, raw in conftest.RAW.items()}
    docs.update(sdr_fixtures.sdr_docs())
    the_roll = labels.ROLLS["dymo-99010"]
    only = set(labels.KINDS)
    rows = roll.roll_rows(docs, only)
    scratch_root = REPO / "tmp"
    scratch_root.mkdir(exist_ok=True)
    sizes = set()
    with tempfile.TemporaryDirectory(dir=scratch_root) as tmp:
        pdf = Path(tmp) / "roll.pdf"
        labels.render(docs, pdf, only, outline=True, stock="dymo-99010")
        sheet = pages(pdf, DPI, tmp)
        for kind, needle, name in EXAMPLES:
            hits = [i for i, r in enumerate(rows) if r[1] == kind and needle in r[2]]
            if len(hits) != 1:
                raise SystemExit(f"{len(hits)} fixture labels match {kind} {needle!r}, want one")
            sheet[hits[0]].save(HERE / f"{name}.png")
            sizes.add(sheet[hits[0]].size)
            print(name, "<-", rows[hits[0]][2])

        # The micro pair: two of render_micro's labels, end to end on a
        # sticker, drawn the way roll.render_roll draws a sticker.
        mpdf = Path(tmp) / "pair.pdf"
        c = canvas.Canvas(str(mpdf), pagesize=the_roll.page)
        c.setStrokeColor(HexColor("#bbbbbb"))
        c.setLineWidth(0.3)
        c.rect(0, 0, *the_roll.page, stroke=1, fill=0)
        c.setStrokeColor(black)
        c.saveState()
        roll._place(c, the_roll)
        s = strip.Strip(c, 0, 0)
        s.outline()
        micro.draw_pair(s, tuple(render_micro.EXAMPLES[:2]))
        c.restoreState()
        c.showPage()
        c.save()
        Path(tmp, "pair").mkdir()
        (pair,) = pages(mpdf, MICRO_DPI, tmp + "/pair")
        pair.save(HERE / "strip-micro-pair.png")
        print("strip-micro-pair <- two micro labels")
    print(f"{len(EXAMPLES) + 1} strips, sizes {sorted(sizes)}")


if __name__ == "__main__":
    main()
