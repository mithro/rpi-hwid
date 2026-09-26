#!/usr/bin/env python3
"""Before and after the I2C settle fix, as one side-by-side image.

    uv run docs/examples/i2c_race_preview.py    # -> docs/examples/i2c-race.png

The race cannot be shown live: rpiz-4 still carries the overlay the census
probe left behind, so /dev/i2c-0 is already there. So both probes run
against the fixture the test suite uses for it -- rpiz-4's tree
(``zero_bonnet_tree``) and ``SlowUdev``, whose /dev/i2c-0 turns up only
after `dtparam i2c_vc=on` has returned -- and each is followed by
`dtparam -l` once udev has caught up. "Before" is probe.py as of b90255f,
the commit this fix was made on, read out of git; "after" is the working
tree's. Lines that differ are highlighted.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "tests"))

import test_probe_collect as fixtures  # noqa: E402
from rpi_hwid import probe as probe_after  # noqa: E402

BEFORE_REV = "b90255f"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def probe_before() -> types.ModuleType:
    src = subprocess.run(["git", "-C", str(REPO), "show",
                          BEFORE_REV + ":src/rpi_hwid/probe.py"],
                         capture_output=True, text=True, check=True).stdout
    mod = types.ModuleType("probe_before")
    exec(compile(src, "probe.py@" + BEFORE_REV, "exec"), mod.__dict__)
    return mod


def run(mod: types.ModuleType) -> list[str]:
    """The probe's own text report and header_buses_read, then `dtparam -l`."""
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        root = Path(tmp)
        fixtures.zero_bonnet_tree(root)
        udev = fixtures.SlowUdev(root)
        mod.ROOT = str(root)
        mod.sh = udev.sh
        mod.i2c_scan = lambda bus, **kw: []
        mod.eeprom_read = lambda bus, addr, length=256: None
        seen = {}
        collect = mod.collect
        mod.collect = lambda: seen.setdefault("d", collect())
        real_sleep, time.sleep = time.sleep, udev.settle
        argv, sys.argv = sys.argv, ["probe.py"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                mod.main()
        finally:
            time.sleep, sys.argv = real_sleep, argv
        udev.settle()                    # the probe has gone; udev catches up
        lines = ["$ rpi-hwid probe"]
        lines += out.getvalue().rstrip("\n").split("\n")
        lines.append("  header_buses_read: " + json.dumps(seen["d"]["header_buses_read"]))
        lines += ["", "$ sudo dtparam -l    # after the probe"]
        lines += udev.sh(["sudo", "dtparam", "-l"]).split("\n")
        return lines


def wrap(lines: list[str], width: int) -> list[str]:
    out = []
    for line in lines:
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            cut = cut if cut > width // 2 else width
            out.append(line[:cut])
            line = "      " + line[cut:].lstrip()
        out.append(line)
    return out


def main() -> None:
    before, after = run(probe_before()), run(probe_after)
    cols = 64
    size, pad, gap = 15, 24, 32
    font, bold = ImageFont.truetype(FONT, size), ImageFont.truetype(FONT_BOLD, size + 3)
    cw = font.getbbox("M")[2]
    lh = int(size * 1.45)
    changed_b = set(before) - set(after)
    changed_a = set(after) - set(before)
    left = [(w, ln in changed_b) for ln in before for w in wrap([ln], cols)]
    right = [(w, ln in changed_a) for ln in after for w in wrap([ln], cols)]
    col_w = cols * cw
    rows = max(len(left), len(right))
    width = pad * 2 + col_w * 2 + gap
    height = pad * 2 + lh * 2 + rows * lh
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    heads = ((f"Before: probe.py at {BEFORE_REV} (main)", "#a01010"),
             ("After: this branch", "#106010"))
    for i, (col, (head, colour)) in enumerate(zip((left, right), heads, strict=True)):
        x = pad + i * (col_w + gap)
        d.text((x, pad), head, font=bold, fill=colour)
        y = pad + lh * 2
        tint = "#fbdada" if i == 0 else "#d8f3d8"
        for text, hot in col:
            if hot:
                d.rectangle((x - 4, y - 2, x + col_w, y + lh - 3), fill=tint)
            d.text((x, y), text, font=font, fill="black")
            y += lh
    x = pad + col_w + gap // 2
    d.line((x, pad, x, height - pad), fill="#bbbbbb", width=1)
    out = HERE / "i2c-race.png"
    img.save(out)
    print(out)


if __name__ == "__main__":
    main()
