#!/usr/bin/env python3
"""Regenerate camera-icons.png and camera-icons-zoom.png: every camera mark
a board label can carry, on labels for boards that do not exist.

    uv run docs/examples/render_cameras.py

Each label is a fixture document from tests/conftest.py's builder with a
different `cameras` value in its summary. The boards are fakes and say so:
the HAT row reads "FAKE", the serials spell fa4e and the MACs are from the
range kept for documentation (00:00:5e:00:53:xx, RFC 7042). camera-icons.png
is the labels at 200 dpi under a caption each; camera-icons-zoom.png is
only their title bands at 600 dpi, which is where the marks are and is the
size to judge them at. The last rows of both are an alternative design that
is drawn here and nowhere else: this script swaps it in for the package's
`mark_camera`. Needs pdftoppm (poppler-utils).
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import black, white
from reportlab.lib.units import mm

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tests"))

import conftest  # noqa: E402
from rpi_hwid import labels  # noqa: E402
from rpi_hwid.labels import GREY, SANS_BOLD  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

PI5 = ("Raspberry Pi 5 Model B Rev 1.0", "c04170")
PI4 = ("Raspberry Pi 4 Model B Rev 1.5", "b03115")
ZERO_W = ("Raspberry Pi Zero W Rev 1.1", "9000c1")
CM5 = ("Raspberry Pi Compute Module 5 Rev 1.0", "c04180")


def cam(sensor, autofocus=None, variant=None, fov=None):
    return {"sensor": sensor, "variant": variant, "autofocus": autofocus, "lens": None,
            "fov": fov}


V3_WIDE_NOIR = cam("imx708", True, "wide_noir")

# (caption, board, fan, rtc cell, cameras)
SAMPLES = [
    ("no camera: looked, none found", PI5, False, False, []),
    ("v1, fixed focus", PI5, False, False, [cam("ov5647", False)]),
    ("v1, autofocus", PI5, False, False, [cam("ov5647", True)]),
    ("v1, autofocus not known", PI5, False, False, [cam("ov5647")]),
    ("v1, fixed, 65° lens supplied", PI5, False, False, [cam("ov5647", False, fov=65)]),
    ("v1, fixed, 120° lens supplied", PI5, False, False, [cam("ov5647", False, fov=120)]),
    ("v1, fixed, 160° lens supplied", PI5, False, False, [cam("ov5647", False, fov=160)]),
    ("v2, fixed focus", PI5, False, False, [cam("imx219", False)]),
    ("v2, autofocus", PI5, False, False, [cam("imx219", True)]),
    ("v3 (always autofocus)", PI5, False, False, [cam("imx708", True)]),
    ("v3 wide", PI5, False, False, [cam("imx708", True, "wide")]),
    ("v3 NoIR", PI5, False, False, [cam("imx708", True, "noir")]),
    ("v3 wide NoIR", PI5, False, False, [V3_WIDE_NOIR]),
    ("HQ, no lens driver found", PI5, False, False, [cam("imx477", False)]),
    ("GS, autofocus not known", PI5, False, False, [cam("imx296")]),
    ("AI, fixed focus", PI5, False, False, [cam("imx500", False)]),
    ("unknown sensor: its driver's name", PI5, False, False, [cam("imx290", False)]),
    ("two cameras: v3 wide + v1 160°", PI5, False, False,
     [cam("imx708", True, "wide"), cam("ov5647", False, fov=160)]),
    ("long title, fan, clock, v3 wide NoIR", CM5, True, True, [V3_WIDE_NOIR]),
    ("fan, clock, v2", PI5, True, True, [cam("imx219", False)]),
    ("worst case: fan, clock, two cameras", PI5, True, True,
     [V3_WIDE_NOIR, cam("imx477", False)]),
    ("Pi 4 title, v3 wide NoIR", PI4, None, None, [V3_WIDE_NOIR]),
    ("Zero W, v1 autofocus not known", ZERO_W, None, None, [cam("ov5647")]),
    ("three cameras (a multiplexer): subtitle is cut", PI5, False, False,
     [cam("imx219", False), cam("imx219", False), cam("ov5647", True)]),
]

# The alternative design, on the cases where it differs most.
ALTERNATIVE = [
    ("ALT v1, fixed focus: solid lens", PI5, False, False, [cam("ov5647", False)]),
    ("ALT v1, autofocus: double ring", PI5, False, False, [cam("ov5647", True)]),
    ("ALT v1, autofocus not known: bare ring", PI5, False, False, [cam("ov5647")]),
    ("ALT v1 fixed, 160° lens supplied", PI5, False, False, [cam("ov5647", False, fov=160)]),
    ("ALT v3 wide: a wedge", PI5, False, False, [cam("imx708", True, "wide")]),
    ("ALT v3 NoIR: dark body", PI5, False, False, [cam("imx708", True, "noir")]),
    ("ALT v3 wide NoIR", PI5, False, False, [V3_WIDE_NOIR]),
    ("ALT long title, fan, clock, v3 wide NoIR", CM5, True, True, [V3_WIDE_NOIR]),
    ("ALT worst case: fan, clock, two cameras", PI5, True, True,
     [V3_WIDE_NOIR, cam("imx477", False)]),
]

SHEET_DPI, ZOOM_DPI = 200, 600
BAND_LEFT, BAND_H = 18.5 * mm, 12.2 * mm        # the title band, right of the mark


def documents(samples):
    """Fake probe documents for `samples`, named so they sort in order."""
    docs = {}
    for n, (_caption, (model, revision), fan, rtc, cameras) in enumerate(samples, 1):
        raw = conftest._doc(
            model, f"fa4e0000000000{n:02d}", revision, ["FAKE: no such board"],
            "undetermined", [],
            [{"kind": "eth", "mac": f"00:00:5e:00:53:{n:02x}"},
             {"kind": "wlan", "mac": f"00:00:5e:00:53:{0x80 + n:02x}"}],
            [], rtc, fan, 3000 if fan is not None else None)
        raw["verdict"]["summary"]["cameras"] = cameras
        host = f"cam-{n:02d}"
        docs[host] = ProbeDocument.from_dict(host, raw)
    return docs


# --- the alternative: pictures instead of words --------------------------------

def alt_wedge(cam_record):
    """The lens angle the alternative draws: a supplied one, or 120 degrees
    for a Camera Module 3 that calls itself wide; None for neither."""
    if cam_record.fov:
        return cam_record.fov
    return 120 if cam_record.variant in ("wide", "wide_noir") else None


def alt_width(lab, cam_record, size):
    _gen_size, body_w, word_size, _w = labels.camera_layout(lab, cam_record, size)
    width = body_w
    if alt_wedge(cam_record):
        width += size * 0.5
    if cam_record.fov:
        width += size * 0.1 + lab.width(f"{cam_record.fov}°", SANS_BOLD, word_size)
    return width


def alt_mark(lab, cam_record, x, y, size):
    """The same camera outline with everything but the generation said by
    its shape: a double lens ring for autofocus, a solid lens for fixed
    focus, a bare ring where nobody could look; a dark body for NoIR; a
    wedge at the lens's angle for a wide or measured lens, with the degrees
    beside it only where a person supplied them."""
    c = lab.c
    gen = labels.camera_generation(cam_record.sensor)
    gen_size, body_w, word_size, _w = labels.camera_layout(lab, cam_record, size)
    noir = cam_record.variant in ("noir", "wide_noir")
    pad, lens, body_h = size * 0.16, size * 0.27, size * 0.86
    px, py = lab.pt(x, y + size)
    ink = white if noir else GREY
    c.setStrokeColor(GREY)
    c.setFillColor(GREY)
    c.setLineWidth(size * 0.07)
    c.roundRect(px, py, body_w, body_h, size * 0.12, stroke=1, fill=1 if noir else 0)
    c.rect(px + pad, py + body_h, lens * 1.2, size * 0.12, stroke=0, fill=1)
    cx, cy = px + pad + lens, py + body_h / 2
    c.setStrokeColor(ink)
    c.setFillColor(ink)
    if cam_record.autofocus is False:
        c.circle(cx, cy, lens, stroke=0, fill=1)
    else:
        c.circle(cx, cy, lens, stroke=1, fill=0)
        if cam_record.autofocus:
            c.circle(cx, cy, lens * 0.5, stroke=1, fill=0)
    lab.text(x + pad + 2 * lens + pad, y + size - body_h / 2 - gen_size * 0.36, gen,
             SANS_BOLD, gen_size, color=white if noir else black)
    angle = alt_wedge(cam_record)
    if angle:
        ax, ay = px + body_w + size * 0.08, py + body_h / 2
        ray = size * 0.42
        c.setStrokeColor(GREY)
        c.setLineCap(1)
        for sign in (1, -1):
            a = math.radians(sign * angle / 2)
            c.line(ax, ay, ax + ray * math.cos(a), ay + ray * math.sin(a))
        c.setLineCap(0)
    if cam_record.fov:
        lab.text(x + body_w + size * 0.6, y + size - body_h / 2 - word_size * 0.36,
                 f"{cam_record.fov}°", SANS_BOLD, word_size, color=GREY)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(1)


# --- rendering -----------------------------------------------------------------

def crops(samples, dpi, tmp, band=False):
    """One image per label in `samples`, cut out of the rendered sheet by
    the label grid; only its title band when `band`."""
    docs = documents(samples)
    pdf = Path(tmp) / "cameras.pdf"
    labels.render(docs, pdf, only={"rpi"}, outline=True)
    for old in Path(tmp).glob("page*.png"):
        old.unlink()
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), tmp + "/page"], check=True)
    sheets = [Image.open(p).convert("RGB") for p in sorted(Path(tmp).glob("page*.png"))]
    px = dpi / 72.0
    per_sheet = labels.COLS * labels.ROWS
    out = []
    for index in range(len(samples)):
        sheet, pos = divmod(index, per_sheet)
        page = sheets[sheet]
        x, y = labels.label_origin(pos)
        left = round(x * px)
        top = round(page.height - y * px - labels.LABEL_H * px)
        right, bottom = left + round(labels.LABEL_W * px), top + round(labels.LABEL_H * px)
        if band:
            left, bottom = left + round(BAND_LEFT * px), top + round(BAND_H * px)
        out.append(page.crop((left, top, right + 1, bottom + 1)))
    return out


def font(size):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def compose(sections, cols, scale):
    """A page of captioned crops: each section a heading over a grid."""
    gap, caption_h, line_h = 14 * scale, 22 * scale, 28 * scale
    cell_w, cell_h = sections[0][1][0][1].size
    width = gap + cols * (cell_w + gap)
    height = gap
    for heading, cells in sections:
        rows = -(-len(cells) // cols)
        height += len(heading) * line_h + gap + rows * (cell_h + caption_h + gap)
    page = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(page)
    y = gap
    for heading, cells in sections:
        for line in heading:
            draw.text((gap, y), line, fill="black", font=font(20 * scale))
            y += line_h
        y += gap
        for i, (caption, image) in enumerate(cells):
            cx = gap + (i % cols) * (cell_w + gap)
            cy = y + (i // cols) * (cell_h + caption_h + gap)
            page.paste(image, (cx, cy))
            draw.text((cx, cy + cell_h + 3 * scale), caption, fill="#333333",
                      font=font(14 * scale))
        y += -(-len(cells) // cols) * (cell_h + caption_h + gap)
    return page


def main() -> None:
    main_heading = ["FAKE boards. Recommended design: the generation on the camera's body,",
                    "focus and optics in words beside it"]
    alt_heading = ["FAKE boards. Alternative design (drawn by this script only): no words.",
                   "Double ring = AF, solid lens = fixed, bare ring = not known;",
                   "dark body = NoIR; wedge = lens angle"]
    real = labels.mark_camera, labels.camera_width
    with tempfile.TemporaryDirectory(dir=HERE) as tmp:
        for name, dpi, band, cols, scale in (("camera-icons", SHEET_DPI, False, 3, 1),
                                             ("camera-icons-zoom", ZOOM_DPI, True, 2, 2)):
            labels.mark_camera, labels.camera_width = real
            recommended = crops(SAMPLES, dpi, tmp, band)
            labels.mark_camera, labels.camera_width = alt_mark, alt_width
            alternative = crops(ALTERNATIVE, dpi, tmp, band)
            labels.mark_camera, labels.camera_width = real
            page = compose(
                [(main_heading, list(zip((s[0] for s in SAMPLES), recommended, strict=True))),
                 (alt_heading, list(zip((s[0] for s in ALTERNATIVE), alternative, strict=True)))],
                cols, scale)
            page.quantize(colors=64).save(HERE / f"{name}.png", optimize=True)
            print(f"{name}.png", page.size)


if __name__ == "__main__":
    main()
