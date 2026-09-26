#!/usr/bin/env python3
"""Regenerate the ESP32 micro-label examples from real reads.

    uv run docs/examples/render_esp32.py

The records are tests/esp32_devices.json: what `rpi-hwid collect
--esp32-read` wrote on 2026-09-26 for the three ESP32-C3 SuperMinis on
rpi5-433mhz and the ESP32-CAM and devkit on rpi4-esp. Five labels make two
stickers, four and one; each is cut out of the rendered sheet by the label
grid at 400 dpi, as render_micro.py does. Needs pdftoppm (poppler-utils).
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
from rpi_hwid import esp32_micro, labels, micro  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

DPI = 400
PX = DPI / 72.0


def main() -> None:
    real = json.loads((HERE.parent.parent / "tests" / "esp32_devices.json").read_text())
    docs = {}
    for host, devices in real.items():
        raw = json.loads(json.dumps(conftest.RAW["rpi5-netv2"]))
        raw["verdict"]["esp32"] = devices
        docs[host] = ProbeDocument.from_dict(host, raw)
    ls = esp32_micro.micro_labels(docs)
    pdf = HERE / "esp32.pdf"
    micro.render_micro(ls, pdf, outline=True)
    box_w, box_h = round(labels.LABEL_W * PX), round(labels.LABEL_H * PX)
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", "1", "-l", "1", str(pdf),
                        tmp + "/page"], check=True)
        (png,) = sorted(Path(tmp).glob("page*.png"))
        page = Image.open(png)
        for i in range(len(micro.pack(ls))):
            x, y = labels.label_origin(i)
            left = round(x * PX)
            top = round(page.height - y * PX - labels.LABEL_H * PX)
            name = f"esp32-sticker-{i + 1}.png"
            page.crop((left, top, left + box_w, top + box_h)).save(HERE / name)
            print(f"{name}, {box_w} x {box_h}")
    pdf.unlink()


if __name__ == "__main__":
    main()
