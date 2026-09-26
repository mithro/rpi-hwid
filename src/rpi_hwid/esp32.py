#!/usr/bin/env python3
"""Which ESP32s are on this host's USB, and who they are.

Run on the host the ESP32s are plugged into (a Pi), like the Tiny Tapeout
module: stand-alone, stdlib-only, Python 3.5 grammar, and embeddable in the
probe that ``rpi-hwid collect`` sends over ssh.

    rpi-hwid esp32 [--json]                           the USB tree only
    rpi-hwid esp32 [--json] --read /dev/ttyACM0 ...   and read these chips

Two depths, because they cost very differently.

The USB tree is free. It is read from sysfs, and no serial port is opened:
opening one asserts DTR and RTS, which resets an ESP32 on most boards and
on the chip's own USB-Serial-JTAG. From the tree alone:

  * An ESP32 on its own USB-Serial-JTAG (303a:1001: the C3, C6, S3, H2 and
    later) puts its base MAC -- the Wi-Fi station MAC burned into eFuse --
    in the USB serial-number string. That is a real identifier read from
    the chip, with nothing sent to it.
  * Behind a USB-UART bridge (CP210x, CH34x, FTDI) the chip is invisible:
    all the tree says is that a bridge is there, which might as well carry
    a GPS or a radio module. Those are listed as candidates, never as
    ESP32s, with the bridge's own serial where it has one.

``--read PORT`` is the other depth, and it is disruptive: it resets the chip
into its ROM bootloader through the port's DTR/RTS lines, asks the ROM, and
resets it back into its application. It runs esptool -- the copy already on
the host, as a library, in a child python3 -- and asks for exactly this:
the chip description, features and crystal; the base MAC; the SPI flash's
JEDEC id (0x9F) and Read Unique ID (0x4B); and the eFuse fields that are
not secret (MAC, custom MAC, OPTIONAL_UNIQUE_ID, the wafer, block and
package versions, the flash and PSRAM capacity and vendor). No key block is
printed and nothing is written, to flash or to eFuse. Only the ports named
are touched, because a port on this host may be in use by something that a
reset would interrupt.
"""

import glob
import json
import os
import re
import subprocess
import sys

ROOT = ""   # tests point this at a fake sysfs

ESPRESSIF_VID = "303a"
USB_SERIAL_JTAG = "303a:1001"

# USB-UART bridges ESP32 boards carry. Named by chip, because the product
# string is often only "USB Serial".
BRIDGES = {
    "10c4:ea60": "CP210x",
    "1a86:7523": "CH340",
    "1a86:55d3": "CH343",
    "1a86:55d4": "CH9102",
    "0403:6001": "FT232R",
    "0403:6010": "FT2232",
    "0403:6014": "FT232H",
    "0403:6015": "FT231X",
}

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read().rstrip(b"\0").decode("ascii", "replace").strip()
    except OSError:
        return None


def normalise_mac(s):
    """'E8:3D:C1:8C:5C:88' -> 'e8:3d:c1:8c:5c:88'; None for anything that
    is not a MAC."""
    if s and MAC_RE.match(s):
        return s.lower()
    return None


def usb_serial_devices():
    """Every USB device on the tree that has a tty, with its descriptors.
    Read from sysfs only: no port is opened."""
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/bus/usb/devices/*")):
        vid, pid = read(p + "/idVendor"), read(p + "/idProduct")
        if not vid or not pid:
            continue
        # CDC ACM puts the tty under tty/, usb-serial drivers put it directly
        # under the interface
        ttys = sorted(set(os.path.basename(t) for t in
                          glob.glob(p + "/" + os.path.basename(p) + ":*/tty/tty*")
                          + glob.glob(p + "/" + os.path.basename(p) + ":*/ttyUSB*")))
        if not ttys:
            continue
        out.append({
            "path": os.path.basename(p), "vidpid": vid + ":" + pid,
            "manufacturer": read(p + "/manufacturer"), "product": read(p + "/product"),
            "serial": read(p + "/serial"), "bcd_device": read(p + "/bcdDevice"),
            "speed": read(p + "/speed"), "tty": ["/dev/" + t for t in ttys],
        })
    return out


def by_id_links():
    """/dev/serial/by-id and any other /dev symlink to a tty, as tty -> links,
    so a port can be named the way the host's udev rules name it."""
    links = {}
    for link in sorted(glob.glob(ROOT + "/dev/serial/by-id/*") + glob.glob(ROOT + "/dev/*")):
        if not os.path.islink(link):
            continue
        target = os.path.normpath(os.path.join(os.path.dirname(link), os.readlink(link)))
        if ROOT:
            target = target[len(ROOT):] if target.startswith(ROOT) else target
            link = link[len(ROOT):]
        if re.match(r"^/dev/tty(ACM|USB)\d+$", target):
            links.setdefault(target, []).append(link)
    return links


def classify(dev):
    """'usb-serial-jtag', 'espressif-usb' or 'bridge' for a device that is
    or may be an ESP32, else None."""
    if dev["vidpid"] == USB_SERIAL_JTAG:
        return "usb-serial-jtag"
    if dev["vidpid"].startswith(ESPRESSIF_VID + ":"):
        return "espressif-usb"
    if dev["vidpid"] in BRIDGES:
        return "bridge"
    return None


def device_from_usb(dev, links):
    """The document's entry for one device from its USB descriptors."""
    how = classify(dev)
    tty = dev["tty"][0] if dev["tty"] else None
    d = {
        "transport": how, "usb_path": dev["path"], "vidpid": dev["vidpid"],
        "tty": tty, "tty_links": links.get(tty, []) if tty else [],
        "usb_manufacturer": dev["manufacturer"], "usb_product": dev["product"],
        "usb_serial": dev["serial"], "bcd_device": dev["bcd_device"],
        "bridge": BRIDGES.get(dev["vidpid"]),
        "mac": None, "mac_source": None,
        "chip": None, "chip_description": None, "revision": None, "package": None,
        "features": [], "crystal_mhz": None,
        "flash_jedec": None, "flash_uid": None, "efuse": {},
        "read": None, "read_error": None, "read_errors": {}, "boot_after": None,
    }
    if how == "usb-serial-jtag":
        # the peripheral's serial-number string is the chip's base MAC
        d["mac"] = normalise_mac(dev["serial"])
        d["mac_source"] = "usb-serial-jtag serial" if d["mac"] else None
    return d


# --- the read -----------------------------------------------------------------
#
# Run in a child python3 that can import esptool, so this file stays
# stdlib-only. It prints one line, RESULT {json}.
#
# Every step after the connect is on its own: a step that fails records its
# error under "errors" and the rest still run, so a flash that will not give
# up a unique id does not cost the chip, MAC, crystal and eFuse already read.
# And whatever happens once the chip is in its bootloader, the `finally`
# resets it back into its application: a read that died half way once left
# three nodes sitting in the ROM (2026-09-26).
#
# After that reset the same port stays open for a few seconds to keep what
# the application prints as it boots -- the evidence that it came back --
# with HUPCL cleared first so that closing the port does not drop DTR and
# RTS and reset it a second time.
#
# The flash's Read Unique ID (0x4B: four dummy bytes, then 64 bits) is read
# in two halves. esptool reads at most 32 bits back from one SPI command
# (esptool 4.7 and 5.2 both refuse more), and 0x4B takes no address, so the
# second half is read by clocking eight bytes out on MOSI first -- the four
# dummies and the first half, which the flash drives on its own output while
# the controller ignores it -- and then 32 bits in. A third read offset by two
# bytes must agree with the join of the two, or the uid is not trusted.
#
# esptool 4.x spells its reset modes default_reset, 5.x default-reset, and
# 5.x dropped espefuse.get_efuses; the eFuse table is taken from the chip's
# own module (espefuse.efuse.<chip>.fields.EspEfuses), which both have.
READ_SCRIPT = r'''
import json, os, re, sys, termios, time
port = sys.argv[1]
listen = float(os.environ.get("RPI_HWID_ESP32_LISTEN", "12"))
out = {"port": port, "python": sys.executable, "errors": {}}


def step(name, fn):
    try:
        out[name] = fn()
    except Exception as exc:
        out["errors"][name] = "%s: %s" % (type(exc).__name__, exc)


import esptool
out["esptool"] = esptool.__version__
mode = "default-reset" if int(esptool.__version__.split(".")[0]) >= 5 else "default_reset"
esp = None
try:
    esp = esptool.cmds.detect_chip(port, 115200, mode)
    step("chip_description", esp.get_chip_description)
    step("features", lambda: list(esp.get_chip_features()))
    step("crystal_mhz", esp.get_crystal_freq)
    step("mac", lambda: ":".join("%02x" % b for b in esp.read_mac()))

    def jedec():
        esp.flash_spi_attach(0)
        fid = esp.flash_id()
        return "0x%02x%02x%02x" % (fid & 0xFF, (fid >> 8) & 0xFF, (fid >> 16) & 0xFF)

    step("flash_jedec", jedec)

    def uid():
        def word(skip):
            w = esp.run_spiflash_command(0x4B, data=b"\0" * (4 + skip), read_bits=32)
            return w.to_bytes(4, "little")
        whole = word(0) + word(4)
        if word(2) != whole[2:6]:
            raise ValueError("halves disagree: %s, offset read %s"
                             % (whole.hex(), word(2).hex()))
        return whole.hex()

    step("flash_uid", uid)

    def efuse():
        import importlib
        name = esp.CHIP_NAME.lower().replace("-", "")
        mod = importlib.import_module("espefuse.efuse.%s.fields" % name)
        efuses = mod.EspEfuses(esp, skip_connect=False)
        wanted = re.compile(
            r"^(MAC|MAC_FACTORY|CUSTOM_MAC|MAC_CUSTOM|OPTIONAL_UNIQUE_ID|"
            r"WAFER_VERSION.*|CHIP_VER.*|CHIP_PACKAGE.*|PKG_VERSION|BLK_VERSION.*|"
            r"FLASH_CAP|FLASH_VENDOR|FLASH_TEMP|PSRAM_CAP|PSRAM_VENDOR|PSRAM_TEMP|"
            r"PSRAM_SIZE|CHIP_CPU_FREQ.*)$")
        fields = {}
        for e in efuses:
            if wanted.match(e.name):
                v = e.get()
                fields[e.name] = v if isinstance(v, (int, bool)) else str(v)
        return fields

    step("efuse", efuse)
except Exception as exc:
    out["errors"]["connect"] = "%s: %s" % (type(exc).__name__, exc)
finally:
    ser = None
    try:
        if esp is not None:
            esp.hard_reset()
            ser = esp._port
        else:
            # never reached the ROM, or reached it and lost it: pulse RTS
            # the way esptool's HardReset does, so nothing is left stranded
            import serial
            ser = serial.Serial(port, 115200, timeout=0.2)
            ser.dtr = False
            ser.rts = True
            time.sleep(0.2)
            ser.rts = False
        out["reset"] = "hard_reset"
    except Exception as exc:
        out["errors"]["reset"] = "%s: %s" % (type(exc).__name__, exc)
    if ser is not None:
        try:
            attrs = termios.tcgetattr(ser.fileno())
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(ser.fileno(), termios.TCSANOW, attrs)
        except Exception as exc:
            out["errors"]["hupcl"] = "%s: %s" % (type(exc).__name__, exc)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < listen:
            try:
                buf += ser.read(4096)
            except Exception as exc:
                out["errors"]["listen"] = "%s: %s" % (type(exc).__name__, exc)
                break
        out["boot_after"] = buf.decode("utf-8", "replace")[-3000:]
        try:
            ser.close()
        except Exception:
            pass
    print("RESULT " + json.dumps(out))
'''


def parse_read(text):
    """The read's result from the child's output, or None."""
    for line in (text or "").splitlines():
        if line.startswith("RESULT "):
            try:
                return json.loads(line[len("RESULT "):])
            except ValueError:
                return None
    return None


def esptool_pythons():
    """The interpreters to try for the read, in order: this one, then any
    virtualenv under ~/.venvs -- where rpi4-esp keeps the esptool it has --
    so a host with esptool in a venv of its own needs nothing installed."""
    home = os.path.expanduser("~")
    return [sys.executable] + sorted(
        glob.glob(os.path.join(home, ".venvs", "*", "bin", "python3")))


def esptool_python():
    """The first interpreter that can import esptool and espefuse, or None."""
    for python in esptool_pythons():
        try:
            r = subprocess.run([python, "-c", "import esptool, espefuse"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return python
    return None


def error_line(text):
    """The line of the child's output that says what went wrong: the last
    that names an error, else the last there is."""
    lines = [s.strip() for s in (text or "").splitlines() if s.strip()]
    errors = [s for s in lines if "Error" in s or "error:" in s]
    return (errors or lines or ["no output"])[-1]


def run_read(port, timeout=120):
    """Run the read on one port: (result or None, error or None, output)."""
    python = esptool_python()
    if python is None:
        return None, ("no python here can import esptool (tried %s)"
                      % ", ".join(esptool_pythons())), ""
    try:
        r = subprocess.run([python, "-u", "-c", READ_SCRIPT, port],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timed out after %d s" % timeout, ""
    except OSError as exc:
        return None, str(exc), ""
    text = r.stdout.decode("utf-8", "replace")
    result = parse_read(text)
    if result is None:
        return None, "esptool read failed: " + error_line(text), text
    return result, None, text


def chip_facts(description):
    """'ESP32-C3 (QFN32) (revision v0.4)' -> ('ESP32-C3', 'QFN32', 'v0.4')."""
    m = re.match(r"^\s*(ESP[0-9A-Za-z-]+)(.*)$", description or "")
    if not m:
        return None, None, None
    rest = m.group(2)
    rev = re.search(r"\(revision\s+(v?[0-9.]+)\)", rest)
    pkgs = [p for p in re.findall(r"\(([^)]*)\)", rest) if not p.startswith("revision")]
    return m.group(1), (pkgs[0] if pkgs else None), (rev.group(1) if rev else None)


def apply_read(d, result):
    """Fold a read's result into a device entry (in place)."""
    chip, package, rev = chip_facts(result.get("chip_description"))
    read_mac = normalise_mac(result.get("mac"))
    if d["mac"] and read_mac and d["mac"] != read_mac:
        # recorded, not resolved: which to believe is not this module's call
        d["mac_conflict"] = {"usb": d["mac"], "efuse": read_mac}
    if read_mac:
        d["mac"] = read_mac
        d["mac_source"] = "efuse" if not d["mac_source"] else d["mac_source"] + "; efuse"
    d.update({
        "chip": chip, "package": package, "revision": rev,
        "chip_description": result.get("chip_description"),
        "features": result.get("features") or [], "crystal_mhz": result.get("crystal_mhz"),
        "flash_jedec": result.get("flash_jedec"), "flash_uid": result.get("flash_uid"),
        "efuse": result.get("efuse") or {}, "read": "esptool " + str(result.get("esptool")),
        # a step that failed, in esptool's words: the rest of the read stands
        "read_errors": result.get("errors") or {},
        "boot_after": result.get("boot_after"),
    })
    if "connect" in d["read_errors"]:
        d["read_error"] = d["read_errors"]["connect"]
    return d


def resolve(port, links):
    """A port as named on the command line -> its /dev/tty name."""
    if port in links:
        return port
    for tty, names in links.items():
        if port in names:
            return tty
    real = os.path.realpath(ROOT + port) if ROOT else os.path.realpath(port)
    return real[len(ROOT):] if ROOT and real.startswith(ROOT) else real


def collect_esp32(read_ports=()):
    """The ESP32s on this host's USB. `read_ports` are the ports whose chip
    may be reset and read; nothing else is opened."""
    usb = usb_serial_devices()
    links = by_id_links()
    devices, candidates, reads = [], [], []
    by_tty = {}
    for dev in usb:
        how = classify(dev)
        if how is None:
            continue
        d = device_from_usb(dev, links)
        by_tty[d["tty"]] = d
        (devices if how != "bridge" else candidates).append(d)
    for port in read_ports:
        tty = resolve(port, links)
        d = by_tty.get(tty)
        result, error, text = run_read(port)
        reads.append({"port": port, "tty": tty, "ok": result is not None,
                      "error": error, "output": text[-4000:]})
        if d is None:
            continue
        if result is None:
            d["read_error"] = error
            continue
        apply_read(d, result)
        if d in candidates:
            # a bridge whose chip answered as an ESP32 is one
            candidates.remove(d)
            devices.append(d)
    return {"usb": usb, "reads": reads, "devices": devices, "candidates": candidates}


def merge_esp32(doc, e):
    """Fold an esp32 document into a probe document (in place). The fixed
    shape is verdict.esp32 -- a list of devices -- with the USB tree and the
    reads kept as evidence beside it."""
    doc["esp32"] = {"usb": e["usb"], "reads": e["reads"], "candidates": e["candidates"]}
    doc.setdefault("verdict", {})["esp32"] = e["devices"]
    return doc


def describe(e):
    for d in e["devices"]:
        what = d["chip_description"] or "ESP32 (chip not read)"
        print("  esp32  : %s  %s  %s (%s)" % (d["mac"] or "no MAC", what, d["tty"],
                                             d["transport"]))
        if d["read_error"]:
            print("           read failed: %s" % d["read_error"])
    for d in e["candidates"]:
        print("  esp32? : %s bridge %s serial %s on %s; --read %s would reset it to ask" % (
            d["bridge"], d["vidpid"], d["usb_serial"] or "(none)", d["tty"], d["tty"]))
    if not e["devices"] and not e["candidates"]:
        print("  esp32  : none found")


def main():
    args = sys.argv[1:]
    ports = [args[i + 1] for i, a in enumerate(args) if a == "--read" and i + 1 < len(args)]
    e = collect_esp32(ports)
    if "--json" in args:
        print(json.dumps(e, indent=1))
        return
    describe(e)


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
