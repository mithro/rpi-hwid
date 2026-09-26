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
    rows                   up to MAX_ROWS captioned facts beside the QR
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
MAX_ROWS = 3                   # beside the subtitle's line, which is always kept

GUIDE = HexColor("#999999")    # the cut guides, lighter than any caption


def foot_top() -> float:
    """Where the foot starts: its caption, then the identifier at its largest."""
    return MICRO_H - MICRO_PAD - (CAPTION * 0.72 + CAP_GAP + IDENT_MAX * 0.72)


def band_top() -> float:
    return MICRO_PAD + HEAD_H + BAND_GAP


def qr_size() -> float:
    """The QR fills the band between the header and the foot."""
    return foot_top() - FOOT_GAP - band_top()


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


def glyph_wifi(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    return float(labels.mark_wifi(cell, x, y, size))


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


def glyph_antenna(cell: Cell, x: float, y: float, size: float, text: str) -> float:
    """A mast on a foot with a wave either side: a radio that is not Wi-Fi.
    The text, if any, is set small at the mast's foot, so
    ``Icon("antenna", "433")`` names the band."""
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
    w = size * 0.75
    if text:
        s = max(MIN_SIZE, size * 0.45)
        cell.text(x + w, y + size - s * 0.72, text, labels.SANS_BOLD, s)
        w += cell.width(text, labels.SANS_BOLD, s)
    return w


ICONS: dict[str, IconFn] = {
    "wifi": glyph_wifi,
    "usb": glyph_usb,
    "ethernet": glyph_ethernet,
    "chip": glyph_chip,
    "antenna": glyph_antenna,
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
    if it cannot fit at the smallest size the label is refused."""

    caption: str
    value: str
    mono: bool = False


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
        if len(self.rows) > MAX_ROWS:
            raise ValueError(
                f"{self.host}: the {self.title} label has {len(self.rows)} rows and a micro "
                f"label holds {MAX_ROWS}; put the rest in an extra section or leave them "
                "in the document")
        for r in self.rows:
            if not (r.value or "").strip():
                raise ValueError(
                    f"{self.host}: the {r.caption} row of the {self.title} label has no "
                    "value; leave the row out rather than print it blank")
        for i in self.icons:
            if i.name not in ICONS:
                raise ValueError(f"{self.host}: no glyph called {i.name!r} "
                                 f"(have {', '.join(sorted(ICONS))})")

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
    title_w = ix - x - (0.5 * mm if m.icons else 0)
    size = cell.fitted_size(m.title, labels.SANS_BOLD, TITLE, title_w, min_size=MIN_SIZE)
    cell.fit(x, MICRO_PAD + (HEAD_H - size * 0.72) / 2, m.title, labels.SANS_BOLD, size,
             title_w, min_size=MIN_SIZE)

    # --- the QR and the rows beside it ---
    top, q = band_top(), qr_size()
    cell.qr(MICRO_PAD, top, q, m.qr_content)
    rx = MICRO_PAD + q + 1.2 * mm
    rw = right - rx
    if m.subtitle:
        cell.fit(rx, top, m.subtitle, labels.SANS, SUBTITLE, rw, min_size=MIN_SIZE)
    y = top + ROW_PITCH
    cap_w = max([cell.width(r.caption, labels.SANS, CAPTION) for r in m.rows] or [0])
    vx = rx + cap_w + (labels.Label.CAPTION_GAP * 0.6 if cap_w else 0)
    for r in m.rows:
        font = labels.MONO_REGULAR if r.mono else labels.SANS
        vw = right - vx
        size = cell.fitted_size(r.value, font, ROW, vw, min_size=MIN_SIZE)
        if r.mono and cell.width(r.value, font, size) > vw + 0.01:
            raise ValueError(
                f"{m.host}: the {r.caption} row of the {m.title} label ({r.value!r}) does "
                f"not fit whole at {MIN_SIZE:.1f} pt, and an identifier is never elided")
        baseline = y + size * 0.72
        if r.caption:
            cell.text(vx - (labels.Label.CAPTION_GAP * 0.6), baseline - CAPTION * 0.72,
                      r.caption, labels.SANS, CAPTION, align="right", color=labels.GREY)
        cell.fit(vx, y, r.value, font, size, vw, min_size=MIN_SIZE)
        y += ROW_PITCH

    if m.extra is not None:
        ey = y - ROW_PITCH + ROW * 0.72 + 0.6 * mm if m.rows or m.subtitle else top
        m.extra(cell, (rx, ey, rw, top + q - ey))

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


def _icon_width(cell: Cell, icon: Icon) -> float:
    """The width a glyph will take at the header's height, without drawing
    it: each is a function of the height and its text."""
    h = HEAD_H
    if icon.name == "usb":
        path = labels.artwork("usb.svg")
        return 0.7 * h / labels.mark_aspect(path) if path else 0.0
    if icon.name == "ethernet":
        return h * 0.95
    if icon.name == "antenna":
        w = h * 0.75
        if icon.text:
            s = max(MIN_SIZE, h * 0.45)
            w += cell.width(icon.text, labels.SANS_BOLD, s)
        return w
    return h          # wifi, chip, and any registered glyph: a square


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
# documents, and may name in ``REPLACES`` a kind whose label it supersedes
# for the devices it labels. They are found by name, so adding one touches
# no shared file.


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
    wanted = {kind: mod for kind, mod in sorted(providers().items()) if kind in only}
    made = {kind: mod.micro_labels(docs) for kind, mod in wanted.items()}
    # A module may say it REPLACES another kind: printed together, a device
    # both label -- the same host and identifier -- gets only its label.
    for kind, mod in wanted.items():
        base = getattr(mod, "REPLACES", None)
        if base in made:
            taken = {(m.host, m.ident) for m in made[kind]}
            made[base] = [m for m in made[base] if (m.host, m.ident) not in taken]
    micros: list[MicroLabel] = [m for kind in wanted for m in made[kind]]
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
