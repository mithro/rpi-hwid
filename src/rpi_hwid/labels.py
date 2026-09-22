"""Print-ready hardware labels from collected probe documents.

    rpi-hwid labels --data data/ --out labels.pdf [--artwork DIR] [--names names.json]

An A4 PDF laid out for 21-per-sheet 63.5 x 38.1 mm address labels (the
Avery L7160 grid: three columns, seven rows, 2.54 mm between columns,
nothing between rows, 7.21 mm side and 15.15 mm top/bottom margins). Print
it at 100 % -- "fit to page" shrinks the grid and every label lands off its
sticker. ``--outline`` draws the sticker edges for a plain-paper alignment
print.

Every label carries only what cannot change: a Pi's revision code, serial
and soldered-down MACs and the HAT it wears; an Orange Pi's SoC serial,
MAC, SoC and device-tree id; an FPGA board's DNA or Digilent serial and the
name derived from it, or, on an ECP5, its configuration flash's uid and the
die's own TraceID; a USB adapter's own MAC; a Tiny Tapeout board's
shuttle, the chip ROM's commit and the demo board's RP2 unique id. Nothing
about where a thing is plugged in, what it is called this month, or which
gateware it happens to be running. Each
identifier someone might need to type is also a QR code, in a monospace
face with a slashed zero where one is installed.

Records come straight from ``rpi-hwid collect`` output (or ``probe --json``
files): one board label per document (a Raspberry Pi or an Orange Pi, the
same layout with the maker's mark and the model decoding swapped, see
``rpi_hwid.boards``), one FPGA label per board the probe found, one Tiny
Tapeout label per demo board, one adapter label per removable USB network
adapter. Artwork: the package ships the Raspberry Pi raspberry, the Orange
Pi orange, the Alphamax, Digilent, SQRL, Great Scott Gadgets and Tiny
Tapeout marks and the public-domain USB trident (see artwork/README.md, each mark
drawn only on its owner's hardware); ``--artwork DIR`` overrides any of
them and may add
``netv2.svg``, and a label whose mark is missing sets the maker's name in
type (or, on a board label, leaves the mark's box empty).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import segno
from reportlab.lib.colors import HexColor, black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from rpi_hwid import boards, tt_boards
from rpi_hwid import names as naming
from rpi_hwid import tinytapeout as tt_data
from rpi_hwid.collect import load_collected
from rpi_hwid.revision import derived_wlan_mac

PACKAGE_ARTWORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artwork")
ARTWORK_DIR = None

# --- the sheet ----------------------------------------------------------------

PAGE_W, PAGE_H = A4
LABEL_W, LABEL_H = 63.5 * mm, 38.1 * mm
COLS, ROWS = 3, 7
MARGIN_X, MARGIN_Y = 7.21 * mm, 15.15 * mm
GAP_X, GAP_Y = 2.54 * mm, 0.0
PAD = 2.5 * mm            # keep ink this far from the die-cut edge

# --- fonts --------------------------------------------------------------------
#
# The identifiers must be unambiguous when read by eye and typed, so they are
# set in a monospace face with a slashed zero if one is installed. Helvetica
# and Courier are the PDF base-14 fallbacks: every viewer and printer has
# them, but Courier's zero is not slashed.

SANS, SANS_BOLD, MONO, MONO_REGULAR = "Helvetica", "Helvetica-Bold", "Courier-Bold", "Courier"

# The one grey for every caption, placeholder and rule. Dark enough not to
# dither into dots on a laser printer, light enough to step back from the
# black values it labels.
GREY = HexColor("#555555")
# A value the probe could not read, said in words: darker than the caption
# beside it, so the row reads as a value in grey and not as a second caption.
NOTE = HexColor("#444444")
CAPTION = 6                # caption size, points, everywhere


def register_fonts():
    global MONO, MONO_REGULAR
    dejavu = "/usr/share/fonts/truetype/dejavu/"
    if os.path.exists(dejavu + "DejaVuSansMono-Bold.ttf"):
        pdfmetrics.registerFont(TTFont("LabelMono", dejavu + "DejaVuSansMono-Bold.ttf"))
        MONO = "LabelMono"
    if os.path.exists(dejavu + "DejaVuSansMono.ttf"):
        pdfmetrics.registerFont(TTFont("LabelMonoRegular", dejavu + "DejaVuSansMono.ttf"))
        MONO_REGULAR = "LabelMonoRegular"


# --- drawing helpers ----------------------------------------------------------


class Label:
    """A drawing context whose origin is the label's top-left corner, with y
    growing downwards, because that is how one thinks about a label."""

    def __init__(self, c, x0, y0):
        self.c, self.x0, self.y0 = c, x0, y0

    def pt(self, x, y):
        return self.x0 + x, self.y0 + LABEL_H - y

    def text(self, x, y, s, font=SANS, size=8, align="left", color=black):
        """Draw `s` with its cap-height top at y."""
        c = self.c
        c.setFont(font, size)
        c.setFillColor(color)
        px, py = self.pt(x, y + size * 0.72)
        if align == "right":
            c.drawRightString(px, py, s)
        elif align == "centre":
            c.drawCentredString(px, py, s)
        else:
            c.drawString(px, py, s)
        c.setFillColor(black)
        return size * 1.0

    def width(self, s, font, size):
        return pdfmetrics.stringWidth(s, font, size)

    def fitted_size(self, s, font, size, max_w, min_size=5.5):
        """The largest size down from `size` at which `s` fits `max_w`."""
        while size > min_size and self.width(s, font, size) > max_w:
            size -= 0.25
        return size

    def fit(self, x, y, s, font, size, max_w, min_size=5.5, color=black):
        """`text`, but shrink the type until `s` fits inside `max_w`; a
        string that still does not fit at `min_size` is cut with an
        ellipsis rather than run off the label."""
        size = self.fitted_size(s, font, size, max_w, min_size)
        while len(s) > 1 and self.width(s, font, size) > max_w:
            s = s[:-2].rstrip() + "…"
        return self.text(x, y, s, font, size, color=color)

    CAPTION_GAP = 1.2 * mm

    def captioned(self, x_cap, x_val, y, cap, val, val_font, val_size,
                  max_w, cap_size=CAPTION, min_size=5.5, color=black):
        """A grey caption and its value on one shared baseline; `y` is the
        cap-height top of the value. The caption is set flush against the
        value, right-aligned just left of `x_val`, so the pair reads as one
        thing; `x_cap` is the left limit of the space it may use. Returns the
        size the value ended at."""
        size = self.fitted_size(val, val_font, val_size, max_w, min_size)
        baseline = y + size * 0.72
        cap_x = max(x_val - self.CAPTION_GAP, x_cap + self.width(cap, SANS, cap_size))
        self.text(cap_x, baseline - cap_size * 0.72, cap, SANS, cap_size,
                  align="right", color=GREY)
        self.fit(x_val, y, val, val_font, size, max_w, min_size, color=color)
        return size

    def captioned_after(self, x_left, x_cap, y, cap, val, val_font, val_size,
                        min_size=5.5, color=black):
        """A value set flush right with its grey caption after it: the caption
        starts at `x_cap`, so captions of different lengths still line up in
        one column, and the value ends just before it -- shrinking and then
        eliding until it fits after `x_left`. `y` is the value's cap-height
        top; the two share a baseline."""
        x_right = x_cap - self.CAPTION_GAP
        room = x_right - x_left
        size = self.fitted_size(val, val_font, val_size, room, min_size)
        while len(val) > 1 and self.width(val, val_font, size) > room:
            val = val[:-2].rstrip() + "…"
        self.text(x_right, y, val, val_font, size, align="right", color=color)
        self.text(x_cap, y + (size - CAPTION) * 0.72, cap, SANS, CAPTION, color=GREY)
        return size

    def no_code(self, x, y, size, text="no id"):
        """What stands where a code would go when there is nothing to encode:
        a grey square the code's size, saying so."""
        c = self.c
        px, py = self.pt(x, y + size)
        c.setStrokeColor(GREY)
        c.setLineWidth(0.5)
        c.rect(px, py, size, size, stroke=1, fill=0)
        c.setStrokeColor(black)
        self.text(x + size / 2, y + (size - CAPTION * 0.72) / 2, text, SANS, CAPTION,
                  align="centre", color=GREY)

    def rule(self, x, y, w):
        """A thin grey line, for something to be written in by hand."""
        c = self.c
        px, py = self.pt(x, y)
        c.setStrokeColor(GREY)
        c.setLineWidth(0.5)
        c.line(px, py, px + w, py)
        c.setStrokeColor(black)

    def rotated(self, x, y, s, font, size, color=black):
        """Draw `s` reading upwards, its baseline's left end at (x, y) where
        y is the bottom of the run; used for text along a label's edge."""
        c = self.c
        px, py = self.pt(x, y)
        c.saveState()
        c.translate(px + size * 0.72, py)
        c.rotate(90)
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawString(0, 0, s)
        c.restoreState()

    def qr(self, x, y, size, content, error="m"):
        """A QR code whose top-left is (x, y), `size` points square. Error
        level M unless told otherwise, and no drawn quiet zone: the label
        stock is white, and the caller keeps the surrounding area clear."""
        code = segno.make(content, error=error)
        matrix = list(code.matrix)
        n = len(matrix)
        module = size / n
        c = self.c
        c.setFillColor(black)
        for r, row in enumerate(matrix):
            for col, v in enumerate(row):
                if v:
                    px, py = self.pt(x + col * module, y + (r + 1) * module)
                    c.rect(px, py, module, module, stroke=0, fill=1)

    def svg(self, path, x, y, height):
        """Place an SVG scaled to `height` with its top-left at (x, y);
        returns the width it took."""
        from reportlab.graphics import renderPDF
        from svglib.svglib import svg2rlg
        d = svg2rlg(path)
        s = height / d.height
        d.width, d.height = d.width * s, d.height * s
        d.scale(s, s)
        px, py = self.pt(x, y + height)
        renderPDF.draw(d, self.c, px, py)
        return d.width

    @staticmethod
    def svg_aspect(path):
        """height / width of an SVG, for sizing it to a column."""
        from svglib.svglib import svg2rlg
        d = svg2rlg(path)
        return d.height / d.width

    def outline(self):
        c = self.c
        c.setStrokeColor(HexColor("#bbbbbb"))
        c.setLineWidth(0.3)
        c.rect(self.x0, self.y0, LABEL_W, LABEL_H, stroke=1, fill=0)
        c.setStrokeColor(black)


# --- marks --------------------------------------------------------------------
#
# The Raspberry Pi raspberry, the Orange Pi orange, the Alphamax and
# Digilent marks and the USB trident are real artwork, shipped in artwork/
# (see artwork/README.md for where each came from). There is no vector NeTV2 logo to be had, so that
# mark is drawn here: drop a `netv2.svg` into the --artwork directory and it
# will be used instead. The Wi-Fi arcs are drawn too, which is simpler than
# tracking a licence for a three-arc glyph.


def artwork(name):
    """A mark's file: the caller's artwork directory first, then the
    package's own. Returns None when neither has it."""
    for d in ([ARTWORK_DIR] if ARTWORK_DIR else []) + [PACKAGE_ARTWORK]:
        path = os.path.join(d, name)
        if os.path.exists(path):
            return path
    return None


def mark_netv2(lab, x, y, height):
    path = artwork("netv2.svg")
    return lab.svg(path, x, y, height) if path else 0


def raster(lab, path, x, y, height):
    """A raster image scaled to `height` with its top-left at (x, y); the
    width it took."""
    from PIL import Image
    w0, h0 = Image.open(path).size
    width = height * w0 / h0
    px, py = lab.pt(x, y + height)
    lab.c.drawImage(path, px, py, width, height, mask="auto")
    return width


def mark_raster(lab, name, x, y, height):
    """A raster mark from the artwork directory, scaled to `height`; the
    width it took, or 0 when the file is absent."""
    path = artwork(name)
    return raster(lab, path, x, y, height) if path else 0


def mark_file(name):
    """The artwork file for a bare mark name, whichever form it ships in."""
    for ext in (".svg", ".png"):
        path = artwork(name + ext)
        if path:
            return path
    return None


def marks_widths(names, height, gap=1.2 * mm):
    """[file] for the marks that exist and the width the row of them takes.
    Every mark gets the same `height` x `height` cell, so a row reads as one
    set however the individual marks are shaped. A mark whose file is
    absent is skipped rather than leaving a hole."""
    drawn = [p for p in (mark_file(n) for n in names) if p]
    total = height * len(drawn) + gap * max(len(drawn) - 1, 0)
    return drawn, total


def marks_row(lab, names, right, y, height, gap=1.2 * mm):
    """Draw the marks in a row ending flush at `right`, in the order given,
    each as large as fits its square cell and centred in it."""
    drawn, total = marks_widths(names, height, gap)
    at = right - total
    for path in drawn:
        mark_in_box(lab, path, at, y, height, height)
        at += height + gap


def mark_aspect(path):
    """height / width of a mark, SVG or raster."""
    if path.endswith(".svg"):
        return Label.svg_aspect(path)
    from PIL import Image
    w0, h0 = Image.open(path).size
    return h0 / w0


def mark_in_box(lab, path, x, y, box_w, box_h, align="centre"):
    """A mark drawn as large as fits inside the box, centred vertically and
    either centred or flush left across it. Fitting rather than scaling to a
    height is what lets a row or a column of marks keep one geometry however
    the individual marks are shaped."""
    aspect = mark_aspect(path)
    h = min(box_h, box_w * aspect)
    left = x if align == "left" else x + (box_w - h / aspect) / 2
    top = y + (box_h - h) / 2
    if path.endswith(".svg"):
        lab.svg(path, left, top, h)
    else:
        raster(lab, path, left, top, h)


def mark_fitted(lab, name, x, y, box_w, box_h):
    """A mark from the artwork directory drawn as large as fits inside the
    box, flush left and centred vertically, so every board label keeps
    the same geometry whatever shape its maker's mark is. Nothing is drawn
    (and the box stays blank) when the file is absent."""
    path = artwork(name)
    if path:
        mark_in_box(lab, path, x, y, box_w, box_h, align="left")


def mark_alphamax(lab, x, y, height):
    return mark_raster(lab, "alphamax.png", x, y, height)


def mark_digilent(lab, x, y, height):
    return mark_raster(lab, "digilent.png", x, y, height)


def mark_maker(lab, board, x, y, height):
    """The board maker's mark, top-left of the text column; the maker's
    name in type when there is no mark (SQRL, or a removed file)."""
    w = 0
    if board.kind == "netv2":
        w = mark_alphamax(lab, x, y, height)
    elif board.kind == "arty":
        w = mark_digilent(lab, x, y, height)
    elif board.kind == "cynthion":
        w = mark_raster(lab, "great-scott-gadgets.png", x, y, height)
    elif board.kind == "acorn":
        path = artwork("sqrl.svg")
        w = lab.svg(path, x, y, height) if path else 0
    if not w:
        # the maker's name in the caption grey, so it labels the word below
        # rather than competing with it as a second heading
        size = 2.5 * mm / 0.72              # a 2.5 mm cap height
        lab.text(x, y + height - 2.5 * mm, board.maker, SANS, size, color=GREY)
        w = lab.width(board.maker, SANS, size)
    return w


def mark_usb(lab, x, y, height):
    return lab.svg(artwork("usb.svg"), x, y, height)


def mark_tinytapeout(lab, x, y, height):
    """The circular Tiny Tapeout mark (tinytapeout.svg, shipped; Tiny
    Tapeout's mark, see artwork/README.md); nothing if the file has been
    removed."""
    path = artwork("tinytapeout.svg")
    return lab.svg(path, x, y, height) if path else 0


def mark_wifi(lab, x, y, height):
    """Three arcs over a dot, the universal radio glyph."""
    c = lab.c
    cx, cy = lab.pt(x + height / 2, y + height * 0.92)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.circle(cx, cy, height * 0.07, stroke=0, fill=1)
    c.setLineWidth(height * 0.1)
    c.setLineCap(1)
    for r in (0.28, 0.52, 0.76):
        rad = height * r
        c.arc(cx - rad, cy - rad, cx + rad, cy + rad, 45, 90)
    c.setLineWidth(1)
    c.setLineCap(0)
    return height


def mark_fan(lab, x, y, size):
    """Three swept blades in a ring: a fan on the Pi 5's header.

    Drawn in grey at a couple of millimetres, so it reads as a mark in the
    corner rather than competing with the MACs, which are what the label is
    for. Blades are beziers from the hub rather than arcs: at this size an
    arc of uniform width reads as a spiral, and the taper is what makes the
    shape a fan.
    """
    c = lab.c
    cx, cy = lab.pt(x + size / 2, y + size / 2)
    r = size / 2

    def at(radius, degrees):
        a = math.radians(degrees)
        return cx + radius * math.cos(a), cy + radius * math.sin(a)

    c.setFillColor(GREY)
    c.setStrokeColor(GREY)
    c.setLineWidth(size * 0.06)
    c.circle(cx, cy, r, stroke=1, fill=0)
    for base in (90, 210, 330):
        p = c.beginPath()
        p.moveTo(cx, cy)
        p.curveTo(*at(r * 0.50, base + 30), *at(r * 0.80, base + 22), *at(r * 0.78, base))
        p.curveTo(*at(r * 0.76, base - 20), *at(r * 0.40, base - 26), cx, cy)
        c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(black)
    c.setLineWidth(1)


def mark_clock(lab, x, y, size):
    """A dial with two hands: the RTC's backup cell is fitted.

    A clock rather than a battery outline: what the cell buys is the time
    surviving a power cut, and a battery glyph on a board label would read
    as a claim about how the board is powered.
    """
    c = lab.c
    cx, cy = lab.pt(x + size / 2, y + size / 2)
    r = size / 2
    c.setStrokeColor(GREY)
    c.setFillColor(GREY)
    c.setLineWidth(size * 0.08)
    c.circle(cx, cy, r * 0.92, stroke=1, fill=0)
    c.setLineCap(1)
    c.setLineWidth(size * 0.07)
    c.line(cx, cy, cx, cy + r * 0.52)                  # minute hand, to twelve
    c.line(cx, cy, cx + r * 0.40, cy + r * 0.12)       # hour hand, to two
    c.circle(cx, cy, size * 0.05, stroke=0, fill=1)
    c.setLineCap(0)
    c.setFillColor(black)
    c.setLineWidth(1)


def mark_rj45(lab, x, y, height):
    """An 8P8C jack outline: the body, the latch tab, eight contacts."""
    c = lab.c
    w = height * 0.95
    px, py = lab.pt(x, y + height)
    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setLineWidth(height * 0.045)
    c.setLineJoin(1)
    # body with the latch step at the bottom
    p = c.beginPath()
    p.moveTo(px, py + height * 0.35)
    p.lineTo(px, py + height)
    p.lineTo(px + w, py + height)
    p.lineTo(px + w, py + height * 0.35)
    p.lineTo(px + w * 0.82, py + height * 0.35)
    p.lineTo(px + w * 0.82, py + height * 0.15)
    p.lineTo(px + w * 0.68, py + height * 0.15)
    p.lineTo(px + w * 0.68, py)
    p.lineTo(px + w * 0.32, py)
    p.lineTo(px + w * 0.32, py + height * 0.15)
    p.lineTo(px + w * 0.18, py + height * 0.15)
    p.lineTo(px + w * 0.18, py + height * 0.35)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    # the eight contacts along the top edge
    pitch = w * 0.72 / 7
    for i in range(8):
        cx = px + w * 0.14 + i * pitch
        c.rect(cx - w * 0.03, py + height * 0.72, w * 0.06, height * 0.22,
               stroke=0, fill=1)
    c.setLineWidth(1)
    c.setLineJoin(0)
    return w


# --- the four label designs ---------------------------------------------------

def draw_fpga(lab, board):
    """One design for every Artix-7 board. Left: a QR of the board's
    identity, the DNA when it is known and otherwise the Digilent serial,
    inset from the die-cut edge by its own quiet zone (the sticker goes on a
    dark board, so the label's edge is where the white stops). Right: the
    maker's mark above the board's word, then model and die, and for an Arty
    its Digilent serial. Below, the flash: its part and its unique id, set
    flush right against the flash's own small code in the right-hand corner
    -- or a square saying "no id" where the part has none. Bottom, the whole
    width: the board's identifier, the Device DNA or the ECP5 TraceID."""
    dna_size = 15
    dna_h = 5.5 * mm
    # 18 mm rather than 20: the flash block needs the two millimetres more
    # than the code does, and a 25-module symbol is still 0.72 mm a module.
    qr_size = 18 * mm
    qr_inset = 3.5 * mm
    # half a millimetre higher than its inset from the side, which is still
    # four 0.72 mm modules of quiet zone to the die-cut edge, for room between
    # it and the flash rows below
    qr_top = 3 * mm
    ident_str = board.ident or board.serial
    if ident_str:
        lab.qr(qr_inset, qr_top, qr_size, ident_str)

    x = qr_inset + qr_size + 3 * mm
    col_w = LABEL_W - PAD - x
    # The foot, and the flash's own code in the right-hand corner above it.
    # The foot is the identifier alone, across the whole width at full size;
    # the code sits clear of the foot's caption, and the flash rows end
    # against it. Its room is taken whatever goes in it -- a code, or the
    # stand-in for a flash with no unique id -- so every label is the same.
    # 6.5 mm is 21 modules at 0.31 mm for a 64-bit uid (16 hex digits, QR
    # version 1), and 25 at 0.26 mm for the 112- and 128-bit ones.
    foot_y = LABEL_H - PAD - dna_h
    cap_y = foot_y + 0.5 * mm - CAPTION * 0.72 - 0.9 * mm   # the caption over the value
    fq = 6.5 * mm
    fx, fy = LABEL_W - PAD - fq, cap_y - 0.5 * mm - fq
    # a row of the right column low enough to reach the code's square stops
    # short of it
    beside = min(col_w, fx - 1.5 * mm - x)
    y = PAD
    # A wide wordmark (Alphamax) already carries its weight at 5 mm; a compact
    # mark scaled to the same height reads as half the size beside it, so the
    # Digilent triangle and the Great Scott Gadgets gears get 6 mm.
    mark_h = 6 * mm if board.kind in ("arty", "cynthion") else 5 * mm
    mark_maker(lab, board, x, y, mark_h)
    y += mark_h + 0.5 * mm
    # The headline is the derived name where there is one. Without a name it
    # falls back to the model, and for a board known only by its chain that
    # model is "FPGA" -- which printed FPGA twice, once as the headline and
    # again in the row below. The die is the more useful of the two, so it
    # takes the headline and the row stops repeating it.
    if board.name:
        word = board.name.split("-", 1)[1]
    else:
        word = board.part or board.model.split()[0]
    # 20 pt: the largest that leaves an Arty's S/N a clear gap above the flash
    # rows, an Arty being the board with the most in this column
    lab.fit(x, y, word, SANS_BOLD, 20, col_w)
    y += 7.2 * mm
    if board.part and board.part != word:
        lab.fit(x, y, "{}  ·  {}".format(board.model, board.part), SANS, 8, col_w)
    else:
        lab.fit(x, y, board.model, SANS, 8, col_w)
        if board.gateware:
            # what the board's own gateware said, where it said anything. A
            # die that was not read is simply left off: "die not read" is the
            # placeholder this package exists to make unnecessary, and a label
            # announcing what it does not know is worse than one that is quiet.
            y += 3 * mm
            lab.fit(x, y, board.gateware, SANS, CAPTION, beside, color=GREY)
    if board.kind == "arty":
        # the serial is a board-printed identifier: its own row, larger
        # 9 pt on a tighter pitch than it used to have: an Arty is the only
        # board carrying a serial, a flash line and a flash uid at once, and
        # that stack is what decides how much room the foot has left.
        y += 2.7 * mm
        lab.captioned(x, x + 6 * mm, y, "S/N", board.serial, MONO, 9, beside - 6 * mm)


    # The flash block: the same rows, in the same place, on every FPGA label.
    # Every board here has a configuration flash; what differs is only how
    # much of it could be read. The values are set flush right with their
    # captions after them, in one column just before the flash's code, so the
    # three read as one group, and they run left under the board's QR as far
    # as they need:
    # "Micron N25Q128/MT25QL128  ·  16 MiB" does not fit the right column at
    # any size worth printing. Centred on the code, so the rows are
    # at the same height on every label whatever the column above ran to.
    cap_x = fx - 1.5 * mm - max(lab.width(c, SANS, CAPTION) for c in ("flash", "uid"))
    row = 7 * 0.72                        # a 7 pt row's cap height, in points
    pitch = 2.3 * mm
    flash_y = fy + (fq - pitch - row) / 2   # the two rows' middle is the code's
    uid_y = flash_y + pitch
    lab.captioned_after(PAD, cap_x, flash_y, "flash", board.flash, SANS, 7, min_size=5)
    if board.flash_uid:
        # mono, like every other identifier here: it is a number someone may
        # have to read off the sticker and type
        lab.captioned_after(PAD, cap_x, uid_y, "uid", board.flash_uid, MONO_REGULAR, 7,
                            min_size=5)
    # A part with no unique id gets no uid row: the "no id" square where its
    # code would be says so, and the chip's own reason (a Macronix's security
    # register) stays in the document, off a 38 mm label.

    # The flash's own code: its unique id, the identity of the chip and not of
    # the board, which is why it is a second code and not part of the big one.
    # A flash with no unique id gets a marked square saying so rather than a
    # code: its JEDEC id once went here, and that is the same on every chip of
    # the family -- a code that looks like an identifier and identifies nothing
    # -- while an empty corner would read as something missing.
    if board.flash_uid:
        lab.qr(fx, fy, fq, board.flash_uid, error="l")
    elif board.flash:
        lab.no_code(fx, fy, fq)

    # The foot always carries a value: a board with none never reaches here,
    # because all_labels refuses it. There used to be a rule to write the
    # digits on by hand, which defeated the point of the package -- a
    # hand-copied Device DNA is exactly the error-prone step it exists to
    # remove, and a sticker with a blank on it still gets stuck to a board.
    lab.text(PAD, cap_y, board.ident_caption, SANS, CAPTION, color=GREY)
    lab.fit(PAD, foot_y + 0.5 * mm, board.ident, MONO, dna_size, LABEL_W - 2 * PAD)


# The header band is the raspberry's height at the QR width that the rest
# of the layout leaves: its aspect (height / width, as svglib measures the
# shipped SVG) is fixed here so the bands are the same on every board
# label, whatever mark sits in the box and even when there is none.
MARK_ASPECT = 262.5 / 205.554


def draw_board(lab, b):
    """One design for every single-board computer, Raspberry Pi or Orange
    Pi: the same bands at the same heights, so the eye finds each fact in
    the same place on every board. Up the left edge: a small QR of the
    serial at the top with the serial reading up to it, a cross-check
    rather than the identity people use (a 16-character serial is 16 bytes,
    which the smallest QR holds at error level L; a Code 128 of it cannot
    reach a printable bar width in a 38 mm spine). Then two columns: the
    maker's mark, the HAT and uuid captions and the two MAC QRs on the
    left, all as wide as a QR; the title, subtitle, HAT line, uuid and MACs
    on the right, sharing one left edge. The MACs are what people look for,
    so they are the largest thing on the label. A row whose MAC is not
    known says why, in grey; so does the header row on a board that has no
    HAT convention to probe."""

    # --- the spine: the serial's QR, the serial, its caption ---
    # The 16 digits are set as two columns of eight reading up, so they can
    # have a 2.5 mm cap height in the room under the QR (one line of 16 at
    # that size would be longer than the label). The first half is the
    # left column, as rotated lines stack to the right.
    ser_qr = 6.5 * mm                  # 21 modules at 0.31 mm
    ser_size = 10
    lab.qr(PAD, PAD, ser_qr, b.serial, error="l")
    half = (len(b.serial) + 1) // 2
    col_pitch = ser_size * 0.72 + 0.7 * mm
    lab.rotated(PAD, LABEL_H - PAD, b.serial[:half], MONO, ser_size)
    lab.rotated(PAD + col_pitch, LABEL_H - PAD, b.serial[half:], MONO, ser_size)
    ser_len = max(lab.width(b.serial[:half], MONO, ser_size),
                  lab.width(b.serial[half:], MONO, ser_size))
    lab.rotated(PAD, LABEL_H - PAD - ser_len - 1 * mm, "serial", SANS, CAPTION, color=GREY)
    x = PAD + ser_qr + 1.5 * mm

    # --- two columns ---
    title_h = 8.2 * mm                 # title over subtitle
    hat_rows = 3.2 * mm + 3.4 * mm
    qr_gap = 2 * mm                    # quiet zone between the stacked QRs
    # the header band is the mark's box, then the HAT rows, then the two
    # QR rows: qr * aspect + 0.8 + hat_rows + 0.5 + qr + qr_gap + qr = usable
    usable = LABEL_H - 2 * PAD
    rest = usable - 0.8 * mm - hat_rows - 0.5 * mm - qr_gap
    qr = min(rest / (MARK_ASPECT + 2), (rest - title_h) / 2)
    logo_h = qr * MARK_ASPECT
    tx = x + qr + 1.5 * mm
    col_w = LABEL_W - PAD - tx
    mark_fitted(lab, b.mark, x, PAD, qr, logo_h)
    y = PAD + max(0, (logo_h - title_h) / 2)

    # What the board is wearing that is not a HAT and has no MAC: a fan on
    # the header, a cell behind the RTC. Both are Pi 5 signals and both are
    # None on every other model, so nothing is drawn where nothing could
    # have been read. Up in the corner beside the title, small and grey:
    # worth knowing when the board is in your hand, never worth crowding
    # out an identifier.
    icons = [m for m, on in ((mark_fan, b.fan), (mark_clock, b.rtc_battery)) if on]
    icon, icon_gap = 2.6 * mm, 1.1 * mm
    icons_w = len(icons) * icon + max(0, len(icons) - 1) * icon_gap
    ix = LABEL_W - PAD - icons_w
    for draw in icons:
        draw(lab, ix, y + 0.2 * mm, icon)
        ix += icon + icon_gap

    # the title gives up the room the icons take, rather than running under
    # them: lab.fit shrinks and then ellipsises, so a long name degrades
    # gracefully instead of colliding.
    title_w = col_w - (icons_w + 1.5 * mm if icons else 0)
    lab.fit(tx, y, b.title, SANS_BOLD, 11, title_w)
    lab.fit(tx, y + 4.6 * mm, b.subtitle, SANS, 6.5, col_w)

    # HAT band: the HAT line, then the uuid line centred in the rest of the
    # band (regular weight: bold mono at 6 pt fills in under toner). Every
    # board with a 40-pin header gets the same band: a HAT does not know
    # what it is plugged into, and the probe reads an Orange Pi's header
    # the same way it reads a Pi's, so the two labels say the same thing.
    y = PAD + max(logo_h, title_h) + 0.8 * mm
    band_top = y
    if b.header:
        lab.captioned(x, tx, y, "HAT", "; ".join(b.header), SANS, 7, col_w)
    else:
        lab.captioned(x, tx, y, "HAT", "none", SANS, 7, col_w)
    y += 3.2 * mm
    if b.hat_uuid:
        uuid_y = y + (3.4 * mm - 6.5 * 0.72) / 2
        lab.captioned(x, tx, uuid_y, "uuid", b.hat_uuid, MONO_REGULAR, 6.5, col_w,
                      min_size=5)
    y = band_top + hat_rows

    # MAC bands: eth then wlan, always both, fixed height
    macs = dict(b.macs)
    if "eth" not in macs:
        macs["eth"] = None
    if "wlan" not in macs:
        macs["wlan"] = None
    reasons = {"eth": b.eth_note or "not read", "wlan": b.wlan_note or "not read"}
    cap_gap = 0.7 * mm
    mac_size = lab.fitted_size("00:00:00:00:00:00", MONO, 13, col_w)
    block = CAPTION * 0.72 + cap_gap + mac_size * 0.72
    for kind in ("eth", "wlan"):
        mac = macs[kind]
        text, font, size, colour = ((mac, MONO, mac_size, black) if mac
                                    else (reasons[kind], SANS, 8, NOTE))
        size = lab.fitted_size(text, font, size, col_w)
        cy = y + 0.5 * mm + (qr - block) / 2
        lab.text(tx, cy, kind + " MAC", SANS, CAPTION, color=GREY)
        cy += CAPTION * 0.72 + cap_gap
        if mac:
            lab.qr(x, y + 0.5 * mm, qr, mac)
        lab.text(tx, cy, text, font, size, color=colour)
        y += qr + qr_gap


def draw_usb(lab, dev):
    """Bus and link glyphs with the chip name across the top, the facts on
    the left with the MAC's QR on the right, and the MAC across the whole
    width at the bottom under its own caption."""
    x, y = PAD, PAD
    h = 5 * mm
    w = mark_usb(lab, x, y + h * 0.15, h * 0.7)
    w += 1.5 * mm
    if dev.kind == "wifi":
        w += mark_wifi(lab, x + w, y, h)
    else:
        w += mark_rj45(lab, x + w, y, h)
    tx = x + w + 2.5 * mm
    # the title's cap height centred on the glyphs' band
    title_size = 12
    lab.fit(tx, y + (h - title_size * 0.72) / 2, dev.title, SANS_BOLD, title_size,
            LABEL_W - PAD - tx)
    band_top = y + h + 2 * mm

    # the MAC block at the foot: caption over the value
    mac_size = 16
    cap_gap = 0.7 * mm
    mac_h = CAPTION * 0.72 + cap_gap + mac_size * 0.72
    band_bottom = LABEL_H - PAD - mac_h - 1.5 * mm
    qr = band_bottom - band_top
    qx = LABEL_W - PAD - qr
    lab.qr(qx, band_top, qr, dev.mac)
    val_x = x + 8.5 * mm
    val_w = qx - 2 * mm - val_x

    # the facts, on a tight pitch so they read as one block, centred on the QR
    lines = [(k, v) for k, v in dev.lines if v]
    val_size = 9
    pitch = 4.5 * mm
    block = (len(lines) - 1) * pitch + val_size * 0.72
    ly = band_top + max(0, (qr - block) / 2)
    for k, v in lines:
        font = MONO if k == "VID:PID" else SANS
        if k:
            lab.captioned(x, val_x, ly, k, v, font, val_size, val_w)
        else:
            lab.fit(val_x, ly, v, font, val_size, val_w)
        ly += pitch

    y = LABEL_H - PAD - mac_h
    lab.text(PAD, y, "MAC", SANS, CAPTION, color=GREY)
    lab.fit(PAD, y + CAPTION * 0.72 + cap_gap, dev.mac, MONO, mac_size, LABEL_W - 2 * PAD)


def swatch(lab, x, y, w, h, mask, silk=None, label=""):
    """A sample of a board: a keyline box filled with the soldermask
    colour and lettered, like the board itself, in the silkscreen colour.
    A colour that is not recorded leaves the box empty and struck through,
    and the lettering falls back to grey. The keyline matters -- a white
    soldermask is a white box on white stock. A newline in `label` breaks
    it across lines, which a two-word name needs: set on one line it has
    to shrink to the box's width, and the box is wider than it is tall."""
    c = lab.c
    px, py = lab.pt(x, y + h)
    c.setStrokeColor(GREY)
    c.setLineWidth(0.4)
    if mask:
        c.setFillColor(HexColor(mask))
        c.rect(px, py, w, h, stroke=1, fill=1)
    else:
        c.rect(px, py, w, h, stroke=1, fill=0)
        c.line(px, py, px + w, py + h)      # struck through: nothing recorded
    c.setStrokeColor(black)
    c.setFillColor(black)
    if label:
        lines = label.split("\n")
        size = min(lab.fitted_size(s, SANS_BOLD, 6, w - 1 * mm, min_size=4.5) for s in lines)
        lead = size * 0.95
        block = (len(lines) - 1) * lead + size * 0.72
        top = y + (h - block) / 2
        for i, s in enumerate(lines):
            lab.text(x + w / 2, top + i * lead, s, SANS_BOLD, size,
                     align="centre", color=HexColor(silk) if silk else GREY)


def draw_tinytapeout(lab, tt):
    """Left: the Tiny Tapeout mark beside the shuttle as the headline, the
    chip's kind and PDK under it, then the demo board and the ROM commit as
    captioned rows, then a sample of each board -- the chip carrier and
    the demo board -- filled with its soldermask and lettered in its
    silkscreen, with both colours named beside it, so the right board is
    picked out of a drawer. Right: a QR that opens the chip's
    page on tinytapeout.com. Foot: the demo board's RP2 unique id, its USB
    serial, the one thing on it that cannot change, set like the FPGA
    label's DNA, with its own small QR at the right end of the row."""
    # --- the chip page QR, its caption ---
    qr_size = 17 * mm
    qx = LABEL_W - PAD - qr_size
    lab.qr(qx, PAD, qr_size, tt.url)
    lab.text(qx + qr_size / 2, PAD + qr_size + 0.4 * mm, "chip page", SANS, CAPTION,
             align="centre", color=GREY)

    # --- the foot: the id, caption over it, its QR at the row's right end ---
    # The QR sits in the die-cut corner (PAD = 2.5 mm quiet zone below and
    # right), the id is fitted 2 mm short of it, and the "id" caption is
    # the only thing near it.
    id_size = 15
    id_h = 6 * mm
    id_y = LABEL_H - PAD - id_h
    id_qr = 6.5 * mm                   # 21 modules at 0.31 mm, like the Pi serial's
    id_w = LABEL_W - 2 * PAD
    cap_y = id_y + 0.5 * mm - CAPTION * 0.72 - 0.9 * mm
    lab.text(PAD, cap_y, "%s id (USB serial)" % (tt.mcu or "RP2"), SANS, CAPTION, color=GREY)
    if tt.usb_serial:
        qr_x, qr_y = LABEL_W - PAD - id_qr, LABEL_H - PAD - id_qr
        lab.qr(qr_x, qr_y, id_qr, tt.usb_serial, error="l")
        lab.text(qr_x + id_qr / 2, qr_y - 0.4 * mm - CAPTION * 0.72, "id", SANS, CAPTION,
                 align="centre", color=GREY)
        id_w -= id_qr + 2 * mm
        lab.fit(PAD, id_y + 0.5 * mm, tt.usb_serial, MONO, id_size, id_w)
    else:
        lab.rule(PAD, id_y + 0.5 * mm + id_size * 0.72 + 0.3 * mm, id_w)

    # --- left column, 3 mm clear of the chip page QR ---
    x, y = PAD, PAD
    col_w = qx - 3 * mm - x
    head_size = 22
    head_cap = head_size * 0.72
    mark_h = 6 * mm
    w = mark_tinytapeout(lab, x, y + (head_cap - mark_h) / 2, mark_h)
    tx = x + (w + 1.5 * mm if w else 0)
    lab.fit(tx, y, tt.headline, SANS_BOLD, head_size, col_w - (tx - x))
    y += head_cap + 1.2 * mm
    # The subtitle line, with the marks of whoever ran the shuttle and whose
    # silicon it is at its right edge: an Efabless chipIgnite run on
    # SkyWater, a wafer.space run on GlobalFoundries, IHP's own. They shrink
    # rather than crowd a long subtitle, and drop out below 2 mm.
    lab.text(x, y, tt.subtitle, SANS, 6.5)          # fixed size, like the Pi subtitle
    if tt.marks:
        free = col_w - lab.width(tt.subtitle, SANS, 6.5) - 2 * mm
        mh = 3.4 * mm
        while mh > 2 * mm and marks_widths(tt.marks, mh)[1] > free:
            mh -= 0.2 * mm
        if marks_widths(tt.marks, mh)[1] <= free:
            marks_row(lab, tt.marks, x + col_w, y - 0.4 * mm, mh)
    row = 3.5 * mm
    y += row
    cap_w = 14 * mm
    lab.captioned(x, x + cap_w, y, "board", tt.demoboard_text, SANS, 7.5, col_w - cap_w)
    y += row
    if tt.commit:
        lab.captioned(x, x + cap_w, y, "ROM commit", tt.commit, MONO, 7.5, col_w - cap_w)
    elif tt.shuttle:
        # the ROM answered, it just holds no commit: TT03p5's carries the
        # shuttle name and nothing else, and the FPGA breakout has no ROM
        lab.captioned(x, x + cap_w, y, "ROM", "no commit", SANS, 7.5, col_w - cap_w)
    else:
        lab.captioned(x, x + cap_w, y, "ROM", "not read", SANS, 7.5, col_w - cap_w)
    y += row

    # The colours, so a board can be matched to its label across the bench.
    # Each box is a sample of the board it names: filled with that board's
    # soldermask and lettered in its silkscreen, the way the board itself
    # is. Beside it the two colours in words, soldermask over silkscreen,
    # for the reader whose eye the print cannot be trusted by.
    sw, sh = 11 * mm, 5.5 * mm
    line = CAPTION * 0.72 + 0.5 * mm
    for i, (cap, mask, silk, mask_name, silk_name) in enumerate((
            ("carrier", tt.chip_colour, tt.chip_silk,
             tt.chip_colour_name, tt.chip_silk_name),
            ("demo\nboard", tt.demoboard_colour, tt.demoboard_silk,
             tt.demoboard_colour_name, tt.demoboard_silk_name))):
        sx = x + i * (col_w / 2)
        swatch(lab, sx, y, sw, sh, mask, silk, cap)
        tx2 = sx + sw + 1 * mm
        tw = col_w / 2 - sw - 1.5 * mm
        if mask_name or silk_name:
            ty = y + (sh - 2 * line + 0.5 * mm) / 2
            lab.fit(tx2, ty, mask_name or "?", SANS, CAPTION, tw, min_size=4.5)
            lab.fit(tx2, ty + line, silk_name or "?", SANS, CAPTION, tw, min_size=4.5,
                    color=GREY)
        else:
            lab.fit(tx2, y + (sh - CAPTION * 0.72) / 2, "unknown", SANS, CAPTION,
                    tw, min_size=4.5, color=GREY)



# --- records from probe documents --------------------------------------------

USB_SPEED = {"12": "FS 12 Mbit/s", "480": "HS 480 Mbit/s", "5000": "SS 5 Gbit/s",
             "10000": "SS+ 10 Gbit/s", "20000": "SS+ 20 Gbit/s"}
BOARD_MODEL = {"netv2": ("Alphamax", "NeTV2"), "arty": ("Digilent", "Arty A7"),
               "acorn": ("SQRL", "Acorn"), "jtag": ("", "FPGA"),
               # no maker: the gateware is known, the board under it is not
               "pcileech": ("", "PCILeech FPGA"),
               # named by its chain alone: the die is printed beside it
               "unknown-fpga": ("", "FPGA"),
               "cynthion": ("Great Scott Gadgets", "Cynthion")}

# Which Acorn a die means. Both variants are SQRL cards on the same P1
# harness, and once a board runs gateware of its own its PCIe id describes
# that gateware rather than the card, so the die is what still tells a
# CLE-215+ from a CLE-101 (the LiteFury). Measured: pi-sw2-p48 is XC7A200T,
# ps1's blades are XC7A100T.
ACORN_MODEL = {"XC7A200T": "Acorn CLE-215+", "XC7A100T": "Acorn CLE-101"}
# ...and what the board says about itself, which beats inferring from the die:
# the SoC's ident string names the card its image was built for.
ACORN_SOC_MODEL = {"cle-215+": "Acorn CLE-215+", "cle-101": "Acorn CLE-101"}

# The ECP5 each Cynthion revision carries, from that revision's platform file
# in the cynthion package (`device` in cynthion/gateware/platform/*.py). Every
# board is an LFE5U-12F but r0.7, and listing them rather than defaulting means
# an unrecognised revision prints no part instead of a plausible wrong one --
# the same rule idcode_part follows.
CYNTHION_PART = {
    "0.1": "LFE5U-12F", "0.2": "LFE5U-12F", "0.3": "LFE5U-12F", "0.4": "LFE5U-12F",
    "0.5": "LFE5U-12F", "0.6": "LFE5U-12F", "0.7": "LFE5U-25F", "1.0": "LFE5U-12F",
    "1.1": "LFE5U-12F", "1.2": "LFE5U-12F", "1.3": "LFE5U-12F", "1.4": "LFE5U-12F",
}

# The configuration flash each Cynthion revision carries, from that
# revision's own published bill of materials. A Cynthion's flash cannot be
# identified the way a Xilinx board's is -- openFPGALoader has no path to it,
# and the chip's pins belong to the ECP5's configuration bank -- but the
# board is open hardware and says what is on it. This is the same standing as
# CYNTHION_PART, which prints the die from the revision rather than from a
# read, and it is only the flash's *type*: its unique id is read off the chip
# itself by the gateware and published as the USB serial.
#
# r1.4: U7, "W25Q32JVSS ... IC FLASH 32M SPI 133MHZ 8SOIC, Winbond,
# W25Q32JVSSIQ" in cynthion-bom.csv of the r1.4.0 hardware release
# (github.com/greatscottgadgets/cynthion-hardware, fetched 2026-09-22).
# 32 Mbit is 4 MiB. Listing revisions rather than defaulting means an
# unrecognised one prints nothing -- and so is refused a label -- instead of
# a plausible wrong part.
#
# Each entry also carries the JEDEC id its part answers, so a board whose
# flash has been asked over background SPI can be checked against its BOM.
# The id alone says only W25Q32xx; the BOM says which. Where the two agree the
# BOM's name prints, so a board does not change its label by being read; where
# they differ the board has been reworked, and the chip's answer prints. The
# id is from Winbond's W25Q32JV datasheet (rev G, table 8.1.1:
# "W25Q32JV-IQ/JQ 15h 4016h"; the -IM/JM parts answer 7016h instead).
CYNTHION_FLASH = {
    "1.4": ("0xef4016", "Winbond W25Q32JV  ·  4 MiB"),
}


def cynthion_flash(hw_rev, jedec):
    """The flash line for a Cynthion of revision `hw_rev` whose flash
    answered `jedec`, or None for a revision whose BOM is not listed and
    whose flash nobody asked."""
    read = flash_text(flash_from_jedec(jedec))
    bom = CYNTHION_FLASH.get(hw_rev or "")
    if not bom:
        return read
    bom_jedec, bom_text = bom
    if read and flash_from_jedec(jedec)["jedec"] != bom_jedec:
        return read
    return bom_text

# The foot of an FPGA label prints the identifier the sticker is keyed on. For
# the Xilinx boards that is the Device DNA; an ECP5 has no such thing, and
# printing "Device DNA" over a configuration flash's id would be a plain lie
# about which chip was read.
DNA_CAPTION = "Device DNA"
CYNTHION_IDENT_CAPTION = "ECP5 config flash UID"
# An ECP5's die identifier sits where a Xilinx part's Device DNA sits: it is
# the same thing -- the number burned into the die -- so it takes the same
# place on the label and the same QR.
TRACE_ID_CAPTION = "ECP5 TraceID"

# What reads each board's identifier, so a board that arrives without one can
# be told where to go and what to run rather than merely refused.
IDENT_READ_WITH = {
    "netv2": "rpi-hwid fpga --jtag",
    "arty": "rpi-hwid fpga --jtag",
    "acorn": "rpi-hwid fpga --jtag",
    "jtag": "rpi-hwid fpga --jtag",
    "unknown-fpga": "rpi-hwid fpga --jtag",
    "cynthion": "rpi-hwid fpga --force-offline",
}

# ...and what reads its configuration flash. Every one of these reconfigures
# the FPGA: on a Xilinx part the flash hangs off the configuration bank, so
# the only way to drive those pins over JTAG is a design in the fabric, and
# openFPGALoader loads its spiOverJtag bridge and then resets the device to
# boot from flash again. There is no cheaper path, not even for the bare
# three-byte id (openfpgaloader-36, from the source, 2026-09-22).
FLASH_READ_WITH = {
    "netv2": "rpi-hwid fpga --jtag --flash",
    "arty": "rpi-hwid fpga --jtag --flash",
    "acorn": "rpi-hwid fpga --jtag --flash --pins=10:9:11:8",
    "cynthion": "rpi-hwid fpga --force-offline",
}


class IdentifierNotReadError(Exception):
    """A board reached the label generator without the identifier its sticker
    exists to carry.

    Fatal, and deliberately so. This package exists precisely so that nobody
    transcribes a Device DNA or a flash uid by hand, so a label rendered with
    the value missing -- or worse, with a rule to write it on -- is a sticker
    that gets printed, peeled and stuck to real hardware still missing the one
    thing it was for. Generation time is the last moment the missing read is
    still cheap.
    """
# Artix-7 dies by JTAG idcode, keyed on the number with its top four bits
# masked off. Those bits are the silicon revision, not the part, and the two
# tools spell the same number differently: openFPGALoader prints 0x362d093,
# openocd pads it to 0x0362d093. Keyed on strings, as this table once was,
# every board read by openocd was labelled "die not read" with its idcode in
# hand, and each revision met in the wild needed its own entry added.
IDCODE_REVISION_MASK = 0x0FFFFFFF
IDCODE_PART = {0x362D093: "XC7A35T", 0x3632093: "XC7A75T", 0x3631093: "XC7A100T",
               0x3636093: "XC7A200T"}


# SPI flash, the one component every FPGA board here has and none of them
# reports the same way. A JEDEC id is three bytes -- manufacturer, memory
# type, capacity -- and the capacity byte is a power of two, so the density
# falls out of any id at all. Only the part number needs a table.
JEDEC_VENDOR = {
    0x01: "Spansion", 0x1F: "Atmel", 0x20: "Micron", 0x9D: "ISSI",
    0xBF: "SST", 0xC2: "Macronix", 0xC8: "GigaDevice", 0xEF: "Winbond",
}
#
# Every id here is answered by more than one part, so each names the family
# they share, with the letters the id cannot settle written as x. That is a
# name a person can read and search for; the id's hex is not, and one of the
# parts would be a part number nobody read. Which parts share an id is taken
# from flashrom's include/flashchips.h, which lists them beside each id.
JEDEC_PART = {
    # MX25L6405, 6405D, 6406E, 6408E, 6436E, 6445E, 6465E, 6473E: the NeTV2's
    0xC22017: "MX25L64xx",
    # S25FL127S, 128P, 128S and 129P. The extended id narrows an FL-S to the
    # 127S or the 128S, and SFDP separates those two: the S25FL127S datasheet
    # (Infineon 001-98282 Rev. *K, 10.2.4) documents RSFDP 5Ah, and the
    # S25FL128S/256S datasheet (002-19099 Rev. *D) has no such command and
    # never mentions SFDP. Not used yet: see SPANSION_FAMILY.
    0x012018: "S25FL12x",
    # S25FL256S, and the 1.8 V S25FS256S: p48's Acorn
    0x010219: "S25Fx256S",
    # Micron's N25Q128 and its second generation, the MT25QL128: pi3's Arty.
    # The extended id tells them apart (MICRON_GENERATION below).
    0x20BA18: "N25Q128/MT25QL128",
    0xEF4016: "W25Q32xx",      # BV, FV and JV-IQ; the Cynthion's
    0xEF4017: "W25Q64xx",      # BV, CV and FV; pi-sw1-p38's PCILeech card
    0xEF4018: "W25Q128xx",     # BV, FV and JV-IQ
    0xEF4019: "W25Q256xx",     # FV and JV-IQ
}


# How each part gives up its unique id -- which is a property of the part and
# not one command. 0x4B is Read Unique ID on a Winbond (four dummy bytes, 64
# bits) and an OTP read taking a three-byte address on a Spansion, whose
# factory 128-bit random number lives in the low 16 bytes of OTP region 0. A
# Micron N25Q carries 112 bits in the extended reply to 0x9F. And a Macronix
# MX25L has no such command at all, which is why a blank beside one is the
# answer rather than a gap: measured on both NeTV2s, 2026-09-21.
FLASH_UID_METHOD = {
    0x01: "otp",            # Spansion / Cypress / Infineon
    0x20: "read-uid",       # Micron, in the extended 0x9F reply
    # Macronix: a factory ESN exists only where one was ordered. The part
    # reports it in its security register (RDSCUR 0x2B, bit 0, factory lock),
    # and both NeTV2s read 0x00 -- so theirs have none, which is measured
    # rather than assumed from the vendor. Macronix AN0218 makes a factory
    # ESN "Special Order" only.
    0xC2: "otp-if-factory-locked",
    0xEF: "read-uid",       # Winbond, 0x4B
}


# Where the three-byte id is shared, the bytes after it can name the part:
# RDID bytes 4-6, which openFPGALoader's flash document reports as
# extended_id for the families that define them.
#
# Micron: a length byte of 10h, then the extended device ID, whose bit 6 is
# "Device Generation: 1 = 2nd generation" in the MT25QL128 datasheet (Rev. I
# 09/16, Table 17) and reserved in the N25Q128's (Rev. M 06/2013, Table 20).
# (first generation, second generation)
MICRON_GENERATION = {0x20BA18: ("N25Q128", "MT25QL128")}
# Spansion S-family: 4Dh, the sector architecture, then the family -- 80h
# FL-S, 81h FS-S -- as Linux's drivers/mtd/spi-nor/spansion.c keys them
# (s25fl256s0/1, s25fs256s0/1, s25fl128s0/1, s25fs128s1). An FL-S at 0x012018
# is an S25FL127S or an S25FL128S, which these bytes do not separate.
SPANSION_FAMILY = {0x010219: {0x80: "S25FL256S", 0x81: "S25FS256S"},
                   0x012018: {0x80: "S25FL12xS", 0x81: "S25FS128S"}}


def extended_part(value, extended):
    """The part an extended id names for JEDEC id `value`, or None when there
    is none, or it is not in the shape this id's family defines."""
    try:
        data = bytes.fromhex(extended[2:]) if extended.startswith("0x") else b""
    except (AttributeError, ValueError):
        return None
    if len(data) != 3:
        return None
    if value in MICRON_GENERATION and data[0] == 0x10:
        return MICRON_GENERATION[value][1 if data[1] & 0x40 else 0]
    if value in SPANSION_FAMILY and data[0] == 0x4D:
        return SPANSION_FAMILY[value].get(data[2])
    return None


def flash_from_jedec(jedec, extended=None):
    """Vendor, part and density from a JEDEC id, as far as each is known --
    the part as precisely as the extended id, where there is one, allows."""
    out = {"vendor": None, "part": None, "size": None, "jedec": None,
           # "unknown" until a part is met: distinct from None, which is this
           # part having no unique id to read at all
           "uid_read_with": "unknown"}
    try:
        value = int(jedec, 16)
    except (TypeError, ValueError):
        return out
    if not value:
        return out
    manufacturer = value >> 16
    if manufacturer in FLASH_UID_METHOD:
        out["uid_read_with"] = FLASH_UID_METHOD[manufacturer]
    out["jedec"] = "0x%06x" % value
    out["vendor"] = JEDEC_VENDOR.get(value >> 16)
    out["part"] = extended_part(value, extended) or JEDEC_PART.get(value)
    capacity = value & 0xFF
    # the third byte is log2 of the part's size in bytes on every vendor here
    if 0x10 <= capacity <= 0x1B:
        out["size"] = "%d MiB" % (1 << (capacity - 20))
    return out


def flash_text(info):
    """The flash block's one line: vendor, then the part where it is known
    and the JEDEC id where it is not, then the density.

    The id's own bytes give the vendor and the density -- a JEP106
    manufacturer code and a capacity byte that is log2 of the size -- so both
    are as measured as the id. The part name is not: it is a database lookup
    on a number several parts can answer to. 0xc22017 is MX25L6405,
    MX25L6406E, MX25L6433F and others; openFPGALoader's database labels it
    MX25L6405 and says as much with `size_source: database`. Printing that
    would put a part number on a sticker that nobody read, so an id whose
    part is not pinned down prints as itself.
    """
    part = info.get("part") or info.get("jedec")
    named = " ".join(x for x in (info.get("vendor"), part) if x)
    parts = [x for x in (named, info.get("size")) if x]
    return "  ·  ".join(parts) if parts else None


def flash_uid_note_text(note):
    """The fact out of a reading tool's note about a unique id, or None.

    openFPGALoader writes the fact and its evidence in one string -- "no
    factory ESN: security register 0x00, bit 0 (factory lock) = 0". The head
    before the colon is the fact. It is not printed -- the label's "no id"
    square says as much -- but its presence is what separates the chip having
    answered "none" from the tool knowing no command to ask it.
    """
    return (note or "").split(":", 1)[0].strip() or None


class FlashNotReadError(Exception):
    """A board reached the label generator without its flash facts.

    The same rule as IdentifierNotReadError and for the same reason. Every
    board here has a configuration flash, every label has a place for it, and
    a label whose flash rows are blank is one that has to be checked against
    the hardware by hand -- which is the work this package exists to remove.
    A part that has no unique id is not this error: that is a fact, and it
    prints.
    """


def flash_not_read(r):
    """Why this record's flash rows cannot be printed, or None.

    Four different silences, one of which is printable:
      no JEDEC id           the flash was never read at all
      no uid state          it was read before unique ids were, or by a tool
                            that does not report them
      state "blank"         the read happened and returned all ones or all
                            zeroes, which is a failed read wearing a value's
                            clothes
      state "none", no note only the reading tool saying it knows no unique-id
                            command for this part -- not evidence the silicon
                            has none, so not a fact to print
    """
    if not r.flash:
        return "its configuration flash was never read"
    if not r.flash_uid_state:
        return ("its configuration flash's unique id was never read (the flash "
                "id was, so this document predates unique-id reading)")
    if r.flash_uid_state == "blank":
        return ("its configuration flash's unique id read back all ones or all "
                "zeroes, which is a failed read and not a value")
    if r.flash_uid_state == "none" and not flash_uid_note_text(r.flash_uid_note):
        return ("nothing asked its configuration flash whether it has a unique "
                "id: the reading tool knows no such command for this part, "
                "which is not evidence that the part has none")
    if r.flash_uid_state == "read" and not r.flash_uid:
        return "its configuration flash's unique id was read as nothing at all"
    return None


def idcode_part(idcode):
    """The Artix-7 die an idcode names, whichever tool read it, or None."""
    try:
        return IDCODE_PART.get(int(idcode, 16) & IDCODE_REVISION_MASK)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class BoardLabel:
    """What a board label prints, from one document: the header from
    ``rpi_hwid.boards``, the identifiers from the summary."""

    kind: str                        # rpi | opi
    short: str                       # "Pi 5", "Orange Pi PC"
    title: str
    subtitle: str
    mark: str                        # artwork file name
    serial: str
    memory: str
    macs: tuple[tuple[str, str], ...]        # (kind, mac), eth first
    header: tuple[str, ...]
    hat_uuid: str | None = None
    eth_note: str | None = None      # why there is no eth MAC
    wlan_note: str | None = None     # why there is no wlan MAC
    # Pi 5 only, and None on every other model: the probe reports these as
    # None where the signal does not exist rather than as False, so a board
    # that cannot answer is never drawn as one that answered "no".
    fan: bool | None = None
    rtc_battery: bool | None = None


@dataclass(frozen=True)
class FpgaLabel:
    """What an FPGA board's label prints."""

    kind: str
    maker: str
    model: str
    host: str
    part: str | None = None
    name: str | None = None
    dna: str | None = None
    serial: str | None = None
    flash: str | None = None          # one line: vendor, part, density
    flash_jedec: str | None = None
    flash_uid: str | None = None      # the flash's own unique id, where read
    flash_uid_state: str | None = None   # read | blank | none
    flash_uid_note: str | None = None    # why, where the part itself says so
    flash_error: str | None = None       # what stopped a read that was tried
    gateware: str | None = None      # "gateware v4.14  ·  FPGA id 9" (pcileech)
    # The ECP5's die identifier, masked to its factory 56 bits. Shown, never
    # keyed on: reaching it costs the board's capture, so a name derived from
    # it could not be recovered without taking the board offline again.
    trace_id: str | None = None
    # The identifier the sticker is keyed on and what to call it. Every Xilinx
    # board keys on its Device DNA; a Cynthion keys on its configuration
    # flash's uid, so the caption travels with the value.
    ident: str | None = None
    ident_caption: str = DNA_CAPTION


@dataclass(frozen=True)
class TinyTapeoutLabel:
    """What a Tiny Tapeout demo board's label prints."""

    host: str
    headline: str                    # "TT06", "FPGA", "TT"
    subtitle: str                    # "ASIC  ·  sky130"
    url: str                         # the chip page, or the chips index
    demoboard_text: str              # "TT06+  ·  Rev 2.0.1"
    shuttle: str | None = None
    chip: str | None = None
    commit: str | None = None
    usb_serial: str | None = None
    mcu: str | None = None           # RP2040 | RP2350
    marks: tuple[str, ...] = ()      # the shuttle's operator, then its foundry
    chip_colour: str | None = None   # hex, from the table
    chip_colour_name: str | None = None
    chip_silk: str | None = None
    chip_silk_name: str | None = None
    demoboard_colour: str | None = None
    demoboard_colour_name: str | None = None
    demoboard_silk: str | None = None
    demoboard_silk_name: str | None = None


@dataclass(frozen=True)
class UsbLabel:
    """What a USB network adapter's label prints."""

    title: str
    kind: str
    mac: str
    vidpid: str
    host: str
    lines: tuple[tuple[str, str], ...]


def board_record(doc):
    """The board label's record from a document. A missing wlan MAC is
    derived where the board's rule allows (the Broadcom-OUI Pis), and
    otherwise explained: no radio on this model, or a radio whose MAC
    cannot be derived and so was disabled when the probe ran."""
    s = doc.summary
    ident = boards.identify(s)
    if ident is None:                 # not a board this package labels
        return None
    macs = [(m.kind, m.mac) for m in s.macs if m.kind in ("eth", "wlan")]
    wlan_note = None
    if not any(k == "wlan" for k, _ in macs):
        # Whether a radio exists is asked before its MAC is worked out: the
        # Broadcom rule derives a wlan MAC from the serial alone, so a board
        # with no radio at all would otherwise be given one.
        derived = None if ident.radio is False else derived_wlan_mac(
            s.serial, [{"kind": m.kind, "mac": m.mac} for m in s.macs])
        if derived:
            macs.append(("wlan", derived))
        elif ident.radio is False:
            wlan_note = "no radio"
        elif ident.kind == "rpi" and not ident.radio_derivable:
            wlan_note = "radio disabled, not readable"
    order = {"eth": 0, "wlan": 1}
    macs.sort(key=lambda m: order[m[0]])
    return BoardLabel(
        kind=ident.kind, short=ident.short, title=ident.title, subtitle=ident.subtitle,
        mark=ident.mark, serial=s.serial, memory=ident.memory, macs=tuple(macs),
        header=tuple(s.header), hat_uuid=s.hat_uuid,
        eth_note="no wired port" if ident.wired is False else None,
        wlan_note=wlan_note,
        fan=s.fan, rtc_battery=s.rtc_battery,
    )


def fpga_records(docs, pinned_names=None):
    """One record per FPGA board across all documents, named."""
    boards = [(host, b) for host in sorted(docs) for b in docs[host].summary.fpga]
    arty_serials = sorted(b.serial for _h, b in boards if b.kind == "arty" and b.serial)
    arty_names = naming.arty_names(arty_serials, pinned_names)
    out = []
    for host, b in boards:
        maker, model = BOARD_MODEL.get(b.kind, ("", b.kind))
        part = idcode_part(b.idcode)
        name = None
        ident, ident_caption = b.dna, DNA_CAPTION
        if b.kind == "netv2" and b.dna:
            name = naming.netv2_name(b.dna)
        elif b.kind == "arty" and b.serial:
            name = arty_names[b.serial]
        elif b.kind == "acorn":
            # named from the DNA like a NeTV2, and the die picks the variant
            model = ACORN_SOC_MODEL.get(b.soc_model or "") or ACORN_MODEL.get(
                part or "", model)
            if b.dna:
                name = naming.acorn_name(b.dna)
        elif b.kind == "cynthion":
            # The revision names both the model and the die; the probe records
            # a flash uid only where the gateware published one, so a board
            # read in Apollo mode arrives here unkeyed and stays unnamed
            # rather than being named from the debug controller's serial.
            model = "Cynthion r%s" % b.hw_rev if b.hw_rev else "Cynthion"
            part = CYNTHION_PART.get(b.hw_rev or "")
            # The die's own id is the identity, exactly as a Device DNA is on
            # a Xilinx part; the configuration flash's uid identifies a chip
            # that could be replaced, so it belongs with the other flash facts.
            ident, ident_caption = b.trace_id, TRACE_ID_CAPTION
            if b.trace_id:
                name = naming.cynthion_name(b.trace_id)
        if b.kind == "arty" and part:
            model = "Arty A7-" + part[len("XC7A"):]
        # The id the chip gave, where anything asked it; failing that, and
        # only on a Cynthion, what that revision's published BOM says is
        # soldered to it. The read wins where both exist, so a board that
        # has been asked is described by its own answer.
        if b.kind == "cynthion":
            flash = cynthion_flash(b.hw_rev, b.flash_jedec)
        else:
            flash = flash_text(flash_from_jedec(b.flash_jedec, b.flash_extended_id))
        gateware = None
        if b.gateware:
            # the number, never a board name: a class is shared by boards
            gateware = "gateware v%s  ·  FPGA id %s" % (b.gateware, b.gateware_id)
        out.append(FpgaLabel(kind=b.kind, maker=maker, model=model, host=host, part=part,
                             name=name, dna=b.dna, serial=b.serial, flash=flash,
                             gateware=gateware, ident=ident, trace_id=b.trace_id,
                             # a Cynthion's USB serial *is* its flash uid, and
                             # any board can also have it read from the flash
                             flash_uid=b.flash_uid or (
                                 b.serial if b.kind == "cynthion" else None),
                             flash_jedec=b.flash_jedec,
                             # A Cynthion's serial is its configuration flash's
                             # unique id, read off that chip by the gateware
                             # and published as a descriptor -- so where the
                             # probe found one, the id was read.
                             flash_uid_state=b.flash_uid_state or (
                                 "read" if b.kind == "cynthion" and b.serial
                                 else None),
                             flash_uid_note=b.flash_uid_note,
                             flash_error=b.flash_error,
                             ident_caption=ident_caption))
    return out


def demoboard_text(detected, version):
    """'TT06+' and 'v2.0.1' -> 'TT06+  ·  Rev 2.0.1'; the v3 SDK's
    'TTDBv3 [3.2]' -> 'TTDBv3  ·  Rev 3.2'; nothing known -> 'not read'."""
    name, rev = detected or "", None
    m = re.match(r"^(.*?)\s*\[([^\]]*)\]$", name)
    if m:
        name, rev = m.group(1), m.group(2)
    if not rev and version:
        rev = version.lstrip("vV")
    parts = [p for p in (name, ("Rev " + rev) if rev else "") if p]
    return "  ·  ".join(parts) if parts else "not read"


def tinytapeout_records(docs):
    """One record per Tiny Tapeout demo board across all documents."""
    out = []
    for host in sorted(docs):
        for b in docs[host].summary.tinytapeout:
            info = tt_data.shuttle_info(b.shuttle)
            # The spreadsheet first, for the colours and the chip's page: it
            # is kept honest by CI and it has the boards the hand-written
            # table in rpi_hwid.tinytapeout never got (TT09, the Sky and GF
            # shuttles, FabricFox), and the real hex of each. That table
            # stays as the fallback, and its COLOURS as the palette for a
            # colour the sheet names but has no hex for.
            # The demo board and the chip are named as well as the shuttle,
            # because the shuttle does not always find them: an FPGA
            # breakout has no shuttle at all, and ttihp25a sits on a board
            # the sheet knows but has not listed it under.
            sheet = tt_boards.colours(b.shuttle, tt_data.COLOURS, b.demoboard, b.chip)
            if b.chip == "fpga":
                headline, parts = "FPGA", ["FPGA breakout, no ASIC"]
            elif b.shuttle:
                headline, parts = tt_data.shuttle_short(b.shuttle), ["ASIC"]
                pdk = tt_data.shuttle_pdk(b.shuttle)
                if pdk:
                    parts.append(pdk)
            else:
                headline, parts = "TT", ["shuttle not read"]
            out.append(TinyTapeoutLabel(
                host=host, headline=headline, subtitle="  ·  ".join(parts),
                url=(tt_boards.chip_page(b.shuttle) or info["url"]
                     or tt_data.CHIPS_INDEX_URL),
                demoboard_text=demoboard_text(
                    b.demoboard, (b.demoboard_version
                                  or tt_boards.demoboard_version(b.shuttle, b.demoboard)
                                  or info["demoboard_version"])),
                shuttle=b.shuttle, chip=b.chip, commit=b.commit, usb_serial=b.usb_serial,
                mcu=b.mcu, marks=tuple(tt_data.shuttle_marks(b.shuttle)),
                chip_colour=sheet["chip"] or tt_data.COLOURS.get(info["chip_colour"] or ""),
                chip_colour_name=sheet["chip_name"] or info["chip_colour"],
                chip_silk=sheet["chip_silk"] or tt_data.COLOURS.get(info["chip_silk"] or ""),
                chip_silk_name=sheet["chip_silk_name"] or info["chip_silk"],
                demoboard_colour=(sheet["demoboard"]
                                  or tt_data.COLOURS.get(info["demoboard_colour"] or "")),
                demoboard_colour_name=sheet["demoboard_name"] or info["demoboard_colour"],
                demoboard_silk=(sheet["demoboard_silk"]
                                or tt_data.COLOURS.get(info["demoboard_silk"] or "")),
                demoboard_silk_name=sheet["demoboard_silk_name"] or info["demoboard_silk"],
            ))
    return out


def usb_records(docs):
    out = []
    for host in sorted(docs):
        for u in docs[host].summary.usb_net:
            bcd = (u.bcd_usb or "").strip()
            speed = USB_SPEED.get(u.usb_speed or "", u.usb_speed or "")
            out.append(UsbLabel(
                title=u.title, kind=u.kind, mac=u.mac, vidpid=u.vidpid, host=host,
                lines=(("USB", ("%s %s" % (bcd, speed)).strip()),
                       ("driver", u.driver or ""), ("VID:PID", u.vidpid)),
            ))
    return out


# --- assembly -----------------------------------------------------------------

KINDS = ("fpga", "tt", "rpi", "opi", "usb")
# A host can carry more than one FPGA board -- rpi5-netv2 has a NeTV2 and a
# Cynthion -- so "fpga" is not fine enough to print one sticker. Naming a kind
# selects that board alone; "fpga" still means all of them.
FPGA_KINDS = ("netv2", "arty", "acorn", "pcileech", "cynthion", "jtag", "unknown-fpga")
ONLY_CHOICES = KINDS + FPGA_KINDS


def all_labels(docs, only, pinned_names=None, order=None):
    """Every label as (host, kind, title, draw, record), host by host.

    A machine's labels come out together and in the order someone works
    through it: the board itself, then what is plugged into it -- FPGA, Tiny
    Tapeout, then the USB adapters -- so a Pi and its dongles are peeled off
    the sheet side by side instead of from three different pages. Positions
    are still filled tightly, so a host whose group will not fit is split
    across the sheet break rather than wasting the stickers before it.

    `order` names hosts in the sequence they should come out, for a caller
    that knows something this package does not -- which switch port each is
    plugged into, say, so a rack can be labelled by walking the ports in
    turn. Hosts it does not name follow, by host name as before.
    """
    only = set(only)
    # "fpga" gathers every board; a bare kind gathers them all too and then
    # drops the ones not asked for, because a record does not know its kind
    # until it has been built.
    wanted_fpga = only & set(FPGA_KINDS)
    any_fpga = "fpga" in only or bool(wanted_fpga)
    attached = {}
    for record_kind, records in (
            ("fpga", fpga_records(docs, pinned_names) if any_fpga else ()),
            ("tt", tinytapeout_records(docs) if "tt" in only else ()),
            ("usb", usb_records(docs) if "usb" in only else ())):
        for r in records:
            if record_kind == "fpga":
                if wanted_fpga and r.kind not in wanted_fpga:
                    continue
                if not r.ident:
                    raise IdentifierNotReadError(
                        "%s: the %s board has no %s, so its label would carry "
                        "nothing that identifies it. Read it with `%s` on that "
                        "host and collect again." % (
                            r.host, r.kind, r.ident_caption,
                            IDENT_READ_WITH.get(r.kind, "rpi-hwid fpga --jtag")))
                why = flash_not_read(r)
                if why:
                    raise FlashNotReadError(
                        "%s: the %s board's label has a place for its "
                        "configuration flash and %s. Read it with `%s` on "
                        "that host and collect again -- note that this "
                        "reconfigures the FPGA." % (
                            r.host, r.kind, why, FLASH_READ_WITH.get(
                                r.kind, "rpi-hwid fpga --jtag --flash"))
                        # a read that was tried and stopped: the command
                        # above will stop the same way until this is fixed
                        + (" The last attempt stopped: %s." % r.flash_error
                           if r.flash_error else ""))
                # the identifier the sticker is keyed on, as a Pi row carries
                # its serial and a USB row its MAC
                ident = r.ident or r.serial or (r.gateware or "").replace("  ·  ", ", ")
                row = (r.kind, f"{r.name or r.model} {ident}".strip(),
                       draw_fpga, r)
            elif record_kind == "tt":
                row = ("tt", f"{r.headline} {r.usb_serial or ''}".strip(), draw_tinytapeout, r)
            else:
                row = ("usb", f"{r.title} {r.mac}", draw_usb, r)
            attached.setdefault(r.host, []).append(row)

    rank = {host: i for i, host in enumerate(order or ())}
    for host in sorted(sorted(docs), key=lambda h: rank.get(h, len(rank))):
        if only & {"rpi", "opi"}:
            # A board that cannot be named is one label lost, not the sheet:
            # every board is asked for at once, so an unreadable revision
            # code used to take the whole print run with it. What is attached
            # to it is still labelled -- a dongle's identity does not depend
            # on the revision code of the Pi it happens to be plugged into.
            try:
                b = board_record(docs[host])
            except ValueError as exc:
                print("%s: %s, skipped" % (host, exc), file=sys.stderr)
                b = None
            else:
                if b is None:
                    print("%s: not a board this package labels, skipped" % host,
                          file=sys.stderr)
            if b is not None and b.kind in only:
                yield host, b.kind, f"{b.short} {b.memory} {b.serial}", draw_board, b
        for row in attached.get(host, ()):
            yield (host,) + row


def label_origin(index):
    """Bottom-left corner of label `index` on its sheet, reading order."""
    col, row = index % COLS, index // COLS
    x = MARGIN_X + col * (LABEL_W + GAP_X)
    y = PAGE_H - MARGIN_Y - (row + 1) * LABEL_H - row * GAP_Y
    return x, y


def render(docs, out, only=KINDS, start=0, outline=False,
           pinned_names=None, order=None):
    """Write the PDF; returns (label count, sheet count)."""
    register_fonts()
    labels = list(all_labels(docs, set(only), pinned_names, order))
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle("Hardware identity labels")
    c.setAuthor("rpi-hwid labels")
    per_sheet = COLS * ROWS
    for i, (_host, _kind, _title, draw, data) in enumerate(labels):
        pos = i + start
        if pos and pos % per_sheet == 0:
            c.showPage()
        lab = Label(c, *label_origin(pos % per_sheet))
        if outline:
            lab.outline()
        draw(lab, data)
    c.showPage()
    c.save()
    sheets = (len(labels) + start + per_sheet - 1) // per_sheet
    return len(labels), sheets


def main(argv=None):
    global ARTWORK_DIR
    ap = argparse.ArgumentParser(prog="rpi-hwid labels", description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True, type=Path, help="directory of probe JSON documents")
    ap.add_argument("--out", default="hardware-labels.pdf", type=Path)
    ap.add_argument("--only", action="append", choices=list(ONLY_CHOICES),
                    metavar="KIND", help="rpi|opi|fpga|tt|usb, or one FPGA board kind "
                                         "(%s)" % "|".join(FPGA_KINDS))
    ap.add_argument("--start", type=int, default=0,
                    help="leave the first N positions of the first sheet blank")
    ap.add_argument("--outline", action="store_true", help="draw each label's edge")
    ap.add_argument("--artwork", type=Path, help="directory holding the maker marks")
    ap.add_argument("--names", type=Path,
                    help="JSON map of Arty serial -> name, the registry that pins names")
    ap.add_argument("--list", action="store_true", help="print what would be generated")
    args = ap.parse_args(argv)

    ARTWORK_DIR = str(args.artwork) if args.artwork else None
    docs = load_collected(args.data)
    pinned = json.loads(args.names.read_text()) if args.names else None
    only = args.only or list(KINDS)
    if args.list:
        rows = list(all_labels(docs, set(only), pinned))
        # The host column is as wide as the widest host and no wider: these
        # are fully qualified names on some fleets and bare ones on others,
        # and a fixed width either truncates the long or strands the short.
        width = max([len(r[0]) for r in rows] or [0])
        for i, (host, kind, title, _, _) in enumerate(rows):
            pos = i + args.start
            print("sheet %d row %d col %d  %-*s  %-6s %s" % (
                pos // (COLS * ROWS) + 1, pos % (COLS * ROWS) // COLS + 1, pos % COLS + 1,
                width, host, kind, title))
        return 0
    n, sheets = render(docs, args.out, only, args.start, args.outline, pinned)
    print("%d labels on %d sheet%s -> %s" % (n, sheets, "" if sheets == 1 else "s", args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
