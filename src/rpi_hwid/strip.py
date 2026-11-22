"""Strip labels: each kind of label laid out for one sticker of a label
printer's roll -- for now DYMO 99010, 28 x 89 mm -- carrying the same facts
and codes as its full label, re-flowed along a strip a third as tall
(docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md).

A layout takes (strip, record), the record its full layout takes, and is
registered in STRIP_OF against that full layout's draw function, so the
rows labels.whole_labels makes for a sheet make a roll unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from reportlab.lib.colors import black
from reportlab.lib.units import mm

from rpi_hwid import labels as lb
from rpi_hwid import riscv

if TYPE_CHECKING:
    from collections.abc import Callable

# The strip is the roll's box turned a quarter: its length runs up the page.
_, _, STRIP_H, STRIP_W = lb.ROLLS["dymo-99010"].strip_box


class Strip(lb.Label):
    """A ``labels.Label`` the strip's size, origin top-left, y down."""

    def __init__(self, c: Any, x0: float, y0: float) -> None:
        super().__init__(c, x0, y0, STRIP_W, STRIP_H)


# full draw function -> strip draw function; each layout registers itself
STRIP_OF: dict[Callable[..., Any], Callable[..., Any]] = {}


# --- board: Raspberry Pi, Orange Pi, RISC-V, x86 ------------------------------
#
#  [mark] Title                [icons] | eth MAC            [eth ]
#  [    ]              [cameras]       | 98:fe:54:13:f5:75  [ QR ]
#  HAT    subtitle  /  HAT line  /  uuid, across to the codes
#  [ser ] serial                       | wlan MAC           [wlan]
#  [ QR ] c36b093f773d46b8             | b8:27:eb:02:a3:24  [ QR ]
#
# The full label's columns turned into bands: the mark and the eth MAC
# along the top, the serial and the wlan MAC along the bottom, each code at
# its band's end, and between them the lines too long for half a strip
# (a subtitle, a HAT's name, a uuid) running the strip's whole length.

BOARD_MAC_QR = 7.5 * mm         # 25 modules at 0.30 mm (0.28 on the full label)
BOARD_MAC_X = 39.5 * mm         # where the MAC column starts
BOARD_CAMERA = 2.8 * mm         # a camera mark's height: its words at 4.6 pt
BOARD_GAP = 1.1 * mm            # between the corner's marks, as the full label


def _row_width(s: Strip, cams: list[Any]) -> float:
    """The width of camera marks set in a row."""
    return (sum(lb.camera_width(s, c, BOARD_CAMERA) for c in cams)
            + BOARD_GAP * max(0, len(cams) - 1))


def _draw_cameras(s: Strip, cams: list[Any], right: float, y: float) -> None:
    """Camera marks in a row ending flush at `right`, their tops at `y`."""
    x = right - _row_width(s, cams)
    for c in cams:
        lb.mark_camera(s, c, x, y, BOARD_CAMERA)
        x += lb.camera_width(s, c, BOARD_CAMERA) + BOARD_GAP


def _board_corner(s: Strip, b: Any, left: float, right: float, y: float,
                  cam_y: float) -> tuple[float, list[Any]]:
    """The fan and clock right-aligned at `right` on the title's line at
    `y`, and the cameras in a row under them at `cam_y`, no further left
    than `left`. Returns the width the title gives up, and the cameras that
    row has no room for: the full label stacks its cameras two deep, so a
    multiplexer's four can be more than one row holds, and the rest go to
    the end of the subtitle's line (`_board_spill`).

    The full label's own corner is measured first, for its refusal: a strip
    refuses exactly the boards the full label refuses, and takes every set
    of cameras it takes. The marks are a little taller than the full
    label's 2.6 mm, whose words come out at 4.3 pt: here they keep to the
    4.5 pt floor."""
    lb.board_corner(s, b, lb.board_geometry(b, lb.BOARD_COLUMNS_X).head_w)
    icon = 2.6 * mm
    icons = [m for m, on in ((lb.mark_fan, b.fan), (lb.mark_clock, b.rtc_battery)) if on]
    icons_w = len(icons) * icon + max(0, len(icons) - 1) * BOARD_GAP
    x = right - icons_w
    for draw in icons:
        draw(s, x, y - 0.2 * mm, icon)          # centred on the title's capitals
        x += icon + BOARD_GAP
    cams = list(b.cameras or ())
    k = len(cams)                               # the row takes cams[k:]
    while k and _row_width(s, cams[k - 1:]) <= right - left:
        k -= 1
    _draw_cameras(s, cams[k:], right, cam_y)
    return (icons_w + 1.5 * mm if icons else 0), cams[:k]


def _two_lines(s: Strip, x: float, y: float, cap: str, val: str, font: str, size: float,
               w: float, color: Any = black) -> None:
    """A grey caption over its value, from `y`, the value `size` or what
    fits `w`."""
    s.text(x, y, cap, lb.SANS, lb.CAPTION, color=lb.GREY)
    s.fit(x, y + lb.CAPTION * 0.72 + 0.7 * mm, val, font, size, w, color=color)


def draw_board_strip(s: Strip, b: Any) -> None:
    """The board label along a strip: the mark, title and eth MAC along the
    top; the serial and wlan MAC along the bottom; the subtitle, HAT and
    uuid (or ISA and harts, or maker) across the middle."""
    W, H = s.w, s.h  # noqa: N806
    q, lc = BOARD_MAC_QR, lb.BOARD_SERIAL_QR
    bx = lc + 1.5 * mm                      # the text's left edge
    qx = W - q                              # the MAC codes' left edge
    mx = BOARD_MAC_X
    tw = qx - 1.5 * mm - mx                 # the MAC column's width
    left_w = mx - 1.5 * mm - bx             # the title's and serial's
    mid_w = qx - 1.5 * mm - bx              # a middle line's
    mac_size = s.fitted_size("00:00:00:00:00:00", lb.MONO, 13, tw)
    block = (lb.CAPTION + mac_size) * 0.72 + 0.7 * mm
    top = (q - block) / 2                   # the top band's caption line
    bot = H - q + top                       # the bottom band's

    # --- top band: mark, title, corner; eth MAC ---
    # the title and serial a step under the MACs, which are what the label is for
    lb.mark_fitted(s, b.mark, 0, 0, lc, q)
    title_size = mac_size - 0.5
    cam_y = top + title_size * 0.72 + 1.0 * mm      # under the title's descenders
    corner, spill = _board_corner(s, b, bx, mx - 1.5 * mm, top, cam_y)
    s.fit(bx, top, b.title, lb.SANS_BOLD, title_size, left_w - corner)

    # --- bottom band: the serial and its code ---
    if b.serial:
        s.qr(0, H - lc, lc, b.serial, error="l")
        _two_lines(s, bx, bot, "serial", b.serial, lb.MONO, mac_size - 0.5, left_w)
    else:
        # a PC whose firmware carries no serial (an unread one is refused)
        s.no_code(0, H - lc, lc, "none")
        s.text(bx, bot, "no serial in firmware", lb.SANS, lb.CAPTION, color=lb.GREY)

    # --- the MACs, each beside its code at the strip's end ---
    macs = dict(b.macs)
    reasons = {"eth": b.eth_note or "not read", "wlan": b.wlan_note or "not read"}
    for kind, y, qy in (("eth", top, 0), ("wlan", bot, H - q)):
        mac = macs.get(kind)
        if mac:
            s.qr(qx, qy, q, mac)
            _two_lines(s, mx, y, kind + " MAC", mac, lb.MONO, mac_size, tw)
        else:
            _two_lines(s, mx, y, kind + " MAC", reasons[kind], lb.SANS, 8, tw, color=lb.NOTE)

    # --- the middle: the lines too long for half a strip ---
    # The left column between the mark and the serial's code holds the
    # captions, or the mark that stands for them: the RISC-V mark beside the
    # ISA and harts lines, a PC maker's mark (beside the project's on the
    # full label, here under it, so the title keeps its room).
    # Cameras the row under the title has no room for end the subtitle's
    # line, which gives up their width (it keeps 51 mm, more than the full
    # label's subtitle has).
    spill_w = _row_width(s, spill) + 1.5 * mm if spill else 0
    rows: list[Callable[[float], Any]] = [
        lambda y: s.fit(bx, y, b.subtitle, lb.SANS, 6.5, mid_w - spill_w)]
    side = None                             # the mark in the left column
    if b.kind == "riscv":
        path = side = lb.artwork(riscv.RISCV_MARK)
        rows.append(lambda y: s.captioned(0, bx, y, "" if path else "ISA", b.isa or "",
                                          lb.MONO_REGULAR, 6.5, mid_w, min_size=4.5))
        if b.riscv_line:
            rows.append(lambda y: s.fit(bx, y, b.riscv_line, lb.SANS, 6.5, mid_w))
    elif b.kind == "x86":
        side = lb.artwork(b.maker_mark) if b.maker_mark else None
        if not side:
            rows.append(lambda y: s.captioned(0, bx, y, "maker", b.maker or "maker not named",
                                              lb.SANS, 7, mid_w))
    else:
        rows.append(lambda y: s.captioned(0, bx, y, "HAT",
                                          "; ".join(b.header) if b.header else "none",
                                          lb.SANS, 7, mid_w))
        if b.hat_uuid:
            rows.append(lambda y: s.captioned(0, bx, y, "uuid", b.hat_uuid, lb.MONO_REGULAR,
                                              6.5, mid_w, min_size=5))
    # spread evenly between the eth MAC's value and the wlan MAC's caption
    row_h = 6.5 * 0.72                     # a middle line's cap height
    lo, hi = top + block, bot
    if b.cameras:
        lo = max(lo, cam_y + BOARD_CAMERA)
    pitch = (hi - lo) / (len(rows) + 1)
    ys = [lo + i * pitch - row_h / 2 for i in range(1, len(rows) + 1)]
    for y, row in zip(ys, rows, strict=True):
        row(y)
    if spill:
        # centred between the eth MAC's value and the line under the subtitle
        below = ys[1] if len(ys) > 1 else hi
        _draw_cameras(s, spill, qx - 1.5 * mm, (top + block + below - BOARD_CAMERA) / 2)
    if side:
        # a maker's mark has the column to itself; the RISC-V mark spans the
        # ISA and harts rows (all but the subtitle's)
        y0, y1 = q + 0.8 * mm, H - lc - 0.8 * mm
        if b.kind == "riscv":
            y0, y1 = ys[1] - 0.5 * mm, min(y1, ys[-1] + row_h + 0.5 * mm)
        lb.mark_in_box(s, side, 0, y0, lc, y1 - y0)


STRIP_OF[lb.draw_board] = draw_board_strip
