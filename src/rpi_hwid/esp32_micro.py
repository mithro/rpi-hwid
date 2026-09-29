"""ESP32 micro labels, from the devices ``rpi_hwid.esp32`` found.

One quarter-sticker label per chip (see ``rpi_hwid.micro``): every ESP32
and, since esptool reads them the same way, the ESP8266EX and ESP8285 in
most Tasmota plugs. Every label has the same parts in the same places:

  header      the Espressif mark; the part number, as the read names it
              (``rpi_hwid.espressif.part_for``: ESP32-C3FH4, ESP32-D0WD-V3,
              ESP8285N08), always whole; then glyphs for the chip's radios
              and USB -- Wi-Fi with its bands and newest 802.11 standard
              and, small on it, Bluetooth; the USB trident lettered O
              (OTG), J (JTAG), S (serial) for what the chip's own USB does;
              an 802.15.4 mesh
  spec strip  what every chip of that part is, from the table: the ISA
              (the RISC-V mark, or the Xtensa "Xt" in the same box), the
              cores (two numbers: the application cores, then the
              low-power ones, small and grey), the on-chip SRAM with any
              in-package PSRAM, and the Tasmota mark where Tasmota ships a
              binary for it; last the chip's revision as read, bare
              ("v0.4": Tim, 2026-09-29)
  rows        what was read from this chip, always in this order:
                flash  the flash's part where the read settles it (the
                       JEDEC id, and for a Boya the unique id's length),
                       else its JEDEC id (with its vendor where that fits);
                       then its size
                uid    the flash's unique id, its ff padding trimmed
                       (trim_flash_uid): one row where that leaves twenty
                       hex digits or fewer, else two
                eFuse  the chip's own 128-bit OPTIONAL_UNIQUE_ID, over two
                       rows
              every id the chip and its flash have is printed (Tim,
              2026-09-27), and each kind of row prints at one size on every
              label (FLASH_PT, UID_PT), whatever the value's length
  foot and QR the base MAC, burned into eFuse: the identifier

A fact that does not apply leaves its place empty rather than moving
another into it: a chip with neither serial has no uid or eFuse rows, and
an ESP8266, which reports no revision, has none on its strip. The crystal
is read and kept in the document but not printed (Tim, 2026-09-27). Every
value is either read or looked up; nothing is derived. The Bluetooth MAC, which
ESP-IDF derives as base+2, is not printed: it is not read from anything,
and the rows are for what was.

Both serials where a chip has both, the flash's first. An ESP32-C3's
in-package XMC reads out 128 bits of which its maker programmed the first
80 -- the rest read ff, as an unprogrammed byte does -- so its uid, trimmed,
takes one row and the chip's eFuse id the two under it; the rows under the
spec strip are set one step tighter to hold them (``micro.SPEC_ROWS``). The
collected document keeps both ids whole, as read. A flash uid that needs two
rows beside an eFuse id -- a Boya's 128 bits beside an ESP32-S3, say --
fills all five.

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

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from reportlab.pdfbase import pdfmetrics

from rpi_hwid import espressif, labels, micro
from rpi_hwid.micro import Icon, MicroLabel, MicroRow

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
#   0xC84014, 0xC84015, 0xC84017, 0xC84018  the rest of GigaDevice's GD25Q
#             family, from flashrom's include/flashchips.h (commit a1de15b):
#             GIGADEVICE_GD25Q80 0x4014 "Same as GD25Q80B", GD25Q16 0x4015
#             "Same as GD25Q16B", GD25Q64 0x4017 "Same as GD25Q64B", GD25Q128
#             0x4018 "Same as GD25Q128B, GD25Q127C, GD25Q128C ...". Not read
#             on the fleet; the sample sheet's 1, 2, 8 and 16 MiB flash.
# A flash whose id is in neither table stops label generation
# (UnknownFlashPartError): a bare id is never printed (Tim, 2026-09-27).
JEDEC_PART = {0xC84016: "GD25Q32x", 0x464016: "XM25QH32D", 0x684016: "BY25Q32xS",
              0xC84014: "GD25Q80x", 0xC84015: "GD25Q16x", 0xC84017: "GD25Q64x",
              0xC84018: "GD25Q128x"}
# Parts that share an id and an SFDP but not the length of their unique id,
# which the read measures (flash_uid_bits). BYTe's BY25Q32BS datasheet (Rev.
# 2.4, 7.3.5) gives a 64-bit id and its BY25Q32ES (Rev. 2.2, 7.3.5) a 128-bit
# one; the ESP32-CAM on rpi4-esp gave 128 bits (2026-09-27).
PART_BY_UID_BITS = {0x684016: {64: "BY25Q32BS", 128: "BY25Q32ES"}}

# The captions a row can have, and the one size each kind of row is set at
# on every label (Tim, 2026-09-27: not one that changes with the value's
# length). The flash row is what fits beside the widest caption, "eFuse":
# its longest named part, XM25QH32D · 4 MiB. The serials -- uid and eFuse
# rows alike -- are set at the size a C3's trimmed flash uid, twenty hex
# digits, fits at on one row. That row is wide (micro.MicroRow.wide), its
# digits starting right of its own short caption: at the smallest size the
# labels print, twenty digits are wider than the column beside "eFuse".
CAPTIONS = ("flash", "uid", "eFuse")
FLASH_PT = 4.4
UID_PT = 4.0
# The hex digits one uid row holds whole at UID_PT beside its caption.
UID_ROW_DIGITS = 20


class Esp32NotReadError(labels.IdentifierNotReadError):
    """An ESP32 reached the label generator without its chip read."""


class UnknownFlashPartError(Exception):
    """An ESP32's flash answered with a JEDEC id no table names. Not a failed
    read -- the id was read -- but a part to add: the label never prints a
    bare id (Tim, 2026-09-27)."""


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


def flash_line(host: str, dev: Esp32Device,
               part: espressif.Part | None = None) -> str | None:
    """The flash row: the part the read settles -- a part number says its
    maker -- then the size.

    One place for the flash, the same on every label, whether it is in the
    chip's package or beside it; the density comes from the flash itself, or
    else from what the chip's eFuse says is in its package. A JEDEC id no
    table names stops here with UnknownFlashPartError, naming the host, the
    ESP32 and the id and saying where the part goes."""
    info = labels.flash_from_jedec(dev.flash_jedec, sfdp=dev.flash_sfdp)
    if not info["jedec"]:
        return None
    value = int(info["jedec"], 16)
    inside = embedded_flash(dev.features)
    named = (PART_BY_UID_BITS.get(value, {}).get(dev.flash_uid_bits or 0)
             or info["part"] or JEDEC_PART.get(value))
    size = info["size"] or (inside[0] if inside else None)
    if not size and part is not None and part.flash_mb:
        size = f"{part.flash_mb:g} MiB"
    if not named:
        vendor = (inside[1] if inside else "") or info["vendor"] or JEDEC_VENDOR.get(value >> 16)
        sfdp = f", SFDP {dev.flash_sfdp}" if dev.flash_sfdp else ""
        raise UnknownFlashPartError(
            f"{host}: the ESP32 {dev.mac} has a flash whose JEDEC id {info['jedec']}"
            f"{sfdp} names no part"
            + (f" (the id's vendor: {vendor}{', ' + size if size else ''})" if vendor else "")
            + ", and a label never prints a bare id. Find the part from its "
            "datasheet or flashrom's include/flashchips.h and add it to JEDEC_PART "
            "in src/rpi_hwid/esp32_micro.py as 0xVVDDCC: \"PART\" (letters the id "
            "cannot settle written as x), with the source cited in the comment "
            "above the table.")
    return " · ".join(x for x in (named, size) if x)


def value_room() -> float:
    """The width a row's value has beside the widest caption a row can have."""
    widest = max(pdfmetrics.stringWidth(c, labels.SANS, micro.CAPTION) for c in CAPTIONS)
    return micro.rows_w() - widest - labels.Label.CAPTION_GAP * 0.6


def valid_flash_uid(uid: str | None) -> bool:
    """A read uid -- 64 bits or 128 -- not the all-ones or all-zeroes a
    flash without one gives."""
    text = uid or ""
    return bool(re.fullmatch(r"[0-9a-f]{16}|[0-9a-f]{32}", text)) and len(set(text)) > 1


def trim_flash_uid(uid: str) -> str:
    """The flash's unique id less its trailing ff bytes: whole bytes, only
    at the end, and never below 64 bits.

    A flash reads out a fixed 128 bits whatever its maker programmed, and a
    byte nobody programmed reads ff: an ESP32-C3's XM25QH32D gives 80
    programmed bits and six bytes of ff (240c1119088539540150ffffffffffff),
    which say nothing and would take a second row (Tim, 2026-09-27: "Drop
    the flash ID's ff padding"). Only the end is trimmed: an ff inside an id
    (a Boya's 343738393844fa77fffcffff968f1f11) is part of it. And never
    below 64 bits, the shortest id these parts have (GigaDevice's, a Boya
    BY25Q32BS's): a 64-bit id stays whole as read, whatever its last byte.
    A longer id whose last programmed byte happens to be ff would lose it
    here; the collected document keeps every id whole, as read."""
    while len(uid) > 16 and uid.endswith("ff"):
        uid = uid[:-2]
    return uid


def uid_rows(caption: str, uid: str, wide: bool = False) -> list[MicroRow]:
    """One row for up to UID_ROW_DIGITS hex digits, else two equal halves:
    a C3's trimmed flash uid on one row, any 128-bit id over two."""
    if len(uid) <= UID_ROW_DIGITS:
        parts = [uid]
    else:
        half = (len(uid) + 1) // 2
        parts = [uid[:half], uid[half:]]
    return [MicroRow(caption if i == 0 else "", h, mono=True, size=UID_PT, wide=wide)
            for i, h in enumerate(parts)]


def serial_rows(dev: Esp32Device) -> list[MicroRow]:
    """Every serial the chip and its flash have: the flash's uid, trimmed,
    on wide rows; then the chip's eFuse id over two rows in the column."""
    rows = []
    if valid_flash_uid(dev.flash_uid):
        rows += uid_rows("uid", trim_flash_uid(dev.flash_uid or ""), wide=True)
    if dev.chip_uid:
        rows += uid_rows("eFuse", dev.chip_uid)
    return rows


USB_LETTER = {"otg": "O", "jtag": "J", "serial": "S"}


def radio_icons(fam: espressif.Family) -> tuple[Icon, ...]:
    """The header's glyphs: the radios the part has and its own USB.

    Wi-Fi with its bands and newest standard ("2.4 n", "2.4/5 ax"), and
    Bluetooth small on it where the part has both; Bluetooth alone where
    it has no Wi-Fi. Then the USB trident, lettered with what the chip's
    own USB does (O OTG, J JTAG, S serial), and no wider than the Wi-Fi
    glyph (Tim, 2026-09-29); none where the chip has no USB. Last the
    802.15.4 mesh."""
    icons = []
    if fam.wifi:
        text = "/".join(fam.wifi_band_ghz) + " " + str(fam.wifi_newest)
        icons.append(Icon("wifi", text + (" " + micro.WIFI_BT if fam.bluetooth else "")))
    elif fam.bluetooth:
        icons.append(Icon("bluetooth"))
    if fam.usb_functions:
        icons.append(Icon("usb", "".join(USB_LETTER[f] for f in fam.usb_functions)))
    if fam.ieee802154:
        icons.append(Icon("mesh"))
    return tuple(icons)


def spec_icons(part: espressif.Part, revision: str | None = None) -> tuple[Icon, ...]:
    """The spec strip: ISA, cores, memory, Tasmota where it builds, and
    last the chip's revision as read, bare (Tim, 2026-09-29); an ESP8266
    reports none."""
    fam = part.family
    cores = "{}+{}".format(*part.core_pair)
    memory = f"{fam.sram_kb}K" + (f"+{part.psram_mb:g}M" if part.psram_mb else "")
    specs = [Icon("riscv" if fam.isa == espressif.RISCV else "xtensa"),
             Icon("cores", cores), Icon("memory", memory)]
    if fam.tasmota in espressif.TASMOTA_BINARY:
        specs.append(Icon("tasmota"))
    if revision:
        specs.append(Icon("revision", revision))
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
    rows = []
    flash = flash_line(host, dev, part)
    if flash:
        rows.append(MicroRow("flash", flash, size=FLASH_PT))
    rows += serial_rows(dev)
    return MicroLabel(
        host=host, title=part.part, mark=MARK, icons=radio_icons(fam),
        specs=spec_icons(part, dev.revision),
        ident_caption="Wi-Fi MAC" if fam.wifi else "MAC",
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


def _sample_hex(part: espressif.Part, what: str, digits: int) -> str:
    """`digits` hex digits that look like a real id and differ from part to
    part: a SHA-256 of the part's name and the field, not a counting pattern."""
    return hashlib.sha256(f"{part.part} {what}".encode()).hexdigest()[:digits]


# The external flash parts the real boards carry, taken in turn by the
# samples so the sheet shows each way a flash row can read: (JEDEC id, uid
# bits). The Boya's 128 bits are what name it a BY25Q32ES.
SAMPLE_FLASH = ((0xC84016, 64), (0x684016, 128))
# ...and inside the package, by size in MiB: (JEDEC id, uid bits, the vendor
# the eFuse would name).
SAMPLE_IN_PACKAGE = {1: (0xC84014, 64, "GD"), 2: (0xC84015, 64, "GD"),
                     4: (0x464016, 128, "XMC"), 8: (0xC84017, 64, "GD"),
                     16: (0xC84018, 64, "GD")}
# The XM25QH32D's 128-bit read holds this many programmed bits, then ff: the
# three C3 SuperMinis read on rpi5-433mhz all do.
XMC_PADDED = (0x464016, 80)


def sample_device(part: espressif.Part, n: int) -> Esp32Device:
    """A clearly synthetic device of `part`: a locally administered MAC
    (02:...), revision v9.9 (none for an ESP8266, which reports none), and
    ids made up from the part's name, so every sample's differ and none is a
    pattern. Its MAC always has a hex letter in it, as real ones do: one of
    digits alone would be drawn as a Micro QR code. For the sample sheet
    only; it was read from nothing."""
    fam = part.family
    salt = 0
    while True:
        tail = _sample_hex(part, f"mac {salt}", 10)
        if re.search("[a-f]", tail):
            break
        salt += 1
    mac = "02:" + ":".join(tail[i:i + 2] for i in range(0, 10, 2))
    size = f"{part.flash_mb:g}" if part.flash_mb else "4"
    feats = ["Wi-Fi"] if fam.wifi else []
    if part.flash_mb:
        # in the package: the 4 MiB XMC the real C3s carry; other sizes, the
        # GigaDevice part of that size, since no read has named theirs and a
        # sample must carry a part the tables name, as a real one must
        jedec, bits, vendor = SAMPLE_IN_PACKAGE.get(int(part.flash_mb), SAMPLE_IN_PACKAGE[4])
        if fam not in (espressif.ESP8266, espressif.ESP32):
            # the eFuse of the later chips names the in-package flash's size
            # and vendor; esptool says only "Embedded Flash" for the older two
            feats.append(f"Embedded Flash {size}MB ({vendor})")
    else:
        jedec, bits = SAMPLE_FLASH[n % len(SAMPLE_FLASH)]
    # every other sample has no flash uid, to show the eFuse id alone; the
    # XMC's is 80 programmed bits and ff after them, as the real C3s' are
    uid = _sample_hex(part, "flash uid", bits // 4) if n % 2 else ""
    if uid and jedec == XMC_PADDED[0]:
        uid = uid[:XMC_PADDED[1] // 4] + "f" * ((bits - XMC_PADDED[1]) // 4)
    efuse: dict[str, Any] = {}
    if fam.chip_uid:
        efuse["OPTIONAL_UNIQUE_ID"] = _sample_hex(part, "efuse uid", 32)
    return Esp32Device(
        tty=None, transport="sample", mac=mac, chip=part.part,
        chip_description=part.part,
        revision=None if fam is espressif.ESP8266 else "v9.9", features=tuple(feats),
        crystal_mhz=26 if fam is espressif.ESP8266 else 40, flash_jedec=f"0x{jedec:06x}",
        flash_uid=uid, flash_uid_bits=bits if uid else None, efuse=efuse)


def sample_label(part: espressif.Part, n: int = 1) -> MicroLabel:
    """The label a device of `part` would get; see ``sample_device``."""
    return _label("sample", sample_device(part, n), part)
