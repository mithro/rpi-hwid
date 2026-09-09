"""Print-ready hardware labels from collected probe documents.

    rpi-hwid labels --data data/ --out labels.pdf [--artwork DIR] [--names names.json]

An A4 PDF laid out for 21-per-sheet 63.5 x 38.1 mm address labels (the
Avery L7160 grid: three columns, seven rows, 2.54 mm between columns,
nothing between rows, 7.21 mm side and 15.15 mm top/bottom margins). Print
it at 100 % -- "fit to page" shrinks the grid and every label lands off its
sticker. ``--outline`` draws the sticker edges for a plain-paper alignment
print.

Every label carries only what cannot change: a Pi's revision code, serial
and soldered-down MACs and the HAT it wears; an FPGA board's DNA or Digilent
serial and the name derived from it; a USB adapter's own MAC. Nothing about
where a thing is plugged in or what it is called this month. Each identifier
someone might need to type is also a QR code, in a monospace face with a
slashed zero where one is installed.

Records come straight from ``rpi-hwid collect`` output (or ``probe --json``
files): one Pi label per document, one FPGA label per board the probe
found, one adapter label per removable USB network adapter. Artwork: the
package ships the Raspberry Pi raspberry and the public-domain USB trident
(see artwork/README.md); ``--artwork DIR`` may supply ``alphamax.png``,
``digilent.png`` and ``netv2.svg`` (trademarks of their owners), and a label
without them uses the maker's name in type.
"""

from __future__ import annotations

import argparse
import json
import os
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

from rpi_hwid import names as naming
from rpi_hwid.collect import load_collected
from rpi_hwid.revision import decode_revision, derived_wlan_mac

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

SANS, SANS_BOLD, MONO = "Helvetica", "Helvetica-Bold", "Courier-Bold"


def register_fonts():
    global MONO
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"):
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("LabelMono", path))
            MONO = "LabelMono"
            return


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

    def fit(self, x, y, s, font, size, max_w, min_size=5.5, color=black):
        """`text`, but shrink the type until `s` fits inside `max_w`."""
        while size > min_size and self.width(s, font, size) > max_w:
            size -= 0.25
        return self.text(x, y, s, font, size, color=color)

    def captioned(self, x_cap, x_val, y, cap, val, val_font, val_size,
                  max_w, cap_size=6):
        """A grey caption and its value on one shared baseline; `y` is the
        cap-height top of the value. Returns the size the value ended at."""
        size = val_size
        while size > 5.5 and self.width(val, val_font, size) > max_w:
            size -= 0.25
        baseline = y + size * 0.72
        self.text(x_cap, baseline - cap_size * 0.72, cap, SANS, cap_size,
                  color=HexColor("#555555"))
        self.text(x_val, y, val, val_font, size)
        return size

    def rule(self, x, y, w):
        """A thin grey line, for something to be written in by hand."""
        c = self.c
        px, py = self.pt(x, y)
        c.setStrokeColor(HexColor("#888888"))
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

    def qr(self, x, y, size, content):
        """A QR code whose top-left is (x, y), `size` points square. Error
        level M and no drawn quiet zone: the label stock is white, and the
        caller keeps the surrounding area clear."""
        code = segno.make(content, error="m")
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

    def outline(self):
        c = self.c
        c.setStrokeColor(HexColor("#bbbbbb"))
        c.setLineWidth(0.3)
        c.rect(self.x0, self.y0, LABEL_W, LABEL_H, stroke=1, fill=0)
        c.setStrokeColor(black)


# --- marks --------------------------------------------------------------------
#
# The Raspberry Pi raspberry and the USB trident are real artwork, shipped in
# artwork/ (see artwork/README.md for where each came from). There is no
# vector NeTV2 logo to be had, so that mark is drawn here: drop a `netv2.svg`
# into the --artwork directory and it will be used instead. The Wi-Fi arcs
# are drawn too, which is simpler than tracking a licence for a three-arc
# glyph.


def artwork(name):
    """A mark's file: the caller's artwork directory first, then the
    package's own (which ships only what is free to redistribute: the
    public-domain USB trident). Returns None when neither has it."""
    for d in ([ARTWORK_DIR] if ARTWORK_DIR else []) + [PACKAGE_ARTWORK]:
        path = os.path.join(d, name)
        if os.path.exists(path):
            return path
    return None


def mark_netv2(lab, x, y, height):
    path = artwork("netv2.svg")
    return lab.svg(path, x, y, height) if path else 0


def mark_raster(lab, name, x, y, height):
    """A raster mark from the artwork directory, scaled to `height`; the
    width it took, or 0 when the file is absent."""
    path = artwork(name)
    if not path:
        return 0
    from PIL import Image
    w0, h0 = Image.open(path).size
    width = height * w0 / h0
    px, py = lab.pt(x, y + height)
    lab.c.drawImage(path, px, py, width, height, mask="auto")
    return width


def mark_alphamax(lab, x, y, height):
    return mark_raster(lab, "alphamax.png", x, y, height)


def mark_digilent(lab, x, y, height):
    return mark_raster(lab, "digilent.png", x, y, height)


def mark_maker(lab, board, x, y, height):
    """The board maker's mark, top-left of the text column, when the
    artwork directory has it; otherwise the maker's name in type."""
    w = 0
    if board.kind == "netv2":
        w = mark_alphamax(lab, x, y, height)
    elif board.kind == "arty":
        w = mark_digilent(lab, x, y, height)
    if not w:
        lab.text(x, y + height * 0.15, board.maker, SANS_BOLD, height * 0.62)
        w = lab.width(board.maker, SANS_BOLD, height * 0.62)
    return w


def mark_rpi(lab, x, y, height):
    """The raspberry, if the artwork directory supplies raspberry-pi.svg
    (a trademark of Raspberry Pi Ltd, not shipped here); else nothing."""
    path = artwork("raspberry-pi.svg")
    return lab.svg(path, x, y, height) if path else 0


def mark_usb(lab, x, y, height):
    return lab.svg(artwork("usb.svg"), x, y, height)


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
    c.setLineWidth(height * 0.06)
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


# --- the three label designs --------------------------------------------------

def draw_fpga(lab, board):
    """One design for every Artix-7 board. Left: a QR of the board's
    identity, the DNA when it is known and otherwise the Digilent serial.
    Right: the maker's mark above the board's word, then model and die, and
    for an Arty its Digilent serial. Bottom, full width: the DNA, or a rule
    to write it on when nobody has read it yet."""
    dna_size = 15
    dna_h = 6 * mm
    body_h = LABEL_H - 2 * PAD - dna_h
    qr_size = min(body_h - 3.5 * mm, 23 * mm)
    ident_str = board.dna or board.serial
    if ident_str:
        lab.qr(PAD, PAD, qr_size, ident_str)

    x = PAD + qr_size + 3 * mm
    col_w = LABEL_W - PAD - x
    y = PAD
    # a wide mark (Alphamax) at 5 mm, a square one (Digilent) at 6.5 mm
    mark_h = 6.5 * mm if board.kind == "arty" else 5 * mm
    mark_maker(lab, board, x, y, mark_h)
    y += mark_h + 1 * mm
    word = board.name.split("-", 1)[1] if board.name else board.model.split()[0]
    lab.fit(x, y, word, SANS_BOLD, 24, col_w)
    y += 9.5 * mm
    die = board.part or "die not read"
    lab.fit(x, y, "{}  ·  {}".format(board.model, die), SANS, 8, col_w)
    if board.kind == "arty":
        y += 4 * mm
        lab.captioned(x, x + 7 * mm, y, "S/N", board.serial, MONO, 8.5, col_w - 7 * mm)
        y += 4 * mm
        flash = ("{} 128 Mb".format(board.flash)) if board.flash else "not read"
        # (S25FL128S/127S: one JEDEC id, two parts; the marking tells them apart)
        lab.captioned(x, x + 7 * mm, y, "flash", flash, SANS, 7.5, col_w - 7 * mm)

    y = LABEL_H - PAD - dna_h
    if board.dna:
        lab.text(PAD, y - 2.6 * mm, "Device DNA", SANS, 6, color=HexColor("#555555"))
        lab.fit(PAD, y + 0.5 * mm, board.dna, MONO, dna_size, LABEL_W - 2 * PAD)
    else:
        lab.fit(PAD, y - 2.6 * mm, "Device DNA, write it in", SANS, 6, qr_size,
                color=HexColor("#555555"))
        lab.text(PAD, y + 0.5 * mm, "0x", MONO, dna_size)
        lab.rule(PAD + 6 * mm, y + dna_h - 0.5 * mm, LABEL_W - 2 * PAD - 6 * mm)


def draw_rpi(lab, pi):
    """Every Pi label has the same five bands at the same heights, so the
    eye finds each fact in the same place on every board: the serial up the
    left edge; model beside the raspberry; a HAT line and its uuid line
    (blank when there is no HAT); an eth row; a wlan row. A row whose MAC is
    not known says why instead of leaving a gap."""
    d = {"model": pi.model, "memory": pi.memory, "revision": pi.rev}
    ser_w = 4.5 * mm
    lab.rotated(PAD, LABEL_H - PAD, pi.serial, MONO, 9)
    lab.rotated(PAD + 2.9 * mm, LABEL_H - PAD, "serial",
                SANS, 5.5, color=HexColor("#555555"))

    x = PAD + ser_w + 1.5 * mm
    col_w = LABEL_W - PAD - x
    y = PAD
    logo_h = 7 * mm
    w = mark_rpi(lab, x, y, logo_h)
    tx = x + w + (2 * mm if w else 0)
    head_w = LABEL_W - PAD - tx
    lab.fit(tx, y, "Raspberry Pi " + d["model"], SANS_BOLD, 11, head_w)
    lab.fit(tx, y + 4.6 * mm,
            "{}  ·  Rev {}  ·  rev code {}".format(d["memory"], d["revision"],
                                                   pi.revision), SANS, 6.5, head_w)

    # HAT band: two lines, always present
    y = PAD + logo_h + 1 * mm
    if pi.header:
        line1 = "; ".join(pi.header)
        line2 = ("uuid", pi.hat_uuid, MONO) if pi.hat_uuid else ("", "", SANS)
    else:
        line1 = "none"
        line2 = ("", "", SANS)
    lab.captioned(x, x + 7 * mm, y, "HAT", line1, SANS, 7, col_w - 7 * mm)
    y += 3.4 * mm
    cap, val, font = line2
    if val:
        if cap:
            lab.captioned(x, x + 7 * mm, y, cap, val, font, 6.5, col_w - 7 * mm)
        else:
            lab.fit(x + 7 * mm, y, val, font, 6.5, col_w - 7 * mm)
    y += 3.8 * mm

    # MAC bands: eth then wlan, always both, fixed height
    macs = dict(pi.macs)
    if "eth" not in macs:
        macs["eth"] = None
    if "wlan" not in macs:
        macs["wlan"] = None
    reasons = {
        "eth": "no wired port on this model" if "Zero" in d["model"] else "not read",
        "wlan": pi.wlan_note or "not read",
    }
    avail = LABEL_H - PAD - y
    row_h = avail / 2
    qr = row_h - 1 * mm
    text_x = x + qr + 2 * mm
    text_w = LABEL_W - PAD - text_x
    for kind in ("eth", "wlan"):
        mac = macs[kind]
        lab.text(text_x, y + 0.4 * mm, kind + " MAC", SANS, 6,
                 color=HexColor("#555555"))
        if mac:
            lab.qr(x, y + 0.5 * mm, qr, mac)
            lab.fit(text_x, y + 3.3 * mm, mac, MONO, 13, text_w)
        else:
            lab.fit(text_x, y + 3.3 * mm, reasons[kind], SANS, 8, text_w)
        y += row_h


def draw_usb(lab, dev):
    """Bus and link glyphs with the chip name across the top, the facts on
    the left with the MAC's QR on the right centred in the band between the
    title and the MAC, and the MAC across the whole width at the bottom."""
    x, y = PAD, PAD
    h = 6.5 * mm
    w = mark_usb(lab, x, y + 1.2 * mm, h * 0.6)
    w += 1.5 * mm
    if dev.kind == "wifi":
        w += mark_wifi(lab, x + w, y, h)
    else:
        w += mark_rj45(lab, x + w, y, h)
    tx = x + w + 2.5 * mm
    title = dev.title
    lab.fit(tx, y + 0.8 * mm, title, SANS_BOLD, 12, LABEL_W - PAD - tx)
    band_top = y + h + 1.5 * mm

    mac_size = 16
    mac_h = 6.5 * mm
    band_bottom = LABEL_H - PAD - mac_h - 2 * mm
    qr = band_bottom - band_top
    qx = LABEL_W - PAD - qr
    lab.qr(qx, band_top, qr, dev.mac)
    val_x = x + 9 * mm
    val_w = qx - 2 * mm - val_x

    lines = [(k, v) for k, v in dev.lines if v]
    # The lines are spread so the first one's top sits on the band's top
    # and the last one's baseline on the band's bottom, like the QR.
    val_size = 9
    cap_h = val_size * 0.72
    n = max(len(lines), 1)
    pitch = (band_bottom - band_top - cap_h) / max(n - 1, 1)
    ly = band_top
    for k, v in lines:
        font = MONO if k == "VID:PID" else SANS
        if k:
            lab.captioned(x, val_x, ly, k, v, font, val_size, val_w)
        else:
            lab.fit(val_x, ly, v, font, val_size, val_w)
        ly += pitch

    y = LABEL_H - PAD - mac_h + 0.5 * mm
    lab.fit(PAD, y, dev.mac, MONO, mac_size, LABEL_W - 2 * PAD)


# --- records from probe documents --------------------------------------------

USB_SPEED = {"12": "FS 12 Mbit/s", "480": "HS 480 Mbit/s", "5000": "SS 5 Gbit/s",
             "10000": "SS+ 10 Gbit/s", "20000": "SS+ 20 Gbit/s"}
BOARD_MODEL = {"netv2": ("Alphamax", "NeTV2"), "arty": ("Digilent", "Arty A7"),
               "acorn": ("SQRL", "Acorn CLE-215+"), "jtag": ("", "FPGA")}
IDCODE_PART = {"0x362d093": "XC7A35T", "0x3631093": "XC7A100T", "0x3636093": "XC7A200T",
               "0x13631093": "XC7A100T", "0x03636093": "XC7A200T"}


@dataclass(frozen=True)
class PiLabel:
    """What the Pi label prints, from one document."""

    serial: str
    revision: str                    # the code, e.g. c04170
    rev: str                         # "1.0"
    model: str                       # "5", "3 Model B+"
    memory: str
    macs: tuple[tuple[str, str], ...]        # (kind, mac), eth first
    header: tuple[str, ...]
    hat_uuid: str | None = None
    wlan_note: str | None = None


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
class UsbLabel:
    """What a USB network adapter's label prints."""

    title: str
    kind: str
    mac: str
    vidpid: str
    host: str
    lines: tuple[tuple[str, str], ...]


def pi_record(doc):
    """The Pi label's record from a document."""
    s = doc.summary
    rev = decode_revision(s.revision)
    macs = [(m.kind, m.mac) for m in s.macs if m.kind in ("eth", "wlan")]
    wlan_note = None
    if not any(k == "wlan" for k, _ in macs):
        derived = derived_wlan_mac(s.serial, [{"kind": m.kind, "mac": m.mac} for m in s.macs])
        if derived:
            macs.append(("wlan", derived))
        elif rev.is_pi5 or rev.model.startswith("4"):
            wlan_note = "radio disabled, not readable"
    order = {"eth": 0, "wlan": 1}
    macs.sort(key=lambda m: order[m[0]])
    return PiLabel(serial=s.serial, revision=rev.code, rev=rev.revision, model=rev.model,
                   memory=rev.memory, macs=tuple(macs), header=tuple(s.header),
                   hat_uuid=s.hat_uuid, wlan_note=wlan_note)


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

def all_labels(docs, only, pinned_names=None):
    if "fpga" in only:
        for b in fpga_records(docs, pinned_names):
            yield b.kind, b.name or b.model, draw_fpga, b
    if "rpi" in only:
        for host in sorted(docs):
            p = pi_record(docs[host])
            yield "rpi", f"Pi {p.model} {p.memory} {p.serial}", draw_rpi, p
    if "usb" in only:
        for u in usb_records(docs):
            yield "usb", f"{u.title} {u.mac}", draw_usb, u


def label_origin(index):
    """Bottom-left corner of label `index` on its sheet, reading order."""
    col, row = index % COLS, index // COLS
    x = MARGIN_X + col * (LABEL_W + GAP_X)
    y = PAGE_H - MARGIN_Y - (row + 1) * LABEL_H - row * GAP_Y
    return x, y


def render(docs, out, only=("fpga", "rpi", "usb"), start=0, outline=False,
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
    ap.add_argument("--only", action="append", choices=["fpga", "rpi", "usb"])
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
    only = args.only or ["fpga", "rpi", "usb"]
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
