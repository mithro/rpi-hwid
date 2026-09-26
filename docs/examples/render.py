#!/usr/bin/env python3
"""Regenerate the label images the README shows.

    uv run docs/examples/render.py

The documents are the ones in tests/conftest.py (probe output captured
from real boards), so the images track the generator; the crops are cut
out of the rendered sheet by the label grid, so they are exactly what the
printer gets. Needs pdftoppm (poppler-utils). Only the package's own
artwork is used, so the maker marks appear as type.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tests"))

import conftest  # noqa: E402
import sdr_fixtures  # noqa: E402
from rpi_hwid import labels  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

DPI = 200
PX = DPI / 72.0

# (kind, title substring) -> file name
EXAMPLES = [
    ("netv2", "netv2-grove", "netv2"),
    ("arty", "arty-hoopoe", "arty"),
    ("acorn", "0x0054b48664b04854", "acorn"),
    ("cynthion", "0x1b808604604e0e", "cynthion"),
    ("tt", "TT06", "tinytapeout"),
    ("tt", "TTGF0p2", "tinytapeout-gf"),
    ("tt", "FPGA", "tinytapeout-fpga"),
    ("rpi", "Pi 5 4 GB", "rpi5"),
    ("rpi", "Pi 5 1 GB", "rpi5-poe-hat"),
    ("rpi", "10000000f1b7bb5a", "rpi4"),
    ("rpi", "Pi 3", "rpi3bplus"),
    ("rpi", "Zero", "rpi-zero-w-bonnet"),
    ("opi", "Orange Pi PC", "orange-pi-pc"),
    ("usb", "AX88179", "usb-asix"),
    ("usb", "6c:1f:f7:51:2e:a3", "usb-wifi"),
    ("usb", "USB3GIGV1", "usb-linksys"),
    ("sdr", "ADALM-Pluto", "sdr-pluto"),
    ("sdr", "KrakenSDR", "sdr-kraken"),
    ("sdr", "XSDR", "sdr-xsdr"),
    ("sdr", "RTL-SDR V3", "sdr-rtlsdr-v3"),
]


def main() -> None:
    docs = {name: ProbeDocument.from_dict(name, raw) for name, raw in conftest.RAW.items()}
    docs.update(sdr_fixtures.sdr_docs())        # the radios' hosts, kept out of RAW
    pdf = HERE / "labels.pdf"
    labels.render(docs, pdf, outline=True)
    kinds = set(labels.KINDS)
    titles = [(kind, title) for _host, kind, title, _d, _r in labels.all_labels(docs, kinds)]

    # The crop is a box of one fixed pixel size moved around the page, not four
    # edges rounded separately: rounding each edge would let a label whose left
    # edge lands on .5 come out a pixel wider than its neighbours, and the
    # README lays these out in a grid that only looks right if every crop is
    # exactly the same size.
    box_w, box_h = round(labels.LABEL_W * PX), round(labels.LABEL_H * PX)

    per_sheet = labels.COLS * labels.ROWS

    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", str(pdf), tmp + "/page"], check=True)
        # One image per sheet, in order. The fixtures outgrew a single sheet,
        # and label_origin() is sheet-relative -- it never wraps at 21 -- so
        # cropping page one for every label put the later ones off the page
        # and saved a blank.
        sheets = [Image.open(p) for p in sorted(Path(tmp).glob("page*.png"))]
        for kind, needle, name in EXAMPLES:
            hits = [i for i, (k, t) in enumerate(titles) if k == kind and needle in t]
            if len(hits) != 1:
                raise SystemExit(f"{len(hits)} fixture labels match {kind} {needle!r}, want one")
            sheet, pos = divmod(hits[0], per_sheet)
            index = hits[0]
            page = sheets[sheet]
            page_h = page.height
            x, y = labels.label_origin(pos)
            left = round(x * PX)
            top = round(page_h - y * PX - labels.LABEL_H * PX)
            page.crop((left, top, left + box_w, top + box_h)).save(HERE / f"{name}.png")
            print(name, "<-", titles[index][1])
    pdf.unlink()

    sizes = {Image.open(HERE / f"{name}.png").size for _kind, _needle, name in EXAMPLES}
    if sizes != {(box_w, box_h)}:
        raise SystemExit(f"crops came out at {sorted(sizes)}, want one size {(box_w, box_h)}")
    print(f"{len(EXAMPLES)} crops, all {box_w} x {box_h}")


if __name__ == "__main__":
    main()
