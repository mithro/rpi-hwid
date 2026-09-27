"""ESP32 micro labels, from the devices ``rpi_hwid.esp32`` found.

One quarter-sticker label per ESP32 (see ``rpi_hwid.micro``). The Espressif
mark and the chip make the title, with the Wi-Fi and chip glyphs beside it.
The base MAC -- the Wi-Fi station MAC, burned into eFuse -- is the
identifier: in the QR and along the foot. Beside the QR, the Bluetooth MAC
and then the second identifier the chip has:

  * a chip with an OPTIONAL_UNIQUE_ID in eFuse (the C3, measured on three
    SuperMinis on 2026-09-26) carries those 128 bits, over two rows. Its
    in-package flash has a unique id too (an XMC's 128 bits, read on
    2026-09-27), which stays in the document: the chip's own is the one
    printed, and the flash's size and vendor go in the subtitle;
  * an original ESP32 has no such field, so its external flash is named on
    a row of its own with the flash's own unique id under it: 64 bits on
    one row (a devkit's GigaDevice), or 128 over two (an ESP32-CAM's Boya),
    which then takes the row the derived Bluetooth MAC would have had.

Everything on it was read from the chip, except the Bluetooth MAC, which is
derived. ESP-IDF hands out the MACs of a chip with four universally
administered addresses as base, base+1 (SoftAP), base+2 (Bluetooth) and
base+3 (Ethernet), adding to the last octet -- its misc_system_api docs,
"MAC Address (4 universally administered, default)", for every target but
the S2, the H2 family and the P4. That is the default, which Arduino, Tasmota
and ESPHome builds keep, and firmware may change it, so the row is kept
only for the chips that table covers.

A device whose chip was never read gets no label: the error names the host,
the MAC and the commands that read it, which reset the chip. The USB tree
alone yields a MAC and nothing to say what the chip is. A flash whose
unique id read back blank is left off rather than printed; a chip whose
eFuse should hold a unique id but was not read is an error.

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

# Chip families whose default is four universally administered MACs, so that
# the Bluetooth MAC is base+2. From ESP-IDF's misc_system_api.rst: every
# target but esp32s2, esp32p4 and the esp32h2 family takes that table. Listed
# rather than assumed, so that a chip not in the list prints no Bluetooth row
# instead of a wrong one.
BT_OFFSET = {"ESP32": 2, "ESP32-S3": 2, "ESP32-C3": 2, "ESP32-C6": 2}

# Families whose eFuse is known to hold an OPTIONAL_UNIQUE_ID, so that a read
# without one is a failed read rather than a chip that has none. Only the C3
# is listed, because it is the one read; any chip whose read carries the
# field prints it.
CHIP_UID = {"ESP32-C3"}

# JEDEC manufacturer codes rpi_hwid.labels does not name, as flashrom's
# include/flashchips.h has them (BOYA_BOHONG_ID 0x68): an ESP32-CAM's flash.
# 0x46 is not in flashrom: it is ESP-IDF's SPI_FLASH_XMC_2 (esp_mspi's
# spi_flash_defs.h), whose generic driver calls a 0x46 part "XMC-D" -- the
# flash inside the ESP32-C3 SuperMinis, whose eFuse FLASH_VENDOR says XMC.
JEDEC_VENDOR = {0x68: "Boya", 0x46: "XMC"}
# ...and parts, with the letters the id cannot settle written as x, as
# rpi_hwid.labels.JEDEC_PART does: flashrom's GIGADEVICE_GD25Q32 0x4016,
# "Same as GD25Q32B" -- the devkit on rpi4-esp.
JEDEC_PART = {0xC84016: "GD25Q32x", 0x684016: "BY25Q32xS"}
# Parts that share an id and an SFDP but not the length of their unique id,
# which the read measures. BYTe's BY25Q32BS datasheet (Rev. 2.4, 7.3.5) gives
# a 64-bit id and its BY25Q32ES (Rev. 2.2, 7.3.5) a 128-bit one, with the SFDP
# headers of the two identical; the ESP32-CAM's gave 128 bits (2026-09-27).
PART_BY_UID_BITS = {0x684016: {64: "BY25Q32BS", 128: "BY25Q32ES"}}


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
    flash_uid_bits: int | None = None
    flash_sfdp: str | None = None
    efuse: Mapping[str, Any] | None = None
    bridge: str | None = None
    usb_serial: str | None = None
    tty_links: tuple[str, ...] = ()
    read_error: str | None = None
    read_errors: Mapping[str, str] | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Esp32Device:
        return cls(
            tty=d.get("tty"), transport=d.get("transport") or "", mac=d.get("mac"),
            chip=d.get("chip"), chip_description=d.get("chip_description"),
            revision=d.get("revision"), package=d.get("package"),
            features=tuple(d.get("features") or ()), crystal_mhz=d.get("crystal_mhz"),
            flash_jedec=d.get("flash_jedec"), flash_uid=d.get("flash_uid"),
            flash_uid_bits=d.get("flash_uid_bits"), flash_sfdp=d.get("flash_sfdp"),
            efuse=d.get("efuse") or {}, bridge=d.get("bridge"),
            usb_serial=d.get("usb_serial"), tty_links=tuple(d.get("tty_links") or ()),
            read_error=d.get("read_error"), read_errors=d.get("read_errors") or {},
        )

    @property
    def port(self) -> str:
        """The port as the host's own udev rules name it, where they do."""
        for link in self.tty_links:
            if not link.startswith("/dev/serial/"):
                return link
        return self.tty or "?"

    @property
    def wifi(self) -> bool:
        # esptool 4 says "WiFi", esptool 5 "Wi-Fi"
        return any(f.replace("-", "") == "WiFi" for f in self.features)

    @property
    def chip_uid(self) -> str | None:
        """OPTIONAL_UNIQUE_ID as 32 hex digits, or None where there is none."""
        raw = (self.efuse or {}).get("OPTIONAL_UNIQUE_ID")
        text = re.sub(r"[^0-9a-f]", "", str(raw or "").lower())
        if len(text) != 32 or len(set(text)) < 2:
            return None
        return text


def devices(docs: Mapping[str, Any]) -> Iterator[tuple[str, Esp32Device]]:
    """Every (host, device) across the documents, host by host."""
    for host in sorted(docs):
        verdict = docs[host].evidence.get("verdict") or {}
        for d in verdict.get("esp32") or ():
            yield host, Esp32Device.from_dict(d)


def family(chip: str) -> str:
    """'ESP32-C3' for an ESP32-C3; 'ESP32' for every original ESP32, whatever
    its package (D0WD-V3, D0WDQ6, PICO-D4)."""
    m = re.match(r"^ESP32-([CSHP]\d+)\b", chip)
    return f"ESP32-{m.group(1)}" if m else "ESP32"


def derived_mac(mac: str, offset: int) -> str:
    """`mac` with `offset` added to its last octet, as ESP-IDF does."""
    octets = mac.split(":")
    octets[-1] = f"{(int(octets[-1], 16) + offset) & 0xFF:02x}"
    return ":".join(octets)


def bt_mac(dev: Esp32Device) -> str | None:
    fam = family(dev.chip or "")
    if not dev.mac or fam not in BT_OFFSET:
        return None
    if not any(f.startswith(("BT", "BLE")) for f in dev.features):
        return None
    return derived_mac(dev.mac, BT_OFFSET[fam])


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
        return " ".join(x for x in (size, vendor) if x)
    info = labels.flash_from_jedec(dev.flash_jedec, sfdp=dev.flash_sfdp)
    if not info["jedec"]:
        return None
    value = int(info["jedec"], 16)
    # A named part says its vendor already, and at a micro label's width the
    # vendor's name is what would push the size off the end of the row.
    part = (PART_BY_UID_BITS.get(value, {}).get(dev.flash_uid_bits or 0)
            or info["part"] or JEDEC_PART.get(value))
    what = part or " ".join(x for x in (
        info["vendor"] or JEDEC_VENDOR.get(value >> 16), info["jedec"]) if x)
    return " · ".join(x for x in (what, info["size"]) if x)


def valid_flash_uid(uid: str | None) -> bool:
    """A read uid -- 64 or 128 bits -- not the all-ones or all-zeroes a flash
    without one gives."""
    return (bool(re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{32}", uid or ""))
            and len(set(uid or "")) > 1)


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
    fam = family(dev.chip)
    chip_uid = dev.chip_uid
    if fam in CHIP_UID and not chip_uid:
        stopped = (dev.read_errors or {}).get("efuse")
        raise Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} on {dev.port} is an {fam}, whose eFuse holds "
            f"a unique id, and it was not read"
            + (f" (the eFuse read stopped: {stopped})" if stopped else "")
            + f". Read it again with `{read_command(host, dev)}` (this resets the chip).")
    short = fam[len("ESP32-"):] if fam != "ESP32" else "32"
    icons = ((Icon("wifi"),) if dev.wifi else ()) + (Icon("chip", short),)
    rows = []
    bt = bt_mac(dev)
    flash = flash_line(dev)
    uid = dev.flash_uid if valid_flash_uid(dev.flash_uid) else None
    # A 128-bit flash uid takes two rows, and the Bluetooth MAC -- derived,
    # not read -- gives up its row to the half of it that would not fit.
    uid_rows = 0 if chip_uid or not uid else len(uid) // 16
    if bt and uid_rows < 2:
        rows.append(MicroRow("BT", bt, mono=True))
    # Single spaces round the dots, unlike the whole labels' subtitles: at the
    # micro label's width the doubled ones cost the line its last fact.
    if chip_uid:
        subtitle = " · ".join(x for x in (dev.revision, dev.package, flash) if x)
        rows += [MicroRow("chip", chip_uid[:16], mono=True),
                 MicroRow("", chip_uid[16:], mono=True)]
    else:
        subtitle = " · ".join(x for x in (
            dev.revision, dev.package,
            f"{dev.crystal_mhz} MHz xtal" if dev.crystal_mhz else None) if x)
        if flash:
            rows.append(MicroRow("flash", flash))
        if uid:
            rows += [MicroRow("uid", uid[:16], mono=True)] + (
                [MicroRow("", uid[16:], mono=True)] if uid[16:] else [])
    return MicroLabel(
        host=host, title=dev.chip, subtitle=subtitle, mark=MARK, icons=icons,
        ident_caption="Wi-Fi MAC" if dev.wifi else "MAC", ident=dev.mac, rows=tuple(rows),
        read_with=read_command(host, dev))


def micro_labels(docs: Mapping[str, Any]) -> list[MicroLabel]:
    return [esp32_label(host, dev) for host, dev in devices(docs)]
