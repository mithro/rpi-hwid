#!/usr/bin/env python3
"""Which FPGA board is attached to this Raspberry Pi?

A separate, dependency-free file like rpi_hwid.probe, for the same reason
(python3 >= 3.5 on the host, sent over ssh, nothing installed), kept apart
from it because few people have an FPGA on their Pi. Standalone:

    ssh pi@host 'python3 -' --json --jtag < src/rpi_hwid/fpga.py
    rpi-hwid fpga --json [--jtag] [--flash] [--pins=TDI:TDO:TCK:TMS]

or appended to the Pi probe by ``rpi-hwid probe --fpga`` and
``rpi-hwid collect --fpga``, which merge its findings into that document's
``verdict.summary.fpga``.

From what the Pi can see without touching the FPGA:
    PCIe: a NeTV2 running LitePCIe is 10ee:7024 with one 1 MiB BAR; an SQRL
    Acorn CLE-215+ is 1e24:021f (or 10ee:7011 under other gateware) with a
    128 KiB plus 64 KiB BAR pair. A board running Ulf Frisk's pcileech-fpga
    gateware is 10ee:0666, class 0x020000, with one 4 KiB BAR; on
    welland.fpgas.online's pi-sw1-p38 it sits beside the FTDI FT601 USB3
    FIFO bridge (0403:601f) the gateware talks through (2026-09-16). The
    gateware names itself, not the hardware under it -- ScreamerM2, Squirrel
    and Enigma X1 all run it -- and the FT601 answers serial 000000000001,
    the part's default, so nothing here identifies the unit. Config space
    and BAR *sizes* are read from sysfs, which the host bridge answers; a
    BAR is never mapped, because that wedged a host.
    USB: a Digilent Arty carries its own FT2232 (0403:6010, manufacturer
    "Digilent") whose serial (210319...) is the board's identity. A NeTV2 has
    no FTDI of its own; its JTAG is bit-banged from the host's GPIO.
    A Great Scott Gadgets Cynthion answers 1d50:615b whatever gateware it is
    running and 1d50:615c when its Apollo debug controller holds the shared
    port; the interface subclass says which gateware (0x10 analyzer, 0x20
    Moondancer, 0x00 the Apollo stub). Its analyzer gateware publishes the
    ECP5 configuration flash's 64-bit unique id as the USB serial number, and
    bcdDevice is the board revision -- 0104 is r1.4 -- so a board's identity,
    its revision and, through the revision, its die are all readable with
    nothing sent to it and no flag at all (rpi5-netv2, 2026-09-21).
With --jtag, openFPGALoader reads the idcode and, on a 7-series, the Device
DNA, over the Arty's own FT2232 when there is one and otherwise over the
host's GPIO harness (libgpiod, pins 27:22:4:17); off by default because it
drives pins. Where openFPGALoader cannot read the chain, openocd reads the same
two values over either cable -- the GPIO harness or a Digilent FT2232 -- so
neither a NeTV2 nor an Arty is left unread. That matters because Raspbian 9
Stretch has no openFPGALoader package at all, and a NeTV2 on a Pi 3 has no
other path whatsoever: nothing on PCIe, the board having no PCIe, and no FTDI
of its own. "Cannot read" includes installed-but-unable: the openFPGALoader on
the welland.fpgas.online sw1 rigs was built without its libgpiod backend and
answers only "error : libgpiod not found", so five NeTV2s went unlabelled
while openocd sat beside it (2026-09-16). With --flash as well, an Arty's SPI
flash is identified by its JEDEC id, which loads openFPGALoader's spiOverJtag
bridge into the FPGA and drops the running design until the next power cycle. Note the S25FL128S and
S25FL127S both answer JEDEC 0x012018.

With --force-offline, a Cynthion's ECP5 gives up its TraceID, the die-level
identifier an ECP5 has in place of a Xilinx Device DNA: UIDCODE_PUB (0x19)
into an 8-bit IR, 64 bits out of the DR, of which only the bottom 56 are
factory (the top 8 come from the bitstream's TRACE_ID_BINARY, so keying a name
on the unmasked value would rename a board on a gateware rebuild). Its own
flag, never --jtag: a Cynthion's TAP is reachable only through the Apollo
debug controller, and Apollo only takes the shared USB port by asking the
gateware to stand down, which ends the capture and may drop power to whatever
is on the board's TARGET port. --jtag is harmless on a NeTV2 or an Arty and a
fleet-wide collect must not stop every analyzer on it. The read puts the board
back -- reconfigure from flash, hand the port back, then wait to see the
analyzer re-enumerate -- which apollo's own `info --force-offline` does not do;
--recover-cynthion is the way home if it ever fails.
"""
import fcntl
import glob
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

# Prefix for every absolute path read; the tests point it at a fake tree.
ROOT = ""


def sh(args, timeout=15):
    """Run a fixed argument list (never a shell) and return its stdout."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)   # 3.5-safe
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read().rstrip(b"\0").decode("ascii", "replace").strip()
    except OSError:
        return None


def pcie_devices():
    """Every PCIe endpoint that is not a bridge, with its BAR sizes from
    sysfs `resource` (start end flags per line) -- never a mapping."""
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/bus/pci/devices/*")):
        cls = read(p + "/class") or ""
        if cls.startswith("0x0604"):        # PCI-PCI bridge: the root port
            continue
        vend, dev = read(p + "/vendor"), read(p + "/device")
        bars = []
        for line in (read(p + "/resource") or "").splitlines():
            parts = line.split()
            if len(parts) == 3:
                start, end = int(parts[0], 16), int(parts[1], 16)
                if end > start:
                    bars.append(end - start + 1)
        out.append({"slot": os.path.basename(p), "id": "%s:%s" % (vend[2:], dev[2:]),
                    "class": cls, "bars": bars,
                    "subsystem": "%s:%s" % ((read(p + "/subsystem_vendor") or "0x????")[2:],
                                            (read(p + "/subsystem_device") or "0x????")[2:])})
    return out


def ftdi_devices():
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/bus/usb/devices/*")):
        if read(p + "/idVendor") == "0403":
            out.append({"path": os.path.basename(p), "id": "0403:" + (read(p + "/idProduct") or ""),
                        "manufacturer": read(p + "/manufacturer"), "product": read(p + "/product"),
                        "serial": read(p + "/serial")})
    return out


# A WCH CH347 in its UART+JTAG mode, which is how pi-sw1-p38 reaches its
# PCILeech card (openfpgaloader-36, 2026-09-22: `--scan-usb` lists it as
# ch347_jtag). Only the id that has been met is listed: the same vendor makes
# CH340 serial adapters, and a scan that took one for a JTAG cable would
# drive whatever it is wired to. Its serial is 0123456789 on every unit, so it
# says nothing about which cable this is and is never keyed on.
CH347_JTAG_IDS = ("1a86:55dd",)


def ch347_cables():
    """The CH347 JTAG cables on this host."""
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/bus/usb/devices/*")):
        vidpid = "%s:%s" % (read(p + "/idVendor"), read(p + "/idProduct"))
        if vidpid in CH347_JTAG_IDS:
            out.append({"path": os.path.basename(p), "id": vidpid,
                        "product": read(p + "/product")})
    return out


# --- Cynthion, from its descriptors alone ---------------------------------------
#
# A Great Scott Gadgets Cynthion answers 1d50:615b whatever gateware is loaded
# and 1d50:615c when its Apollo debug controller holds the shared port. The
# interface subclass is what tells the gateware apart, which is the reason
# cynthion/shared/usb.toml exists at all: "Cynthion reports the same idVendor
# and idProduct values in the USB device descriptor irrespective of the
# gateware running on the device."
#
# Nothing here is sent to the board -- it is all sysfs -- so no flag guards it,
# unlike every other FPGA this file identifies. Two identifiers fall out for
# free. The analyzer gateware publishes the ECP5 configuration flash's 64-bit
# unique id as the USB serial number (cynthion/gateware/analyzer/top.py:232,
# `iSerialNumber = ECP5FlashUIDStringDescriptor`), and bcdDevice is the board
# revision rather than any gateware version (apollo_fpga/__init__.py:260
# reads it as major = bcdDevice >> 8, minor = bcdDevice & 0xFF).
#
# The ECP5's own die-level identifier, its 64-bit TraceID, is not here: it is
# read with UIDCODE_PUB (0x19) over JTAG, and a Cynthion's TAP hangs off the
# Apollo controller, which only reaches the port by asking the gateware to
# stand down -- ending the capture. That read lives behind its own flag.
CYNTHION_VID = "1d50"
CYNTHION_GATEWARE_PID, CYNTHION_APOLLO_PID = "615b", "615c"
# bInterfaceSubClass, from cynthion/shared/usb.toml. 0x00 is the Apollo stub
# that shares the port and says nothing about which gateware is loaded.
CYNTHION_SUBCLASS = {"10": "analyzer", "20": "moondancer"}
# Only the analyzer gateware is *known* to publish the flash uid as its serial.
# Moondancer's descriptors come from firmware this has not read, so its serial
# is left unclaimed rather than guessed at.
CYNTHION_FLASH_UID_MODES = ("analyzer",)
# An Apollo major of 0xFF is an external board (a Daisho, a Pergola) and 0xFE a
# Cynthion subdevice; neither is a Cynthion revision, and "r255.1" on a label
# would be a confident lie.
CYNTHION_NOT_A_REVISION = (0xFE, 0xFF)


def cynthion_devices():
    """Every Cynthion on this host's USB, with the descriptors a label needs."""
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/bus/usb/devices/*")):
        pid = read(p + "/idProduct")
        if read(p + "/idVendor") != CYNTHION_VID or pid not in (
                CYNTHION_GATEWARE_PID, CYNTHION_APOLLO_PID):
            continue
        subclasses = []
        for i in sorted(glob.glob(p + "/*:*")):
            sub = read(i + "/bInterfaceSubClass")
            if sub is not None:
                subclasses.append(sub)
        out.append({"path": os.path.basename(p), "id": CYNTHION_VID + ":" + pid,
                    "manufacturer": read(p + "/manufacturer"),
                    "product": read(p + "/product"), "serial": read(p + "/serial"),
                    "bcd_device": read(p + "/bcdDevice"), "subclasses": subclasses})
    return out


def cynthion_mode(dev):
    """Which gateware is loaded, by interface subclass, or None if nothing says."""
    if dev["id"].endswith(CYNTHION_APOLLO_PID):
        return "apollo"
    for sub in dev.get("subclasses") or ():
        if sub in CYNTHION_SUBCLASS:
            return CYNTHION_SUBCLASS[sub]
    return None


def cynthion_flash_uid(dev):
    """The ECP5 configuration flash's unique id, where the gateware published
    it as the USB serial.

    In Apollo mode the serial is the debug controller's own, so a board found
    that way is left unkeyed rather than named from the wrong chip -- the same
    board would otherwise get two permanent names depending on what it
    happened to be running when the probe ran.
    """
    if cynthion_mode(dev) in CYNTHION_FLASH_UID_MODES:
        return dev.get("serial")
    return None


# --- the ECP5's TraceID, over Apollo ---------------------------------------------
#
# The die-level identifier, the ECP5's answer to a Xilinx Device DNA. Read by
# shifting UIDCODE_PUB (0x19) into an 8-bit IR and 64 bits out of the DR --
# the same shape as the FUSE_DNA read above. _PUB is the public instruction,
# so the read itself does not disturb a configured device, as ecpdap's
# read_uid() shows by issuing it with no ISC_ENABLE.
#
# Reaching it is what costs. A Cynthion's TAP hangs off the Apollo debug
# controller, and Apollo only takes the shared USB port by asking the gateware
# to stand down, which ends the capture and leaves the FPGA held offline. So
# this is behind its own flag, never --jtag: on a NeTV2 or an Arty --jtag is
# harmless, and a routine collect across the fleet must not silently stop
# every analyzer on it.
#
# Of the 64 bits the top 8 are the design's own, set from the bitstream's
# TRACE_ID_BINARY preference, and only the bottom 56 are factory. Naming a
# board on the unmasked value would rename it whenever its gateware was
# rebuilt.
UIDCODE_PUB = 0x19
TRACE_ID_BITS = 64
TRACE_ID_MASK = 0x00FFFFFFFFFFFFFF
ECP5_IR_BITS = 8


def wire_hex_to_int(raw):
    """A scan result's bytes, as the chain clocked them out, as a number.

    Least significant byte first. Settled by a known answer rather than by
    reading apollo's source: with nothing shifted into the IR, a TAP reset
    leaves the IDCODE in the DR, and rpi5-netv2's ECP5 returned 43101121 --
    0x21111043, the LFE5U-12F a Cynthion r1.4 carries, reversed (2026-09-21).
    """
    try:
        data = bytearray.fromhex(raw)
    except (TypeError, ValueError):
        return None
    value = 0
    for byte in reversed(data):
        value = (value << 8) | byte
    return value


def trace_id_value(raw):
    """The factory 56 bits of a TraceID, or None if the chain said nothing."""
    value = wire_hex_to_int(raw)
    if value is None:
        return None
    masked = value & TRACE_ID_MASK
    # An absent or unpowered chain shifts all ones or all zeroes; either would
    # otherwise become a board's permanent name.
    if masked in (0, TRACE_ID_MASK):
        return None
    return "0x%014x" % masked


def flash_id_from_raw(raw):
    """The JEDEC id out of a 0x9F reply: one turnaround byte, then three.

    Rejects the two answers a chip that is not there gives -- all ones and
    all zeroes -- for the same reason the TraceID mask does: either would
    otherwise be printed on a sticker as an identity.
    """
    try:
        data = bytearray.fromhex(raw)
    except (TypeError, ValueError):
        return None
    if len(data) < 4:
        return None
    ident = data[1] << 16 | data[2] << 8 | data[3]
    # JEP106 has no manufacturer 0x00, and 0xFF is a line nothing drove. A
    # first byte of either is not an id, however plausible the rest: the
    # first background-SPI read (rpi5-netv2, 2026-09-22) came back
    # 0x009966, which is its own reset commands echoed.
    if data[1] in (0x00, 0xFF):
        return None
    return "0x%06x" % ident


def flash_uid_from_raw(raw):
    """The unique id out of a 0x4B reply: four dummy bytes, then eight.

    In the order the flash clocked them out. apollo folds the same bytes up
    little-endian for display, which is a presentation choice rather than a
    fact about the chip; what makes this readable at all is that the same
    number is already on the USB bus as the gateware's serial, so the order
    is settled by comparison rather than by assumption (see the caller).
    """
    try:
        data = bytearray.fromhex(raw)
    except (TypeError, ValueError):
        return None
    if len(data) < 13:
        return None
    uid = data[5:13]
    if not any(uid) or all(b == 0xFF for b in uid):
        return None
    return "".join("%02x" % b for b in uid)


def cynthion_offline_parse(out):
    """What the offline reader said, as values rather than text.

    The reader moves bytes and nothing else; every decision about what they
    mean is made here, where it can be tested without hardware -- the split
    the pcileech reader already uses.
    """
    res = {}
    # Whether the board came back is reported even when the read failed: a
    # stranded rig is the thing the caller most needs to hear about, and it
    # is independent of whether a number was obtained.
    err = re.search(r"ERROR=(.*)", out)
    if err:
        res["error"] = err.group(1).strip()
    m = re.search(r"TRACEIDRAW=([0-9a-fA-F]+)", out)
    res["trace_id"] = trace_id_value(m.group(1)) if m else None
    m = re.search(r"FLASHUID=([0-9a-fA-F]+)", out)
    res["flash_uid"] = m.group(1).lower() if m else None
    # ...and the same chip asked directly, over the ECP5's background SPI.
    m = re.search(r"FLASHIDRAW=([0-9a-fA-F]+)", out)
    res["flash_jedec"] = flash_id_from_raw(m.group(1)) if m else None
    m = re.search(r"FLASHUIDRAW=([0-9a-fA-F]+)", out)
    read = flash_uid_from_raw(m.group(1)) if m else None
    if read:
        # Two paths to one number: the gateware read it off the flash and
        # published it as a USB string descriptor, and this read it off the
        # flash over JTAG. They are independent enough that agreement is
        # real evidence -- a framing error in one cannot agree with the
        # other -- so the disagreement is recorded rather than resolved.
        # The gateware publishes the eight bytes folded up little-endian, as
        # apollo's read_flash_uid prints them: measured on rpi5-netv2
        # (2026-09-22), the chip clocked out de60c430df257126 and the serial
        # is 267125df30c460de. `read` stays as clocked; the comparison, and
        # the value recorded, are in the published order.
        published = "".join(reversed([read[i:i + 2] for i in range(0, len(read), 2)]))
        res["flash_uid_read"] = read
        res["flash_uid_bits"] = 64
        res["flash_uid_state"] = "read"
        if res["flash_uid"]:
            res["flash_uid_agree"] = published == res["flash_uid"]
        else:
            res["flash_uid"] = published
    # Whether the analyzer came back, which apollo's own --force-offline never
    # checks: it reads and leaves the FPGA held offline.
    m = re.search(r"RESTORED=(\S+)", out)
    res["restored"] = bool(m and m.group(1) != "none")
    return res


def cynthion_revision(bcd):
    """The board revision bcdDevice carries: '0104' -> '1.4'."""
    try:
        value = int(bcd, 16)
    except (TypeError, ValueError):
        return None
    major, minor = value >> 8, value & 0xFF
    if major in CYNTHION_NOT_A_REVISION:
        return None
    return "%d.%d" % (major, minor)




# The GPIO harness, as openFPGALoader spells it on the command line and as
# openocd wants it counted out. openFPGALoader documents --pins as
# TDI:TDO:TCK:TMS; openocd's *_jtag_nums take tck tms tdi tdo.
HARNESS_PINS = "27:22:4:17"
HARNESS_TCK, HARNESS_TMS, HARNESS_TDI, HARNESS_TDO = 4, 17, 27, 22
# ...and it is only the NeTV2's. An Acorn's JTAG comes off the card's own P1
# Pico-EZmate header on different pins entirely: 2:3:4:14 on the ps1 Compute
# Blades, 10:9:11:8 on the Welland Pi 5s (measured by the Acorn deployment,
# 2026-09-20). Hardcoding one harness means the other's boards cannot be read
# at all, which since an unread identifier became fatal is the difference
# between a label and no label. --pins overrides it.


# Which board each harness reaches. A harness is not generic wiring: its pins
# are the card's own JTAG header, so driving them is driving that card. This
# is the same evidence that has always named a NeTV2 -- it was simply the only
# harness there was. Measured on the fleet 2026-09-20/21.
HARNESS_BOARD = {
    "27:22:4:17": "netv2",      # the NeTV2's, off the Pi's 40-pin header
    "10:9:11:8": "acorn",       # an Acorn's P1 Pico-EZmate, on a Pi 5
    "2:3:4:14": "acorn",        # an Acorn's P1, on a ps1 Compute Blade
}


# --- the fpgas.online Acorn SoC, over PCIe BAR0 ---------------------------------
#
# A second way to read an Acorn's identity, and the only one that needs no
# JTAG harness at all. The SoC these cards now carry keeps an ident string and
# the Device DNA in its register space:
#     BAR0 + 0x800   ident, one ASCII character per 32-bit word, NUL-terminated
#                    ("fpgas-online Acorn PCIe SoC cle-215+ <build date>")
#     BAR0 + 0x2800  DNA, high word      BAR0 + 0x2804  DNA, low word
# Offsets from the Acorn deployment, which built the SoC (2026-09-21).
#
# This is what makes the DNA checkable rather than merely read: the same
# number comes back over JTAG and over PCIe, and cross_check() compares them.
# The ident string is also the only thing on one of these cards that still
# names the board once the Sqrl factory image is gone -- its PCIe id describes
# the gateware, not the card under it.
#
# Reads only, and only within this one register window. The file's standing
# rule against mapping a BAR came from mapping an *unknown* board's BAR, which
# wedged a host; this is a known window in a known SoC, identified by its own
# magic before anything is believed.
SOC_IDENT_OFFSET = 0x800
SOC_IDENT_WORDS = 64
SOC_DNA_HI, SOC_DNA_LO = 0x2800, 0x2804
SOC_IDENT_MAGIC = "fpgas-online"
SOC_MODELS = ("cle-215+", "cle-101")


def soc_ident(words):
    """The ident string from BAR0: the low byte of each 32-bit word.

    Takes the words themselves rather than a block of bytes, because this
    BAR only answers naturally-aligned 32-bit reads. A bytewise copy of the
    same window -- an ordinary mmap slice -- comes back as every byte 0xff
    while word reads return the string (measured on pi-sw2-p48, 2026-09-21),
    so how the window is read is part of what it means.
    """
    text = ""
    for word in words:
        char = word & 0xFF
        if char == 0:
            break
        text += chr(char)
    text = text.strip()
    return text if text.startswith(SOC_IDENT_MAGIC) else None


def soc_model(ident):
    """Which Acorn the SoC says it was built for, from its ident string."""
    if not ident:
        return None
    lowered = ident.lower()
    for model in SOC_MODELS:
        if model in lowered:
            return model
    return None


def soc_dna(hi, lo):
    """The Device DNA the SoC reports, or None for an unconfigured read."""
    value = ((hi & 0xFFFFFFFF) << 32) | (lo & 0xFFFFFFFF)
    if value in (0, 0xFFFFFFFFFFFFFFFF):
        return None
    return "0x%016x" % value


# Run as root by the same interpreter, like the other readers here, and kept
# to moving bytes: it maps the one register window, copies it, and unmaps.
# Bus mastering is never enabled and nothing is written.
SOC_READER = r'''
import mmap, os, struct, sys
bdf = sys.argv[1]
path = "/sys/bus/pci/devices/%s/resource0" % bdf
try:
    fd = os.open(path, os.O_RDONLY)
except OSError as e:
    print("ERROR=cannot open %s: %s" % (path, e)); sys.exit(0)
try:
    size = 0x3000
    m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
except (OSError, ValueError) as e:
    os.close(fd); print("ERROR=cannot map BAR0: %s" % e); sys.exit(0)
try:
    # word reads, never a slice: this BAR answers a bytewise copy with 0xff
    words = [struct.unpack_from("<I", m, 0x800 + 4 * i)[0] for i in range(64)]
    hi, lo = struct.unpack_from("<I", m, 0x2800)[0], struct.unpack_from("<I", m, 0x2804)[0]
    print("IDENT=" + ",".join("%08x" % w for w in words))
    print("DNA=%08x%08x" % (hi, lo))
finally:
    m.close()
    os.close(fd)
'''


def soc_probe(slot):
    """The SoC's ident and DNA over PCIe, or {"error": why}."""
    out = sh_all(["sudo", sys.executable or "python3", "-c", SOC_READER, slot],
                 timeout=30)
    err = re.search(r"ERROR=(.*)", out)
    if err:
        return {"error": err.group(1).strip()}
    res = {}
    m = re.search(r"IDENT=([0-9a-f,]*)", out)
    if m:
        words = [int(w, 16) for w in m.group(1).split(",") if w]
        ident = soc_ident(words)
        res["ident"] = ident
        res["model"] = soc_model(ident)
    m = re.search(r"DNA=([0-9a-f]{16})", out)
    if m:
        res["dna"] = soc_dna(int(m.group(1)[:8], 16), int(m.group(1)[8:], 16))
    return res


def normalise_id(value):
    """A hex identifier as one canonical string, however a tool spelled it.

    openFPGALoader prints 0x0054b48664b04854 where another path hands back
    54b48664b04854; they are the same number and must compare equal, or a
    board read two ways would look like two boards.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    try:
        return "0x%016x" % int(text, 16)
    except ValueError:
        return None


def cross_check(readings):
    """One identifier read several ways, compared.

    A value read twice is worth more than a value read once only if the two
    readings are actually checked against each other. `readings` maps the
    method to what it returned; the result carries the agreed value, which
    methods contributed, and whether they agreed.

    A disagreement is never resolved here. There is no way to tell which
    reading is the lie, and picking one would put an arbitrary number on a
    sticker; the caller is told instead, and an identifier in conflict is
    treated as one that was not read.
    """
    got = {}
    for method, raw in sorted(readings.items()):
        value = normalise_id(raw)
        if value is not None:
            got[method] = value
    if not got:
        return {"value": None, "sources": [], "agree": None}
    distinct = set(got.values())
    if len(distinct) > 1:
        return {"value": None, "sources": sorted(got), "agree": False,
                "conflict": dict(got)}
    return {"value": distinct.pop(), "sources": sorted(got),
            "agree": True if len(got) > 1 else None}


def harness_board(pins):
    """The board a GPIO harness is wired to, or None if nobody has said."""
    return HARNESS_BOARD.get(pins or HARNESS_PINS)


def harness_nums(pins):
    """openFPGALoader's TDI:TDO:TCK:TMS as openocd's (tck, tms, tdi, tdo).

    The two tools take the same four wires in different orders, and handing
    one the other's order drives the wrong lines without saying so.
    """
    try:
        tdi, tdo, tck, tms = (int(p) for p in str(pins).split(":"))
    except (AttributeError, TypeError, ValueError):
        return None
    return tck, tms, tdi, tdo
# openocd's bcm2835gpio driver refuses to start without a reset line even when
# reset is never asserted ("Require at least one of trst or srst gpios to be
# specified"). 24 is the spare Alphamax's own NeTV2-on-a-Pi interface config
# nominates, and reset_config none keeps it from ever being driven.
HARNESS_SRST = 24

# bcm2835gpio mmaps the SoC register block, so it needs that block's address.
# A Pi 5 drives its header from the RP1 instead and is not reachable this way
# at all -- it needs linuxgpiod, which is why that is tried first.
PERIPHERAL_BASE = (
    ("Raspberry Pi 4", "0xFE000000"),
    ("Raspberry Pi 3", "0x3F000000"),
    ("Raspberry Pi 2", "0x3F000000"),
    ("Raspberry Pi Zero 2", "0x3F000000"),
)
DEFAULT_PERIPHERAL_BASE = "0x20000000"        # BCM2835: Pi 1, Zero, CM1

# Xilinx 7-series FUSE_DNA, from openFPGALoader's src/xilinx.cpp: shift it into
# a 6-bit IR, shift 64 bits out of the DR, reverse them and keep 57. Verified
# against openFPGALoader on a board it can reach, which reported the same DNA.
FUSE_DNA = "0x32"
DNA_BITS = 64
DNA_MASK = 0x1ffffffffffffff


def sh_rc(args, timeout=15):
    """As sh_all(), but the exit status too: a tool that failed and a tool
    that printed nothing useful are different things, and only the status
    tells them apart -- openFPGALoader printed a whole flash report for a
    read that never happened until it was taught to exit 1."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)   # 3.5-safe
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, str(exc)


def sh_split(args, timeout=15):
    """stdout and stderr kept apart: a tool whose stdout is a JSON document
    and whose stderr says why cannot have the two run together."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)   # 3.5-safe
        return r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        return "", str(exc)


def sh_all(args, timeout=15):
    """As sh(), but stderr too: openocd says everything on stderr."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=timeout)   # 3.5-safe
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def peripheral_base():
    model = read(ROOT + "/proc/device-tree/model") or ""
    for prefix, base in PERIPHERAL_BASE:
        if model.startswith(prefix):
            return base
    return DEFAULT_PERIPHERAL_BASE


# <linux/gpio.h>: _IOR(0xB4, 0x01, struct gpiochip_info), whose 68 bytes are
# name[32], label[32] and a __u32 line count. The same call gpiodetect makes.
GPIO_GET_CHIPINFO_IOCTL = 0x8044B401

# The driver label of the GPIO chip that owns the 40-pin header, measured on
# the fleet 2026-09-16: a 3B+ registers pinctrl-bcm2835 and a Pi 4
# pinctrl-bcm2711, both as gpiochip0; a Pi 5 registers pinctrl-rp1, and on
# kernel 6.12 as gpiochip15.
HEADER_GPIO_LABELS = ("pinctrl-rp1", "pinctrl-bcm2711", "pinctrl-bcm2835")


def gpiochips():
    """[(number, label)] for every GPIO chip, numbered as the kernel has them.

    Asked of the kernel, not of the device tree, because on a Pi 5 the two
    disagree: /proc/device-tree/aliases/gpiochip0 names the RP1, while the
    kernel registered the RP1 as gpiochip15. openocd's own
    raspberrypi5-gpiod.cfg goes by the alias and would open the wrong chip.

    The number comes from the name the kernel reports, not the device file:
    a 3B+ also has /dev/gpiochip4, a udev alias that answers as gpiochip0.
    Needs read access to /dev/gpiochip*, which the gpio group gives.
    """
    out = set()
    for dev in glob.glob(ROOT + "/dev/gpiochip*"):
        try:
            fd = os.open(dev, os.O_RDONLY)
        except OSError:
            continue
        try:
            info = fcntl.ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, b"\0" * 68)
        except (OSError, IOError):
            continue
        finally:
            os.close(fd)
        name, label, _lines = struct.unpack("32s32sI", info)
        m = re.match(r"gpiochip(\d+)$", name.rstrip(b"\0").decode("ascii", "replace"))
        if m:
            out.add((int(m.group(1)), label.rstrip(b"\0").decode("ascii", "replace")))
    return sorted(out)


def header_gpiochip(chips):
    """The number of the chip that drives the 40-pin header, or None."""
    for want in HEADER_GPIO_LABELS:
        for number, label in chips:
            if label == want:
                return number
    return None


def openocd_adapter(digilent_serial=None, pins=None):
    """The openocd commands that select and configure the cable, or None.

    Commands that openocd renamed between releases are wrapped in catch{}, so
    both spellings can be offered and whichever this build does not have is
    swallowed rather than aborting the run. That is cheaper and far more
    robust than ordering version strings like "0.10.0-00019-gfb5691f0-dirty".
    """
    if digilent_serial is not None:
        # openocd's own Digilent config carries the FT2232H layout bits in the
        # spelling this version understands, so source it rather than restate
        # them -- but widen its product-name filter, which pins "Digilent
        # Adept USB Device" while an Arty's on-board cable answers "Digilent
        # USB Device". Pinning the serial keeps two cables apart.
        return ["source [find interface/ftdi/digilent-hs1.cfg]",
                "catch {adapter usb product_name \"Digilent USB Device\"}",
                "catch {ftdi_device_desc \"Digilent USB Device\"}",
                "catch {adapter serial %s}" % digilent_serial,
                "catch {ftdi_serial %s}" % digilent_serial,
                "catch {adapter speed 1000}", "catch {adapter_khz 1000}"]
    # linuxgpiod is asked for by capability: it is the only driver that can
    # reach a Pi 5's header, the RP1 being nothing bcm2835gpio can mmap.
    pi5 = (read(ROOT + "/proc/device-tree/model") or "").startswith("Raspberry Pi 5")
    if "invalid" not in sh_all(["openocd", "-c", "adapter driver linuxgpiod",
                                "-c", "shutdown"]).lower():
        # Every signal has to name its chip: a gpio given without -chip is
        # left unassigned, and openocd then refuses with "Require tck, tms,
        # tdi and tdo gpios for JTAG mode" having driven nothing. Which chip
        # is found by label (header_gpiochip). A board that will not say is
        # assumed to be chip 0 everywhere but a Pi 5, where guessing would
        # mean toggling pins on some other controller, so it is skipped.
        chip = header_gpiochip(gpiochips())
        if chip is None:
            if pi5:
                return None
            chip = 0
        nums = harness_nums(pins) if pins else None
        tck, tms, tdi, tdo = nums or (HARNESS_TCK, HARNESS_TMS, HARNESS_TDI, HARNESS_TDO)
        pin_pairs = (("tck", tck), ("tms", tms), ("tdi", tdi), ("tdo", tdo))
        # 0.11 spelled this as one chip for the adapter and four numbers.
        # Offered first, so that on a later release the deprecated wrapper
        # (if it still exists) is overridden by the explicit form after it.
        # Unmeasured: no 0.11 build is in the fleet to try it on.
        return (["adapter driver linuxgpiod",
                 "catch {linuxgpiod_gpiochip %d}" % chip,
                 "catch {linuxgpiod_jtag_nums %d %d %d %d}" % tuple(
                     p for _s, p in pin_pairs)]
                + ["catch {adapter gpio %s -chip %d %d}" % (sig, chip, pin)
                   for sig, pin in pin_pairs]
                + ["adapter speed 1000"])
    if pi5:
        return None           # RP1 header, and this openocd has no linuxgpiod
    nums = harness_nums(pins) if pins else None
    return ["interface bcm2835gpio",
            "bcm2835gpio_peripheral_base %s" % peripheral_base(),
            "bcm2835gpio_speed_coeffs 146203 36",
            "bcm2835gpio_jtag_nums %d %d %d %d" % (
                nums or (HARNESS_TCK, HARNESS_TMS, HARNESS_TDI, HARNESS_TDO)),
            "bcm2835gpio_srst_num %d" % HARNESS_SRST,
            "adapter_khz 1000"]


def openocd_probe(digilent_serial=None, pins=None):
    """idcode and Device DNA over openocd, on either cable.

    The fallback for a host that cannot have openFPGALoader: Raspbian 9
    Stretch has no package for it, so a NeTV2 there is invisible to every
    other path (no PCIe on a Pi 3, no FTDI of its own). openocd is packaged
    much more widely, drives the Digilent FT2232 as well as the GPIO harness,
    and the chain is the same chain either way.
    """
    if not sh(["which", "openocd"]):
        return None
    adapter = openocd_adapter(digilent_serial, pins)
    if adapter is None:
        return None
    cmds = adapter + [
        "transport select jtag", "reset_config none",
        # No -expected-id: the point of the scan is to be told which part it
        # is. -ignore-version keeps a silicon revision from being an error.
        "jtag newtap fpga tap -irlen 6 -ignore-version -expected-id 0",
        "init", "scan_chain",
        # Test-Logic-Reset first, as openFPGALoader does before FUSE_DNA.
        "jtag arp_init",
        "irscan fpga.tap %s" % FUSE_DNA,
        "set dna [drscan fpga.tap %d 0]" % DNA_BITS,
        "echo \"RAWDNA=$dna\"",
        "shutdown",
    ]
    argv = ["sudo", "openocd"]
    for c in cmds:
        argv += ["-c", c]
    out = sh_all(argv, timeout=60)
    m = re.search(r"tap/device found: (0x[0-9a-f]+)", out)
    if not m:
        return {"idcode": None, "tool": "openocd", "raw": out[-200:]}
    # fpga_verdict reads `cable` to tell an Arty's own FT2232 from the GPIO
    # harness that reaches a NeTV2, so it has to say which one this was.
    res = {"idcode": m.group(1), "tool": "openocd", "dna": None,
           "cable": "digilent" if digilent_serial else "gpio"}
    if not digilent_serial:
        res["pins"] = pins or HARNESS_PINS
    m = re.search(r"RAWDNA=([0-9a-fA-F]+)", out)
    if m:
        raw = int(m.group(1), 16)
        rev = int(format(raw, "0%db" % DNA_BITS)[::-1], 2) & DNA_MASK
        # An all-zero or all-ones shift is an absent or unpowered chain, not a
        # DNA; naming a board from one would mint a wrong name permanently.
        if rev and rev != DNA_MASK:
            res["dna"] = "0x%016x" % rev
    return res


# --- which openFPGALoader, and where an able one comes from --------------------
#
# Reading a flash needs a build carrying the flash-info series, and most hosts
# here have a distro build from before it existed. The version string cannot
# be used to tell: the rp1-jtag static build of the whole series prints
# "openFPGALoader v1.1.1", character for character what an upstream build
# without it prints (measured on rpi5-netv2, 2026-09-22), while the
# fpgas.online build prints a "+fpgasonline." suffix. A version test is
# therefore a false negative on the first and a false positive on any later
# build that drops the suffix. Ask for the flag instead.
OFL_FLASH_FLAG = "--flash-info-json"

# Where a build that has it can be fetched from. fpgas.online-fpga-tools
# publishes one rolling prerelease per series, each asset beside its own
# `<asset>.sha256` in sha256sum format, and a `latest.json` index naming the
# current asset per track, tool and architecture. The index is fetched rather
# than the name constructed, so a change to their naming scheme is followed
# instead of having to be taught here.
OFL_RELEASE_URL = ("https://github.com/fpgas-online/fpgas.online-fpga-tools"
                   "/releases/download/%s/%s")
OFL_SERIES = "v0.0"
OFL_LATEST_JSON = "latest.json"
# `uname -m` on the left, the published asset's arch on the right. Nothing
# reports "armhf" or "arm64" literally; a Pi 3/4 on a 32-bit userland answers
# armv7l even under a 64-bit kernel, and that is the binary it can run.
OFL_ARCH = {"aarch64": "arm64", "armv7l": "armv7", "armv6l": "armv6"}
# Where a fetched build is kept. Under the collecting user's home, not
# /tmp and not a system directory: the probe runs unprivileged and a
# binary it is going to hand to sudo should not be somewhere another
# user could have written it.
OFL_CACHE = "~/.cache/rpi-hwid/openfpgaloader"


def ofl_supports_flash_info(help_text):
    """Whether this build can read a flash, asked of its own --help."""
    return OFL_FLASH_FLAG in (help_text or "")


# Searched after the collecting user's PATH, which over a non-interactive ssh
# can be short: these are where a package or a hand install puts it.
OFL_SYSTEM_DIRS = ("/usr/local/bin", "/usr/bin", "/bin")


def ofl_installed():
    """Every openFPGALoader this host has, in PATH order, each file once.

    All of them, not the first: a host can carry more than one, and the first
    is not necessarily the one that can read a flash. rpi5-netv2 (2026-09-22)
    has a hand-installed v1.1.0 in /usr/local/bin that no package owns, ahead
    of the packaged v1.1.1 in /usr/bin -- and /bin, which is /usr/bin under
    another name, so each is kept once by the file it really is.
    """
    dirs = (os.environ.get("PATH") or "").split(os.pathsep) + list(OFL_SYSTEM_DIRS)
    found, seen = [], set()
    for d in dirs:
        path = os.path.join(d, "openFPGALoader")
        if not d or not os.path.isfile(path) or not os.access(path, os.X_OK):
            continue
        real = os.path.realpath(path)
        if real not in seen:
            seen.add(real)
            found.append(path)
    return found


def ofl_arch(machine):
    """The published arch for a host's `uname -m`, or None if none is built."""
    return OFL_ARCH.get((machine or "").strip())


def ofl_asset(latest, track, arch):
    """(asset, version) from a latest.json document, or None.

    A track or an arch the release has not built is not an error to paper over
    with a constructed name: there is no such file to fetch.
    """
    try:
        entry = latest["latest"][track]["openfpgaloader"][arch]
        return entry["asset"], entry["version"]
    except (KeyError, TypeError):
        return None


def ofl_tree(cache, version, arch):
    """Where the binary and its bridge bitstreams sit once unpacked.

    The asset is a tarball and not a bare executable, because a bare
    executable cannot read a flash. Measured on rpi5-netv2 (2026-09-22) with
    rp1-jtag's static build: --detect and --read-dna work perfectly, and
    --flash-info fails with "Can't program SPI flash: missing device-package
    information", because openFPGALoader's spiOverJtag bridge bitstreams are
    runtime data in a compiled-in DATA_DIR that does not exist on these
    hosts. The tarball carries them; OPENFPGALOADER_SOJ_DIR points the
    binary at them (upstream src/xilinx.cpp, in v1.1.1 and master).
    """
    root = "%s/openFPGALoader-%s-linux-%s" % (cache, version, arch)
    return {"root": root, "binary": root + "/bin/openFPGALoader",
            "bridges": root + "/share/openFPGALoader"}


def ofl_cached(cache, arch):
    """The newest build already fetched for `arch`, or None.

    Its version is read back out of the directory name the release gave it,
    so a host with no route out still knows what it is holding.
    """
    best = None
    for root in sorted(glob.glob("%s/openFPGALoader-*-linux-%s" % (cache, arch))):
        name = os.path.basename(root)
        version = name[len("openFPGALoader-"):-len("-linux-" + arch)]
        tree = ofl_tree(cache, version, arch)
        if os.path.exists(tree["binary"]):
            tree["version"] = version
            best = tree
    return best


def ofl_argv(tree):
    """How to invoke openFPGALoader: the host's own copy, or a fetched tree.

    `sudo` drops the environment, so the bridge directory has to be set on
    the far side of it rather than merged into this process's environ.
    """
    if not tree:
        return ["sudo", "openFPGALoader"]
    return ["sudo", "env", "OPENFPGALOADER_SOJ_DIR=" + tree["bridges"],
            tree["binary"]]


def sha256_expected(sums, name):
    """The digest `sums` gives for `name`, or None.

    The name is checked, not skipped. A digest lifted from a neighbouring
    asset would verify nothing, and this is the only thing between a download
    and a binary run as root on a host full of hardware.
    """
    for line in (sums or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        digest, named = fields[0].lower(), fields[1].lstrip("*")
        if named != name or len(digest) != 64:
            continue
        try:
            int(digest, 16)
        except ValueError:
            continue
        return digest
    return None


def ofl_get(url, timeout=180):
    """The bytes at `url`, or None. Nothing here is fatal on its own: a host
    with no route to GitHub still has whatever openFPGALoader it has."""
    import http.client
    import urllib.request
    try:
        handle = urllib.request.urlopen(url, timeout=timeout)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    try:
        return handle.read()
    except (OSError, http.client.HTTPException):
        return None
    finally:
        handle.close()


def ofl_unpack(blob, into):
    """Unpack a release tarball under `into`, refusing any member that would
    land outside it. Python 3.5 has no extraction filter, and a tarball
    fetched over the network is exactly what one is for."""
    import io
    import tarfile
    try:
        archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except tarfile.TarError:
        return False
    try:
        root = os.path.abspath(into)
        for member in archive.getmembers():
            if not (member.isfile() or member.isdir()):
                return False       # links, devices: not in a tool tarball
            where = os.path.abspath(os.path.join(root, member.name))
            if where != root and not where.startswith(root + os.sep):
                return False
        try:
            # 3.12 and later: ask for the safe filter as well, even though
            # every member has just been checked. 3.5 has no such argument,
            # which is why the check above exists rather than the filter.
            archive.extractall(root, filter="data")
        except TypeError:
            archive.extractall(root)
    except (tarfile.TarError, OSError):
        return False
    finally:
        archive.close()
    return True


def openfpgaloader_tool(download=True):
    """An openFPGALoader that can read a flash, and where it came from.

    The host's own copy if it has the flash-info series, else the published
    static build, fetched once into a cache and verified against the digest
    published beside it before anything is executed. A host whose copy is too
    old is the normal case rather than an error: these Pis mostly carry a
    distro build from before the series existed.
    """
    installed = ofl_installed()
    for binary in installed:
        if ofl_supports_flash_info(sh_rc([binary, "--help"], timeout=20)[1]):
            # by its full path: under sudo a bare name finds sudo's first copy,
            # which is the one that was just passed over
            return {"argv": ["sudo", binary], "source": "host", "flash_info": True}
    unable = {"argv": ofl_argv(None), "source": "host", "flash_info": False,
              "why": "no openFPGALoader on this host has " + OFL_FLASH_FLAG}
    if not installed:
        unable["why"] = "no openFPGALoader on this host"
    if not download:
        return unable
    arch = ofl_arch(os.uname()[4])
    if arch is None:
        unable["why"] = "no static build is published for " + os.uname()[4]
        return unable
    cache = os.path.expanduser(OFL_CACHE)
    index = ofl_get(OFL_RELEASE_URL % (OFL_SERIES, OFL_LATEST_JSON), timeout=60)
    try:
        latest = json.loads(index.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError):
        latest = None
    found = ofl_asset(latest, "stable", arch) if latest else None
    if found is None:
        # No index: no route out, or a release that has not been cut. A build
        # fetched earlier is still on this host and still reads a flash, and
        # falling back to it is the difference between a label and none on a
        # rig with no path to GitHub.
        held = ofl_cached(cache, arch)
        if held is None:
            unable["why"] = ("no static %s build could be found, and none is "
                             "cached on this host" % arch)
            return unable
        return {"argv": ofl_argv(held), "source": os.path.basename(held["root"]),
                "version": held["version"], "sha256": None, "cached": True,
                "flash_info": True}
    asset, version = found
    tree = ofl_tree(cache, version, arch)
    digest = None
    if not os.path.exists(tree["binary"]):
        sums = ofl_get(OFL_RELEASE_URL % (OFL_SERIES, asset + ".sha256"), timeout=60)
        want = sha256_expected(sums.decode("utf-8", "replace") if sums else "", asset)
        blob = ofl_get(OFL_RELEASE_URL % (OFL_SERIES, asset)) if want else None
        if not want or not blob:
            unable["why"] = "could not fetch %s and its digest" % asset
            return unable
        digest = hashlib.sha256(blob).hexdigest()
        if digest != want:
            # Never executed, never cached, and said out loud: a digest that
            # does not match is the one case here that is not just a host
            # being old.
            unable["why"] = "%s did not match its published sha256" % asset
            return unable
        try:
            os.makedirs(cache)
        except OSError:
            pass
        if not ofl_unpack(blob, cache) or not os.path.exists(tree["binary"]):
            unable["why"] = "%s did not unpack as the release documents" % asset
            return unable
        os.chmod(tree["binary"], 0o755)
    got = sh_rc([tree["binary"], "--help"], timeout=20)[1]
    if not ofl_supports_flash_info(got):
        unable["why"] = "the fetched %s has no %s either" % (asset, OFL_FLASH_FLAG)
        return unable
    return {"argv": ofl_argv(tree), "source": asset, "version": version,
            # the digest this run verified, where this run is the one that
            # fetched it; a cache hit re-verifies nothing and says so
            "sha256": digest, "flash_info": True}


# openFPGALoader's --flash-info report: JEDEC id, the part, its density, and
# the flash's own unique id, all in one invocation. Fields are padded to
# column 18 as "Label<spaces>: value" under a header line that is exactly
# "SPI Flash information".
#
# Two things make the parse strict rather than generous, both of them
# openFPGALoader bugs found while this was being specified:
#   Before 5c83c71, --detect -f and --flash-info exited 0 even when the flash
#   was never read at all.
#   Before 7a11a6a, a NeTV2 with no spiOverJtag bridge loaded answered RDID
#   with garbage (0xc009a0) which was printed as an ordinary report.
# Either would have put a wrong flash identity on a printed sticker, so the
# report is believed only when the tool exited 0 *and* printed its header.
# The older unpadded "JEDEC ID:" and "Detected:" lines are never read: they
# come out before the id is validated, so garbage appears there too.
#
# This is human-oriented text and not a promised interface, so a wording
# change upstream will make this return nothing rather than something wrong.
FLASH_INFO_HEADER = "SPI Flash information"
FLASH_INFO_FIELD = re.compile(r"^(\S[^:]*?)\s*:\s*(.*\S)\s*$")
# "<hex> (opcode 0xNN, NNN bits)", or "blank (...)", or "not available (...)"
FLASH_UID_READ = re.compile(r"^([0-9a-fA-F]{8,})\s*\(opcode\s*(0x[0-9a-fA-F]+),\s*(\d+)\s*bits")
FLASH_UID_BLANK = re.compile(r"^blank\s*\(opcode\s*(0x[0-9a-fA-F]+),\s*(\d+)\s*bits")


def flash_info_parse(returncode, out):
    """What openFPGALoader's flash report said, or {} if it said nothing good."""
    if returncode != 0:
        return {}
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == FLASH_INFO_HEADER:
            break
    else:
        return {}
    fields = {}
    for line in lines[i + 1:]:
        m = FLASH_INFO_FIELD.match(line)
        if m:
            fields[m.group(1).strip()] = m.group(2)
    if "JEDEC ID" not in fields:
        return {}
    res = {"jedec": fields["JEDEC ID"].split()[0],
           "manufacturer": fields.get("Manufacturer"),
           "part": fields.get("Part"),
           "uid": None, "uid_bits": None, "uid_opcode": None, "uid_state": None}
    # a part openFPGALoader has never met says so in words, not a number
    if res["part"] and res["part"].startswith("unknown"):
        res["part"] = None
    uid = fields.get("Unique ID")
    if uid:
        m = FLASH_UID_READ.match(uid)
        if m:
            res.update(uid=m.group(1).lower(), uid_opcode=m.group(2),
                       uid_bits=int(m.group(3)), uid_state="read")
        elif FLASH_UID_BLANK.match(uid):
            # all 0x00 or all 0xFF: a read that happened and answered nothing,
            # which is a failure wearing a value's clothes
            m = FLASH_UID_BLANK.match(uid)
            res.update(uid_opcode=m.group(1), uid_bits=int(m.group(2)),
                       uid_state="blank")
        elif uid.startswith("not available"):
            # the part has no unique-id command; this is an answer, not a gap
            res["uid_state"] = "none"
    return res


# openFPGALoader can also write the same facts as a document, which is the
# better of the two paths and the one tried first. `--flash-info-json FILE`
# deletes FILE at startup and writes it, through a .tmp and a rename, only
# when every flash access succeeded -- so exit 0 plus the file existing means
# the flash was really read, with no stdout to scrape and no progress bars or
# status-register noise to step around. It also promises to bump `version`
# when a field changes meaning, which the printed report cannot.
FLASH_JSON_FORMAT = "openFPGALoader-flash-info"
# 1, and 2 (openFPGALoader 5d0ae2e), which changed what "sfdp": null means:
# in 2 it is only the part answering RSFDP with no SFDP signature, a failed
# read being an error that writes no document; in 1 it could also follow a
# failed read.
FLASH_JSON_VERSIONS = (1, 2)


def sfdp_answer(version, flash):
    """What a flash document says of the part's SFDP: a revision, "none", or
    None when it says nothing that can be relied on."""
    sfdp = flash.get("sfdp")
    if isinstance(sfdp, dict) and sfdp.get("revision"):
        return sfdp["revision"]
    if version >= 2 and "sfdp" in flash and sfdp is None:
        return "none"
    return None


def flash_info_from_json(doc):
    """The identity fields of a flash document, or {} if it is not one.

    A document whose version this code has not been taught is refused rather
    than read hopefully: the schema says a bump means a field changed
    meaning, so a hopeful read is how a wrong number reaches a sticker.
    """
    if not isinstance(doc, dict) or doc.get("format") != FLASH_JSON_FORMAT:
        return {}
    version = doc.get("version")
    if version not in FLASH_JSON_VERSIONS:
        return {}
    flashes = doc.get("flashes") or []
    if not flashes or not isinstance(flashes[0], dict):
        return {}
    f = flashes[0]
    if not f.get("jedec_id"):
        return {}
    uid = f.get("unique_id") or {}
    return {"jedec": f["jedec_id"],
            "manufacturer": f.get("manufacturer"),
            "part": f.get("part"),
            "size_bytes": f.get("size_bytes"),
            # `none` means this part has no known unique-id command, and since
            # openFPGALoader 9754753 only that: a transfer that failed now
            # exits non-zero and writes no file, where it used to be reported
            # as "not available" -- which is read here as a closed question
            # and would have stopped anyone looking for a number that was
            # really there.
            "uid": uid.get("value"),
            "uid_bits": uid.get("bits"),
            "uid_opcode": uid.get("opcode"),
            "uid_state": uid.get("state"),
            # why, where the part says so: a Macronix reports whether a
            # factory ESN was ever programmed, which turns "none" from an
            # assumption about the vendor into a measurement of the chip
            "uid_note": uid.get("note"),
            # RDID bytes 4-6, for the families that define them (Micron
            # N25Q/MT25Q, Spansion S-family); null for every other part and
            # absent from documents written before the field existed. They name
            # the part where the three-byte id cannot.
            "extended_id": f.get("extended_id"),
            # The SFDP revision the part answered RSFDP with; "none" where a
            # version-2 document says it answered without one, which is the
            # part's own answer; None where nothing can be said -- a version-1
            # null could also follow a failed read.
            "sfdp": sfdp_answer(version, f)}


# The package each die comes in, on the boards found on a GPIO harness. The
# spiOverJtag bridge is built per die *and package* -- it has to know which
# balls reach the flash -- and an idcode gives only the die: without a board
# profile or a part, openFPGALoader stops with "Can't program SPI flash:
# missing device-package information" (rpi5-netv2's NeTV2, 2026-09-22). Every
# board ever found on a GPIO harness here is a NeTV2 or an Acorn, and in
# litex-boards (58634aa, 2026-09-17) those settle the package by die alone:
# kosagi_netv2 "xc7a100t-fgg484-2", sqrl_acorn cle-101 "xc7a100t-fgg484-2",
# cle-215 and cle-215+ "xc7a200t-fbg484". The NeTV2's other variant is
# "xc7a35t-fgg484-2" (f593330, 2026-09-22), and no Acorn is a 35T, so that die
# settles it too: pi-sw1-p10..p18 and rpi3-netv2. A die not listed here gets
# no bridge: loading one for the wrong package drives the wrong pins.
GPIO_HARNESS_PART = {0x3631093: "xc7a100tfgg484", 0x3636093: "xc7a200tfbg484",
                     0x362D093: "xc7a35tfgg484"}


# The package a PCILeech gateware was built for, by the FPGA id it reports
# (register 0x0A), where its project builds for exactly one: LeechCore's
# device_fpga.c lists id 9 as "Enigma X1", and pcileech-fpga's EnigmaX1
# project (vivado_generate_project.tcl at 7938a89) is xc7a75tfgg484-2. Keyed
# with the die too, because a gateware only says what it was built for: the
# part is used only where the chain reads that same die. It picks the bridge
# and nothing else -- the flash's own bitstream header is what confirms it.
PCILEECH_GATEWARE_PART = {9: (0x3632093, "xc7a75tfgg484")}


def gateware_parts(pcileech):
    """{die: part} for the chain beside this PCILeech gateware, or {}."""
    entry = PCILEECH_GATEWARE_PART.get((pcileech or {}).get("fpga_id"))
    return {entry[0]: entry[1]} if entry else {}


def flash_info_probe(harness, board=None, part=None):
    """(openFPGALoader's flash report over `harness` or {}, and the tail of
    what it said when there is no report).

    Loads the spiOverJtag bridge, so it drops the running design exactly as
    the JEDEC read already does -- which is why it is only ever reached with
    --flash.
    """
    argv = list(harness)
    if board:
        argv += ["-b", board]
    if part:
        argv += ["--fpga-part", part]
    # The document first. Its existence after a zero exit is the whole test:
    # the tool removes it at startup and only renames it into place once every
    # flash access has succeeded, so there is nothing to interpret.
    workdir = tempfile.mkdtemp(prefix="rpi-hwid-flash-")
    path = os.path.join(workdir, "flash.json")
    try:
        rc, out = sh_rc(argv + ["--flash-info-json", path], timeout=180)
        if rc == 0 and os.path.exists(path):
            try:
                with open(path) as handle:
                    info = flash_info_from_json(json.load(handle))
            except (OSError, ValueError):
                info = {}
            if info:
                return info, None
        # No document: an older build with no such flag, or a read that did
        # not happen. The printed report is tried next, and it applies the
        # same "exited 0 and printed its header" test.
        rc2, out2 = sh_rc(argv + ["--flash-info"], timeout=180)
        info = flash_info_parse(rc2, out2)
        if info:
            return info, None
        # the first attempt's words: the second is only ever a fallback for a
        # build without the document, and says the same thing again if not
        return {}, (out or out2 or "").strip()[-200:] or None
    finally:
        for leftover in glob.glob(os.path.join(workdir, "*")):
            try:
                os.unlink(leftover)
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass


def digilent_cables():
    """The Digilent FT2232 cables on this host, an Arty's own JTAG."""
    return [f for f in ftdi_devices()
            if f["id"] == "0403:6010" and (f["manufacturer"] or "").startswith("Digilent")]


# A flash read replaces the running design, so a card whose design is a PCIe
# endpoint vanishes from under a live link -- which can upset the Pi 5's
# root port (openfpgaloader-36, 2026-09-22; the Acorn deployment does the
# same by hand). The endpoint is removed first and the bus rescanned once the
# FPGA has booted from flash again, which can take a few seconds to show
# DONE. Only FPGA endpoints: the Pi 5's own RP1 is one too, and removing it
# would take the header, Ethernet and USB with it.
PCIE_SLOT = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
PCIE_SETTLE_S = 5


def fpga_endpoints(pcie):
    """The PCIe slots an FPGA's design answers on."""
    return [pc["slot"] for pc in pcie or () if pc["id"].startswith(("10ee:", "1e24:"))]


def pcie_command(slot):
    """The slot's PCI command register, read from sysfs, or None."""
    try:
        with open(ROOT + "/sys/bus/pci/devices/" + slot + "/config", "rb") as f:
            f.seek(4)
            data = f.read(2)
    except OSError:
        return None
    return data[0] | data[1] << 8 if len(data) == 2 else None


def pcie_detach(slots, saved=None):
    """Remove each slot from the bus; returns the ones that were there.

    Each one's command register goes into `saved` first: a rescanned endpoint
    comes back with memory decoding off, and with no driver bound nothing
    turns it on again (pi-sw2-p48's Acorn, 2026-09-22, whose SoC then read
    all ones)."""
    removed = []
    for slot in slots:
        where = ROOT + "/sys/bus/pci/devices/" + slot
        if PCIE_SLOT.match(slot) and os.path.exists(where):
            command = pcie_command(slot)
            if saved is not None and command is not None:
                saved[slot] = command
            # a fixed path, the slot checked above: nothing reaches the shell
            # but "echo 1 >" and a sysfs file
            sh(["sudo", "sh", "-c", "echo 1 > " + where + "/remove"])
            removed.append(slot)
    return removed


def pcie_rescan(slots, tries=3, restore=None):
    """Rescan until every slot is back, then give each back the command
    register it was removed with; whether they all came back."""
    import time
    for _ in range(tries):
        time.sleep(PCIE_SETTLE_S)
        sh(["sudo", "sh", "-c", "echo 1 > " + ROOT + "/sys/bus/pci/rescan"])
        if all(os.path.exists(ROOT + "/sys/bus/pci/devices/" + s) for s in slots):
            for slot in slots:
                if (restore or {}).get(slot) is not None:
                    sh(["sudo", "setpci", "-s", slot, "COMMAND=%04x" % restore[slot]])
            return True
    return False


def read_flash(res, harness, board, part):
    """The flash's facts onto `res`, or what stopped the read."""
    info, said = flash_info_probe(harness, board, part)
    if info:
        res["flash_jedec"] = info["jedec"]
        res["flash"] = " ".join(x for x in (info.get("manufacturer"),
                                            info.get("part")) if x) or None
        res["flash_uid"] = info["uid"]
        res["flash_uid_bits"] = info["uid_bits"]
        res["flash_uid_state"] = info["uid_state"]
        # only the document carries a note or an extended id
        res["flash_uid_note"] = info.get("uid_note")
        res["flash_extended_id"] = info.get("extended_id")
        res["flash_sfdp"] = info.get("sfdp")
    else:
        argv = list(harness) + (["-b", board] if board else []) \
            + (["--fpga-part", part] if part else [])
        fl = sh(argv + ["--detect", "-f"], timeout=120)
        m = re.search(r"JEDEC ID: (0x[0-9a-f]+)", fl)
        res["flash_jedec"] = m.group(1) if m else None
        m = re.search(r"Detected: (.*)", fl)
        res["flash"] = m.group(1).strip() if m else None
        if not res["flash_jedec"]:
            res["flash_error"] = said or fl.strip()[-200:] or None


# --- an Acorn's flash, read over PCIe by the fpgas.online SoC ------------------
#
# The SoC the Acorns now carry can read its own configuration flash, so an
# Acorn's flash is asked there first: a JTAG read loads a bridge in place of
# the running design, which may be someone's session. The Acorn deployment's
# `fpgas-acorn-verify --identify` does the reading (fpgas-online-acorn-tools)
# and prints one JSON document whatever its exit status -- exit 1 includes a
# board still on SQRL's factory image -- so the document is what is read and
# the status is not. Anything it cannot read falls back to JTAG.
ACORN_VERIFY = "fpgas-acorn-verify"
ACORN_VERIFY_SCHEMA = 1


def acorn_flash_parse(out):
    """({slot: flash facts}, why the rest were not read) from what
    `fpgas-acorn-verify --identify` printed."""
    try:
        doc = json.loads(out)
    except ValueError:
        return {}, "%s printed no document: %s" % (ACORN_VERIFY, out.strip()[-200:])
    if not isinstance(doc, dict) or doc.get("schema_version") != ACORN_VERIFY_SCHEMA:
        # a schema this code was not taught is refused, not read hopefully
        return {}, "%s wrote schema_version %r" % (
            ACORN_VERIFY, doc.get("schema_version") if isinstance(doc, dict) else None)
    read, why = {}, []
    for b in doc.get("boards") or ():
        fl = b.get("flash") or {}
        if b.get("result") == "read" and fl.get("jedec") and fl.get("unique_id"):
            uid = fl["unique_id"].lower()
            read[b.get("bdf")] = {
                "flash_source": "pcie",
                "flash_jedec": "0x%06x" % int(fl["jedec"], 16),
                "flash": fl.get("part"),
                "flash_uid": uid,
                "flash_uid_bits": len(uid) * 4,
                "flash_uid_state": "read",
                "flash_uid_note": None,
                # the tool reports neither, so neither is claimed
                "flash_extended_id": None,
                "flash_sfdp": None}
        elif b.get("result") == "read":
            why.append("%s: read (no unique id)" % b.get("bdf"))
        else:
            why.append("%s: %s (%s)" % (b.get("bdf"), b.get("result"), b.get("reason")))
    if not doc.get("boards"):
        why.append("result %s: no board found" % doc.get("result"))
    return read, "; ".join(why) or None


def acorn_flash_probe():
    """What the SoC read of each Acorn's flash, and why any were not."""
    if not sh(["which", ACORN_VERIFY]):
        return {}, ACORN_VERIFY + " not installed"
    out, err = sh_split(["sudo", ACORN_VERIFY, "--identify"], timeout=120)
    read, why = acorn_flash_parse(out)
    if why and err.strip():
        why += "; stderr: " + err.strip()[-200:]
    return read, why


def jtag_probe(want_flash=False, pins=None, parts=None, detach=None):
    """openFPGALoader over whichever cable this host has, else openocd.
    Returns the idcode line when a chain answers. `parts` is {die: part} from
    what the card's own gateware said, for a cable with no harness to go by;
    `detach` the PCIe slots to take off the bus while the flash is read."""
    cables = digilent_cables()
    # Which openFPGALoader, and whether it can read a flash at all. The
    # static build is only worth fetching when the flash is actually wanted:
    # every host's own copy can read a chain, and the tarball is 8 MB of
    # bridge bitstreams that a --detect has no use for.
    tool = openfpgaloader_tool(download=want_flash)
    if not sh(["which", "openFPGALoader"]) and tool["source"] == "host":
        # openocd drives the Digilent FT2232 as well as the GPIO harness, so
        # the fallback covers an Arty too, not just a NeTV2.
        ocd = openocd_probe(cables[0]["serial"] if cables else None, pins)
        if ocd is not None:
            return ocd
        return {"error": "openFPGALoader not installed"}
    digilent = bool(cables)
    # A CH347 on the host's USB is the card's own JTAG cable, as a Digilent
    # FT2232 is an Arty's: no GPIO harness is involved at all.
    ch347 = not digilent and bool(ch347_cables())
    cable = "digilent" if digilent else "ch347" if ch347 else "gpio"
    if digilent:
        harness = tool["argv"] + ["-c", "digilent"]
    elif ch347:
        harness = tool["argv"] + ["-c", "ch347_jtag"]
    else:
        harness = tool["argv"] + ["-c", "libgpiod",
                                  "--pins=" + (pins or HARNESS_PINS)]
        # Which chip, by driver label, the same way openocd is told. Its
        # default is gpiochip0, which on a Pi 5 is not the header -- the RP1
        # registers as gpiochip15 -- and on pi-sw2-p48 there is no gpiochip0
        # at all, so the default reaches nothing.
        chip = header_gpiochip(gpiochips())
        if chip is not None:
            harness += ["-d", "/dev/gpiochip%d" % chip]
    # stderr as well: that is where openFPGALoader says why it failed, and
    # without it a build missing its GPIO backend recorded raw "" and the
    # board simply vanished from the verdict.
    det = sh_all(harness + ["--detect"], timeout=60)
    m = re.search(r"idcode\s+(0x[0-9a-f]+)", det)
    if not m:
        if ch347:
            # openocd is only ever set up here for the Digilent cable and the
            # GPIO harness, and the harness is not what this card is wired to
            return {"idcode": None, "cable": cable, "raw": det[-200:]}
        # Installed is not the same as able. Try openocd before giving up, and
        # keep both tools' last words, so a chain neither can read says why.
        ocd = openocd_probe(cables[0]["serial"] if cables else None, pins)
        if ocd is not None and ocd.get("idcode"):
            # openocd reads a chain but no flash, so a reading that comes from
            # it has no flash in it. Why openFPGALoader could not read the
            # chain is the whole explanation, and it belongs in the document:
            # rpi3-netv2 (kernel 4.14) answers the static build with a libgpiod
            # assertion, and without this the flash was simply absent.
            if want_flash:
                ocd["flash_jedec"] = ocd["flash"] = None
                ocd["flash_error"] = det.strip()[-200:] or None
            return ocd
        res = {"idcode": None, "raw": det[-200:]}
        if ocd is not None:
            res["openocd"] = ocd.get("raw")
        return res
    res = {"idcode": m.group(1)}
    fam = re.search(r"family\s+(.*?)\s*$", det, re.M)
    if fam:
        res["family"] = fam.group(1)
    dna = sh(harness + ["--read-dna"], timeout=60)
    m = re.search(r'"dna":\s*"(0x[0-9a-f]+)"', dna)
    res["dna"] = m.group(1) if m else None
    res["cable"] = cable
    # Which build read this, so a value can be traced to the thing that read
    # it -- and so a host whose copy cannot read a flash says why rather than
    # simply having no flash in its document.
    res["openfpgaloader"] = tool
    if cable == "gpio":
        res["pins"] = pins or HARNESS_PINS
    if want_flash:
        res["flash_source"] = "jtag"
        # The bridge replaces the running design either way, which is what
        # --flash pays for. The board profile is only needed on a Digilent
        # cable; on the GPIO harness the part comes from the chain. Matched
        # by number with the revision nibble masked, as labels.idcode_part
        # does: a string list only ever matched the revisions written into it.
        board = part = None
        die = int(res["idcode"], 16) & 0x0FFFFFFF
        if digilent:
            board = "arty_a7_100t" if die == 0x3631093 else "arty_a7_35t"
        else:
            # A CH347's card is known only by its die and what its gateware
            # was built for, so it gets a part only where the two agree
            part = (GPIO_HARNESS_PART if cable == "gpio" else parts or {}).get(die)
            if part is None:
                res["flash_jedec"] = res["flash"] = None
                res["flash_error"] = ("no package is known for idcode %s on a %s "
                                      "cable, so no bridge was loaded"
                                      % (res["idcode"], cable))
                return res
        # --flash-info first: it reports the part, the density and the flash's
        # own unique id in one go, and it exits non-zero when the read did not
        # actually happen. Older builds have no such flag, so the JEDEC-only
        # read stays as the fallback rather than the flash going unread.
        saved = {}
        detached = pcie_detach(detach or (), saved)
        try:
            read_flash(res, harness, board, part)
        finally:
            if detached:
                res["pcie_detached"] = detached
                res["pcie_back"] = pcie_rescan(detached, restore=saved)
    return res


# --- pcileech-fpga gateware, read over its FT601 --------------------------------
#
# The gateware keeps a read-only register block, readable through the FT601
# it streams over (ufrisk/pcileech-fpga pcileech_fifo.sv, core ro space):
#   +000 magic 0xab89   +008 version major   +009 minor   +00a FPGA id
#   +010 uptime, 64-bit ticks at 100 MHz     +022 bit0 PCIe PRSNT#, bit1 PERST#
# The "FPGA id" is a performance-profile class, not a board: upstream, class 9
# is set by the Enigma X1 and two CaptainDMA boards alike. It is recorded as a
# number and never turned into a board name. There is no Device DNA.
#
# Only reads. LeechCore's own open sequence is not side-effect free -- it can
# reset the FPGA, hot-reset its PCIe link, write an inactivity timer, and
# rewrite the FT601's configuration -- so none of it is copied: the requests
# below are register reads and nothing else, and no vendor control transfer
# (0xCF) is made at all. Protocol as LeechCore's device_fpga_session.c and
# the libusb FT601 driver in LeechCore-plugins implement it, checked against
# pi-sw1-p38 on welland.fpgas.online 2026-09-16.
PCILEECH_MAGIC = 0xAB89
PCILEECH_ADDRS = (0x0000, 0x0008, 0x000A, 0x0010, 0x0012, 0x0014, 0x0016, 0x0022)
PCILEECH_FILLER = 0x55556666        # an idle FIFO word, read little-endian


# An Acorn names itself in its PCIe subsystem id, which is where a board
# under a generic controller belongs. Measured on pi-sw2-p48 2026-09-21.
# An image that states no model keeps the Xilinx default 10ee:0007 and is
# deliberately absent here: it must stay unknown rather than become a
# different board when the flash image comes back after a power cycle.
ACORN_SUBSYSTEM = {"1e24:021f": "cle-215+", "1e24:0101": "cle-101"}


def is_pcileech_pcie(pc):
    """PCIe 10ee:0666 with one 4 KiB BAR: the gateware's own default id."""
    return pc["id"] == "10ee:0666" and sorted(pc["bars"], reverse=True) == [4 << 10]


def pcileech_request(addrs=PCILEECH_ADDRS):
    """The bytes that ask for each 16-bit register in `addrs`.

    A resync filler first, then eight bytes per read: four zero, the address
    big-endian, 0x13 (0x10 read | 0x03 core space; read-only space, so bit 15
    of the address is clear), and the 0x77 command magic."""
    out = bytearray(b"\x66\x66\x55\x55" * 4)
    for a in addrs:
        out += bytearray([0, 0, 0, 0, (a >> 8) & 0xFF, a & 0xFF, 0x13, 0x77])
    return bytes(out)


def pcileech_parse(reply):
    """{address: byte} from the FIFO bytes, as DeviceFPGA_Session_ParseConfigReply.

    32-byte records of little-endian words: a status word whose top nibble is
    0xE and whose seven low nibbles each give the source of one following data
    word (3 is the core register space). A core data word carries the address
    byte-swapped in its low 16 bits and two register bytes above them. Filler
    words between records are skipped; records from any other source (PCIe
    data a session would have been reading) are ignored."""
    staged = {}
    i = 0
    while i + 32 <= len(reply):
        while i + 4 <= len(reply) and struct.unpack_from("<I", reply, i)[0] == PCILEECH_FILLER:
            i += 4
        if i + 32 > len(reply):
            break
        status = struct.unpack_from("<I", reply, i)[0]
        if status & 0xF0000000 == 0xE0000000:
            for j in range(7):
                source = status & 0x0F
                status >>= 4
                if source != 3:
                    continue
                data = struct.unpack_from("<I", reply, i + 4 + 4 * j)[0]
                addr = ((data & 0xFF) << 8) | ((data >> 8) & 0xFF)
                staged[addr] = (data >> 16) & 0xFF
                staged[addr + 1] = (data >> 24) & 0xFF
        i += 32
    return staged


def pcileech_identity(staged):
    """What the registers say, or None unless the magic proves it is the gateware."""
    if staged.get(0) is None or (staged[0] | staged.get(1, 0) << 8) != PCILEECH_MAGIC:
        return None
    res = {"version": "%d.%d" % (staged.get(8, 0), staged.get(9, 0)),
           "fpga_id": staged.get(0x0A)}
    if all(a in staged for a in range(0x10, 0x18)):
        ticks = sum(staged[0x10 + k] << (8 * k) for k in range(8))
        res["uptime_s"] = ticks // 100000000
    if 0x22 in staged:
        res["pcie_present"] = bool(staged[0x22] & 1)
    return res


# Run as root (the FT601's usbfs node is root's) by the same python, and kept
# to moving bytes: every decision about what they mean is made unprivileged,
# above, where it can be tested. usbdevfs directly rather than libusb, which
# a Pi does not have. USBDEVFS_BULK's number is derived from the struct's size
# because it differs with the userland: pi-sw1-p38 runs a 32-bit userland on a
# 64-bit kernel, where it is 0xC0105502, not the 0xC0185502 of a 64-bit one.
PCILEECH_READER = r'''
import ctypes, fcntl, glob, os, struct, sys, time
class Bulk(ctypes.Structure):
    _fields_ = [("ep", ctypes.c_uint), ("len", ctypes.c_uint),
                ("timeout", ctypes.c_uint), ("data", ctypes.c_void_p)]
USBDEVFS_BULK = 0xC0000000 | (ctypes.sizeof(Bulk) << 16) | (ord("U") << 8) | 2
CLAIM, RELEASE = 0x8004550F, 0x80045510
def bulk(fd, ep, payload=None, size=0, timeout=1000):
    buf = ctypes.create_string_buffer(payload, len(payload)) if payload is not None \
        else ctypes.create_string_buffer(size)
    n = len(payload) if payload is not None else size
    got = fcntl.ioctl(fd, USBDEVFS_BULK, Bulk(ep, n, timeout, ctypes.addressof(buf)))
    return buf.raw[:got]
def read_pipe(fd, size=0x10000, timeout=250):
    # a session request on 0x01 precedes every bulk read of 0x82 (ftdi_SendCmdRead)
    bulk(fd, 0x01, struct.pack("<IBBBBIII", 1, 0x82, 1, 0, 0, size, 0, 0))
    try:
        return bulk(fd, 0x82, size=size, timeout=timeout)
    except OSError:
        return b""
def rd(d, name):
    return open(d + "/" + name).read().strip()
dev = None
for d in glob.glob("/sys/bus/usb/devices/*"):
    try:
        if rd(d, "idVendor") == "0403" and rd(d, "idProduct") == "601f":
            dev = "/dev/bus/usb/%03d/%03d" % (int(rd(d, "busnum")), int(rd(d, "devnum")))
    except OSError:
        pass
if dev is None:
    print("ERROR=no FT601 found"); sys.exit(0)
fd = os.open(dev, os.O_RDWR)
claimed = []
try:
    for i in (0, 1):
        try:
            fcntl.ioctl(fd, CLAIM, struct.pack("I", i))
        except OSError as e:
            print("ERROR=interface %d in use: %s" % (i, e)); sys.exit(0)
        claimed.append(i)
    sum(len(read_pipe(fd)) for _ in range(4))           # drain any stale reply
    ADDRS = [0x0000, 0x0008, 0x000A, 0x0010, 0x0012, 0x0014, 0x0016, 0x0022]
    cmd = bytes([0x66, 0x66, 0x55, 0x55]) * 4           # resync filler, then the reads
    for a in ADDRS:      # core space (..03), read-only (no bit-15 address, no 0x8000)
        cmd += bytes([0, 0, 0, 0, a >> 8, a & 0xFF, 0x13, 0x77])
    bulk(fd, 0x02, cmd)
    # Read, parse, and stop the instant every register is in hand. Each
    # read_pipe sends a fresh session request; sending more of them than there
    # are replies to collect desyncs the FT601 and it then answers config reads
    # with zeroes. Waiting for all of ADDRS (not just the magic) is what makes
    # the reply arrive whole -- an earlier break was the bug that made this
    # return nothing.
    staged, reply = {}, b""
    for _ in range(10):
        time.sleep(0.01)
        reply += read_pipe(fd)
        i = 0
        while i + 32 <= len(reply):          # DeviceFPGA_Session_ParseConfigReply
            while i + 4 <= len(reply) and struct.unpack_from("<I", reply, i)[0] == 0x55556666:
                i += 4
            if i + 32 > len(reply):
                break
            status = struct.unpack_from("<I", reply, i)[0]
            if status & 0xF0000000 == 0xE0000000:
                for j in range(7):
                    src = status & 0x0F
                    data = struct.unpack_from("<I", reply, i + 4 + 4 * j)[0]
                    status >>= 4
                    if src != 3:
                        continue
                    addr = ((data & 0xFF) << 8) | ((data >> 8) & 0xFF)
                    staged[addr] = (data >> 16) & 0xFF
                    staged[addr + 1] = (data >> 24) & 0xFF
            i += 32
        if all(a in staged for a in ADDRS):
            break
    print("STAGED=" + ",".join("%d:%d" % kv for kv in sorted(staged.items())))
finally:
    for i in claimed:
        fcntl.ioctl(fd, RELEASE, struct.pack("I", i))
    os.close(fd)
'''


# Apollo's JTAG is a pure vendor-control-request protocol -- no bulk endpoints
# -- so it needs nothing but usbdevfs, which is why this can run on a Pi with
# no libusb and no apollo installed. Requests from apollo_fpga/jtag.py and
# apollo_fpga/__init__.py; the TAP state numbers are JTAGChain.STATE_NUMBERS.
#
# The restore at the end is the part apollo itself does not do: `apollo info
# --force-offline` reads and leaves the FPGA held offline. Ours reconfigures
# from flash, hands the shared port back, and then waits to see the analyzer
# re-enumerate, so the caller learns whether the rig came back. With
# "restore-only" it does the handoff and the restore and no JTAG at all,
# which is how the dangerous half gets proven before a read is attempted.
# The reader's helpers apart from its script, so a test can run them against a
# recording firmware and hold them to the bytes apollo itself sends.
APOLLO_HELPERS = r'''
import ctypes, fcntl, glob, os, sys, time
class Ctrl(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]
# _IOWR('U', 0, struct usbdevfs_ctrltransfer); the size differs with the
# userland, so it is derived rather than written down.
USBDEVFS_CONTROL = 0xC0000000 | (ctypes.sizeof(Ctrl) << 16) | (ord("U") << 8) | 0
CLAIM, RELEASE = 0x8004550F, 0x80045510
OUT_DEV, IN_DEV, OUT_IFACE = 0x40, 0xC0, 0x41
GET_INFO, START, STOP = 0xb8, 0xbf, 0xbe
CLEAR_OUT, SET_OUT, GET_IN, SCAN, GO_TO, RUN_CLOCK = 0xb0, 0xb1, 0xb2, 0xb3, 0xb5, 0xb4
FORCE_OFFLINE, RECONFIGURE, ALLOW_TAKEOVER = 0xc1, 0xc0, 0xc2
RESET, IDLE, DRSHIFT, IRSHIFT, IRPAUSE, DRPAUSE = 0, 1, 4, 11, 13, 6
UIDCODE_PUB = 0x19
# The ECP5 will hand its configuration SPI lines to JTAG: this instruction,
# then a two-byte unlock into the DR, after which every DR shift is an SPI
# transaction with the configuration flash. It is how `apollo flash-info`
# reads the flash, and the only way to reach a chip whose pins belong to the
# FPGA's configuration bank.
ENTER_BACKGROUND_SPI = 0x3A
SPI_UNLOCK = (0x68, 0xFE)
READ_JEDEC_ID, READ_UID = 0x9F, 0x4B
# 0xFF x8 clears any half-issued command, then 0x66 0x99 is the flash's own
# reset-enable/reset pair; apollo sends the same three before it trusts a
# reply. Sizes: the id is three bytes after one turnaround byte, and 0x4B is
# four dummy bytes then eight of unique id.
SPI_WAKE = ((0xFF,) * 8, (0x66,), (0x99,))

def rd(d, name):
    try:
        f = open(d + "/" + name)
    except IOError:
        return None
    try:
        return f.read().strip()
    finally:
        f.close()

def find(pid):
    for d in sorted(glob.glob("/sys/bus/usb/devices/*")):
        if rd(d, "idVendor") == "1d50" and rd(d, "idProduct") == pid:
            try:
                node = "/dev/bus/usb/%03d/%03d" % (int(rd(d, "busnum")), int(rd(d, "devnum")))
            except (TypeError, ValueError):
                continue
            return d, node
    return None, None

def wait_for(pid, seconds=6.0):
    end = time.time() + seconds
    while time.time() < end:
        d, node = find(pid)
        if d:
            return d, node
        time.sleep(0.1)
    return None, None

def ctrl(fd, rtype, req, value=0, index=0, data=None, length=0, timeout=2000):
    buf = None
    if data is not None:
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        length = len(data)
    elif length:
        buf = ctypes.create_string_buffer(length)
    t = Ctrl(rtype, req, value, index, length, timeout,
             ctypes.addressof(buf) if buf is not None else None)
    n = fcntl.ioctl(fd, USBDEVFS_CONTROL, t)
    return buf.raw[:n] if buf is not None else b""

def stub_iface(sysdir):
    """The Apollo stub's interface number: class ff, subclass 00."""
    for i in sorted(glob.glob(sysdir + "/*:*")):
        if rd(i, "bInterfaceSubClass") == "00" and rd(i, "bInterfaceClass") == "ff":
            try:
                return int(rd(i, "bInterfaceNumber"), 16)
            except (TypeError, ValueError):
                pass
    return None

def scan(fd, bits, out=None):
    """One JTAG scan: optional TDI bytes, then `bits` clocks, then TDO.

    `advance` is apollo's advance_state flag, which its _scan_data sets on
    the last chunk of a write ("advance_state = not bool(bits_to_scan)") and
    its _receive_data never sets at all. It is TMS on the final clock, so a
    write without it never leaves the shift state and the instruction is
    never latched: measured on rpi5-netv2, UIDCODE_PUB shifted without it
    returned 64 zero bits, and with it returned the TraceID.
    """
    advance = 0
    if out is None:
        ctrl(fd, OUT_DEV, CLEAR_OUT)
    else:
        ctrl(fd, OUT_DEV, SET_OUT, data=out)
        advance = 1
    ctrl(fd, OUT_DEV, SCAN, value=bits, index=advance)
    return ctrl(fd, IN_DEV, GET_IN, length=(bits + 7) // 8)


def rev(b):
    return int("{:08b}".format(b)[::-1], 2)


def spi(fd, data, flip):
    """One SPI transaction with the configuration flash, over background SPI.

    The firmware shifts each byte LSB first and SPI wants MSB first, so every
    byte is bit-reversed on the way out and on the way back, and none of them
    moves: reply[i] is what the flash sent while it received data[i]. That
    is what apollo 1.1.1 hands its firmware, recorded rather than reasoned
    out -- its _background_spi_transfer reverses the byte order and its
    _scan_data reverses it again. The first version of this reversed it once,
    sent 9F 00 00 00 as 00 00 00 f9, and read back its own commands. Firmware
    that flips whole bytes itself gets them raw, as apollo's chain undoes the
    reversal in that case.
    """
    def turn(seq):
        return [b if flip else rev(b) for b in seq]
    ctrl(fd, OUT_DEV, GO_TO, value=DRSHIFT)
    got = scan(fd, len(data) * 8, bytearray(turn(data)))
    return bytearray(turn(bytearray(got)))


def enter_background_spi(fd, flip):
    """Hand the configuration flash's pins to JTAG: the instruction, then the
    unlock into the DR, then the flash's own reset.

    The unlock is a JTAG value and not an SPI byte, so it is not reversed:
    fe 68 on the wire, as apollo sends b"\x68\xFE" and openFPGALoader
    {0xFE, 0x68}. The first version reversed it like SPI payload, and the
    part never entered background SPI at all.
    """
    ctrl(fd, OUT_DEV, GO_TO, value=IRSHIFT)
    scan(fd, 8, bytearray([rev(ENTER_BACKGROUND_SPI) if flip else ENTER_BACKGROUND_SPI]))
    ctrl(fd, OUT_DEV, GO_TO, value=IRPAUSE)
    ctrl(fd, OUT_DEV, GO_TO, value=DRSHIFT)
    unlock = list(SPI_UNLOCK)[::-1]
    scan(fd, 16, bytearray([rev(b) for b in unlock] if flip else unlock))
    ctrl(fd, OUT_DEV, GO_TO, value=IDLE)
    ctrl(fd, OUT_DEV, RUN_CLOCK, value=1)
    for wake in SPI_WAKE:
        spi(fd, wake, flip)
'''

APOLLO_READER = APOLLO_HELPERS + r'''
restore_only = "restore-only" in sys.argv
if "recover" in sys.argv:
    # A board left in Apollo mode -- because a restore failed, or because
    # something else put it there -- told to reconfigure from flash and give
    # the shared port back. No handoff, no JTAG: the way home and nothing else.
    sysdir, node = find("615c")
    if sysdir is None:
        print("ERROR=no Apollo (1d50:615c) to recover"); sys.exit(0)
    fd = os.open(node, os.O_RDWR)
    try:
        ctrl(fd, OUT_DEV, RECONFIGURE)
        ctrl(fd, OUT_DEV, ALLOW_TAKEOVER)
    finally:
        os.close(fd)
    sysdir, _node = wait_for("615b", 15.0)
    print("RESTORED=" + ((rd(sysdir, "serial") or "yes") if sysdir else "none"))
    sys.exit(0)

sysdir, node = find("615b")
if sysdir is None:
    print("ERROR=no Cynthion gateware (1d50:615b) on this host"); sys.exit(0)
iface = stub_iface(sysdir)
if iface is None:
    print("ERROR=no Apollo stub interface: this gateware will not hand the port over")
    sys.exit(0)
# The serial the gateware is publishing right now is the configuration
# flash's uid, and it is what says which board this reading belongs to once
# the handoff has changed what is on the bus.
before = rd(sysdir, "serial")
if before:
    print("FLASHUID=" + before)
fd = os.open(node, os.O_RDWR)
try:
    try:
        fcntl.ioctl(fd, CLAIM, ctypes.c_uint(iface))
    except IOError:
        pass                      # no driver is bound to the stub; claiming is best effort
    # REQUEST_APOLLO_ADV_STOP: the gateware stands down and the port is Apollo's
    ctrl(fd, OUT_IFACE, 0xF0, index=iface, timeout=5000)
finally:
    os.close(fd)

sysdir, node = wait_for("615c")
if sysdir is None:
    print("ERROR=handoff sent but Apollo (1d50:615c) never appeared"); sys.exit(0)
fd = os.open(node, os.O_RDWR)
err = None
try:
  if not restore_only:
    try:
        ctrl(fd, OUT_DEV, FORCE_OFFLINE)
        # GET_INFO is optional firmware: apollo's own JTAGChain.__enter__
        # wraps this very call in `except IOError: pass`, and this board
        # stalls it. A stall here means "no quirks reported", not a failure.
        quirks = 0
        try:
            info = ctrl(fd, IN_DEV, GET_INFO, length=8)
            if len(info) == 8:
                quirks = info[4] | (info[5] << 8) | (info[6] << 16) | (info[7] << 24)
        except (IOError, OSError):
            pass
        flip = bool(quirks & 1)          # QUIRK_FLIP_BITS_IN_WHOLE_BYTES
        ctrl(fd, OUT_DEV, START)
        ctrl(fd, OUT_DEV, GO_TO, value=RESET)
        # IR <- UIDCODE_PUB. Resting in IRPAUSE is what apollo's own ECP5 code
        # does, and leaving it toward DRSHIFT is what passes through IRUPDATE
        # and latches the instruction.
        ctrl(fd, OUT_DEV, GO_TO, value=IRSHIFT)
        scan(fd, 8, bytearray([rev(UIDCODE_PUB) if flip else UIDCODE_PUB]))
        ctrl(fd, OUT_DEV, GO_TO, value=IRPAUSE)
        ctrl(fd, OUT_DEV, GO_TO, value=DRSHIFT)
        got = bytearray(scan(fd, 64))
        if flip:
            got = bytearray(rev(b) for b in got)
        ctrl(fd, OUT_DEV, GO_TO, value=DRPAUSE)
        # The bytes exactly as the chain clocked them out. What they mean is
        # decided above, unprivileged, where a test can hold the reader to
        # the numbers a real board returned.
        print("TRACEIDRAW=" + "".join("%02x" % b for b in got))
        # The configuration flash, in the same offline window: the die's
        # number and the flash's are both wanted and the window is the
        # expensive part, so one visit takes both. Background SPI is entered
        # from RESET, because the TAP has just been left in DRPAUSE.
        ctrl(fd, OUT_DEV, GO_TO, value=RESET)
        enter_background_spi(fd, flip)
        time.sleep(0.1)
        # 0x9F: one turnaround byte, then manufacturer, type, capacity.
        print("FLASHIDRAW=" + "".join(
            "%02x" % b for b in spi(fd, (READ_JEDEC_ID, 0, 0, 0), flip)))
        # 0x4B: four dummy bytes, then eight of unique id. Read as well as
        # the id because the gateware already published it as the USB serial,
        # so the two together say whether this transport is being read right
        # at all -- a framing error cannot agree with a number taken off the
        # bus by an entirely different path.
        print("FLASHUIDRAW=" + "".join(
            "%02x" % b for b in spi(fd, (READ_UID,) + (0,) * 12, flip)))
        ctrl(fd, OUT_DEV, GO_TO, value=RESET)
        ctrl(fd, OUT_DEV, STOP)
    except (IOError, OSError) as e:
        # A failed read must not cost the reporting of whether the rig came
        # back: that is the line the caller most needs.
        err = "jtag read failed: %s" % e
finally:
    # Always, even if the read above threw: a board left unconfigured is a
    # dead rig, and that matters more than any number.
    try:
        ctrl(fd, OUT_DEV, RECONFIGURE)
        ctrl(fd, OUT_DEV, ALLOW_TAKEOVER)
    except (IOError, OSError) as e:
        err = err or ("restore failed: %s" % e)
    os.close(fd)

sysdir, _node = wait_for("615b", 15.0)
after = rd(sysdir, "serial") if sysdir else None
print("RESTORED=" + (after if after else "none"))
if err:
    print("ERROR=" + err)
if before and after and before != after:
    print("NOTE=came back with a different serial: %s then %s" % (before, after))
'''


def cynthion_offline_probe(restore_only=False, recover=False):
    """The ECP5 TraceID over Apollo, putting the analyzer back afterwards.

    Ends the board's capture for the duration and may drop power to whatever
    is on its TARGET port, which is why nothing calls this without being asked
    to. `restore_only` does the handoff and the restore and no JTAG, to prove
    the board comes back before a read is ever attempted on it.
    """
    argv = ["sudo", sys.executable or "python3", "-c", APOLLO_READER]
    if recover:
        argv.append("recover")
    elif restore_only:
        argv.append("restore-only")
    return cynthion_offline_parse(sh_all(argv, timeout=120))


def pcileech_probe():
    """The gateware's identity over its FT601, or {"error": why}.

    The reader parses the FT601 stream on the host (it must break out of the
    read loop the instant the reply is in hand; see the comment there) and
    hands back the staged register bytes as "STAGED=addr:val,...". The meaning
    of those bytes is worked out here, where it can be tested.
    """
    out = sh_all(["sudo", sys.executable or "python3", "-c", PCILEECH_READER], timeout=30)
    m = re.search(r"STAGED=([0-9:,]*)", out)
    if not m:
        err = re.search(r"ERROR=(.*)", out)
        return {"error": err.group(1) if err else out[-200:]}
    staged = {}
    for pair in m.group(1).split(","):
        if ":" in pair:
            addr, val = pair.split(":")
            staged[int(addr)] = int(val)
    ident = pcileech_identity(staged)
    return ident if ident else {"error": "no 0xab89 magic in the reply: not pcileech gateware"}


def fpga_verdict(d):
    """Name the FPGA board(s) this Pi hosts, from PCIe, USB and JTAG."""
    boards = []
    for pc in d["pcie"]:
        sizes = sorted(pc["bars"], reverse=True)
        if pc["id"] == "10ee:7024" and sizes == [1 << 20]:
            boards.append({"kind": "netv2", "slot": pc["slot"],
                           "how": "PCIe 10ee:7024, one 1 MiB BAR (LitePCIe NeTV2 gateware)"})
        elif pc["id"] in ("1e24:021f", "10ee:7011") and sizes == [128 << 10, 64 << 10]:
            # 10ee:7011 is not "the Acorn with a default Xilinx id": it is RHS
            # Research's XDMA sample image, which says what is loaded and not
            # what it is loaded on (measured by the Acorn deployment, ps1
            # 2026-09-20). Kept because on this fleet it has only ever been
            # seen on an Acorn, but named for what it is.
            boards.append({"kind": "acorn", "slot": pc["slot"],
                           "how": "PCIe %s, 128 KiB + 64 KiB BARs (%s)" % (
                               pc["id"], "SQRL Acorn CLE-215+" if pc["id"] == "1e24:021f"
                               else "RHS Research XDMA sample image")})
        elif pc["subsystem"] in ACORN_SUBSYSTEM:
            # vendor:device describes the gateware; the subsystem id exists to
            # name the board under it, and 1e24 is Squirrels Research Labs'
            # own. This is what still identifies an Acorn once its factory
            # image is gone, with no harness, no BAR and no guess. It remains
            # a claim made by gateware, like every PCI id here -- but the
            # image that makes it is refused by its own flash tool when the
            # IDCODE does not match the part.
            model = ACORN_SUBSYSTEM[pc["subsystem"]]
            boards.append({"kind": "acorn", "slot": pc["slot"], "soc_model": model,
                           "how": "PCIe %s, subsystem %s (SQRL Acorn %s)" % (
                               pc["id"], pc["subsystem"], model.upper())})
        elif pc["id"] == "1e24:0101":
            # SQRL's own vendor id, so this one does identify the card: the
            # CLE-101, sold as the LiteFury. pi14 and pi16 answer with it.
            boards.append({"kind": "acorn", "slot": pc["slot"],
                           "how": "PCIe 1e24:0101 (SQRL Acorn CLE-101 / LiteFury)"})
        elif is_pcileech_pcie(pc):
            # Checked before the Xilinx catch-all below, which is what named
            # it "unknown-fpga". The FT601 is reported when present but not
            # required: the PCIe id is the gateware's own, while an FT601 is
            # on plenty of things that are not this.
            ft601 = [f for f in d["ftdi"] if f["id"] == "0403:601f"]
            board = {"kind": "pcileech", "slot": pc["slot"],
                     "how": "PCIe 10ee:0666, one 4 KiB BAR (pcileech-fpga gateware)%s" % (
                         "; FT601 USB3 bridge at %s" % ft601[0]["path"] if ft601 else "")}
            ident = d.get("pcileech") or {}
            if ident.get("version"):
                board.update(gateware=ident["version"], gateware_id=ident.get("fpga_id"))
                board["how"] += "; gateware v%s, FPGA id %s" % (
                    ident["version"], ident.get("fpga_id"))
            boards.append(board)
        elif pc["id"].startswith("10ee:") or pc["id"].startswith("1e24:"):
            boards.append({"kind": "unknown-fpga", "how": "PCIe %s, BARs %s" % (pc["id"], sizes),
                           "slot": pc["slot"]})
    # A TraceID belongs to the board whose flash uid the offline read came
    # back with, not to whichever Cynthion happens to be listed first.
    read = d.get("cynthion_jtag") or {}
    for c in d.get("cynthion") or ():
        mode = cynthion_mode(c)
        uid = cynthion_flash_uid(c)
        rev = cynthion_revision(c.get("bcd_device"))
        mine = bool(uid) and read.get("flash_uid") == uid
        trace = read.get("trace_id") if mine else None
        # The flash's own answers, from the same offline window -- but only
        # when that read found the unique id the gateware publishes. That
        # match is the one evidence the transport works; without it the JEDEC
        # id that came over the same transport is no better than the uid.
        spi = mine and read.get("flash_uid_agree") is True
        boards.append({
            "kind": "cynthion", "path": c["path"], "serial": uid,
            "hw_rev": rev, "mode": mode, "trace_id": trace,
            "flash_jedec": read.get("flash_jedec") if spi else None,
            "flash_uid": uid,
            "flash_uid_bits": read.get("flash_uid_bits") if spi else None,
            "flash_uid_state": read.get("flash_uid_state") if spi else None,
            "how": "USB %s%s%s%s" % (
                c["id"],
                (", Cynthion r%s" % rev) if rev else "",
                (", %s gateware" % mode) if mode else "",
                # said plainly: an Apollo-mode board has a serial, it is just
                # not the flash's, and the label must not imply otherwise
                "" if uid else "; no flash uid published in this mode")})
    for f in d["ftdi"]:
        if f["id"] == "0403:6010" and (f["manufacturer"] or "").startswith("Digilent"):
            boards.append({"kind": "arty", "how": "Digilent FT2232 %s" % f["serial"],
                           "serial": f["serial"]})
    j = d.get("jtag")
    if j and j.get("idcode"):
        # documents from before the cable was recorded were all GPIO harnesses
        cable = j.get("cable") or "gpio"
        entry = {"kind": "jtag", "how": "%s JTAG idcode %s%s" % (
            {"digilent": "FT2232", "ch347": "CH347"}.get(cable, "GPIO"),
            j["idcode"], (" " + j["family"]) if j.get("family") else ""), "idcode": j["idcode"],
            "dna": j.get("dna")}
        # A chain on the GPIO harness is how a NeTV2 is reached, and it is
        # the only board in this fleet driven that way, so a chain with no
        # Arty beside it is a NeTV2 whether or not its PCIe edge is cabled
        # (a Pi 5 always lists the RP1 as a PCIe endpoint, so "no PCIe" is
        # never a usable test). When PCIe already named the board, the
        # idcode and DNA join that entry. An Arty is on its own FTDI, never
        # the harness, so a chain beside an Arty stays "jtag".
        arty = [b for b in boards if b["kind"] == "arty"]
        # What the harness says this card is. Only a GPIO harness says
        # anything: on a Digilent cable the board is already named by its own
        # FTDI, and a CH347 is a cable, not wiring to a known card. Asking
        # the harness table about a chain with no pins used to answer with
        # the NeTV2's, the default.
        named = harness_board(j.get("pins")) if cable == "gpio" else None
        # One card, one label: a chain read beside a PCIe endpoint that has no
        # idcode yet is that endpoint's, not a second board. When the harness
        # names the card, a generic PCIe entry is upgraded to it -- an Acorn
        # running gateware of its own reports a PCIe id that describes the
        # gateware, and the harness is what still knows the board.
        same = [b for b in boards if b["kind"] == named] if named else []
        unclaimed = [b for b in boards
                     if b["kind"] in ("unknown-fpga", "pcileech")
                     and not b.get("idcode")]
        # The flash on the chain belongs to whichever board the chain turns out
        # to be. It was once copied onto an Arty alone, and only its id and
        # part string, so no document ever carried a unique id for any board.
        flash = {k: j[k] for k in JTAG_FLASH_KEYS if j.get(k) is not None}
        netv2 = same if named else []
        if netv2:
            netv2[0].update(flash, idcode=j["idcode"], dna=j.get("dna"),
                            how=netv2[0]["how"] + "; " + entry["how"])
        elif arty and j.get("cable") == "digilent":
            arty[0].update(flash, idcode=j["idcode"], dna=j.get("dna"),
                           how=arty[0]["how"] + "; " + entry["how"])
        elif named and unclaimed:
            unclaimed[0].update(flash, kind=named, idcode=j["idcode"], dna=j.get("dna"),
                                how=unclaimed[0]["how"] + "; " + entry["how"])
        elif cable == "ch347" and len(unclaimed) == 1:
            # One FPGA on PCIe and one chain on the host's own JTAG cable are
            # one card (pi-sw1-p38's PCILeech board). The chain says nothing
            # of what the card is, so it keeps the kind PCIe gave it -- it
            # only gains the Device DNA its gateware could not give.
            unclaimed[0].update(flash, idcode=j["idcode"], dna=j.get("dna"),
                                how=unclaimed[0]["how"] + "; " + entry["how"])
        else:
            if named and not arty:
                entry["kind"] = named
            entry.update(flash)
            boards.append(entry)
    return boards


# What a chain's flash read leaves on the board it belongs to
JTAG_FLASH_KEYS = ("flash_jedec", "flash", "flash_uid", "flash_uid_bits",
                   "flash_uid_state", "flash_uid_note", "flash_error",
                   "flash_extended_id", "flash_sfdp", "flash_source")


def merge_soc(boards, soc):
    """Fold each SoC reading into its board, checking the DNA against JTAG.

    This is the point of reading a thing twice: the DNA the chain gave up and
    the DNA the SoC reports are compared, and a board whose two readings
    disagree keeps neither -- there is no way to tell which is the lie, and
    an arbitrary choice would be printed on a sticker.
    """
    for board in boards:
        reading = soc.get(board.get("slot") or "")
        if not reading or reading.get("error"):
            continue
        checked = cross_check({"jtag": board.get("dna"), "pcie": reading.get("dna")})
        board["dna_sources"] = checked["sources"]
        if checked["agree"] is not None:
            board["dna_agree"] = checked["agree"]
        if checked.get("conflict"):
            board["dna_conflict"] = checked["conflict"]
        board["dna"] = checked["value"]
        if reading.get("ident"):
            board["soc_ident"] = reading["ident"]
            # the SoC names the card its image was built for, which on a board
            # whose factory image is gone is the only thing that still does
            if reading.get("model") and board["kind"] in ("acorn", "unknown-fpga", "jtag"):
                board["kind"] = "acorn"
                board["soc_model"] = reading["model"]
    return boards


def merge_acorn_flash(boards, read):
    """Put each flash an Acorn's SoC read on the board at that PCIe address.

    By slot, not through the chain: the chain belongs to whatever board its
    harness names, and on a Pi 4's default pins that is a NeTV2."""
    for board in boards:
        reading = read.get(board.get("slot") or "")
        if reading:
            board.update(reading)
    return boards


def fpga_summary(boards):
    """Only the identity keys, in a fixed shape."""
    out = []
    for b in boards:
        entry = {"kind": b["kind"]}
        for k in ("serial", "dna", "idcode", "flash", "flash_jedec", "gateware",
                  "gateware_id", "hw_rev", "mode", "trace_id",
                  "dna_sources", "dna_agree", "dna_conflict", "soc_model",
                  "flash_uid", "flash_uid_bits", "flash_uid_state",
                  "flash_uid_note", "flash_error", "flash_extended_id",
                  "flash_sfdp", "flash_source"):
            # not plain truthiness: FPGA id 0 is a real class (SP605_FT601)
            if b.get(k) is not None and b.get(k) != "":
                entry[k] = b[k]
        out.append(entry)
    return out


def collect_fpga(jtag=False, flash=False, force_offline=False, pins=None, soc=False):
    # A Cynthion is read from its descriptors alone, so it is collected
    # unconditionally: unlike every other board here, nothing is sent to it.
    f = {"pcie": pcie_devices(), "ftdi": ftdi_devices(), "cynthion": cynthion_devices()}
    # The gateware is asked only when the PCIe edge has already shown its
    # signature and an FT601 is present: sending register reads into some
    # other device's FT601 would be writing into whatever that device is.
    # Opt-in with --jtag, which already means "talk to the FPGA". Asked before
    # the chain, because a flash read replaces the gateware -- and what it
    # says it was built for is what picks that read's bridge.
    f["pcileech"] = None
    if jtag and any(is_pcileech_pcie(pc) for pc in f["pcie"]) \
            and any(u["id"] == "0403:601f" for u in f["ftdi"]):
        f["pcileech"] = pcileech_probe()
    # A second reading of the same identity, over PCIe rather than JTAG, from
    # the SoC these Acorns now carry. Opt-in because it maps a BAR: the window
    # is known and read-only, but the rule against mapping one was written
    # after an unknown board's BAR wedged a host, so it is asked for by name.
    # Asked before the chain for the same reason as the gateware: a flash
    # read replaces the SoC, and read afterwards it answered nothing at all.
    f["soc"] = {}
    if soc:
        for pc in f["pcie"]:
            if pc["id"].startswith(("10ee:", "1e24:")):
                f["soc"][pc["slot"]] = soc_probe(pc["slot"])
    # An Acorn's SoC reads its own flash without replacing itself, and needs
    # no chain to do it: pi-sw2-p48's was read this way while its harness
    # said "TDO is stuck at 0". So it is asked first, and on its own; only a
    # board it could not read goes on to the bridge, which replaces whatever
    # design is running -- possibly someone's session.
    endpoints = fpga_endpoints(f["pcie"])
    f["acorn_flash"] = None
    if flash and endpoints:
        read, why = acorn_flash_probe()
        f["acorn_flash"] = {"read": read, "error": why}
    unread = [s for s in endpoints if s not in (f["acorn_flash"] or {}).get("read", {})]
    f["jtag"] = jtag_probe(flash and (unread or not endpoints), pins,
                           gateware_parts(f["pcileech"]), endpoints) if jtag else None
    # The ECP5 TraceID, and only when asked for by name. This ends the
    # board's capture and may drop power to whatever is on its TARGET port,
    # so it is not folded into --jtag, which is harmless everywhere else.
    f["cynthion_jtag"] = None
    if force_offline and any(cynthion_flash_uid(c) for c in f["cynthion"]):
        f["cynthion_jtag"] = cynthion_offline_probe()
    f["boards"] = merge_acorn_flash(merge_soc(fpga_verdict(f), f["soc"]),
                                    (f["acorn_flash"] or {}).get("read", {}))
    f["summary"] = fpga_summary(f["boards"])
    return f


def merge_fpga(doc, f):
    """Fold an fpga document into a Pi probe document (in place)."""
    doc["fpga"] = {k: f[k] for k in ("pcie", "ftdi", "jtag", "cynthion",
                                     "cynthion_jtag", "soc", "acorn_flash")}
    doc["verdict"]["fpga"] = f["boards"]
    doc["verdict"]["summary"]["fpga"] = f["summary"]
    return doc


def describe(boards):
    for b in boards:
        print("  fpga   : %s (%s)%s" % (b["kind"], b["how"],
                                        (", DNA " + b["dna"]) if b.get("dna") else ""))
    if not boards:
        print("  fpga   : none found")


def main():
    if "--recover-cynthion" in sys.argv:
        # The way home for a board left in Apollo mode, which is the one
        # state this tool can leave a rig in that a person has to undo.
        res = cynthion_offline_probe(recover=True)
        print("cynthion: %s" % ("back in gateware mode" if res.get("restored")
                                else res.get("error") or "did not come back"))
        return
    pins = None
    for arg in sys.argv[1:]:
        if arg.startswith("--pins="):
            pins = arg.split("=", 1)[1]
    f = collect_fpga("--jtag" in sys.argv, "--flash" in sys.argv,
                     "--force-offline" in sys.argv, pins, "--soc" in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(f, indent=1))
        return
    describe(f["boards"])
    read = f.get("cynthion_jtag") or {}
    if read.get("error"):
        print("  cynthion: %s" % read["error"])
    if read and not read.get("restored"):
        print("  cynthion: THE ANALYZER DID NOT COME BACK. Recover with:\n"
              "            rpi-hwid fpga --recover-cynthion")


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
