"""ESP32 micro labels, from the devices ``rpi_hwid.esp32`` found.

One quarter-sticker label per ESP32 (see ``rpi_hwid.micro``): the Espressif
mark and the chip as the title, the Wi-Fi and chip glyphs, the silicon
revision, package and crystal under it, and beside the QR the Bluetooth
MAC, the flash and the flash's unique id. The base MAC -- the Wi-Fi station
MAC, burned into eFuse -- is the identifier: in the QR and along the foot.

Everything on it was read from the chip, except the Bluetooth MAC, which is
derived. ESP-IDF hands out the MACs of a chip with four universally
administered addresses as base, base+1 (SoftAP), base+2 (Bluetooth) and
base+3 (Ethernet), adding to the last octet -- its misc_system_api docs,
"MAC Address (4 universally administered, default)", for every target but
the S2, the H2 family and the P4. That is the default, which Arduino, Tasmota
and ESPHome builds keep, and firmware may change it, so the row is kept
only for the chips that table covers.

A device whose chip was never read gets no label: the error names the host
and the command that reads it, which resets the chip. The USB tree alone
yields a MAC and nothing to say what the chip is.

Built for extension. ``esp32_label(host, device)`` returns the plain label,
which a caller -- the ESP32 + 433 MHz radio node labels, say -- can take
apart with ``dataclasses.replace`` to add an icon, swap a row or draw an
extra section, and ``devices(docs)`` yields every (host, device) there is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rpi_hwid import labels
from rpi_hwid.micro import Icon, MicroLabel, MicroRow

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

KIND = "esp32"

MARK = "espressif.svg"

# Chips whose default is four universally administered MACs, so that the
# Bluetooth MAC is base+2. From ESP-IDF's misc_system_api.rst: every target
# but esp32s2, esp32p4 and the esp32h2 family takes that table. Listed rather
# than assumed, so that a chip not in the list prints no Bluetooth row
# instead of a wrong one.
BT_OFFSET = {"ESP32": 2, "ESP32-S3": 2, "ESP32-C3": 2, "ESP32-C6": 2}


class Esp32NotReadError(labels.IdentifierNotReadError):
    """An ESP32 reached the label generator without its chip read."""


@dataclass(frozen=True)
class Esp32Device:
    """One ESP32 as the probe module wrote it into verdict.esp32."""

    tty: str | None
    transport: str
    mac: str | None
    chip: str | None = None
    chip_description: str | None = None
    revision: str | None = None
    package: str | None = None
    features: tuple[str, ...] = ()
    crystal_mhz: int | None = None
    flash_jedec: str | None = None
    flash_uid: str | None = None
    efuse: Mapping[str, Any] | None = None
    bridge: str | None = None
    usb_serial: str | None = None
    tty_links: tuple[str, ...] = ()
    read_error: str | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Esp32Device:
        return cls(
            tty=d.get("tty"), transport=d.get("transport") or "", mac=d.get("mac"),
            chip=d.get("chip"), chip_description=d.get("chip_description"),
            revision=d.get("revision"), package=d.get("package"),
            features=tuple(d.get("features") or ()), crystal_mhz=d.get("crystal_mhz"),
            flash_jedec=d.get("flash_jedec"), flash_uid=d.get("flash_uid"),
            efuse=d.get("efuse") or {}, bridge=d.get("bridge"),
            usb_serial=d.get("usb_serial"), tty_links=tuple(d.get("tty_links") or ()),
            read_error=d.get("read_error"),
        )

    @property
    def port(self) -> str:
        """The port as the host's own udev rules name it, where they do."""
        for link in self.tty_links:
            if not link.startswith("/dev/serial/"):
                return link
        return self.tty or "?"


def devices(docs: Mapping[str, Any]) -> Iterator[tuple[str, Esp32Device]]:
    """Every (host, device) across the documents, host by host."""
    for host in sorted(docs):
        verdict = docs[host].evidence.get("verdict") or {}
        for d in verdict.get("esp32") or ():
            yield host, Esp32Device.from_dict(d)


def derived_mac(mac: str, offset: int) -> str:
    """`mac` with `offset` added to its last octet, as ESP-IDF does."""
    octets = mac.split(":")
    octets[-1] = f"{(int(octets[-1], 16) + offset) & 0xFF:02x}"
    return ":".join(octets)


def bt_mac(dev: Esp32Device) -> str | None:
    if not dev.mac or dev.chip not in BT_OFFSET:
        return None
    if not any(f in ("BLE", "BT") or f.startswith(("BT", "BLE")) for f in dev.features):
        return None
    return derived_mac(dev.mac, BT_OFFSET[dev.chip])


def embedded_flash(features: tuple[str, ...]) -> tuple[str, str] | None:
    """('4 MiB', 'XMC') from an 'Embedded Flash 4MB (XMC)' feature."""
    for f in features:
        m = re.match(r"^Embedded Flash (\d+)MB(?: \(([^)]*)\))?$", f)
        if m:
            return f"{m.group(1)} MiB", m.group(2) or ""
    return None


def flash_line(dev: Esp32Device) -> str | None:
    """Vendor and density: an in-package flash as the chip's eFuse names it,
    and an external one from its JEDEC id."""
    inside = embedded_flash(dev.features)
    if inside:
        size, vendor = inside
        return " ".join(x for x in (vendor, size, "in package") if x)
    info = labels.flash_from_jedec(dev.flash_jedec)
    return labels.flash_text(info)


def valid_uid(uid: str | None) -> bool:
    """A read uid, not the all-ones or all-zeroes of a failed read."""
    return bool(uid) and bool(re.fullmatch(r"[0-9a-f]{16}", uid or "")) \
        and len(set(uid or "")) > 1


def read_command(host: str, dev: Esp32Device) -> str:
    """Both ways to read it: on the host, or through the collector."""
    return (f"rpi-hwid esp32 --read {dev.port}` on that host, or "
            f"`rpi-hwid collect --esp32-read {host}={dev.port} {host}")


def esp32_label(host: str, dev: Esp32Device) -> MicroLabel:
    """The plain ESP32 micro label for one device; see the module docstring."""
    if not dev.mac:
        raise Esp32NotReadError(
            f"{host}: the ESP32 on {dev.port} has no MAC, so its label would carry "
            f"nothing that identifies it. Read it with `{read_command(host, dev)}` "
            "(this resets the chip).")
    if not dev.chip or not dev.flash_jedec:
        why = f" The last attempt stopped: {dev.read_error}." if dev.read_error else ""
        raise Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} on {dev.port} was found on USB but its chip "
            f"and flash were never read, and its label has a place for both. Read "
            f"them with `{read_command(host, dev)}` -- note that this resets the "
            f"chip into its bootloader and back.{why}")
    if not valid_uid(dev.flash_uid):
        raise Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} on {dev.port}: its flash's unique id read "
            f"back as {dev.flash_uid!r}, which is a failed read and not a value. Read it "
            f"again with `{read_command(host, dev)}` (this resets the chip).")
    wifi = "WiFi" in dev.features
    short = dev.chip[len("ESP32-"):] if dev.chip.startswith("ESP32-") else "32"
    icons = ((Icon("wifi"),) if wifi else ()) + (Icon("chip", short),)
    # single spaces round the dots, unlike the whole labels' subtitles: at
    # the micro label's width the doubled ones cost the crystal its line
    subtitle = " · ".join(x for x in (
        dev.revision, dev.package,
        f"{dev.crystal_mhz} MHz xtal" if dev.crystal_mhz else None) if x)
    rows = []
    bt = bt_mac(dev)
    if bt:
        rows.append(MicroRow("BT", bt, mono=True))
    flash = flash_line(dev)
    if flash:
        rows.append(MicroRow("flash", flash))
    rows.append(MicroRow("uid", dev.flash_uid or "", mono=True))
    return MicroLabel(
        host=host, title=dev.chip, subtitle=subtitle, mark=MARK, icons=icons,
        ident_caption="Wi-Fi MAC" if wifi else "MAC", ident=dev.mac, rows=tuple(rows),
        read_with=read_command(host, dev))


def micro_labels(docs: Mapping[str, Any]) -> list[MicroLabel]:
    return [esp32_label(host, dev) for host, dev in devices(docs)]
