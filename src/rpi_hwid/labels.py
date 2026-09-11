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
name derived from it; a USB adapter's own MAC; a Tiny Tapeout board's
shuttle, the chip ROM's commit and the demo board's RP2 unique id. Nothing
about where a thing is plugged in or what it is called this month. Each
identifier someone might need to type is also a QR code, in a monospace
face with a slashed zero where one is installed.

Records come straight from ``rpi-hwid collect`` output (or ``probe --json``
files): one board label per document (a Raspberry Pi or an Orange Pi, the
same layout with the maker's mark and the model decoding swapped, see
``rpi_hwid.boards``), one FPGA label per board the probe found, one Tiny
Tapeout label per demo board, one adapter label per removable USB network
adapter. Artwork: the package ships the Raspberry Pi raspberry, the Orange
Pi orange, the Alphamax, Digilent and Tiny Tapeout marks and the
public-domain USB trident (see artwork/README.md, each mark drawn only on
its owner's hardware); ``--artwork DIR`` overrides any of them and may add
``netv2.svg``, and a label whose mark is missing sets the maker's name in
type (or, on a board label, leaves the mark's box empty).
"""

from __future__ import annotations

import argparse
import json
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

from rpi_hwid import boards
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


def mark_aspect(path):
    """height / width of a mark, SVG or raster."""
    if path.endswith(".svg"):
        return Label.svg_aspect(path)
    from PIL import Image
    w0, h0 = Image.open(path).size
    return h0 / w0


def mark_fitted(lab, name, x, y, box_w, box_h):
    """A mark from the artwork directory drawn as large as fits inside the
    box, flush left and centred vertically, so every board label keeps
    the same geometry whatever shape its maker's mark is. Nothing is drawn
    (and the box stays blank) when the file is absent."""
    path = artwork(name)
    if not path:
        return
    aspect = mark_aspect(path)
    h = min(box_h, box_w * aspect)
    top = y + (box_h - h) / 2
    if path.endswith(".svg"):
        lab.svg(path, x, top, h)
    else:
        raster(lab, path, x, top, h)


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
    its Digilent serial and flash part. Bottom, full width: the DNA, or a
    rule to write it on when nobody has read it yet."""
    dna_size = 15
    dna_h = 6 * mm
    qr_size = 20 * mm
    qr_inset = 3.5 * mm            # four modules of a 25-module code at 20 mm
    ident_str = board.dna or board.serial
    if ident_str:
        lab.qr(qr_inset, qr_inset, qr_size, ident_str)

    x = qr_inset + qr_size + 3 * mm
    col_w = LABEL_W - PAD - x
    y = PAD
    mark_h = 6 * mm if board.kind == "arty" else 5 * mm
    mark_maker(lab, board, x, y, mark_h)
    y += mark_h + 0.5 * mm
    word = board.name.split("-", 1)[1] if board.name else board.model.split()[0]
    lab.fit(x, y, word, SANS_BOLD, 24, col_w)
    y += 8.3 * mm
    if board.part:
        lab.fit(x, y, "{}  ·  {}".format(board.model, board.part), SANS, 8, col_w)
    else:
        lab.fit(x, y, board.model, SANS, 8, col_w)
        y += 3 * mm
        lab.text(x, y, "die not read", SANS, CAPTION, color=GREY)
    if board.kind == "arty":
        # the serial is a board-printed identifier: its own row, larger
        y += 3.8 * mm
        lab.captioned(x, x + 6 * mm, y, "S/N", board.serial, MONO, 10, col_w - 6 * mm)
        y += 4 * mm
        # the part name alone tells the two flash fits apart; the density
        # is the same on both (S25FL128S/127S: one JEDEC id, two parts)
        lab.captioned(x, x + 6 * mm, y, "flash", board.flash or "not read", SANS, 7.5,
                      col_w - 6 * mm)

    y = LABEL_H - PAD - dna_h
    cap_y = y + 0.5 * mm - CAPTION * 0.72 - 0.9 * mm    # the caption sits over the DNA
    if board.dna:
        lab.text(PAD, cap_y, "Device DNA", SANS, CAPTION, color=GREY)
        lab.fit(PAD, y + 0.5 * mm, board.dna, MONO, dna_size, LABEL_W - 2 * PAD)
    else:
        # the "0x" sits on the rule at the foot; the space above the rule,
        # to the right of the QR column, is where the digits get written
        lab.fit(PAD, cap_y, "Device DNA, write it in", SANS, CAPTION, qr_size, color=GREY)
        lab.text(PAD, y + 0.5 * mm, "0x", MONO, dna_size)
        lab.rule(PAD + 6 * mm, y + 0.5 * mm + dna_size * 0.72 + 0.3 * mm,
                 LABEL_W - 2 * PAD - 6 * mm)


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
    lab.fit(tx, y, b.title, SANS_BOLD, 11, col_w)
    lab.fit(tx, y + 4.6 * mm, b.subtitle, SANS, 6.5, col_w)

    # HAT band: the HAT line, then the uuid line centred in the rest of the
    # band (regular weight: bold mono at 6 pt fills in under toner). A
    # board without the HAT convention keeps the row, captioned "header"
    # and saying in grey that nothing was probed there.
    y = PAD + max(logo_h, title_h) + 0.8 * mm
    band_top = y
    if b.header:
        lab.captioned(x, tx, y, "HAT", "; ".join(b.header), SANS, 7, col_w)
    elif b.header_note:
        lab.captioned(x, tx, y, "header", b.header_note, SANS, 7, col_w)
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


def swatch(lab, x, y, w, h, colour):
    """One colour-identification box: a keyline rectangle filled with the
    colour, or left empty and struck through when it is not recorded. The
    keyline matters: a white soldermask is a white box on white stock."""
    c = lab.c
    px, py = lab.pt(x, y + h)
    c.setStrokeColor(GREY)
    c.setLineWidth(0.4)
    if colour:
        c.setFillColor(HexColor(colour))
        c.rect(px, py, w, h, stroke=1, fill=1)
    else:
        c.rect(px, py, w, h, stroke=1, fill=0)
        c.line(px, py, px + w, py + h)      # struck through: nothing recorded
    c.setStrokeColor(black)
    c.setFillColor(black)


def swatch_pair(lab, x, y, w, h, mask, silk):
    """A board's two colours side by side: the soldermask in a wide box and
    the silkscreen printed on it in a narrow one, in that order and in that
    proportion, so which box is which needs no caption of its own. Returns
    the width the pair took."""
    mask_w = w * 0.62
    swatch(lab, x, y, mask_w, h, mask)
    swatch(lab, x + mask_w + 0.5 * mm, y, w - mask_w - 0.5 * mm, h, silk)
    return w


def draw_tinytapeout(lab, tt):
    """Left: the Tiny Tapeout mark beside the shuttle as the headline, the
    chip's kind and PDK under it, then the demo board and the ROM commit as
    captioned rows, then a colour box each for the chip carrier and the
    demo board (board colour, the colour's name beside it) so the right
    board is picked out of a drawer. Right: a QR that opens the chip's
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
    lab.text(x, y, tt.subtitle, SANS, 6.5)          # fixed size, like the Pi subtitle
    row = 3.7 * mm
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

    # The colours, so a board can be matched to its label across the bench:
    # for each of the two boards, its soldermask and the silkscreen printed
    # on it, with the caption over the two colours named in the same order.
    sw, sh = 7.5 * mm, 4.5 * mm
    line = CAPTION * 0.72 + 0.6 * mm
    for i, (cap, mask, silk, names) in enumerate((
            ("carrier", tt.chip_colour, tt.chip_silk,
             colour_names(tt.chip_colour_name, tt.chip_silk_name)),
            ("demo board", tt.demoboard_colour, tt.demoboard_silk,
             colour_names(tt.demoboard_colour_name, tt.demoboard_silk_name)))):
        sx = x + i * (col_w / 2)
        swatch_pair(lab, sx, y, sw, sh, mask, silk)
        tx2 = sx + sw + 1 * mm
        tw = col_w / 2 - sw - 1.5 * mm
        ty = y + (sh - 2 * line + 0.6 * mm) / 2
        lab.fit(tx2, ty, cap, SANS, CAPTION, tw, min_size=5, color=GREY)
        lab.fit(tx2, ty + line, names, SANS, CAPTION, tw, min_size=5)



# --- records from probe documents --------------------------------------------

USB_SPEED = {"12": "FS 12 Mbit/s", "480": "HS 480 Mbit/s", "5000": "SS 5 Gbit/s",
             "10000": "SS+ 10 Gbit/s", "20000": "SS+ 20 Gbit/s"}
BOARD_MODEL = {"netv2": ("Alphamax", "NeTV2"), "arty": ("Digilent", "Arty A7"),
               "acorn": ("SQRL", "Acorn CLE-215+"), "jtag": ("", "FPGA")}
IDCODE_PART = {"0x362d093": "XC7A35T", "0x3631093": "XC7A100T", "0x3636093": "XC7A200T",
               "0x13631093": "XC7A100T", "0x03636093": "XC7A200T"}


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
    header_note: str | None = None   # in place of the HAT line, on a board with no HATs
    eth_note: str | None = None      # why there is no eth MAC
    wlan_note: str | None = None     # why there is no wlan MAC


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
    flash: str | None = None


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
        derived = derived_wlan_mac(s.serial, [{"kind": m.kind, "mac": m.mac} for m in s.macs])
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
        header_note=None if ident.kind == "rpi" else "40-pin",
        eth_note="no wired port" if ident.wired is False else None,
        wlan_note=wlan_note,
    )


def fpga_records(docs, pinned_names=None):
    """One record per FPGA board across all documents, named."""
    boards = [(host, b) for host in sorted(docs) for b in docs[host].summary.fpga]
    arty_serials = sorted(b.serial for _h, b in boards if b.kind == "arty" and b.serial)
    arty_names = naming.arty_names(arty_serials, pinned_names)
    out = []
    for host, b in boards:
        maker, model = BOARD_MODEL.get(b.kind, ("", b.kind))
        part = IDCODE_PART.get(b.idcode or "")
        name = None
        if b.kind == "netv2" and b.dna:
            name = naming.netv2_name(b.dna)
        elif b.kind == "arty" and b.serial:
            name = arty_names[b.serial]
        if b.kind == "arty" and part:
            model = "Arty A7-" + part[len("XC7A"):]
        flash = None
        if b.flash_jedec:
            # S25FL128S and S25FL127S both answer 0x012018
            flash = "S25FL128S/127S" if b.flash_jedec == "0x012018" else b.flash_jedec
        out.append(FpgaLabel(kind=b.kind, maker=maker, model=model, host=host, part=part,
                             name=name, dna=b.dna, serial=b.serial, flash=flash))
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


def colour_names(mask, silk):
    """The two colours of one board, in the order their boxes are drawn."""
    if mask and silk:
        return "%s/%s" % (mask, silk)
    return mask or silk or "not recorded"


def tinytapeout_records(docs):
    """One record per Tiny Tapeout demo board across all documents."""
    out = []
    for host in sorted(docs):
        for b in docs[host].summary.tinytapeout:
            info = tt_data.shuttle_info(b.shuttle)
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
                url=info["url"] or tt_data.CHIPS_INDEX_URL,
                demoboard_text=demoboard_text(
                    b.demoboard, b.demoboard_version or info["demoboard_version"]),
                shuttle=b.shuttle, chip=b.chip, commit=b.commit, usb_serial=b.usb_serial,
                mcu=b.mcu,
                chip_colour=tt_data.COLOURS.get(info["chip_colour"] or ""),
                chip_colour_name=info["chip_colour"],
                chip_silk=tt_data.COLOURS.get(info["chip_silk"] or ""),
                chip_silk_name=info["chip_silk"],
                demoboard_colour=tt_data.COLOURS.get(info["demoboard_colour"] or ""),
                demoboard_colour_name=info["demoboard_colour"],
                demoboard_silk=tt_data.COLOURS.get(info["demoboard_silk"] or ""),
                demoboard_silk_name=info["demoboard_silk"],
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


def all_labels(docs, only, pinned_names=None):
    only = set(only)
    if "fpga" in only:
        for b in fpga_records(docs, pinned_names):
            yield b.kind, b.name or b.model, draw_fpga, b
    if "tt" in only:
        for t in tinytapeout_records(docs):
            yield "tt", f"{t.headline} {t.usb_serial or ''}".strip(), draw_tinytapeout, t
    if only & {"rpi", "opi"}:
        for host in sorted(docs):
            b = board_record(docs[host])
            if b is None:
                print("%s: not a board this package labels, skipped" % host, file=sys.stderr)
                continue
            if b.kind in only:
                yield b.kind, f"{b.short} {b.memory} {b.serial}", draw_board, b
    if "usb" in only:
        for u in usb_records(docs):
            yield "usb", f"{u.title} {u.mac}", draw_usb, u


def label_origin(index):
    """Bottom-left corner of label `index` on its sheet, reading order."""
    col, row = index % COLS, index // COLS
    x = MARGIN_X + col * (LABEL_W + GAP_X)
    y = PAGE_H - MARGIN_Y - (row + 1) * LABEL_H - row * GAP_Y
    return x, y


def render(docs, out, only=KINDS, start=0, outline=False,
           pinned_names=None):
    """Write the PDF; returns (label count, sheet count)."""
    register_fonts()
    labels = list(all_labels(docs, set(only), pinned_names))
    c = canvas.Canvas(str(out), pagesize=A4)
    c.setTitle("Hardware identity labels")
    c.setAuthor("rpi-hwid labels")
    per_sheet = COLS * ROWS
    for i, (_kind, _title, draw, data) in enumerate(labels):
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
    ap.add_argument("--only", action="append", choices=list(KINDS))
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
        for i, (kind, title, _, _) in enumerate(all_labels(docs, set(only), pinned)):
            pos = i + args.start
            print("sheet %d row %d col %d  %-6s %s" % (
                pos // (COLS * ROWS) + 1, pos % (COLS * ROWS) // COLS + 1, pos % COLS + 1,
                kind, title))
        return 0
    n, sheets = render(docs, args.out, only, args.start, args.outline, pinned)
    print("%d labels on %d sheet%s -> %s" % (n, sheets, "" if sheets == 1 else "s", args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
