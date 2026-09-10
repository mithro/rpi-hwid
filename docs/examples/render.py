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
from rpi_hwid import labels  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

DPI = 200
PX = DPI / 72.0

# (kind, title substring) -> file name
EXAMPLES = [
    ("netv2", "netv2-grove", "netv2"),
    ("arty", "arty-hawk", "arty"),
    ("acorn", "Acorn", "acorn"),
    ("tt", "TT06", "tinytapeout"),
    ("tt", "TTIHP25a", "tinytapeout-ihp"),
    ("rpi", "Pi 5 4 GB", "rpi5"),
    ("rpi", "Pi 5 1 GB", "rpi5-poe-hat"),
    ("rpi", "Pi 4", "rpi4-pmod-hat"),
    ("rpi", "Pi 3", "rpi3bplus"),
    ("rpi", "Zero", "rpi-zero-w-bonnet"),
    ("usb", "AX88179", "usb-asix"),
]


def main() -> None:
    docs = {name: ProbeDocument.from_dict(name, raw) for name, raw in conftest.RAW.items()}
    pdf = HERE / "labels.pdf"
    labels.render(docs, pdf, outline=True)
    kinds = {"fpga", "tt", "rpi", "usb"}
    titles = [(kind, title) for kind, title, _d, _r in labels.all_labels(docs, kinds)]

    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", str(pdf), tmp + "/page"], check=True)
        page = Image.open(next(Path(tmp).glob("page*.png")))
        page_h = page.height
        for kind, needle, name in EXAMPLES:
            index = next(i for i, (k, t) in enumerate(titles) if k == kind and needle in t)
            x, y = labels.label_origin(index)
            left, bottom = x * PX, page_h - y * PX
            top, right = bottom - labels.LABEL_H * PX, left + labels.LABEL_W * PX
            page.crop((round(left), round(top), round(right), round(bottom))).save(
                HERE / f"{name}.png"
            )
            print(name, "<-", titles[index][1])
    pdf.unlink()


if __name__ == "__main__":
    main()
