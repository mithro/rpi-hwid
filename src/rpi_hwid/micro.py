"""Micro labels: a small device label, four of them to one sticker.

The board labels in ``rpi_hwid.labels`` are a whole 63.5 x 38.1 mm sticker
each, which is right for a Pi and far too much for a thumb-sized module or a
mains plug. A micro label is a quarter of that sticker -- 31.75 x 19.05 mm,
two across and two down -- so it prints on the same Avery L7160 stock, in
the same grid, and four small devices share one sticker, cut apart along
the dotted guides.

It is the board label's family shrunk, not a new design. The same fonts and
the same greys; the maker's mark top left beside a bold title; the
identifier people look for in a QR code on the left and again in monospace
along the whole foot, the largest thing on the label; the facts in small
captioned rows beside the code. And the same rules: nothing that can change
is printed, and a label whose identifier was not read is not drawn at all --
constructing one raises, naming the host.

The layout is device-neutral. What a caller fills in is a ``MicroLabel``:

    title, subtitle        the header, beside the maker's mark
    mark                   an artwork file name (see labels.artwork), or None
    icons                  glyphs at the header's right: ``Icon("wifi")``,
                           ``Icon("chip", "C3")``; ``ICONS`` is the registry,
                           and a caller may add its own
    ident_caption, ident   the primary identifier: the foot and the QR
    qr                     what the QR encodes, if not the identifier
    specs                  a strip of glyphs heading the band beside the QR,
                           for what the device *is* (``Icon("riscv")``,
                           ``Icon("cores", "2+1")``, ``Icon("memory",
                           "400K")``), in place of a line of text
    rows                   up to MAX_ROWS captioned facts beside the QR, or
                           SPEC_ROWS under a spec strip; the last may run
                           down beside the foot's caption
    extra                  a callable drawing a section of the caller's own
                           in the room left under the rows

and ``render_micro`` lays any number of them out, four to a sticker, 84 to
an A4 sheet. ``pack`` and ``draw_quad`` are the same thing in pieces, for a
caller that places whole stickers itself (``draw_quad`` has the signature
of every ``draw_*`` in ``rpi_hwid.labels``).
"""

from __future__ import annotations

import importlib
import math
import pkgutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, Any

from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from rpi_hwid import labels

if TYPE_CHECKING:
    from pathlib import Path

# --- the quarter sticker ------------------------------------------------------

MICRO_COLS, MICRO_ROWS = 2, 2
PER_STICKER = MICRO_COLS * MICRO_ROWS
MICRO_W = labels.LABEL_W / MICRO_COLS          # 31.75 mm
MICRO_H = labels.LABEL_H / MICRO_ROWS          # 19.05 mm
# Ink this far from every edge of the quarter. Less than a whole label's
# 2.5 mm, because the cut is made by hand along a printed guide rather than
# by a die, and a millimetre either side of it is plenty.
MICRO_PAD = 1.2 * mm

# The sizes: the board label's, scaled to a label half as high.
CAPTION = 4.5                  # captions, points
TITLE = 8.0                    # the title's largest size
SUBTITLE = 5.0
ROW = 5.5                      # a row's value
MIN_SIZE = 4.0                 # nothing smaller is printed
IDENT_MAX = 8.5                # the foot's identifier, at most

HEAD_H = 3.6 * mm              # the header band: mark, title, icons
MARK_W = 5.5 * mm              # the widest the maker's mark may be
ICON_GAP = 0.6 * mm
BAND_GAP = 0.6 * mm            # header to the QR band
FOOT_GAP = 0.5 * mm            # QR band to the foot
CAP_GAP = 0.35 * mm            # foot caption to the identifier
ROW_PITCH = 2.1 * mm
MAX_ROWS = 4                   # beside the subtitle's line, which is always kept
SPEC_H = 2.4 * mm              # the spec strip's glyphs, in the subtitle's place
SPEC_GAP = 0.3 * mm            # the strip to the first line under it
# Under a spec strip the rows are one step tighter than ROW and ROW_PITCH:
# five of them, none larger than SPEC_ROW, at spec_pitch -- room for a
# flash row and three rows of serials under a chip row (the ESP32-C3's
# flash uid and its chip's eFuse id, Tim, 2026-09-27).
SPEC_ROWS = 5
SPEC_ROW = 4.4
EXTRA_GAP = 0.6 * mm           # the last row to an extra section under it
SPEC_EXTRA_GAP = 0.3 * mm      # ...closed up with the rows under a spec strip
SPEC_ICON_GAP = 0.45 * mm      # between the strip's glyphs

GUIDE = HexColor("#999999")    # the cut guides, lighter than any caption


def foot_top() -> float:
    """Where the foot starts: its caption, then the identifier at its largest."""
    return MICRO_H - MICRO_PAD - (CAPTION * 0.72 + CAP_GAP + IDENT_MAX * 0.72)


def band_top() -> float:
    return MICRO_PAD + HEAD_H + BAND_GAP


def qr_size() -> float:
    """The QR fills the band between the header and the foot."""
    return foot_top() - FOOT_GAP - band_top()


def rows_x() -> float:
    """Where the subtitle, the rows and the spec strip start: right of the QR."""
    return MICRO_PAD + qr_size() + 1.2 * mm


def rows_w() -> float:
    return MICRO_W - MICRO_PAD - rows_x()


def caption_baseline() -> float:
    """The foot caption's baseline. Right of the QR nothing else is on the
    caption's line, so the rows may run down to it: the last row's letters
    stand on the same line as the caption's."""
    return foot_top() + CAPTION * 0.72


def spec_pitch() -> float:
    """The row pitch under a spec strip: SPEC_ROWS rows of at most
    SPEC_ROW-point type between the strip and the caption's baseline.
    Tighter than ROW_PITCH, which is what buys the strip its height and
    the fifth row its place."""
    first = band_top() + SPEC_H + SPEC_GAP
    return (caption_baseline() - first - SPEC_ROW * 0.72) / (SPEC_ROWS - 1)


def max_rows(specs: bool) -> int:
    """How many rows a label holds: more under a spec strip, set tighter."""
    return SPEC_ROWS if specs else MAX_ROWS


# --- the drawing context ------------------------------------------------------


class Cell(labels.Label):
    """A ``labels.Label`` a quarter the size: the same helpers, with the
    origin at the quarter's top-left corner and y growing downwards."""

    def __init__(self, c: Any, x0: float, y0: float,
                 w: float = MICRO_W, h: float = MICRO_H) -> None:
        super().__init__(c, x0, y0)
        self.w, self.h = w, h

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return self.x0 + x, self.y0 + self.h - y

    def outline(self) -> None:
        c = self.c
        c.setStrokeColor(HexColor("#bbbbbb"))
        c.setLineWidth(0.3)
        c.rect(self.x0, self.y0, self.w, self.h, stroke=1, fill=0)
        c.setStrokeColor(black)


# --- glyphs -------------------------------------------------------------------
#
# Each takes (cell, x, y, height, text) with (x, y) the glyph's top-left and
# returns the width it took. The Wi-Fi arcs, the RJ45 jack and the USB
# trident are the board labels' own; the chip and the antenna are drawn here.

IconFn = Callable[[Cell, float, float, float, str], float]


# The 802.11 amendments the Wi-Fi glyph letters: the classic single letters
# on the line beside the arcs, the later two-letter ones on the line under it.
WIFI_STANDARDS = ("a", "b", "g", "n", "ac", "ax", "be")
WIFI_TYPE = MIN_SIZE           # the glyph's lettering: the smallest the labels print
WIFI_FAN = 40.0                # degrees either side of upright the arcs sweep
WIFI_GAP = 0.25 * mm           # arcs or band to the standards beside them


def wifi_lines(text: str) -> tuple[str, str, str]:
    """'2.4/5 a/b/g/n/ac/ax' -> ('2.4/5', 'a/b/g/n', 'ac/ax'): the bands in
    GHz, then the standards, which split into the classic single-letter
    amendments and the later two-letter ones. Refused unless it is that."""
    bands, _, standards = text.partition(" ")
    got = standards.split("/")
    if (not all(b.replace(".", "", 1).isdigit() for b in bands.split("/"))
            or not standards or any(g not in WIFI_STANDARDS for g in got)):
        raise ValueError(
            "a Wi-Fi glyph's text is its bands and its 802.11 standards, "
            f"'2.4/5 a/b/g/n/ac/ax', not {text!r}")
    if len(got) == 1:
        # one standard alone (the newest, as the ESP32 labels print it) is
        # joined to the band under the arcs: "2.4n", "2.4/5ax" (Tim,
        # 2026-09-27)
        return bands + got[0], "", ""
    return (bands, "/".join(g for g in got if len(g) == 1),
            "/".join(g for g in got if len(g) > 1))


def _wifi_baseline(size: float) -> float:
    """The bottom line's baseline, below the glyph's top: its descenders
    (the g of b/g/n) just reach the glyph's foot."""
    return size - WIFI_TYPE * 0.21


def _wifi_geometry(size: float) -> tuple[float, float, float, float]:
    """The lettered glyph's arcs: their box's height, the line width, the
    dot's radius and the outer arc's, at a glyph `size` high. The bottom
    line of lettering stands on the glyph's foot; the arcs fill the rest."""
    box_h = _wifi_baseline(size) - WIFI_TYPE * 0.72 - 0.25 * mm
    lw, dot = box_h * 0.11, box_h * 0.08
    return box_h, lw, dot, box_h - dot - lw / 2


def wifi_arcs_width(size: float) -> float:
    """How wide the lettered glyph's arcs are, round caps and all."""
    _, lw, _, r = _wifi_geometry(size)
    return 2 * (r * math.sin(math.radians(WIFI_FAN)) + lw / 2)


def _bold(s: str) -> float:
    return pdfmetrics.stringWidth(s, labels.SANS_BOLD, WIFI_TYPE) if s else 0.0


def wifi_width(size: float, text: str) -> float:
    if not text:
        return size
    bands, first, second = wifi_lines(text)
    arcs = wifi_arcs_width(size)
    return max(arcs + (WIFI_GAP + _bold(first) if first else 0.0),
               max(_bold(bands), arcs if not first else 0.0)
               + (WIFI_GAP + _bold(second) if second else 0.0))


def glyph_wifi(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """The Wi-Fi arcs. With text -- ``Icon("wifi", "2.4/5 a/b/g/n/ac/ax")``
    -- the bands and the 802.11 standards the radio has (Tim, 2026-09-27):
    the band in GHz under the arcs, as the source they spread from; the
    classic single-letter standards beside the arcs, and the two-letter
    ones (ac, ax), where there are any, beside the band. The standards are
    set flush right, so each line takes only the room it needs; all of it
    bold, at the smallest size the labels print."""
    if not text:
        return float(labels.mark_wifi(cell, x, y, size))
    bands, first, second = wifi_lines(text)
    w = wifi_width(size, text)
    box_h, lw, dot, r = _wifi_geometry(size)
    arcs_w, band_w = wifi_arcs_width(size), _bold(bands)
    # the band is centred under the arcs; one wider than them (2.4/5) runs
    # on to the right from under their left end
    bx = x + (arcs_w - band_w) / 2 if band_w <= arcs_w else x
    c = cell.c
    cx, cy = cell.pt(x + arcs_w / 2, y + box_h - dot)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.circle(cx, cy, dot, stroke=0, fill=1)
    c.setLineWidth(lw)
    c.setLineCap(1)
    for k in (0.36, 0.68, 1.0):
        rad = r * k
        c.arc(cx - rad, cy - rad, cx + rad, cy + rad, 90 - WIFI_FAN, 2 * WIFI_FAN)
    c.setLineWidth(1)
    c.setLineCap(0)
    bottom = y + _wifi_baseline(size) - WIFI_TYPE * 0.72
    cell.text(bx, bottom, bands, labels.SANS_BOLD, WIFI_TYPE)
    if first:
        cell.text(x + w, bottom - WIFI_TYPE * 1.12, first, labels.SANS_BOLD, WIFI_TYPE,
                  align="right")
    if second:
        cell.text(x + w, bottom, second, labels.SANS_BOLD, WIFI_TYPE, align="right")
    return w


def glyph_usb(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    path = labels.artwork("usb.svg")
    if not path:
        return 0.0
    # the trident is wide and flat: give it the height's middle 70 %
    return float(cell.svg(path, x, y + size * 0.15, size * 0.7))


def glyph_ethernet(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    return float(labels.mark_rj45(cell, x, y, size))


def glyph_chip(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """A square package with pins along all four sides, lettered inside --
    ``Icon("chip", "C3")`` is an ESP32-C3's die at a glance."""
    c = cell.c
    body = size * 0.72
    inset = (size - body) / 2
    px, py = cell.pt(x + inset, y + inset + body)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(size * 0.06)
    c.rect(px, py, body, body, stroke=1, fill=0)
    pins, pin_w, pin_l = 4, size * 0.07, inset * 0.9
    step = body / (pins + 1)
    for i in range(1, pins + 1):
        o = i * step - pin_w / 2
        c.rect(px + o, py + body, pin_w, pin_l, stroke=0, fill=1)        # top
        c.rect(px + o, py - pin_l, pin_w, pin_l, stroke=0, fill=1)       # bottom
        c.rect(px - pin_l, py + o, pin_l, pin_w, stroke=0, fill=1)       # left
        c.rect(px + body, py + o, pin_l, pin_w, stroke=0, fill=1)        # right
    if text:
        room = body * 0.84
        s = cell.fitted_size(text, labels.SANS_BOLD, body * 0.62, room, min_size=2.5)
        cell.text(x + size / 2, y + (size - s * 0.72) / 2, text, labels.SANS_BOLD, s,
                  align="centre")
    c.setLineWidth(1)
    return size


MAST_RUN = 0.84        # of the glyph's height: how tall a lettered mast stands


def mast_size(text: str, size: float) -> float:
    """The bold point size at which `text`, set upright, is the mast of a
    `size`-high antenna. Refused below MIN_SIZE rather than shrunk further:
    a band that would print smaller than that belongs in a row instead."""
    per_point = pdfmetrics.stringWidth(text, labels.SANS_BOLD, 1)
    s = size * MAST_RUN / per_point
    if s < MIN_SIZE:
        raise ValueError(
            f"the antenna's band {text!r} would stand as its mast at {s:.1f} pt, below "
            f"the {MIN_SIZE:.0f} pt the micro labels print; give it fewer characters")
    return s


def glyph_antenna(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """A mast on a foot with a wave either side: a radio that is not Wi-Fi.

    With text -- ``Icon("antenna", "433")`` -- the text *is* the mast: set
    bold and upright, reading upwards like the board labels' serial, standing
    on the foot, with the waves either side of its top (Tim, 2026-09-27). At
    the header's 3.6 mm "433" stands at about 5.1 pt. Without, the mast is a
    plain line."""
    if text:
        return _lettered_antenna(cell, x, y, size, text)
    c = cell.c
    cx = x + size * 0.35
    top, bottom = cell.pt(cx, y + size * 0.1), cell.pt(cx, y + size)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(size * 0.09)
    c.setLineCap(1)
    c.line(top[0], top[1], bottom[0], bottom[1])
    foot = size * 0.22
    c.line(bottom[0] - foot, bottom[1], bottom[0] + foot, bottom[1])
    hx, hy = cell.pt(cx, y + size * 0.3)
    for r in (0.2, 0.34):
        rad = size * r
        c.arc(hx - rad, hy - rad, hx + rad, hy + rad, -40, 80)
        c.arc(hx - rad, hy - rad, hx + rad, hy + rad, 140, 80)
    c.setLineWidth(1)
    c.setLineCap(0)
    return size * 0.75


def _lettered_antenna(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """The antenna whose mast is `text`, upright; a `size`-wide square."""
    c = cell.c
    s = mast_size(text, size)
    run, thick = cell.width(text, labels.SANS_BOLD, s), s * 0.72
    lw = size * 0.09
    cx = x + size / 2
    foot_y = y + size - lw / 2
    bottom = foot_y - lw / 2 - size * 0.02
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(lw)
    c.setLineCap(1)
    fx, fy = cell.pt(cx, foot_y)
    foot = size * 0.25
    c.line(fx - foot, fy, fx + foot, fy)
    # the waves round the top of the lettering, clear of its sides
    hx, hy = cell.pt(cx, bottom - run + size * 0.18)
    for r in (thick / 2 + lw * 1.4, thick / 2 + lw * 2.9):
        c.arc(hx - r, hy - r, hx + r, hy + r, -40, 80)
        c.arc(hx - r, hy - r, hx + r, hy + r, 140, 80)
    c.setLineWidth(1)
    c.setLineCap(0)
    cell.rotated(cx - thick / 2, bottom, text, labels.SANS_BOLD, s)
    return size


def glyph_tasmota(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """Tasmota's own mark, the house with the power symbol in it."""
    path = labels.artwork("tasmota.svg")
    if not path:
        return 0.0
    return float(cell.svg(path, x, y, size))


RISCV_MARK = "risc-v-simple.svg"


def isa_width(size: float) -> float:
    """The box both ISA marks take: the RISC-V mark's, at `size` high."""
    return _artwork_width(RISCV_MARK, size) or size


def glyph_riscv(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """The RISC-V mark: RISC-V International's "RV" without the "RISC-V"
    wordmark under it, cut from their own file (artwork/README.md). The
    processor implements the RISC-V ISA."""
    path = labels.artwork(RISCV_MARK)
    if not path:
        return 0.0
    return float(cell.svg(path, x, y, size))


XTENSA_FONT = "Helvetica-BoldOblique"


def _xtensa_sizes(size: float) -> tuple[float, float, float]:
    """The X's point size, the t's, and how wide the pair runs: the X
    stands 94 % of the glyph's height and the t sits on its baseline at
    60 % of its size, both shrunk together where the pair would be wider
    than the RISC-V mark's box."""
    big = size * 0.94 / 0.72
    small = big * 0.6
    run = (pdfmetrics.stringWidth("X", XTENSA_FONT, big) * 0.92
           + pdfmetrics.stringWidth("t", XTENSA_FONT, small))
    k = min(1.0, isa_width(size) / run)
    return big * k, small * k, run * k


def xtensa_width(size: float) -> float:
    return isa_width(size)


def glyph_xtensa(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """The processor is a Cadence Xtensa. Cadence registers "Xtensa" as a
    word mark and publishes no logo for the architecture that could be
    fetched (docs/ESPRESSIF.md), so the glyph is set from the name: its
    "Xt", a tall bold oblique X with a small t on its baseline, in the box
    the RISC-V mark takes on other labels (Tim, 2026-09-27)."""
    big, small, run = _xtensa_sizes(size)
    left = x + (isa_width(size) - run) / 2
    top = y + (size - big * 0.72) / 2
    cell.text(left, top, "X", XTENSA_FONT, big)
    xw = pdfmetrics.stringWidth("X", XTENSA_FONT, big) * 0.92
    cell.text(left + xw, top + (big - small) * 0.72, "t", XTENSA_FONT, small)
    return xtensa_width(size)


def core_counts(text: str) -> tuple[int, int]:
    """'2+1' -> (2, 1): the application cores, then the low-power ones; a
    bare '2' has none of the second. One digit each, and at least one
    application core."""
    head, plus, tail = text.partition("+")
    if (len(head) != 1 or not head.isdigit() or head == "0"
            or (plus and (len(tail) != 1 or not tail.isdigit()))):
        raise ValueError(f"a core count is 'N' or 'N+M', one digit each, not {text!r}")
    return int(head), int(tail or 0)


CORES_MAIN = 0.74              # the application cores' figure: its cap height, of the die's
CORES_PAD = 0.3 * mm           # the die's edge, and the divider, to a figure


def _cores_sizes(size: float) -> tuple[float, float, float]:
    """The die's height, and the two figures' point sizes."""
    body = size * 0.8
    return body, body * CORES_MAIN / 0.72, MIN_SIZE


def cores_width(size: float, text: str) -> float:
    hp, lp = core_counts(text)
    body, big, small = _cores_sizes(size)
    return (pdfmetrics.stringWidth(str(hp), labels.SANS_BOLD, big)
            + pdfmetrics.stringWidth(str(lp), labels.SANS_BOLD, small)
            + 4 * CORES_PAD + (size - body))


def glyph_cores(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """A package with two numbers on its die (Tim, 2026-09-27):
    ``Icon("cores", "2+1")`` is the application cores, large and black,
    and past a divider the low-power cores, small and grey. A part
    without a low-power core says 0."""
    hp, lp = core_counts(text)
    body, big, small = _cores_sizes(size)
    w = cores_width(size, text)
    inset = (size - body) / 2
    bw = w - 2 * inset
    c = cell.c
    px, py = cell.pt(x + inset, y + inset + body)
    lw = size * 0.06
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(lw)
    c.rect(px, py, bw, body, stroke=1, fill=0)
    pin_w, pin_l = size * 0.07, inset * 0.9
    for n, run, along in ((4, bw, True), (3, body, False)):
        step = run / (n + 1)
        for i in range(1, n + 1):
            o = i * step - pin_w / 2
            if along:
                c.rect(px + o, py + body, pin_w, pin_l, stroke=0, fill=1)       # top
                c.rect(px + o, py - pin_l, pin_w, pin_l, stroke=0, fill=1)      # bottom
            else:
                c.rect(px - pin_l, py + o, pin_l, pin_w, stroke=0, fill=1)      # left
                c.rect(px + bw, py + o, pin_l, pin_w, stroke=0, fill=1)         # right
    divider = x + inset + 2 * CORES_PAD + cell.width(str(hp), labels.SANS_BOLD, big)
    dx, _ = cell.pt(divider, 0)
    c.setLineWidth(lw * 0.6)
    c.setStrokeColor(labels.GREY)
    c.line(dx, py + body * 0.2, dx, py + body * 0.8)
    c.setStrokeColor(black)
    c.setLineWidth(1)
    # both figures stand on one baseline, the large one centred in the die
    base = y + inset + (body + big * 0.72) / 2
    cell.text(x + inset + CORES_PAD, base - big * 0.72, str(hp), labels.SANS_BOLD, big)
    cell.text(divider + CORES_PAD, base - small * 0.72, str(lp), labels.SANS_BOLD, small,
              color=labels.GREY)
    return w


def memory_type(size: float) -> float:
    """The point size of a memory glyph's lettering: its cap height is 60 %
    of the module's board, which is 78 % of the glyph."""
    return size * 0.78 * 0.6 / 0.72


def memory_width(size: float, text: str) -> float:
    return pdfmetrics.stringWidth(text, labels.SANS_BOLD, memory_type(size)) + size * 0.3


def glyph_memory(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """A memory module -- a board with its contacts along the bottom edge,
    the way a DIMM is drawn -- with its size lettered on it:
    ``Icon("memory", "400K")``, or ``"512K+8M"`` for on-chip SRAM and the
    PSRAM in the package with it."""
    c = cell.c
    s = memory_type(size)
    w = memory_width(size, text)
    lw = size * 0.06
    board_h = size * 0.78
    top = y + size * 0.02
    px, py = cell.pt(x + lw / 2, top + board_h)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(lw)
    c.rect(px, py, w - lw, board_h - lw / 2, stroke=1, fill=0)
    # the contacts under the board, with the key notch a third of the way along
    teeth_h, pitch = size * 0.16, size * 0.17
    tw = pitch * 0.55
    notch = x + w * 0.35
    tx = x + pitch * 0.5
    while tx + tw <= x + w - pitch * 0.4:
        if not notch - pitch < tx < notch + pitch * 0.2:
            bx, by = cell.pt(tx, top + board_h + teeth_h)
            c.rect(bx, by, tw, teeth_h, stroke=0, fill=1)
        tx += pitch
    cell.text(x + w / 2, top + (board_h - s * 0.72) / 2, text, labels.SANS_BOLD, s,
              align="centre")
    c.setLineWidth(1)
    return w


BLUETOOTH_W = 0.55     # of the height


def glyph_bluetooth(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """The Bluetooth rune, drawn: a stem with the two arrowheads crossing
    it, as the chip's features say Bluetooth."""
    c = cell.c
    w = size * BLUETOOTH_W

    def p(u: float, v: float) -> tuple[float, float]:
        return cell.pt(x + w * u, y + size * v)

    c.setStrokeColor(black)
    c.setLineWidth(size * 0.09)
    c.setLineCap(1)
    c.setLineJoin(1)
    path = c.beginPath()
    path.moveTo(*p(0.12, 0.29))
    for u, v in ((0.88, 0.71), (0.5, 0.93), (0.5, 0.07), (0.88, 0.29), (0.12, 0.71)):
        path.lineTo(*p(u, v))
    c.drawPath(path, stroke=1, fill=0)
    c.setLineCap(0)
    c.setLineJoin(0)
    c.setLineWidth(1)
    return w


def glyph_mesh(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """An IEEE 802.15.4 radio -- the one Thread and Zigbee run over -- drawn
    as what it is for: a mesh, four nodes each linked to the others."""
    c = cell.c
    nodes = ((0.5, 0.12), (0.1, 0.6), (0.9, 0.6), (0.5, 0.9))
    pts = [cell.pt(x + size * u, y + size * v) for u, v in nodes]
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(size * 0.06)
    for i, a in enumerate(pts):
        for b in pts[i + 1:]:
            c.line(a[0], a[1], b[0], b[1])
    for px, py in pts:
        c.circle(px, py, size * 0.1, stroke=0, fill=1)
    c.setLineWidth(1)
    return size


ICONS: dict[str, IconFn] = {
    "wifi": glyph_wifi,
    "usb": glyph_usb,
    "ethernet": glyph_ethernet,
    "chip": glyph_chip,
    "antenna": glyph_antenna,
    "tasmota": glyph_tasmota,
    "riscv": glyph_riscv,
    "xtensa": glyph_xtensa,
    "cores": glyph_cores,
    "memory": glyph_memory,
    "bluetooth": glyph_bluetooth,
    "mesh": glyph_mesh,
}


@dataclass(frozen=True)
class Icon:
    """A glyph from ``ICONS`` by name, with the text some of them carry."""

    name: str
    text: str = ""


# --- the record ---------------------------------------------------------------


class IdentifierMissingError(ValueError):
    """A micro label was asked for without the identifier it exists to carry.

    The package's rule, as for every board label: a sticker with a blank on it
    still gets printed, peeled and stuck to a device, so the missing read is
    an error at the last moment it is cheap -- naming the host, and the
    command that would read it when the caller says what that is."""


@dataclass(frozen=True)
class MicroRow:
    """One captioned fact beside the QR. A ``mono`` value is an identifier
    someone may type, so it is set in the monospace face and never elided:
    if it cannot fit at the smallest size the label is refused.

    A row shrinks to fit its value unless it has a ``size``: then it is set
    at exactly that size, so a kind of row a caller prints on every label
    reads the same on all of them, and a value that does not fit whole at
    it is refused rather than shrunk. ``BLANK_ROW`` keeps a place empty.

    The values start in one column, right of the widest caption. A ``wide``
    row's value starts sooner, right of the widest caption among the wide
    rows: the room for a value longer than the column holds (an ESP32-C3's
    twenty-digit flash uid beside its "eFuse" rows). Its caption stays flush
    against it, as every caption is."""

    caption: str
    value: str
    mono: bool = False
    size: float | None = None
    wide: bool = False

    @property
    def blank(self) -> bool:
        return not self.caption and not self.value


# A place left empty: the fact it holds on other labels does not apply here,
# and the rows under it stay where they are on every other label.
BLANK_ROW = MicroRow("", "")


ExtraFn = Callable[[Cell, tuple[float, float, float, float]], None]


@dataclass(frozen=True)
class MicroLabel:
    host: str
    title: str
    ident_caption: str
    ident: str
    subtitle: str = ""
    mark: str | None = None
    icons: tuple[Icon, ...] = ()
    rows: tuple[MicroRow, ...] = ()
    qr: str | None = None
    specs: tuple[Icon, ...] = ()
    extra: ExtraFn | None = field(default=None, compare=False)
    # the command that reads the identifier, for the error when it is missing
    read_with: str = ""

    def __post_init__(self) -> None:
        if not (self.ident or "").strip():
            how = (f" Read it with `{self.read_with}` and collect again."
                   if self.read_with else "")
            raise IdentifierMissingError(
                f"{self.host}: the {self.title} label has no {self.ident_caption}, so it "
                f"would carry nothing that identifies the device.{how}")
        if len(self.rows) > max_rows(bool(self.specs)):
            raise ValueError(
                f"{self.host}: the {self.title} label has {len(self.rows)} rows and a micro "
                f"label holds {max_rows(bool(self.specs))}; put the rest in an extra "
                "section or leave them in the document")
        for r in self.rows:
            if not r.blank and not (r.value or "").strip():
                raise ValueError(
                    f"{self.host}: the {r.caption} row of the {self.title} label has no "
                    "value; leave the row out rather than print it blank")
        for i in (*self.icons, *self.specs):
            if i.name not in ICONS:
                raise ValueError(f"{self.host}: no glyph called {i.name!r} "
                                 f"(have {', '.join(sorted(ICONS))})")
            try:
                if i.name == "antenna" and i.text:
                    mast_size(i.text, HEAD_H)
                if i.name == "cores":
                    core_counts(i.text)
                if i.name == "wifi" and i.text:
                    wifi_lines(i.text)
            except ValueError as exc:
                raise ValueError(f"{self.host}: {exc}") from None
        if self.specs:
            need = strip_width(self.specs)
            if need > rows_w() + 0.01:
                raise ValueError(
                    f"{self.host}: the {self.title} label's spec strip "
                    f"({', '.join(i.name for i in self.specs)}) is {need / mm:.1f} mm "
                    f"wide and the band beside the QR {rows_w() / mm:.1f} mm; drop a glyph")

    @property
    def qr_content(self) -> str:
        return self.qr or self.ident

    @property
    def listing_title(self) -> str:
        return f"{self.title} {self.ident}"


# --- drawing one --------------------------------------------------------------


def draw_micro(cell: Cell, m: MicroLabel) -> None:
    """Header across the top: mark, title, icons. Beside the QR: the
    subtitle and the rows. Across the foot: the identifier, whole."""
    w = cell.w
    right = w - MICRO_PAD

    # --- header ---
    x = MICRO_PAD
    path = labels.artwork(m.mark) if m.mark else None
    if path:
        aspect = labels.mark_aspect(path)
        mw = min(MARK_W, HEAD_H / aspect)
        labels.mark_in_box(cell, path, x, MICRO_PAD, mw, HEAD_H, align="left")
        x += mw + 1.0 * mm
    ix = right
    for icon in reversed(m.icons):
        # right to left, so the last icon sits against the edge
        ix -= _icon_width(cell, icon)
        ICONS[icon.name](cell, ix, MICRO_PAD, HEAD_H, icon.text)
        ix -= ICON_GAP
    title_w = title_room(m)
    size = cell.fitted_size(m.title, labels.SANS_BOLD, TITLE, title_w, min_size=MIN_SIZE)
    cell.fit(x, MICRO_PAD + (HEAD_H - size * 0.72) / 2, m.title, labels.SANS_BOLD, size,
             title_w, min_size=MIN_SIZE)

    # --- the QR and the rows beside it ---
    top, q = band_top(), qr_size()
    cell.qr(MICRO_PAD, top, q, m.qr_content)
    rx = rows_x()
    rw = right - rx
    pitch, y = ROW_PITCH, top
    if m.specs:
        # the strip heads the band, and the lines under it close up a little
        sx = rx
        for icon in m.specs:
            ICONS[icon.name](cell, sx, top, SPEC_H, icon.text)
            sx += _icon_width(cell, icon, SPEC_H) + SPEC_ICON_GAP
        pitch, y = spec_pitch(), top + SPEC_H + SPEC_GAP
    if m.subtitle:
        cell.fit(rx, y, m.subtitle, labels.SANS, SUBTITLE, rw, min_size=MIN_SIZE)
    if m.subtitle or not m.specs:
        y += pitch
    # rows below the QR's foot run beside the foot's caption: it must end
    # short of them
    if m.rows and y + (len(m.rows) - 1) * pitch + ROW * 0.72 > top + q:
        room = rx - MICRO_PAD - 0.8 * mm
        if cell.width(m.ident_caption, labels.SANS, CAPTION) > room:
            raise ValueError(
                f"{m.host}: the {m.title} label's foot caption {m.ident_caption!r} runs "
                "under the rows beside it; shorten the caption or drop a row")
    def column(rows: list[MicroRow]) -> float:
        cap_w = max([cell.width(r.caption, labels.SANS, CAPTION) for r in rows] or [0])
        return rx + cap_w + (labels.Label.CAPTION_GAP * 0.6 if cap_w else 0)

    columns = {False: column([r for r in m.rows if not r.wide]),
               True: column([r for r in m.rows if r.wide])}
    largest = SPEC_ROW if m.specs else ROW
    last_h = ROW * 0.72
    for r in m.rows:
        if r.blank:
            y += pitch
            continue
        font = labels.MONO_REGULAR if r.mono else labels.SANS
        vx = columns[r.wide]
        vw = right - vx
        if r.size is not None and cell.width(r.value, font, r.size) > vw + 0.01:
            raise ValueError(
                f"{m.host}: the {r.caption} row of the {m.title} label ({r.value!r}) does "
                f"not fit whole at its {r.size:g} pt")
        size = r.size or cell.fitted_size(r.value, font, largest, vw, min_size=MIN_SIZE)
        if r.mono and cell.width(r.value, font, size) > vw + 0.01:
            raise ValueError(
                f"{m.host}: the {r.caption} row of the {m.title} label ({r.value!r}) does "
                f"not fit whole at {MIN_SIZE:.1f} pt, and an identifier is never elided")
        baseline = y + size * 0.72
        if r.caption:
            cell.text(vx - (labels.Label.CAPTION_GAP * 0.6), baseline - CAPTION * 0.72,
                      r.caption, labels.SANS, CAPTION, align="right", color=labels.GREY)
        cell.fit(vx, y, r.value, font, size, vw, min_size=MIN_SIZE)
        # under a spec strip, what an extra section clears is the last row as
        # it is set; elsewhere, a row at ROW
        last_h = size * 0.72 if m.specs else ROW * 0.72
        y += pitch

    if m.extra is not None:
        gap = SPEC_EXTRA_GAP if m.specs else EXTRA_GAP
        ey = (y - pitch + last_h + gap if m.rows or m.subtitle
              else y if m.specs else top)
        # under a spec strip the rows run down beside the foot's caption, and
        # so does the room left under them
        bottom = caption_baseline() if m.specs else top + q
        if m.specs and bottom > top + q and cell.width(
                m.ident_caption, labels.SANS, CAPTION) > rx - MICRO_PAD - 0.8 * mm:
            raise ValueError(
                f"{m.host}: the {m.title} label's foot caption {m.ident_caption!r} runs "
                "under the extra section beside it; shorten the caption")
        m.extra(cell, (rx, ey, rw, bottom - ey))

    # --- the foot: caption over the identifier, the whole width ---
    fy = foot_top()
    cell.text(MICRO_PAD, fy, m.ident_caption, labels.SANS, CAPTION, color=labels.GREY)
    iw = w - 2 * MICRO_PAD
    size = cell.fitted_size(m.ident, labels.MONO, IDENT_MAX, iw, min_size=MIN_SIZE)
    if cell.width(m.ident, labels.MONO, size) > iw + 0.01:
        raise ValueError(
            f"{m.host}: the {m.title} label's {m.ident_caption} ({m.ident!r}) does not fit "
            f"whole at {MIN_SIZE:.1f} pt")
    vy = fy + CAPTION * 0.72 + CAP_GAP + (IDENT_MAX - size) * 0.72
    cell.text(MICRO_PAD, vy, m.ident, labels.MONO, size)


def title_room(m: MicroLabel) -> float:
    """The width the header leaves the title: the cell less the mark and
    the icons. A caller whose title must never be elided (a part number)
    checks it fits here at MIN_SIZE."""
    x = MICRO_PAD
    path = labels.artwork(m.mark) if m.mark else None
    if path:
        x += min(MARK_W, HEAD_H / labels.mark_aspect(path)) + 1.0 * mm
    icons = sum(_icon_width(None, i) + ICON_GAP for i in m.icons)
    return MICRO_W - MICRO_PAD - icons - x - (0.5 * mm if m.icons else 0)


def title_fits(m: MicroLabel) -> bool:
    """Whether the title prints whole, at MIN_SIZE or larger."""
    return pdfmetrics.stringWidth(m.title, labels.SANS_BOLD, MIN_SIZE) <= title_room(m)


def _artwork_width(name: str, h: float) -> float:
    path = labels.artwork(name)
    return h / labels.mark_aspect(path) if path else 0.0


# The glyphs that are not square: their width at a height, with their text.
# A glyph a caller registers is square unless it adds itself here too.
WIDTHS: dict[str, Callable[[float, str], float]] = {
    "usb": lambda h, t: 0.7 * _artwork_width("usb.svg", h),
    "ethernet": lambda h, t: h * 0.95,
    # the lettered mast stands in a square; the plain one is narrower
    "antenna": lambda h, t: h if t else h * 0.75,
    "riscv": lambda h, t: isa_width(h),
    "cores": cores_width,
    "wifi": wifi_width,
    "tasmota": lambda h, t: _artwork_width("tasmota.svg", h),
    "memory": lambda h, t: memory_width(h, t),
    "bluetooth": lambda h, t: h * BLUETOOTH_W,
    "xtensa": lambda h, t: xtensa_width(h),
}


def _icon_width(cell: Cell | None, icon: Icon, h: float = HEAD_H) -> float:
    """The width a glyph will take at height `h` (the header's, unless
    said), without drawing it: each is a function of the height and its
    text."""
    fn = WIDTHS.get(icon.name)
    return fn(h, icon.text) if fn else h


def strip_width(specs: Sequence[Icon]) -> float:
    """How wide a spec strip is, gaps and all."""
    return (sum(_icon_width(None, i, SPEC_H) for i in specs)
            + SPEC_ICON_GAP * max(len(specs) - 1, 0))


# --- four to a sticker --------------------------------------------------------

Group = tuple[MicroLabel | None, ...]


def pack(micros: Sequence[MicroLabel], start: int = 0) -> list[Group]:
    """The labels in fours, in order, one group per sticker; `start` quarters
    of the first sticker are left empty (a partly used one)."""
    slots: list[MicroLabel | None] = [None] * start + list(micros)
    slots += [None] * (-len(slots) % PER_STICKER)
    return [tuple(slots[i:i + PER_STICKER]) for i in range(0, len(slots), PER_STICKER)]


def micro_origin(x0: float, y0: float, quarter: int) -> tuple[float, float]:
    """Bottom-left corner of `quarter` (0-3, reading order) of the sticker
    whose bottom-left corner is (x0, y0)."""
    col, row = quarter % MICRO_COLS, quarter // MICRO_COLS
    return x0 + col * MICRO_W, y0 + (MICRO_ROWS - 1 - row) * MICRO_H


def cut_guides(lab: Any) -> None:
    """Dotted lines where the sticker is cut into quarters, drawn in the
    padding between them so no label's ink is crossed."""
    c = lab.c
    c.saveState()
    c.setStrokeColor(GUIDE)
    c.setLineWidth(0.3)
    c.setDash(1, 1.5)
    xm = lab.x0 + MICRO_W
    ym = lab.y0 + MICRO_H
    c.line(xm, lab.y0, xm, lab.y0 + labels.LABEL_H)
    c.line(lab.x0, ym, lab.x0 + labels.LABEL_W, ym)
    c.restoreState()


def draw_quad(lab: Any, group: Group) -> None:
    """One sticker: up to four micro labels and the guides to cut them
    apart. `lab` is the whole sticker's ``labels.Label``."""
    for q, m in enumerate(group):
        if m is not None:
            draw_micro(Cell(lab.c, *micro_origin(lab.x0, lab.y0, q)), m)
    if sum(m is not None for m in group) > 1:
        cut_guides(lab)


def listing(micros: Sequence[MicroLabel], start: int = 0
            ) -> list[tuple[int, int, int, int, str, str]]:
    """(sheet, row, col, quarter, host, title) for every label, 1-based, as
    ``rpi-hwid labels --list`` prints its rows."""
    per_sheet = labels.COLS * labels.ROWS
    out = []
    for i, m in enumerate(micros):
        pos = i + start
        sticker, quarter = divmod(pos, PER_STICKER)
        on_sheet = sticker % per_sheet
        out.append((sticker // per_sheet + 1, on_sheet // labels.COLS + 1,
                    on_sheet % labels.COLS + 1, quarter + 1, m.host, m.listing_title))
    return out


# --- device modules -----------------------------------------------------------
#
# A device that gets micro labels brings its own module, named ``*_micro``
# in this package, with a ``KIND`` (what ``rpi-hwid labels --only`` calls
# it) and ``micro_labels(docs) -> list[MicroLabel]`` over the collected
# documents. They are found by name, so adding one touches no shared file.


def provider_modules() -> list[str]:
    """The names of this package's micro-label device modules."""
    import rpi_hwid

    return sorted(i.name for i in pkgutil.iter_modules(rpi_hwid.__path__)
                  if i.name.endswith("_micro"))


def providers() -> dict[str, Any]:
    """KIND -> module, for every device module."""
    found = {}
    for name in provider_modules():
        mod = importlib.import_module("rpi_hwid." + name)
        found[mod.KIND] = mod
    return found


def kinds() -> tuple[str, ...]:
    return tuple(sorted(providers()))


StickerRow = tuple[str, str, str, Callable[[Any, Group], None], Group]


def sticker_rows(docs: Any, only: set[str]) -> list[StickerRow]:
    """``labels.all_labels`` rows for the micro labels of the kinds in
    `only`: one row per sticker of four, kind "micro", its title every
    label's on it. They come after every whole label, so the quarters of a
    sticker are never left empty between two hosts' whole labels."""
    micros: list[MicroLabel] = []
    for kind, mod in sorted(providers().items()):
        if kind in only:
            micros += mod.micro_labels(docs)
    rows: list[StickerRow] = []
    for group in pack(micros):
        present = [m for m in group if m is not None]
        rows.append((present[0].host, "micro", " | ".join(m.listing_title for m in present),
                     draw_quad, group))
    return rows


def render_micro(micros: Sequence[MicroLabel], out: str | Path | IO[bytes],
                 start: int = 0, outline: bool = False) -> tuple[int, int, int]:
    """Write an A4 PDF of the labels four to a sticker on the L7160 grid;
    returns (labels, stickers, sheets)."""
    labels.register_fonts()
    groups = pack(micros, start)
    target = out if hasattr(out, "write") else str(out)
    c = canvas.Canvas(target, pagesize=A4)
    c.setTitle("Hardware identity micro labels")
    c.setAuthor("rpi-hwid labels")
    per_sheet = labels.COLS * labels.ROWS
    for i, group in enumerate(groups):
        if i and i % per_sheet == 0:
            c.showPage()
        lab = labels.Label(c, *labels.label_origin(i % per_sheet))
        if outline:
            lab.outline()
        draw_quad(lab, group)
    c.showPage()
    c.save()
    return len(micros), len(groups), math.ceil(len(groups) / per_sheet) if groups else 0
