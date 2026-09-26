"""Micro labels for ESP32 433 MHz radio nodes: the ESP32 label and its radio.

The nodes github.com/mithro/esp32-to-433mhz builds are an ESP32-C3
SuperMini wired to a 433 MHz radio board: the blue Ebyte E07-M1101D or the
green D-Sun (both a TI CC1101), or the Ai-Thinker Ra-02 breakout (a Semtech
SX1278). Their label is the plain ESP32 label from
``rpi_hwid.esp32_micro`` -- the Wi-Fi MAC in the QR and along the foot, the
chip's revision, package and flash, its 128-bit unique id -- with the radio
added:

  * an antenna glyph with the band, "433", beside the Wi-Fi and chip glyphs;
  * under the unique id, a line naming the radio: the chip maker's mark and
    the board maker's where there is one to draw, then the chip and the
    board: "[TI] CC1101 · E07-M1101D", "[Semtech] [Ai-Thinker] SX1278 · Ra-02".

The room for that line is the Bluetooth MAC's row. That row is the one fact
on the plain label that was not read from anything: it is the Wi-Fi MAC
plus two, which anyone holding the label can still work out, whereas the
unique id's two rows are the only copy of a serial read from the chip and
the radio is what this label exists to add.

Everything on the radio line was read from the node (``rpi_hwid.esp32_radio``):
the chip from the firmware's answers, and the board from the pins its
driver found the chip on at boot, which differ from board to board
(``BOARDS``, the pin maps esp32-to-433mhz documents). A radio chip has no
serial number -- neither the CC1101 nor the SX1278 carries one; the nearest
is a version register (CC1101 VERSION, SX1278 RegVersion), a silicon
revision every part of that revision shares, which the collected document
keeps and the label has no room for (``radio_text``). The band is the board's:
all three are sold and wired as 433 MHz parts, where the chips themselves
cover more.

What is not printed, because nothing reads it: the carrier. The nodes'
firmware finds the same pins whether the radio board sits in
esp32-to-433mhz's socket adapter or on the jumper wires its README
describes as the same hook-up, and the adapter's revision exists only in
its silkscreen.

An ESP32 that was never asked about a radio is not a radio node, and gets
only the plain label. One that was asked and has none fitted (the reference
board on rpi5-433mhz) likewise. One whose read failed, or whose chip answered
on pins no board uses, is an error that names the host and what to do.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from reportlab.lib.units import mm

from rpi_hwid import esp32_micro, labels, micro
from rpi_hwid.micro import Icon, MicroLabel

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

KIND = "esp32-433"
# A node this kind labels gets this label instead of the plain one when both
# kinds are printed (rpi_hwid.micro.sticker_rows).
REPLACES = esp32_micro.KIND

CHIP_MARK = {"CC1101": "ti.svg", "SX1278": "semtech.svg"}
MAKER_MARK = {"Ai-Thinker": "ai-thinker.png"}


@dataclass(frozen=True)
class RadioBoard:
    name: str
    maker: str
    chip: str
    band: str
    pins: Mapping[str, int]


# The pin each board's signals reach, as the firmware names them in its
# bring-up line. From esp32-to-433mhz's README (commit f847042, "Read as
# per-board maps"), and every one as a real boot log printed it: the blue
# board on /dev/radio-cc1101-blue and the Ra-02 on /dev/radio-sx1278-ra02
# (2026-09-26), the green board as its own log is quoted in the 433mhz
# inventory. The blue board and the Ra-02 share their SPI pins; the chip
# tells them apart.
BOARDS = (
    RadioBoard("E07-M1101D", "Ebyte", "CC1101", "433",
               {"SCK": 3, "MISO": 7, "MOSI": 4, "CS": 1, "GDO0": 10, "GDO2": 6}),
    RadioBoard("D-Sun", "D-Sun", "CC1101", "433",
               {"SCK": 1, "MISO": 3, "MOSI": 10, "CS": 6, "GDO0": 7, "GDO2": 4}),
    RadioBoard("Ra-02", "Ai-Thinker", "SX1278", "433",
               {"SCK": 3, "MISO": 7, "MOSI": 4, "NSS": 1, "RST": 10, "DIO0": 6}),
)

LINE_H = 2.1 * mm        # the marks at most this tall
MARK_GAP = 0.5 * mm


class UnknownRadioBoardError(ValueError):
    """The radio answered on pins that no board in BOARDS uses."""


def read_command(host: str, dev: esp32_micro.Esp32Device) -> str:
    return (f"rpi-hwid esp32 --radio {dev.port}` on that host, or "
            f"`rpi-hwid collect --esp32-radio {host}={dev.port} {host}")


def board_for(host: str, mac: str, radio: Mapping[str, Any]) -> RadioBoard:
    """The board whose pin map the chip answered on."""
    pins = dict(radio.get("pins") or {})
    for b in BOARDS:
        if b.chip == radio.get("chip") and pins and all(
                b.pins.get(k) == v for k, v in pins.items()):
            return b
    seen = " ".join(f"{k}={v}" for k, v in pins.items())
    raise UnknownRadioBoardError(
        f"{host}: the {radio.get('chip')} on {mac} answered on {seen}, which is no board's "
        "pin map in esp32-to-433mhz that rpi_hwid.esp32_433_micro.BOARDS knows; add the "
        "board there")


def radio_text(board: RadioBoard) -> str:
    """The chip and the board. Not the chip's version register: that is a
    silicon revision, not this radio's own, and with the marks beside it the
    line has room at the micro label's smallest size for the board's name or
    for the register, not both (the real E07-M1101D line with it came out
    0.9 mm too long); the collected document keeps it."""
    return f"{board.chip} · {board.name}"


def marks(board: RadioBoard) -> tuple[str, ...]:
    """The chip maker's mark, then the board maker's where there is one."""
    return tuple(m for m in (CHIP_MARK.get(board.chip), MAKER_MARK.get(board.maker)) if m)


def draw_radio(host: str, board: RadioBoard, text: str) -> micro.ExtraFn:
    """The radio line, for MicroLabel.extra: the marks, then the text, as
    large as the room left under the rows allows."""

    def draw(cell: micro.Cell, box: tuple[float, float, float, float]) -> None:
        x, y, w, h = box
        if h < micro.MIN_SIZE * 0.72:
            raise ValueError(f"{host}: no room left on the label for the radio line")
        hm = min(h, LINE_H)
        for name in marks(board):
            path = labels.artwork(name)
            if not path:
                raise ValueError(f"{host}: the {name} artwork is missing")
            mw = hm / labels.mark_aspect(path)
            labels.mark_in_box(cell, path, x, y + (h - hm) / 2, mw, hm, align="left")
            x += mw + MARK_GAP
        room = box[0] + w - x
        size = cell.fitted_size(text, labels.SANS, micro.ROW, room, min_size=micro.MIN_SIZE)
        if cell.width(text, labels.SANS, size) > room + 0.01:
            raise ValueError(f"{host}: the radio line {text!r} does not fit whole at "
                             f"{micro.MIN_SIZE:.1f} pt")
        cell.text(x, y + (h - size * 0.72) / 2, text, labels.SANS, size)

    return draw


def radio_label(host: str, raw: Mapping[str, Any]) -> MicroLabel | None:
    """The label of one ESP32 as verdict.esp32 holds it, or None when it is
    not a radio node."""
    dev = esp32_micro.Esp32Device.from_dict(raw)
    if "radio" not in raw:
        return None
    radio = raw.get("radio")
    if radio is None:
        raise esp32_micro.Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} on {dev.port} was asked which radio it drives and "
            f"did not say ({raw.get('radio_error') or 'no reason given'}). Ask again with "
            f"`{read_command(host, dev)}` (this resets the node).")
    if not radio.get("present"):
        return None
    if not radio.get("pins"):
        raise esp32_micro.Esp32NotReadError(
            f"{host}: the {radio.get('chip')} on the ESP32 {dev.mac} ({dev.port}) was read "
            "without the pins its driver found it on at boot, which are what name its "
            f"board. Read it again with `{read_command(host, dev)}` (this resets the node).")
    plain = esp32_micro.esp32_label(host, dev)
    board = board_for(host, dev.mac or "", radio)
    rows = tuple(r for r in plain.rows if r.caption != "BT")
    return dataclasses.replace(
        plain, icons=(*plain.icons, Icon("antenna", board.band)), rows=rows,
        extra=draw_radio(host, board, radio_text(board)))


def devices(docs: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    for host in sorted(docs):
        verdict = docs[host].evidence.get("verdict") or {}
        for d in verdict.get("esp32") or ():
            yield host, d


def micro_labels(docs: Mapping[str, Any]) -> list[MicroLabel]:
    out = []
    for host, raw in devices(docs):
        m = radio_label(host, raw)
        if m is not None:
            out.append(m)
    return out
