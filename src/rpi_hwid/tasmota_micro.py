"""Tasmota micro labels, from the documents ``rpi_hwid.tasmota`` wrote.

One quarter-sticker label per device (see ``rpi_hwid.micro``): a smart
plug is too small for a whole sticker. The maker's mark and the model make
the title -- the Athom wordmark and "Plug V3" -- with the Tasmota mark, a
plug where the device switches mains through a relay, and the chip beside
them. The Wi-Fi MAC is the identifier, in the QR and along the foot: it is
what the sheet, the router's leases and Tasmota's own name for the device
(``tasmota-D7C0E8-0232``) all key on. Beside the QR:

  * the chip and its revision, and the flash size, in the subtitle;
  * the ESP chip id, as the device's Information page gives it -- the
    number in the sheet's Device ID column and in the web UI. Tasmota
    derives it from the eFuse MAC's low 24 bits (checked on all 52 devices
    read on 2026-09-26), so it is a second way to find the same device,
    not a second identity;
  * the flash chip's JEDEC id (manufacturer, type, capacity), read by the
    firmware at boot.

Nothing that can change is printed: not the IP, the host name, the Wi-Fi
network or the firmware version. The model is what the device says it is
-- the NAME of its template, or the module Tasmota ships for it -- rather
than what the sheet says, so a label never disagrees with the device it
is stuck to.

A label whose identifier or one of its facts was not read is not drawn:
the error names the device and the command that reads it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from reportlab.lib.colors import black

from rpi_hwid import labels, micro
from rpi_hwid.micro import Icon, MicroLabel, MicroRow

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

KIND = "tasmota"

# A maker whose own mark is shipped (see artwork/README.md), by the first
# word of the model name the device reports.
MAKER_MARKS = {"athom": "athom.png", "sonoff": "sonoff.png"}

# Models whose name does not start with their maker, as (maker, title).
# "ZHA ZBBridge" is the template the Sonoff Zigbee Bridge is flashed with:
# its BASE is ESP8266 module 75, SONOFF_ZB_BRIDGE in Tasmota's
# tasmota/include/tasmota_template.h.
KNOWN_MODELS = {"zha zbbridge": ("sonoff", "Zigbee Bridge")}

READ_WITH = ("rpi-hwid tasmota --sheet <gdoc2netcfg IoT sheet> --site <site>=<octet> "
             "--out <dir> {host}")


class TasmotaNotReadError(labels.IdentifierNotReadError):
    """A Tasmota device reached the label generator without a fact its label
    carries."""


def _not_read(host: str, what: str, why: str | None) -> TasmotaNotReadError:
    return TasmotaNotReadError(
        f"{host}: the Tasmota label needs the {what}, which was not read"
        + (f" ({why})" if why else "")
        + f". Read it with `{READ_WITH.format(host=host)}` and collect again.")


# --- the plug glyph -------------------------------------------------------------


def glyph_plug(cell: micro.Cell, x: float, y: float, size: float, text: str) -> float:
    """A mains plug seen from the side: two pins over a body, and the cord
    leaving it -- a device that switches mains through a relay."""
    c = cell.c
    c.setStrokeColor(black)
    c.setFillColor(black)
    body_w, body_h = size * 0.62, size * 0.42
    bx, by = x + (size - body_w) / 2, y + size * 0.26
    px, py = cell.pt(bx, by + body_h)
    c.roundRect(px, py, body_w, body_h, size * 0.08, stroke=0, fill=1)
    pin_w, pin_h = size * 0.1, size * 0.26
    for fx in (0.3, 0.7):
        qx, qy = cell.pt(bx + body_w * fx - pin_w / 2, by)
        c.rect(qx, qy, pin_w, pin_h, stroke=0, fill=1)
    c.setLineWidth(size * 0.09)
    c.setLineCap(1)
    top = cell.pt(x + size / 2, by + body_h)
    bottom = cell.pt(x + size / 2, y + size * 0.97)
    c.line(top[0], top[1], bottom[0], bottom[1])
    c.setLineCap(0)
    c.setLineWidth(1)
    return size


def glyph_tasmota(cell: micro.Cell, x: float, y: float, size: float, text: str) -> float:
    """Tasmota's own mark, the house with the power symbol in it."""
    # rpi_hwid.labels is untyped; these are its signatures
    artwork: Callable[[str], str | None] = labels.artwork
    draw_svg: Callable[[str, float, float, float], float] = cell.svg
    path = artwork("tasmota.svg")
    if not path:
        return 0.0
    return float(draw_svg(path, x, y, size))


micro.ICONS.setdefault("plug", glyph_plug)
micro.ICONS.setdefault("tasmota", glyph_tasmota)


# --- the label ----------------------------------------------------------------------


def chip_glyph_text(chip: str) -> str:
    """What the chip glyph says: ``C3`` for an ESP32-C3, ``32`` for an
    original ESP32, ``8266`` for an ESP8266EX."""
    m = re.fullmatch(r"ESP32-?([A-Z]\d+)", chip, re.I)
    if m:
        return m.group(1).upper()
    if re.fullmatch(r"ESP32", chip, re.I):
        return "32"
    m = re.fullmatch(r"ESP(8266|8285)\w*", chip, re.I)
    return m.group(1) if m else ""


def maker_and_title(model: str) -> tuple[str | None, str]:
    """(mark file or None, title) for a model name as the device gives it:
    ``Athom Plug V3`` -> (athom.png, ``Plug V3``); ``Athom_IR_Remote`` ->
    (athom.png, ``IR Remote``); a maker with no mark keeps its name."""
    name = re.sub(r"[_\s]+", " ", model).strip()
    known = KNOWN_MODELS.get(name.lower())
    if known:
        return MAKER_MARKS.get(known[0]), known[1]
    first, _, rest = name.partition(" ")
    mark = MAKER_MARKS.get(first.lower())
    if mark and rest:
        return mark, rest
    return None, name


def _size(kb: Any) -> str:
    if not isinstance(kb, int) or kb <= 0:
        return ""
    return f"{kb // 1024} MB" if kb % 1024 == 0 else f"{kb} KB"


def tasmota_label(host: str, t: Mapping[str, Any]) -> MicroLabel:
    """The label for one device's ``verdict.tasmota``."""
    errors = t.get("read_errors") or {}
    if not t.get("model"):
        raise _not_read(host, "model (its Module and Template)",
                        errors.get("module") or errors.get("template"))
    if not t.get("chip"):
        raise _not_read(host, "chip (StatusFWR.Hardware of Status 0)", None)
    if t.get("esp_chip_id") is None:
        raise _not_read(host, "ESP chip id (the Information page, /in)", errors.get("info"))
    jedec = t.get("flash_jedec")
    if not jedec:
        raise _not_read(host, "flash id (StatusMEM.FlashChipId of Status 0)", None)
    size = _size(t.get("flash_size_kb"))
    if not size:
        raise _not_read(host, "flash size (StatusMEM.FlashSize of Status 0)", None)

    if t.get("generic"):
        mark, title = None, t["chip"]
    else:
        mark, title = maker_and_title(t["model"])
    icons = [Icon("tasmota")]
    if t.get("relays"):
        icons.append(Icon("plug"))
    glyph = chip_glyph_text(t["chip"])
    icons.append(Icon("chip", glyph) if glyph else Icon("chip"))
    chip = " ".join(x for x in (t["chip"], t.get("chip_revision")) if x)
    return MicroLabel(
        host=host, title=title, mark=mark, icons=tuple(icons),
        subtitle=f"{chip}  ·  {size} flash",
        ident_caption="Wi-Fi MAC", ident=t["mac"],
        rows=(MicroRow("chip id", str(t["esp_chip_id"]), mono=True),
              MicroRow("flash id", " ".join(re.findall("..", jedec)), mono=True)),
        read_with=READ_WITH.format(host=host),
    )


def micro_labels(docs: Mapping[str, Any]) -> list[MicroLabel]:
    """A label for every document the Tasmota collector wrote, by host;
    every other document is not this module's."""
    out = []
    for host in sorted(docs):
        t = (docs[host].evidence.get("verdict") or {}).get("tasmota")
        if t is not None:
            out.append(tasmota_label(host, t))
    return out
