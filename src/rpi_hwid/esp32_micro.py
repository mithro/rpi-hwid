"""ESP32 micro labels, from the devices ``rpi_hwid.esp32`` found.

One quarter-sticker label per chip (see ``rpi_hwid.micro``): every ESP32
and, since esptool reads them the same way, the ESP8266EX and ESP8285 in
most Tasmota plugs. Every label has the same parts in the same places:

  header      the Espressif mark; the part number, as the read names it
              (``rpi_hwid.espressif.part_for``: ESP32-C3FH4, ESP32-D0WD-V3,
              ESP8285N08), always whole; then the chip's radios as glyphs --
              Wi-Fi with its bands and 802.11 standards, Bluetooth, an
              802.15.4 mesh
  spec strip  what every chip of that part is, from the table: the ISA
              (the RISC-V mark, or the Xtensa "Xt" in the same box), the
              cores (two numbers: the application cores, then the
              low-power ones, small and grey), the on-chip SRAM with any
              in-package PSRAM, and the Tasmota mark where Tasmota ships a
              binary for it
  rows        what was read from this chip, always in this order:
                chip   its revision
                flash  the flash's part where the read settles it (the
                       JEDEC id, and for a Boya the unique id's length),
                       else its JEDEC id (with its vendor where that fits);
                       then its size
                uid    one serial beyond the MAC, the first in SERIALS that
                       the chip has: the flash's unique id ("uid"), 64 bits
                       on one row or 128 on two; else the chip's own 128-bit
                       eFuse OPTIONAL_UNIQUE_ID ("eFuse") over two
              the flash and uid rows each print at one size on every label
              (FLASH_PT, UID_PT), whatever the value's length
  foot and QR the base MAC, burned into eFuse: the identifier

A fact that does not apply leaves its place empty rather than moving
another into it: an ESP8266 reports no revision, so its chip row's place is
blank (BLANK_ROW); a chip with neither serial has no uid rows. The crystal
is read and kept in the document but not printed (Tim, 2026-09-27). Every
value is either read or looked up; nothing is derived. The Bluetooth MAC, which
ESP-IDF derives as base+2, is not printed: it is not read from anything,
and the rows are for what was.

One serial beyond the MAC, not two, because two 128-bit serials do not fit
a quarter sticker. The MAC already identifies the chip's die, so the
flash's id, the one that names a second part, comes first; the chip's
eFuse id stands in where the flash has none to give. An ESP32-C3 has both
(its in-package XMC's 128 bits and the eFuse id), so which one prints is a
choice, made in one place: SERIALS. Both stay in the collected document
whichever it is.

A device whose chip was never read gets no label: the error names the host,
the MAC and the commands that read it, which reset the chip. The USB tree
alone yields a MAC and nothing to say what the chip is. A chip whose eFuse
holds a unique id that was not read is an error too, and so is a chip the
table has no row for (``espressif.UnknownPartError``): that is not a
missing identifier but a missing row, and one to add with its source.

Built for extension. ``esp32_label(host, device)`` returns the plain label,
which a caller -- the ESP32 + 433 MHz radio node labels, say -- can take
apart with ``dataclasses.replace`` to add an icon, swap a row or draw an
extra section, and ``devices(docs)`` yields every (host, device) there is.
``sample_label(part)`` makes a clearly synthetic one for any row of the
table, for the docs' sample sheet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from reportlab.pdfbase import pdfmetrics

from rpi_hwid import espressif, labels, micro
from rpi_hwid.micro import BLANK_ROW, Icon, MicroLabel, MicroRow

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

KIND = "esp32"

MARK = "espressif.svg"

# JEDEC manufacturer codes rpi_hwid.labels does not name. 0x68 is flashrom's
# BOYA_BOHONG_ID (include/flashchips.h): an ESP32-CAM's flash. 0x46 is not in
# flashrom: it is ESP-IDF's SPI_FLASH_XMC_2 (esp_mspi's spi_flash_defs.h),
# whose generic driver calls a 0x46 part "XMC-D" -- the flash inside the
# ESP32-C3 SuperMinis, whose eFuse FLASH_VENDOR says XMC.
JEDEC_VENDOR = {0x68: "Boya", 0x46: "XMC"}
# ...and parts, with the letters the id cannot settle written as x, as
# rpi_hwid.labels.JEDEC_PART does. Each is argued, with the reads, in
# docs/research/esp32-flash.md:
#   0xC84016  flashrom's GIGADEVICE_GD25Q32 0x4016, "Same as GD25Q32B" -- the
#             devkit on rpi4-esp. Its SFDP says a C or older, never an E.
#   0x464016  the ESP32-C3 SuperMinis' in-package flash. ESP-IDF calls a 0x46
#             part XMC's D series ("XMC-D"), and its SFDP is the XM25QH32D
#             datasheet's table (Rev 1.2, 2024-04-08) byte for byte but for
#             the vendor id: the D, not the XM25QH32C (0x204016) the eFuse
#             vendor alone would suggest.
#   0x684016  Boya's BY25Q32BS or BY25Q32ES, whose SFDP headers are the same;
#             PART_BY_UID_BITS tells the two apart where the uid was read.
JEDEC_PART = {0xC84016: "GD25Q32x", 0x464016: "XM25QH32D", 0x684016: "BY25Q32xS"}
# Parts that share an id and an SFDP but not the length of their unique id,
# which the read measures (flash_uid_bits). BYTe's BY25Q32BS datasheet (Rev.
# 2.4, 7.3.5) gives a 64-bit id and its BY25Q32ES (Rev. 2.2, 7.3.5) a 128-bit
# one; the ESP32-CAM on rpi4-esp gave 128 bits (2026-09-27).
PART_BY_UID_BITS = {0x684016: {64: "BY25Q32BS", 128: "BY25Q32ES"}}

# Which serial the uid rows carry, in order of preference: the first of these
# the chip has is printed, and only that one. "flash" is the flash's own
# unique id (captioned "uid"), "efuse" the chip's OPTIONAL_UNIQUE_ID
# (captioned "eFuse"). An ESP32-C3 has both, so for a C3 this order is the
# whole decision; swap the two to print the chip's own id first.
SERIALS = ("flash", "efuse")

# The captions a row can have, and the one size each kind of row is set at
# on every label (Tim, 2026-09-27: not one that changes with the value's
# length). Each is what fits beside the widest caption, "eFuse": the flash
# row's longest named part (XM25QH32D · 4 MiB) and a uid row's sixteen hex
# digits. The chip row, a revision, fits at the full row size everywhere.
CAPTIONS = ("chip", "flash", "uid", "eFuse")
FLASH_PT = 4.4
UID_PT = 4.3


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


def part_of(host: str, dev: Esp32Device) -> espressif.Part:
    """The table's row for the chip, with the host and MAC on any refusal."""
    try:
        return espressif.part_for(dev.chip, dev.chip_description or "", dev.features,
                                  dev.efuse, dev.revision)
    except espressif.UnknownPartError as exc:
        raise espressif.UnknownPartError(
            f"{host}: the ESP32 {dev.mac} on {dev.port}: {exc}") from None


def embedded_flash(features: tuple[str, ...]) -> tuple[str, str] | None:
    """('4 MiB', 'XMC') from an 'Embedded Flash 4MB (XMC)' feature."""
    for f in features:
        m = re.match(r"^Embedded Flash (\d+)MB(?: \(([^)]*)\))?$", f)
        if m:
            return f"{m.group(1)} MiB", m.group(2) or ""
    return None


def flash_line(dev: Esp32Device, part: espressif.Part | None = None) -> str | None:
    """The flash row: the part where the read settles one -- a part number
    says its maker -- else the vendor and the JEDEC id; then the size.

    One place for the flash, the same on every label, whether it is in the
    chip's package or beside it. The vendor of an in-package flash is the
    one the chip's eFuse names (FLASH_VENDOR), which esptool reports with
    its size; the id and the density come from the flash itself."""
    info = labels.flash_from_jedec(dev.flash_jedec, sfdp=dev.flash_sfdp)
    if not info["jedec"]:
        return None
    value = int(info["jedec"], 16)
    inside = embedded_flash(dev.features)
    named = (PART_BY_UID_BITS.get(value, {}).get(dev.flash_uid_bits or 0)
             or info["part"] or JEDEC_PART.get(value))
    vendor = (inside[1] if inside else "") or info["vendor"] or JEDEC_VENDOR.get(value >> 16)
    size = info["size"] or (inside[0] if inside else None)
    if not size and part is not None and part.flash_mb:
        size = f"{part.flash_mb:g} MiB"

    def line(what: str | None) -> str:
        return " · ".join(x for x in (what, size) if x)

    if named:
        return line(named)
    # an id no part is known for: its vendor too where that fits at the
    # row's one size, else the id alone, whose first byte names the vendor
    full = line(" ".join(x for x in (vendor, info["jedec"]) if x))
    return full if fits_flash_row(full) else line(info["jedec"])


def value_room() -> float:
    """The width a row's value has beside the widest caption a row can have."""
    widest = max(pdfmetrics.stringWidth(c, labels.SANS, micro.CAPTION) for c in CAPTIONS)
    return micro.rows_w() - widest - labels.Label.CAPTION_GAP * 0.6


def fits_flash_row(value: str) -> bool:
    return pdfmetrics.stringWidth(value, labels.SANS, FLASH_PT) <= value_room()


def valid_flash_uid(uid: str | None) -> bool:
    """A read uid -- 64 bits or 128 -- not the all-ones or all-zeroes a
    flash without one gives."""
    text = uid or ""
    return bool(re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{32}", text)) and len(set(text)) > 1


def uid_rows(caption: str, uid: str) -> list[MicroRow]:
    """A 64-bit uid on one row, a 128-bit one over two: sixteen hex digits
    is what a row holds whole at the smallest size."""
    halves = [uid[i:i + 16] for i in range(0, len(uid), 16)]
    return [MicroRow(caption if i == 0 else "", h, mono=True, size=UID_PT)
            for i, h in enumerate(halves)]


def serial_rows(dev: Esp32Device) -> list[MicroRow]:
    """The uid rows: the first serial in SERIALS that this chip has."""
    have = {"flash": ("uid", dev.flash_uid if valid_flash_uid(dev.flash_uid) else None),
            "efuse": ("eFuse", dev.chip_uid)}
    for which in SERIALS:
        caption, value = have[which]
        if value:
            return uid_rows(caption, value)
    return []


def radio_icons(fam: espressif.Family) -> tuple[Icon, ...]:
    """The header's glyphs: the radios the part has. Not its USB: the
    trident is the widest glyph there is, and beside three radios it would
    leave a C5's or C6's part number no room to print whole; the table
    keeps it."""
    icons = []
    if fam.wifi:
        # the bands and the newest 802.11 standard: "2.4 n", "2.4/5 ax"
        icons.append(Icon("wifi", "/".join(fam.wifi_band_ghz) + " " + str(fam.wifi_newest)))
    if fam.bluetooth:
        icons.append(Icon("bluetooth"))
    if fam.ieee802154:
        icons.append(Icon("mesh"))
    return tuple(icons)


def spec_icons(part: espressif.Part) -> tuple[Icon, ...]:
    """The spec strip: ISA, cores, memory, and Tasmota where it builds."""
    fam = part.family
    cores = "{}+{}".format(*part.core_pair)
    memory = f"{fam.sram_kb}K" + (f"+{part.psram_mb:g}M" if part.psram_mb else "")
    specs = [Icon("riscv" if fam.isa == espressif.RISCV else "xtensa"),
             Icon("cores", cores), Icon("memory", memory)]
    if fam.tasmota in espressif.TASMOTA_BINARY:
        specs.append(Icon("tasmota"))
    return tuple(specs)


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
    chip_uid = dev.chip_uid
    stopped = (dev.read_errors or {}).get("efuse")
    family = _family_of(dev.chip)
    if family is not None and family.chip_uid and not chip_uid:
        raise Esp32NotReadError(
            f"{host}: the ESP32 {dev.mac} on {dev.port} is an {family.name}, whose eFuse "
            f"holds a unique id, and it was not read"
            + (f" (the eFuse read stopped: {stopped})" if stopped else "")
            + f". Read it again with `{read_command(host, dev)}` (this resets the chip).")
    return _label(host, dev, part_of(host, dev))


def _label(host: str, dev: Esp32Device, part: espressif.Part) -> MicroLabel:
    fam = part.family
    # the chip row is the revision; an ESP8266 reports none, and leaves the
    # row's place empty so the flash row stays where it is on every label
    rows = [MicroRow("chip", dev.revision) if dev.revision else BLANK_ROW]
    flash = flash_line(dev, part)
    if flash:
        rows.append(MicroRow("flash", flash, size=FLASH_PT))
    rows += serial_rows(dev)
    return MicroLabel(
        host=host, title=part.part, mark=MARK, icons=radio_icons(fam),
        specs=spec_icons(part), ident_caption="Wi-Fi MAC" if fam.wifi else "MAC",
        ident=dev.mac or "", rows=tuple(rows),
        read_with=read_command(host, dev))


def _family_of(chip: str) -> espressif.Family | None:
    """The family of an esptool chip name, before the part is settled: the
    family decides whether the eFuse must hold a unique id."""
    for f in sorted(espressif.FAMILIES, key=lambda f: -len(f.name)):
        if chip == f.name or (chip.startswith(f.name) and not chip[len(f.name):][:1].isdigit()):
            return f
    if chip.startswith("ESP8684"):
        return espressif.ESP32_C2
    if chip.startswith("ESP8685"):
        return espressif.ESP32_C3
    if chip.startswith(("ESP8266", "ESP8285")):
        return espressif.ESP8266
    return None


def micro_labels(docs: Mapping[str, Any]) -> list[MicroLabel]:
    return [esp32_label(host, dev) for host, dev in devices(docs)]


# --- samples ---------------------------------------------------------------------


def sample_device(part: espressif.Part, n: int) -> Esp32Device:
    """A clearly synthetic device of `part`: a locally administered MAC
    (02:...), revision v9.9 (none for an ESP8266, which reports none), and ids
    counting up from n. For the sample sheet only; it was read from nothing."""
    fam = part.family
    mac = f"02:00:00:00:{n >> 8 & 0xff:02x}:{n & 0xff:02x}"
    size = f"{part.flash_mb:g}" if part.flash_mb else "4"
    feats = ["Wi-Fi"] if fam.wifi else []
    if part.flash_mb and fam not in (espressif.ESP8266, espressif.ESP32):
        # the eFuse of the later chips names the in-package flash's size and
        # vendor; esptool says only "Embedded Flash" for the older two
        feats.append(f"Embedded Flash {size}MB (XMC)")
    # 0x16: 4 MiB; a GigaDevice outside the package, an XMC inside it
    capacity = {1: 0x14, 2: 0x15, 4: 0x16, 8: 0x17}.get(int(part.flash_mb or 4), 0x16)
    jedec = f"0x{0x46 if part.flash_mb else 0xc8:02x}40{capacity:02x}"
    uid = f"{n:04x}" * 4 if n % 2 else ""
    efuse: dict[str, Any] = {}
    if fam.chip_uid:
        efuse["OPTIONAL_UNIQUE_ID"] = f"5a{n:02x}" * 8
    return Esp32Device(
        tty=None, transport="sample", mac=mac, chip=part.part,
        chip_description=part.part,
        revision=None if fam is espressif.ESP8266 else "v9.9", features=tuple(feats),
        crystal_mhz=26 if fam is espressif.ESP8266 else 40, flash_jedec=jedec,
        flash_uid=uid, efuse=efuse)


def sample_label(part: espressif.Part, n: int = 1) -> MicroLabel:
    """The label a device of `part` would get; see ``sample_device``."""
    return _label("sample", sample_device(part, n), part)
