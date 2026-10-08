"""Micro labels for ESP32 GPS nodes: the ESP32 label and its receiver.

The nodes github.com/mithro/esp32-to-gps builds are an ESP32-C3 SuperMini
wired to a GPS receiver board: the GoouuuTech GT-U7 (a u-blox 7), a u-blox
MAX-M10S breakout, the Quectel LC29H(AA) board, or the Huawei WD22UGRC card
with its u-blox LEA-M8T. Their label is the plain ESP32 label from
``rpi_hwid.esp32_micro`` -- the part number and its spec strip, the Wi-Fi
MAC in the QR and along the foot, the flash, and both the node's ids --
with the receiver added, as the 433 MHz node's label adds its radio
(``rpi_hwid.esp32_433_micro``):

  * an antenna whose mast is "GPS", after the Wi-Fi and USB glyphs. Not a
    band: the u-blox parts here are L1 receivers but the LC29H(AA) hears
    L1 and L5, and neither "L1L5" nor "1575" fits the mast;
  * under the ids, a line naming the receiver: its maker's mark, then the
    model and the firmware it reports: "[u-blox] u-blox M10 · SPG 5.10",
    "[Quectel] LC29H(AA) · LC29HAANR11A05S".

Everything on the receiver line was read from the node
(``rpi_hwid.esp32_gps``): the receiver's own answer to UBX MON-VER or
Quectel's $PQTMVERNO, as the node's firmware relays it. A GPS receiver has
no serial number to print, and the board it sits on is not read: a
MAX-M10S reports itself as an M10 (its MON-VER carries no MOD= extension),
so the label names the receiver, never the board.

An ESP32 that was never asked about a receiver is not a GPS node, and gets
only the plain label. One that was asked and has not found its receiver
yet likewise. One whose read failed is an error that names the host and
what to do.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from reportlab.lib.units import mm

from rpi_hwid import esp32_micro, labels, micro
from rpi_hwid.micro import Icon, MicroLabel

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

KIND = "esp32-gps"
# A node this kind labels gets this label instead of the plain one when both
# kinds are printed (rpi_hwid.micro.sticker_rows).
REPLACES = esp32_micro.KIND

BAND = "GPS"

# The firmware's name for each receiver type (GpsModule) -> its maker.
MAKER = {"ublox7": "u-blox", "m8": "u-blox", "m10": "u-blox", "lc29h": "Quectel"}
MAKER_MARK = {"u-blox": "u-blox.png", "Quectel": "quectel.svg"}

LINE_H = 2.1 * mm        # the mark at most this tall
MARK_GAP = 0.5 * mm


class UnknownReceiverError(ValueError):
    """The node drives a receiver type this module has no maker for."""


def read_command(host: str, dev: esp32_micro.Esp32Device) -> str:
    return (f"rpi-hwid esp32 --gps {dev.port}` on that host, or "
            f"`rpi-hwid collect --esp32-gps {host}={dev.port} {host}")


def maker_for(host: str, mac: str, gps: Mapping[str, Any]) -> str:
    module = gps.get("module")
    if module not in MAKER:
        raise UnknownReceiverError(
            f"{host}: the ESP32 {mac} drives a {module!r} receiver ({gps.get('model')}), "
            "which rpi_hwid.esp32_gps_micro.MAKER does not know; add it there")
    return MAKER[module]


def receiver_text(gps: Mapping[str, Any]) -> str:
    """The receiver as the line names it: its model, then its firmware.

    Both as the receiver reports them. Not the hardware string beside them
    (a u-blox part's is its generation as hex, "000A0000"; the LC29H's is
    its chip, "AG3335M") nor the protocol version: the collected document
    keeps them, and the line has room for two of these at the micro label's
    smallest size, not four.

    A Quectel firmware name starts with the model it is for
    ("LC29HAANR11A05S" is the LC29H(AA)'s release NR11A05S), which the line
    already says, and whole it does not fit (21.7 mm at 4 pt where the
    u-blox lines take 14.3), so that prefix is dropped."""
    model = str(gps.get("model") or "")
    firmware = str(gps.get("firmware") or "")
    prefix = "".join(ch for ch in model if ch.isalnum())
    if prefix and firmware.startswith(prefix) and len(firmware) > len(prefix):
        firmware = firmware[len(prefix):]
    return " · ".join(v for v in (model, firmware) if v)


def draw_receiver(host: str, mark: str, text: str) -> micro.ExtraFn:
    """The receiver line, for MicroLabel.extra: the mark, then the text, as
    large as the room left under the rows allows."""

    def draw(cell: micro.Cell, box: tuple[float, float, float, float]) -> None:
        x, y, w, h = box
        if h < micro.MIN_SIZE * 0.72:
            raise ValueError(f"{host}: no room left on the label for the receiver line")
        hm = min(h, LINE_H)
        path = labels.artwork(mark)
        if not path:
            raise ValueError(f"{host}: the {mark} artwork is missing")
        mw = hm / labels.mark_aspect(path)
        labels.mark_in_box(cell, path, x, y + (h - hm) / 2, mw, hm, align="left")
        x += mw + MARK_GAP
        room = box[0] + w - x
        size = cell.fitted_size(text, labels.SANS, micro.ROW, room, min_size=micro.MIN_SIZE)
        if cell.width(text, labels.SANS, size) > room + 0.01:
            raise ValueError(f"{host}: the receiver line {text!r} does not fit whole at "
                             f"{micro.MIN_SIZE:.1f} pt")
        cell.text(x, y + (h - size * 0.72) / 2, text, labels.SANS, size)

    return draw


def gps_label(host: str, raw: Mapping[str, Any]) -> MicroLabel | None:
    """The label of one ESP32 as verdict.esp32 holds it, or None when it is
    not a GPS node."""
    dev = esp32_micro.Esp32Device.from_dict(raw)
    if "gps" not in raw:
        return None
    gps = raw.get("gps")
    if gps is None:
        raise esp32_micro.Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} on {dev.port} was asked which GPS receiver it "
            f"drives and did not say ({raw.get('gps_error') or 'no reason given'}). Ask "
            f"again with `{read_command(host, dev)}`.")
    if not gps.get("module"):
        return None
    if not gps.get("model"):
        raise esp32_micro.Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} ({dev.port}) remembers a {gps.get('module')} "
            "receiver but did not say its model; is the receiver still wired and powered? "
            f"Read it again with `{read_command(host, dev)}`.")
    plain = esp32_micro.esp32_label(host, dev)
    maker = maker_for(host, dev.mac or "", gps)
    return dataclasses.replace(
        plain, icons=(*plain.icons, Icon("antenna", BAND)),
        extra=draw_receiver(host, MAKER_MARK[maker], receiver_text(gps)))


def devices(docs: Mapping[str, Any]) -> Iterator[tuple[str, Mapping[str, Any]]]:
    for host in sorted(docs):
        verdict = docs[host].evidence.get("verdict") or {}
        for d in verdict.get("esp32") or ():
            yield host, d


def micro_labels(docs: Mapping[str, Any]) -> list[MicroLabel]:
    out = []
    for host, raw in devices(docs):
        m = gps_label(host, raw)
        if m is not None:
            out.append(m)
    return out
