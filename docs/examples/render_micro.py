#!/usr/bin/env python3
"""Regenerate the micro-label example: one sticker, four labels.

    uv run docs/examples/render_micro.py

The four records are synthetic -- the layout is device-neutral, and the
labels built on it (see LABELS.md) carry their own examples from real
hardware -- so every slot is shown filled once: a maker's mark, the glyphs,
a subtitle, rows including an identifier row, an extra section, and a QR
that encodes something other than the identifier. The locally administered
MACs (02:...) and the example.org URL are not any device's. The crop is cut
out of a rendered sheet by the label grid, at twice the README crops' 200
dpi because the type is half the size. Needs pdftoppm (poppler-utils).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from reportlab.lib.units import mm

from rpi_hwid import labels, micro
from rpi_hwid.micro import Icon, MicroLabel, MicroRow

HERE = Path(__file__).resolve().parent
DPI = 400
PX = DPI / 72.0


def tag(cell: micro.Cell, box: tuple[float, float, float, float]) -> None:
    """An extra section: a hollow box with a word in it, the kind of thing a
    caller adds under the rows."""
    x, y, w, h = box
    bw, bh = min(w, 12 * mm), min(h, 2.4 * mm)
    px, py = cell.pt(x, y + bh)
    cell.c.setStrokeColor(labels.GREY)
    cell.c.setLineWidth(0.4)
    cell.c.roundRect(px, py, bw, bh, 0.6 * mm, stroke=1, fill=0)
    cell.text(x + bw / 2, y + (bh - micro.CAPTION * 0.72) / 2, "extra section",
              labels.SANS, micro.CAPTION, align="centre", color=labels.GREY)


EXAMPLES = [
    MicroLabel(host="bench-1", title="Wi-Fi module", subtitle="rev 1.0  ·  4 MiB flash",
               ident_caption="Wi-Fi MAC", ident="02:00:5e:10:00:01",
               icons=(Icon("wifi"), Icon("chip", "C3")),
               rows=(MicroRow("BT", "02:00:5e:10:00:03", mono=True),
                     MicroRow("uid", "5e0a1c2b3d4e5f60", mono=True))),
    MicroLabel(host="bench-1", title="USB bridge", subtitle="CP2102N  ·  10c4:ea60",
               ident_caption="USB serial", ident="0a1b2c3d4e5f6071",
               mark="usb.svg",   # the public-domain trident, in the mark slot
               rows=(MicroRow("port", "1-1.3"),)),
    MicroLabel(host="bench-2", title="Radio node", subtitle="433 MHz",
               ident_caption="MAC", ident="02:00:5e:10:00:21",
               icons=(Icon("antenna", "433"), Icon("wifi")),
               rows=(MicroRow("radio", "CC1101"),), extra=tag),
    MicroLabel(host="bench-2", title="Wired thing", subtitle="the QR opens a page",
               ident_caption="eth MAC", ident="02:00:5e:10:00:31",
               icons=(Icon("ethernet"),), qr="https://example.org/device/31",
               rows=(MicroRow("page", "example.org/device/31"),)),
]


def main() -> None:
    pdf = HERE / "micro.pdf"
    micro.render_micro(EXAMPLES, pdf, outline=True)
    box_w, box_h = round(labels.LABEL_W * PX), round(labels.LABEL_H * PX)
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", "1", "-l", "1", str(pdf),
                        tmp + "/page"], check=True)
        (png,) = sorted(Path(tmp).glob("page*.png"))
        page = Image.open(png)
        x, y = labels.label_origin(0)
        left = round(x * PX)
        top = round(page.height - y * PX - labels.LABEL_H * PX)
        page.crop((left, top, left + box_w, top + box_h)).save(HERE / "micro-4up.png")
    pdf.unlink()
    print(f"micro-4up.png, {box_w} x {box_h}")


if __name__ == "__main__":
    main()
