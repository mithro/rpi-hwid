#!/usr/bin/env python3
"""Regenerate the ESP32 433 MHz radio-node label example from real reads.

    uv run docs/examples/render_esp32_433.py

The records are tests/esp32_433_devices.json: what `rpi-hwid collect
--esp32-read ... --esp32-radio ...` wrote on 2026-09-26 for the three
ESP32-C3 SuperMinis on rpi5-433mhz. The sticker is what `rpi-hwid labels`
prints for them with both the esp32 and esp32-433 kinds: the SX1278 and
the blue CC1101 node get the radio label, and the reference board, which
has no radio fitted, the plain ESP32 one. It is cut out of the rendered
sheet by the label grid at 400 dpi, as render_micro.py does. Needs pdftoppm
(poppler-utils).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tests"))

import conftest  # noqa: E402
from rpi_hwid import labels, micro  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

DPI = 400
PX = DPI / 72.0
NAME = "esp32-433-sticker.png"


def main() -> None:
    real = json.loads((HERE.parent.parent / "tests" / "esp32_433_devices.json").read_text())
    docs = {}
    for host, devices in real.items():
        raw = json.loads(json.dumps(conftest.RAW["rpi5-netv2"]))
        raw["verdict"]["esp32"] = devices
        docs[host] = ProbeDocument.from_dict(host, raw)
    (row,) = micro.sticker_rows(docs, {"esp32", "esp32-433"})
    ls = [m for m in row[4] if m is not None]
    pdf = HERE / "esp32-433.pdf"
    micro.render_micro(ls, pdf, outline=True)
    box_w, box_h = round(labels.LABEL_W * PX), round(labels.LABEL_H * PX)
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", "1", "-l", "1", str(pdf),
                        tmp + "/page"], check=True)
        (png,) = sorted(Path(tmp).glob("page*.png"))
        page = Image.open(png)
        x, y = labels.label_origin(0)
        left = round(x * PX)
        top = round(page.height - y * PX - labels.LABEL_H * PX)
        page.crop((left, top, left + box_w, top + box_h)).save(HERE / NAME)
        print(f"{NAME}, {box_w} x {box_h}")
    pdf.unlink()


if __name__ == "__main__":
    main()
