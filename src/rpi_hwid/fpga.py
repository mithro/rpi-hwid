#!/usr/bin/env python3
"""Which FPGA board is attached to this Raspberry Pi?

A separate, dependency-free file like rpi_hwid.probe, for the same reason
(python3 >= 3.5 on the host, sent over ssh, nothing installed), kept apart
from it because few people have an FPGA on their Pi. Standalone:

    ssh pi@host 'python3 -' --json --jtag < src/rpi_hwid/fpga.py
    rpi-hwid fpga --json [--jtag] [--flash]

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
"""
import fcntl
import glob
import json
import os
import re
import struct
import subprocess
import sys

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




# The GPIO harness, as openFPGALoader spells it on the command line and as
# openocd wants it counted out. openFPGALoader documents --pins as
# TDI:TDO:TCK:TMS; openocd's *_jtag_nums take tck tms tdi tdo.
HARNESS_PINS = "27:22:4:17"
HARNESS_TCK, HARNESS_TMS, HARNESS_TDI, HARNESS_TDO = 4, 17, 27, 22
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


def openocd_adapter(digilent_serial=None):
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
        pins = (("tck", HARNESS_TCK), ("tms", HARNESS_TMS),
                ("tdi", HARNESS_TDI), ("tdo", HARNESS_TDO))
        # 0.11 spelled this as one chip for the adapter and four numbers.
        # Offered first, so that on a later release the deprecated wrapper
        # (if it still exists) is overridden by the explicit form after it.
        # Unmeasured: no 0.11 build is in the fleet to try it on.
        return (["adapter driver linuxgpiod",
                 "catch {linuxgpiod_gpiochip %d}" % chip,
                 "catch {linuxgpiod_jtag_nums %d %d %d %d}" % tuple(p for _s, p in pins)]
                + ["catch {adapter gpio %s -chip %d %d}" % (sig, chip, pin)
                   for sig, pin in pins]
                + ["adapter speed 1000"])
    if pi5:
        return None           # RP1 header, and this openocd has no linuxgpiod
    return ["interface bcm2835gpio",
            "bcm2835gpio_peripheral_base %s" % peripheral_base(),
            "bcm2835gpio_speed_coeffs 146203 36",
            "bcm2835gpio_jtag_nums %d %d %d %d" % (
                HARNESS_TCK, HARNESS_TMS, HARNESS_TDI, HARNESS_TDO),
            "bcm2835gpio_srst_num %d" % HARNESS_SRST,
            "adapter_khz 1000"]


def openocd_probe(digilent_serial=None):
    """idcode and Device DNA over openocd, on either cable.

    The fallback for a host that cannot have openFPGALoader: Raspbian 9
    Stretch has no package for it, so a NeTV2 there is invisible to every
    other path (no PCIe on a Pi 3, no FTDI of its own). openocd is packaged
    much more widely, drives the Digilent FT2232 as well as the GPIO harness,
    and the chain is the same chain either way.
    """
    if not sh(["which", "openocd"]):
        return None
    adapter = openocd_adapter(digilent_serial)
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
    m = re.search(r"RAWDNA=([0-9a-fA-F]+)", out)
    if m:
        raw = int(m.group(1), 16)
        rev = int(format(raw, "0%db" % DNA_BITS)[::-1], 2) & DNA_MASK
        # An all-zero or all-ones shift is an absent or unpowered chain, not a
        # DNA; naming a board from one would mint a wrong name permanently.
        if rev and rev != DNA_MASK:
            res["dna"] = "0x%016x" % rev
    return res


def digilent_cables():
    """The Digilent FT2232 cables on this host, an Arty's own JTAG."""
    return [f for f in ftdi_devices()
            if f["id"] == "0403:6010" and (f["manufacturer"] or "").startswith("Digilent")]


def jtag_probe(want_flash=False):
    """openFPGALoader over whichever cable this host has, else openocd.
    Returns the idcode line when a chain answers."""
    cables = digilent_cables()
    if not sh(["which", "openFPGALoader"]):
        # openocd drives the Digilent FT2232 as well as the GPIO harness, so
        # the fallback covers an Arty too, not just a NeTV2.
        ocd = openocd_probe(cables[0]["serial"] if cables else None)
        if ocd is not None:
            return ocd
        return {"error": "openFPGALoader not installed"}
    digilent = bool(cables)
    if digilent:
        harness = ["sudo", "openFPGALoader", "-c", "digilent"]
    else:
        harness = ["sudo", "openFPGALoader", "-c", "libgpiod", "--pins=" + HARNESS_PINS]
    # stderr as well: that is where openFPGALoader says why it failed, and
    # without it a build missing its GPIO backend recorded raw "" and the
    # board simply vanished from the verdict.
    det = sh_all(harness + ["--detect"], timeout=60)
    m = re.search(r"idcode\s+(0x[0-9a-f]+)", det)
    if not m:
        # Installed is not the same as able. Try openocd before giving up, and
        # keep both tools' last words, so a chain neither can read says why.
        ocd = openocd_probe(cables[0]["serial"] if cables else None)
        if ocd is not None and ocd.get("idcode"):
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
    res["cable"] = "digilent" if digilent else "gpio"
    if want_flash and digilent:
        # the board profile supplies the part; the bridge replaces the design
        # by number with the revision nibble masked, as labels.idcode_part
        # does: a string list only ever matched the revisions written into it
        is_100t = (int(res["idcode"], 16) & 0x0FFFFFFF) == 0x3631093
        board = "arty_a7_100t" if is_100t else "arty_a7_35t"
        fl = sh(["sudo", "openFPGALoader", "-b", board, "--detect", "-f"], timeout=120)
        m = re.search(r"JEDEC ID: (0x[0-9a-f]+)", fl)
        res["flash_jedec"] = m.group(1) if m else None
        m = re.search(r"Detected: (.*)", fl)
        res["flash"] = m.group(1).strip() if m else None
    return res


def fpga_verdict(d):
    """Name the FPGA board(s) this Pi hosts, from PCIe, USB and JTAG."""
    boards = []
    for pc in d["pcie"]:
        sizes = sorted(pc["bars"], reverse=True)
        if pc["id"] == "10ee:7024" and sizes == [1 << 20]:
            boards.append({"kind": "netv2", "slot": pc["slot"],
                           "how": "PCIe 10ee:7024, one 1 MiB BAR (LitePCIe NeTV2 gateware)"})
        elif pc["id"] in ("1e24:021f", "10ee:7011") and sizes == [128 << 10, 64 << 10]:
            boards.append({"kind": "acorn", "slot": pc["slot"],
                           "how": "PCIe %s, 128 KiB + 64 KiB BARs (SQRL Acorn CLE-215+%s)" % (
                               pc["id"], "" if pc["id"] == "1e24:021f" else ", default Xilinx id")})
        elif pc["id"] == "10ee:0666" and sizes == [4 << 10]:
            # Checked before the Xilinx catch-all below, which is what named
            # it "unknown-fpga". The FT601 is reported when present but not
            # required: the PCIe id is the gateware's own, while an FT601 is
            # on plenty of things that are not this.
            ft601 = [f for f in d["ftdi"] if f["id"] == "0403:601f"]
            boards.append({"kind": "pcileech", "slot": pc["slot"],
                           "how": "PCIe 10ee:0666, one 4 KiB BAR (pcileech-fpga gateware)%s" % (
                               "; FT601 USB3 bridge at %s" % ft601[0]["path"] if ft601 else "")})
        elif pc["id"].startswith("10ee:") or pc["id"].startswith("1e24:"):
            boards.append({"kind": "unknown-fpga", "how": "PCIe %s, BARs %s" % (pc["id"], sizes),
                           "slot": pc["slot"]})
    for f in d["ftdi"]:
        if f["id"] == "0403:6010" and (f["manufacturer"] or "").startswith("Digilent"):
            boards.append({"kind": "arty", "how": "Digilent FT2232 %s" % f["serial"],
                           "serial": f["serial"]})
    j = d.get("jtag")
    if j and j.get("idcode"):
        entry = {"kind": "jtag", "how": "%s JTAG idcode %s%s" % (
            "FT2232" if j.get("cable") == "digilent" else "GPIO",
            j["idcode"], (" " + j["family"]) if j.get("family") else ""), "idcode": j["idcode"],
            "dna": j.get("dna")}
        # A chain on the GPIO harness is how a NeTV2 is reached, and it is
        # the only board in this fleet driven that way, so a chain with no
        # Arty beside it is a NeTV2 whether or not its PCIe edge is cabled
        # (a Pi 5 always lists the RP1 as a PCIe endpoint, so "no PCIe" is
        # never a usable test). When PCIe already named the board, the
        # idcode and DNA join that entry. An Arty is on its own FTDI, never
        # the harness, so a chain beside an Arty stays "jtag".
        netv2 = [b for b in boards if b["kind"] == "netv2"]
        arty = [b for b in boards if b["kind"] == "arty"]
        if netv2:
            netv2[0].update(idcode=j["idcode"], dna=j.get("dna"),
                            how=netv2[0]["how"] + "; " + entry["how"])
        elif arty and j.get("cable") == "digilent":
            arty[0].update(idcode=j["idcode"], dna=j.get("dna"),
                           how=arty[0]["how"] + "; " + entry["how"])
            if j.get("flash_jedec"):
                arty[0].update(flash_jedec=j["flash_jedec"], flash=j.get("flash"))
        else:
            if not arty:
                entry["kind"] = "netv2"
            boards.append(entry)
    return boards


def fpga_summary(boards):
    """Only the identity keys, in a fixed shape."""
    out = []
    for b in boards:
        entry = {"kind": b["kind"]}
        for k in ("serial", "dna", "idcode", "flash", "flash_jedec"):
            if b.get(k):
                entry[k] = b[k]
        out.append(entry)
    return out


def collect_fpga(jtag=False, flash=False):
    f = {"pcie": pcie_devices(), "ftdi": ftdi_devices()}
    f["jtag"] = jtag_probe(flash) if jtag else None
    f["boards"] = fpga_verdict(f)
    f["summary"] = fpga_summary(f["boards"])
    return f


def merge_fpga(doc, f):
    """Fold an fpga document into a Pi probe document (in place)."""
    doc["fpga"] = {k: f[k] for k in ("pcie", "ftdi", "jtag")}
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
    f = collect_fpga("--jtag" in sys.argv, "--flash" in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(f, indent=1))
        return
    describe(f["boards"])


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
