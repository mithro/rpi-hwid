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
#  [mark] Title ............................ [fan][clock]  [eth ]
#  [    ]              [cameras]       | eth MAC            [    ]
#  [    ]                              | 98:fe:54:13:f5:75  [ QR ]
#  HAT    subtitle  /  HAT line  /  uuid, across to the codes
#  [ser ] serial                       | wlan MAC           [wlan]
#  [ QR ] c36b093f773d46b8             | b8:27:eb:02:a3:24  [ QR ]
#
# The full label's columns turned into bands: the mark, title and eth MAC
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


def _board_corner(s: Strip, b: Any, icons_right: float, y: float, left: float,
                  right: float, cam_y: float) -> tuple[float, list[Any]]:
    """The fan and clock right-aligned at `icons_right` on the title's line
    at `y`, and the cameras in a row under the title at `cam_y`, between
    `left` and `right`. Returns the width the title gives up, and the
    cameras that row has no room for: the full label stacks its cameras two
    deep, so a multiplexer's four can be more than one row holds, and the
    rest go to the end of the subtitle's line.

    The full label's own corner is measured first, for its refusal: a strip
    refuses exactly the boards the full label refuses, and takes every set
    of cameras it takes. The marks are a little taller than the full
    label's 2.6 mm, whose words come out at 4.3 pt: here they keep to the
    4.5 pt floor."""
    lb.board_corner(s, b, lb.board_geometry(b, lb.BOARD_COLUMNS_X).head_w)
    icon = 2.6 * mm
    icons = [m for m, on in ((lb.mark_fan, b.fan), (lb.mark_clock, b.rtc_battery)) if on]
    icons_w = len(icons) * icon + max(0, len(icons) - 1) * BOARD_GAP
    x = icons_right - icons_w
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
    left_w = mx - 1.5 * mm - bx             # the serial's
    mid_w = qx - 1.5 * mm - bx              # a middle line's
    mac_size = s.fitted_size("00:00:00:00:00:00", lb.MONO, 13, tw)
    block = (lb.CAPTION + mac_size) * 0.72 + 0.7 * mm
    bot = H - q + (q - block) / 2           # the wlan MAC's caption line

    # --- top band: mark; the title across to the codes, the fan and clock
    # at its end; under it the cameras, then the eth MAC beside its code ---
    # The title has the strip's whole width (54.7 mm with both icons, where
    # the full label's has 41.9 mm at most), so a name the full label prints
    # whole is never cut here. It and the serial are a step under the MACs,
    # which are what the label is for.
    lb.mark_fitted(s, b.mark, 0, 0, lc, q)
    title_size = mac_size - 0.5
    t0 = 0.2 * mm                           # the title's cap-height top
    top = t0 + title_size * 0.72 + 0.8 * mm  # the eth MAC's caption line
    corner, spill = _board_corner(s, b, qx - 1.5 * mm, t0, bx, mx - 1.5 * mm, top)
    s.fit(bx, t0, b.title, lb.SANS_BOLD, title_size, mid_w - corner)

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
    lo, hi = top + block, bot               # the cameras end above `lo`
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


# --- FPGA boards: NeTV2, Arty, Acorn, PCILeech, Cynthion, a bare chain -------
#
#  [    ] [mark]
#  [    ] hoopoe
#  [ QR ] Arty A7-35T  ·  XC7A35T
#  [    ] S/N 210319A43AD3
#  [    ]                     Micron N25Q128  ·  16 MiB  flash  [fl ]
#  [    ]                  235351451900080037091015126b  uid    [QR ]
#  Device DNA  0x0064f5483229085c
#
# The full label's two columns side by side again, the identity's code at
# the left and the board's column beside it, with the flash rows at the
# column's foot against the flash's own code, and the identifier along the
# bottom after its caption (over it, on the full label). Every row has more
# room than its counterpart on the full label -- the word and the model line
# the column's whole width, the flash rows and the identifier everything up
# to the flash's code -- so nothing the full label prints whole is cut or set
# smaller than that label's floor for it here.

FPGA_FOOT = 14                  # the identifier's size (15 pt on the full label)
FPGA_FLASH_QR = 6.5 * mm        # the flash's code, as the full label's
FPGA_ROW = 7                    # a row of the board's column
FPGA_PITCH = 2.3 * mm           # between the flash rows, as the full label's
FPGA_WORD = 13                  # the board's word (20 pt on the full label)


def draw_fpga_strip(s: Strip, board: Any) -> None:
    """The FPGA label along a strip: the identity's code at the left; the
    mark, the board's word, model and die and an Arty's serial beside it;
    the flash rows against the flash's own code under them; the identifier
    across the foot."""
    W, H = s.w, s.h  # noqa: N806
    fq = FPGA_FLASH_QR
    fx = W - fq                             # the flash code's left edge
    row = FPGA_ROW * 0.72                   # a row's cap height
    foot_y = H - FPGA_FOOT * 0.72           # the identifier's cap-height top

    # --- the foot: the identifier after its caption, the whole width short
    # of the flash's code, which comes down beside it ---
    cap_w = s.width(board.ident_caption, lb.SANS, lb.CAPTION)
    val_x = cap_w + s.CAPTION_GAP
    s.captioned(0, val_x, foot_y, board.ident_caption, board.ident, lb.MONO, FPGA_FOOT,
                fx - 1.5 * mm - val_x)

    # --- the identity's code, as tall as the room above the foot ---
    q = foot_y - 1.2 * mm
    ident = board.ident or board.serial
    if ident:
        s.qr(0, 0, q, ident)
    x = q + 1.5 * mm                        # the board's column
    col_w = W - x

    # --- the flash rows, flush right against the flash's code, the uid's
    # row just above the foot; both centred on the code as on the
    # full label, the flash row alone beside a "no id" square ---
    uid_y = foot_y - 0.9 * mm - row
    flash_y = uid_y - FPGA_PITCH
    fy = (flash_y + uid_y + row) / 2 - fq / 2
    if not board.flash_uid:
        flash_y = fy + (fq - row) / 2
    cap_x = fx - 1.5 * mm - max(s.width(c, lb.SANS, lb.CAPTION) for c in ("flash", "uid"))
    s.captioned_after(x, cap_x, flash_y, "flash", board.flash, lb.SANS, FPGA_ROW, min_size=5)
    if board.flash_uid:
        s.captioned_after(x, cap_x, uid_y, "uid", board.flash_uid, lb.MONO_REGULAR,
                          FPGA_ROW, min_size=5)
        s.qr(fx, fy, fq, board.flash_uid, error="l")
    elif board.flash:
        s.no_code(fx, fy, fq)

    # --- the board's column, from the top: the maker's mark, the word, the
    # model and die, an Arty's serial ---
    # a compact mark a little taller than a wide one, as on the full label
    mark_h = 4.4 * mm if board.kind in ("arty", "cynthion") else 4 * mm
    lb.mark_maker(s, board, x, 0, mark_h)
    word, line = lb.fpga_headline(board)
    y = mark_h + 0.8 * mm                   # clear of a maker's name's descenders
    s.fit(x, y, word, lb.SANS_BOLD, FPGA_WORD, col_w)
    y += FPGA_WORD * 0.72 + 1.2 * mm        # clear of the word's descenders
    s.fit(x, y, line, lb.SANS, FPGA_ROW, col_w)
    if board.kind == "arty":
        y += row + 0.8 * mm
        s.captioned(x, x + 5 * mm, y, "S/N", board.serial, lb.MONO, FPGA_ROW,
                    fx - 1.5 * mm - x - 5 * mm)


STRIP_OF[lb.draw_fpga] = draw_fpga_strip


# --- USB network adapters ----------------------------------------------------
#
#  [usb][rj45] Linksys Linksys USB3GIGV1, across the whole strip
#      USB  3.00 SS 5 Gbit/s                                    [    ]
#   driver  r8152                                               [ QR ]
#  VID:PID  13b1:0041                                           [    ]
#  MAC                                                          [    ]
#  60:38:e0:e3:56:4f                                            [    ]
#
# The full label as it is, squeezed: the glyphs and title along the top, the
# facts, then the MAC under its caption along the foot, and the MAC's code at
# the right end under the title. Every row has more room than its
# counterpart on the full label (the title 65 mm where it has 42.5, a fact's
# value 51.7 mm where it has 29.7, the MAC 60.2 mm where it has 58.5), so
# nothing the full label prints whole is cut or set smaller than that
# label's floor for it here.

USB_BAND = 4.5 * mm             # the glyphs' band (5 mm on the full label)
USB_TITLE = 11                  # the title's size (12 pt on the full label)
USB_FACT = 8                    # a fact's value (9 pt on the full label)
USB_MAC = 14                    # the MAC (16 pt on the full label)
USB_VALUE_X = 8.5 * mm          # a fact's value, after its caption, as the full label


def draw_usb_strip(s: Strip, dev: Any) -> None:
    """The USB adapter label along a strip: bus and link glyphs with the
    chip name across the top, the facts under them, the MAC under its
    caption along the foot, and the MAC's code at the right end."""
    W, H = s.w, s.h  # noqa: N806
    h = USB_BAND

    # --- the top: the glyphs, and the title the whole strip's width ---
    w = lb.mark_usb(s, 0, h * 0.15, h * 0.7) + 1.5 * mm
    w += (lb.mark_wifi if dev.kind == "wifi" else lb.mark_rj45)(s, w, 0, h)
    tx = w + 2 * mm
    s.fit(tx, (h - USB_TITLE * 0.72) / 2, dev.title, lb.SANS_BOLD, USB_TITLE, W - tx)

    # --- the MAC's code: the right end, under the title's band ---
    top = h + 1.5 * mm
    qr = H - top
    qx = W - qr
    s.qr(qx, top, qr, dev.mac)
    room = qx - 2 * mm

    # --- the foot: the MAC under its caption ---
    cap_gap = 0.7 * mm
    mac_y = H - USB_MAC * 0.72
    cap_y = mac_y - cap_gap - lb.CAPTION * 0.72
    s.text(0, cap_y, "MAC", lb.SANS, lb.CAPTION, color=lb.GREY)
    s.fit(0, mac_y, dev.mac, lb.MONO, USB_MAC, room)

    # --- the facts, spread evenly between the title's band and the MAC's
    # caption, each row clear of the one above's descenders. usb_records
    # makes three; more are set smaller, to keep at least 0.4 mm apart ---
    facts = lb.usb_facts(dev)
    n = max(1, len(facts))
    lo = top + 0.3 * mm
    avail = cap_y - 0.8 * mm - lo           # cap height and descenders, every row
    size = min(USB_FACT, (avail - (n - 1) * 0.4 * mm) / (0.93 * n))
    gap = (avail - n * 0.93 * size) / max(1, n - 1)
    if n == 1:
        lo += gap / 2                       # a lone fact in the middle
    val_w = room - USB_VALUE_X
    for i, (k, v, font) in enumerate(facts):
        y = lo + i * (0.93 * size + gap)
        if k:
            s.captioned(0, USB_VALUE_X, y, k, v, font, size, val_w)
        else:
            s.fit(USB_VALUE_X, y, v, font, size, val_w)


STRIP_OF[lb.draw_usb] = draw_usb_strip


# --- Tiny Tapeout demo boards -------------------------------------------------
#
#  [TT] TT06                          [carrier] pink           [         ]
#  ASIC  ·  sky130     [efabless][sky]  [ swatch] teal           [chip QR  ]
#       board  TT06+  ·  Rev 2.0.1   [demo   ] pink           [         ]
#  ROM commit  0f5a1b2c              [ board ] teal           [         ]
#  RP2040 id (USB serial)                        id           chip page
#  E6614C311B7A7A37                           [id QR]
#
# The full label's left column as it is (its width, its rows, the same
# room for every value), the two board samples beside it instead of under
# it, the chip page's code at the right end, and the demo board's id along
# the foot with its own code at the row's end. The id has more room than on
# the full label (50.9 mm, 50 there) and its code is bigger (7.6 mm, 6.5
# there), so every id the full label prints whole is whole here, its code
# at the module floor or above. Every other value has at least its
# full-label room (the headline 32.5 mm, 31 there; a colour name 9 mm, 6.75
# there; the board and ROM rows and the subtitle's free space for the
# shuttle's marks the same), so nothing the full label prints whole is cut
# or set smaller than that label's floor for it here, and the shuttle's
# marks, starting smaller, are drawn wherever the full label draws them.

TT_QR = 17 * mm                 # the chip page's code, as the full label's
TT_COL = 38.5 * mm              # the left column, as the full label's
TT_HEAD = 16                    # the headline (22 pt on the full label)
TT_MARK = 4.5 * mm              # the Tiny Tapeout mark (6 mm on the full label)
TT_SHUTTLE_MARK = 3.0 * mm      # the shuttle's marks, at most (3.4 mm there)
TT_ROW = 7.5                    # the board and ROM rows, as the full label's
TT_CAP_W = 14 * mm              # their values' indent, as the full label's
TT_SWATCH = (10 * mm, 5.5 * mm)  # a board's sample (11 x 5.5 mm there)
TT_ID = 15                      # the demo board's id, as the full label's
TT_ID_QR = 7.6 * mm             # its code: 29 modules at 0.26 mm (6.5 mm there)


def draw_tinytapeout_strip(s: Strip, tt: Any) -> None:
    """The Tiny Tapeout label along a strip: the mark and shuttle, the
    subtitle and the shuttle's marks, the board and ROM rows at the left;
    the two board samples beside them; the chip page's code at the right
    end; the demo board's id along the foot, its code at the row's end."""
    W, H = s.w, s.h  # noqa: N806
    qx = W - TT_QR

    # --- the chip page's code, its caption under it ---
    s.qr(qx, 0, TT_QR, tt.url)
    s.text(qx + TT_QR / 2, TT_QR + 0.4 * mm, "chip page", lb.SANS, lb.CAPTION,
           align="centre", color=lb.GREY)

    # --- the foot: the id under its caption, its code at the row's end ---
    id_y = H - 0.3 * mm - TT_ID * 0.72       # the id's cap-height top
    cap_y = id_y - 0.7 * mm - lb.CAPTION * 0.72
    s.text(0, cap_y, "%s id (USB serial)" % (tt.mcu or "RP2"), lb.SANS, lb.CAPTION,
           color=lb.GREY)
    if tt.usb_serial:
        qr_x = qx - 2 * mm - TT_ID_QR
        s.qr(qr_x, H - TT_ID_QR, TT_ID_QR, tt.usb_serial, error="l")
        s.text(qr_x + TT_ID_QR / 2, H - TT_ID_QR - 0.6 * mm - lb.CAPTION * 0.72, "id",
               lb.SANS, lb.CAPTION, align="centre", color=lb.GREY)
        s.fit(0, id_y, tt.usb_serial, lb.MONO, TT_ID, qr_x - 2 * mm)
    else:
        s.rule(0, id_y + TT_ID * 0.72 + 0.2 * mm, qx - 2 * mm)

    # --- the left column: the mark and headline, the subtitle and the
    # shuttle's marks, the board and the ROM ---
    col_w = TT_COL
    w = lb.mark_tinytapeout(s, 0, 0, TT_MARK)
    tx = w + 1.5 * mm if w else 0
    head_cap = TT_HEAD * 0.72
    t0 = (TT_MARK - head_cap) / 2           # the headline's capitals centred on the mark
    s.fit(tx, t0, tt.headline, lb.SANS_BOLD, TT_HEAD, col_w - tx)
    y = t0 + head_cap + 0.21 * TT_HEAD + 0.4 * mm   # clear of the headline's descenders
    lb.tinytapeout_subtitle(s, tt, 0, y, col_w, TT_SHUTTLE_MARK)
    y += TT_SHUTTLE_MARK
    s.captioned(0, TT_CAP_W, y, "board", tt.demoboard_text, lb.SANS, TT_ROW,
                col_w - TT_CAP_W)
    y += 3.0 * mm
    caption, text, font = lb.tinytapeout_rom_line(tt)
    s.captioned(0, TT_CAP_W, y, caption, text, font, TT_ROW, col_w - TT_CAP_W)

    # --- the two boards' samples, beside the column ---
    sw, sh = TT_SWATCH
    sx = col_w + 2 * mm
    tw = qx - 2 * mm - (sx + sw + 1 * mm)
    for i, sample in enumerate(lb.tinytapeout_samples(tt)):
        lb.tinytapeout_sample(s, sx, 0.2 * mm + i * (sh + 1.5 * mm), sw, sh, tw, sample)


STRIP_OF[lb.draw_tinytapeout] = draw_tinytapeout_strip


# --- software-defined radios --------------------------------------------------
#
# The SDR label lives in sdr_labels.py, and its strip beside it.

from rpi_hwid import sdr_labels  # noqa: E402  (it imports labels, not strip)

STRIP_OF[sdr_labels.draw_sdr] = sdr_labels.draw_sdr_strip
