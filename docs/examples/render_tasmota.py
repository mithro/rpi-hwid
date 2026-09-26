#!/usr/bin/env python3
"""Regenerate the Tasmota micro-label examples.

    uv run docs/examples/render_tasmota.py                 # tasmota-4up.png
    uv run docs/examples/render_tasmota.py --data DIR      # and tasmota-sheet.png

tasmota-4up.png is one sticker of the four devices in
tests/tasmota_devices.json -- an Athom Plug V3, a Sonoff S31, an Athom IR
remote and a bare ESP32-C3, read through the collector's allowlist on
2026-09-26 -- cut out of a rendered sheet by the label grid at 400 dpi, as
micro-4up.png is. With --data, tasmota-sheet.png is the whole first sheet
of every document in DIR (what ``rpi-hwid tasmota --out DIR`` wrote) at 150
dpi: the fleet as it prints. Needs pdftoppm (poppler-utils).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from rpi_hwid import collect, labels, micro, tasmota, tasmota_micro

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent.parent / "tests" / "tasmota_devices.json"


def fixture_docs() -> dict[str, object]:
    docs = {}
    for host, d in json.loads(FIXTURES.read_text()).items():
        mac = tasmota.normalise_mac(d["status"]["StatusNET"]["Mac"])
        dev = tasmota.SheetDevice(host=host, name=host, ip=d["ip"], mac=mac)
        raw = {"status": d["status"], "module": d["module"], "template": d["template"],
               "info": tasmota.parse_info_page(d["in"])}
        docs[host] = tasmota.document(dev, raw, {})
    return docs


def render_page(micros: list[micro.MicroLabel], dpi: int, tmp: str) -> Image.Image:
    pdf = Path(tmp) / "t.pdf"
    micro.render_micro(micros, pdf, outline=True)
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(pdf),
                    tmp + "/page"], check=True)
    (png,) = sorted(Path(tmp).glob("page*.png"))
    return Image.open(png)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, help="documents rpi-hwid tasmota wrote")
    args = ap.parse_args()
    dpi = 400
    px = dpi / 72.0
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        page = render_page(tasmota_micro.micro_labels(fixture_docs()), dpi, tmp)
        x, y = labels.label_origin(0)
        left, top = round(x * px), round(page.height - y * px - labels.LABEL_H * px)
        box = (left, top, left + round(labels.LABEL_W * px), top + round(labels.LABEL_H * px))
        page.crop(box).save(HERE / "tasmota-4up.png", optimize=True)
        print("tasmota-4up.png")
    if args.data:
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            ms = tasmota_micro.micro_labels(collect.load_collected(args.data))
            page = render_page(ms, 150, tmp)
            page.convert("L").save(HERE / "tasmota-sheet.png", optimize=True)
            print(f"tasmota-sheet.png, {len(ms)} labels")


if __name__ == "__main__":
    main()
