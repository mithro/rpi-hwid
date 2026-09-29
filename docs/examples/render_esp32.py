#!/usr/bin/env python3
"""Regenerate the ESP32 micro-label examples.

    uv run docs/examples/render_esp32.py

Two sets, both cut out of the sheet `rpi-hwid labels` prints, by the label
grid (needs pdftoppm, from poppler-utils):

  esp32-sticker-1.png, -2.png  the real boards, from tests/esp32_devices.json:
                               what `rpi-hwid collect --esp32-read` wrote for
                               the three ESP32-C3 SuperMinis on rpi5-433mhz
                               and the ESP32-CAM and devkit on rpi4-esp.
                               Five labels make two stickers, four and one.
  esp32-parts.png              one SAMPLE label for every part in
                               rpi_hwid.espressif's table, with synthetic
                               data (locally administered 02:... MACs,
                               revision v9.9), under a banner saying so.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tests"))

import conftest  # noqa: E402
from rpi_hwid import esp32_micro, espressif, labels, micro  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402


def page_png(ls: list[micro.MicroLabel], dpi: int, tmp: str) -> Image.Image:
    pdf = Path(tmp) / "sheet.pdf"
    micro.render_micro(ls, pdf, outline=True)
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(pdf),
                    tmp + "/page"], check=True)
    (png,) = sorted(Path(tmp).glob("page*.png"))
    return Image.open(png)


def sticker_box(i: int, page: Image.Image, px: float) -> tuple[int, int, int, int]:
    x, y = labels.label_origin(i)
    left = round(x * px)
    top = round(page.height - y * px - labels.LABEL_H * px)
    return left, top, left + round(labels.LABEL_W * px), top + round(labels.LABEL_H * px)


def real() -> None:
    dpi = 400
    data = json.loads((HERE.parent.parent / "tests" / "esp32_devices.json").read_text())
    docs = {}
    for host, devices in data.items():
        raw = json.loads(json.dumps(conftest.RAW["rpi5-netv2"]))
        raw["verdict"]["esp32"] = devices
        docs[host] = ProbeDocument.from_dict(host, raw)
    ls = esp32_micro.micro_labels(docs)
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        page = page_png(ls, dpi, tmp)
        for i in range(len(micro.pack(ls))):
            name = f"esp32-sticker-{i + 1}.png"
            crop = page.crop(sticker_box(i, page, dpi / 72.0))
            crop.save(HERE / name)
            print(f"{name}, {crop.width} x {crop.height}")


def parts() -> None:
    dpi = 300
    ls = [esp32_micro.sample_label(p, i + 1) for i, p in enumerate(espressif.PARTS)]
    stickers = len(micro.pack(ls))
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        page = page_png(ls, dpi, tmp)
    px = dpi / 72.0
    first, last = sticker_box(0, page, px), sticker_box(stickers - 1, page, px)
    right = sticker_box(min(stickers, labels.COLS) - 1, page, px)[2]
    sheet = page.crop((first[0], first[1], right, last[3]))
    banner = 90
    out = Image.new("RGB", (sheet.width, sheet.height + banner), "white")
    out.paste(sheet, (0, banner))
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 22), f"SAMPLE LABELS: synthetic data, one per part in "
                        f"rpi_hwid.espressif ({len(ls)})", fill="black", font=font)
    out.save(HERE / "esp32-parts.png", optimize=True)
    print(f"esp32-parts.png, {out.width} x {out.height}, {len(ls)} labels")


if __name__ == "__main__":
    real()
    parts()
