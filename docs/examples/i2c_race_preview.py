#!/usr/bin/env python3
"""Before and after the I2C settle fix, as one side-by-side image.

    uv run docs/examples/i2c_race_preview.py    # -> docs/examples/i2c-race.png

The race cannot be shown live: rpiz-4 still carries the overlay the census
probe left behind, so /dev/i2c-0 is already there. So both probes run
against rpiz-4's tree (the test suite's ``zero_bonnet_tree``) and
``SlowUdev`` (below), whose /dev/i2c-0 turns up only after `dtparam
i2c_vc=on` has returned -- and each is followed by `dtparam -l` once udev
has caught up. "Before" is probe.py as of b90255f, the commit this fix was
made on, and "after" is probe.py as of 9e36e5b, the fix; both are read out
of git, since today's probe applies no dtparam at all (mithro/rpi-hwid
issue #91) and so has no race to show. Lines that differ are highlighted.
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
from typing import ClassVar

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "tests"))

import test_probe_collect as fixtures  # noqa: E402

BEFORE_REV = "b90255f"
AFTER_REV = "9e36e5b"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def probe_at(rev: str) -> types.ModuleType:
    src = subprocess.run(["git", "-C", str(REPO), "show", rev + ":src/rpi_hwid/probe.py"],
                         capture_output=True, text=True, check=True).stdout
    mod = types.ModuleType("probe_" + rev)
    exec(compile(src, "probe.py@" + rev, "exec"), mod.__dict__)
    return mod


class SlowUdev:
    """dtparam and udev as a Pi Zero behaves: `dtparam i2c_vc=on` returns
    before udev has made /dev/i2c-0, which only turns up once time passes
    (a sleep, or the probe having finished); `i2c_arm=on` made /dev/i2c-1
    in time on rpiz-4, so it appears at once. `dtparam -r` takes the last
    overlay out, as the real one does, and `-l` lists what is loaded. (The
    test suite's model of it until the probe stopped applying dtparams.)"""

    LATE: ClassVar[dict[str, int]] = {"i2c_vc=on": 0}
    PROMPT: ClassVar[dict[str, int]] = {"i2c_arm=on": 1}

    def __init__(self, root):
        self.root, self.overlays, self.pending, self.calls = root, [], [], []

    def node(self, bus):
        return self.root / f"dev/i2c-{bus}"

    def sh(self, args, timeout=15):
        self.calls.append(list(args))
        if args[:2] != ["sudo", "dtparam"]:
            return "throttled=0x0" if args == ["vcgencmd", "get_throttled"] else ""
        param = args[2]
        if param == "-l":
            if not self.overlays:
                return "No overlays loaded"
            return "Overlays (in load order):\n" + "\n".join(
                f"{i}:  dtparam  {o}" for i, o in enumerate(self.overlays))
        if param == "-r":
            gone = self.overlays.pop(int(args[3]) if len(args) > 3 else -1)
            bus = {**self.LATE, **self.PROMPT}[gone]
            self.pending = [b for b in self.pending if b != bus]
            if self.node(bus).exists():
                self.node(bus).unlink()
            return ""
        self.overlays.append(param)
        if param in self.LATE:
            self.pending.append(self.LATE[param])
        else:
            fixtures._w(self.root, f"/dev/i2c-{self.PROMPT[param]}", "")
        return ""

    def settle(self, seconds=None):
        """Time passes, and udev makes whatever it was going to."""
        for bus in self.pending:
            fixtures._w(self.root, f"/dev/i2c-{bus}", "")
        self.pending = []


def run(mod: types.ModuleType) -> list[str]:
    """The probe's own text report and header_buses_read, then `dtparam -l`."""
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        root = Path(tmp)
        fixtures.zero_bonnet_tree(root)
        udev = SlowUdev(root)
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
    before, after = run(probe_at(BEFORE_REV)), run(probe_at(AFTER_REV))
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
