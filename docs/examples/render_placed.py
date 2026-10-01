#!/usr/bin/env python3
"""Regenerate placed-sheet.png, the first pass of a sheet printed a few
labels at a time (rpi-hwid labels --place).

    uv run docs/examples/render_placed.py

Three of tests/conftest.py's boards' labels, in slots 2, 9 and 16 of a
fresh sheet, with the sheet's id, note and registration ticks in its
margins: the whole A4 page at 100 dpi, as it prints. Needs pdftoppm
(poppler-utils).
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
from rpi_hwid import labels, placement  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

PICKS = [("rpi", "d88100008543dc30", "2"), ("netv2", "netv2-grove", "9"),
         ("usb", "00:0e:c6:82:b5:e1", "16")]


def main() -> None:
    docs = {n: ProbeDocument.from_dict(n, r) for n, r in conftest.RAW.items()}
    entries = placement.entries(docs, set(labels.KINDS))
    plan = {"labels": [{"id": next(e.id for e in entries if e.kind == kind and key in e.title),
                        "slot": slot} for kind, key, slot in PICKS],
            "sheet": {"id": "K7QX",
                      "note": "L7160 · started 2026-10-01 10:12 on ten64 by tim"},
            "outline": True}
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        pdf = Path(tmp) / "p.pdf"
        placement.render(docs, plan, pdf)
        subprocess.run(["pdftoppm", "-r", "100", "-png", "-singlefile", str(pdf),
                        tmp + "/page"], check=True)
        page = Image.open(Path(tmp) / "page.png")
        page.convert("RGB").quantize(colors=32).save(HERE / "placed-sheet.png", optimize=True)
    print("placed-sheet.png")


if __name__ == "__main__":
    main()
