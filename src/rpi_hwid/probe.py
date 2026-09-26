#!/usr/bin/env python3
"""What is this Raspberry Pi wearing, and what powers it?

This module is deliberately a single, dependency-free file that runs on any
Raspberry Pi with python3 >= 3.5 (sudo is wanted for the Pi's own firmware
tools; the I2C header is read directly, needing neither sudo nor
i2c-tools), so it can be
sent to a host over ssh without installing anything:

    ssh pi@host 'python3 -' --json < src/rpi_hwid/probe.py
    rpi-hwid probe --json                # on the Pi, with the package installed
    rpi-hwid collect --out data/ hosts…  # from elsewhere, over ssh

The JSON it prints has a fixed-shape `verdict.summary` that the rest of the
package (collect, labels) consumes; the evidence behind each verdict is in
the other keys. The one option is --json (machine-readable). FPGA boards
attached to the Pi are a separate concern with a separate module,
rpi_hwid.fpga, which the collector appends to this one on request.

What each signal proves, established on the welland fleet on 2026-09-09:

HAT ID EEPROM at 0x50 on the ID bus (GPIO0/1)
    The firmware reads it and publishes /proc/device-tree/hat. The official
    Raspberry Pi PoE/PoE+ HATs and the Digilent Pmod HAT Adaptor have one.
HAT ID EEPROM at another address (0x51-0x57)
    Waveshare's PoE M.2 HAT+ (B) carries a well-formed HAT EEPROM at 0x52,
    which the firmware never reads, so the HAT is invisible in device-tree
    yet fully identifiable: product "Waveshare PoE M.2 HAT+ (B)", pid
    0x6d87. (Its vendor string is the literal placeholder "vendor " and
    every unit shares one UUID, so it names the model, not the unit.)
I2C devices on the header's user bus (pins 3/5)
    Waveshare PoE HAT (B) for 3B+/4B has an SSD1306 OLED at 0x3c and a
    PCF8574 fan controller at 0x20. Nothing else in the line-up puts
    anything there. The pair is the HAT's own silicon, so it names the HAT
    on whatever board the HAT is fitted to.

    Waveshare's 2-DOF Pan-Tilt HAT answers at 0x29, 0x40 and 0x70. Neither
    of the first two identifies it: 0x40 is the PCA9685's default and the
    INA219's, 0x29 the TSL2591's and the VL53L0X's. 0x70 is what settles
    it, being the PCA9685's All Call address, which it answers as well as
    its own -- so 0x40 and 0x70 are one chip, not two, and on rpi5-pantilt
    both return the same MODE1 (0x21) while 0x29 returns 0x50 from the
    TSL2591's ID register (2026-09-15).

    Beware that an address in this list is not a device. The scan is a
    zero-byte quick write, and rpi5-pantilt also acknowledges 0x28, where
    every actual read fails EREMOTEIO: nothing is fitted there. Signatures
    are written from registers read back, never from the scan alone.
USB tree
    Waveshare's PoE-ETH-USB-HUB-HAT for a Pi Zero is a Terminus 1a40:0101
    hub on the root port with an RTL8152 (0bda:8152) on its port 4.
Pi 5 firmware power fields
    /proc/device-tree/chosen/power/max_current is the firmware's verdict on
    the USB-C source: 5000 after a PD contract, 3000 when there is no PD
    contract *including when nothing is on USB-C at all* (a HAT feeding the
    GPIO 5 V pins), 1500 or 900 when a resistor-only USB-C source advertises
    that much. So 900 or 1500 proves an external USB-C supply, i.e. a
    splitter; 3000 is a GPIO HAT or a 3 A splitter and needs another signal.
Pi 5 PMIC ADC (vcgencmd pmic_read_adc)
    EXT5V_V is the 5 V input as the PMIC sees it: GPIO-fed HATs sit at
    5.1-5.4 V, splitter-fed boards at 4.8-5.0 V. BATT_V is the RTC backup
    cell: about 3 V when one is fitted, near 0 V when not.
Pi 5 fan header
    /proc/device-tree/cooling_fan/status is "okay" only when the firmware
    found a fan on the header; the pwmfan hwmon then gives its speed. A fan
    here is the Pi's own: Waveshare's PoE M.2 HAT+ (B) ships without one.
Network interfaces
    Each interface's driver and device path say whether it is soldered
    down (SoC Ethernet, SDIO radio, or the 3B+'s LAN7800 on an internal
    USB bus) or a removable USB adapter; the removable ones are listed
    separately with their USB descriptors, so a label can be printed for
    each dongle and the Pi's own label carries only its own MACs.

    Driver names run out before the older boards do. Every Pi up to the
    3B reaches Ethernet through a USB chip soldered beside the SoC -- the
    LAN9512/9514, driver smsc95xx -- and that driver serves removable
    SMSC dongles too, so it cannot be the test. Their MAC can: those
    boards derive both of their own from the serial number, so a MAC that
    matches is by definition the board's own port, and one that does not
    is by definition not. Measured on rpib-serial, a Model B, whose eth0
    is 0424:ec00 on an internal hub wearing b8:27:eb:0a:ee:d6 against a
    serial of 00000000110aeed6, beside a USB radio (0bda:818b) whose MAC
    matches nothing and which stays a dongle (2026-09-14).

    sysfs `removable` looks like it should answer this and does not: on a
    3B+ the two genuinely removable dongles both read "fixed", because it
    describes the internal hub's port rather than what is plugged into it.

What still cannot be told apart from the Pi: an EEPROM-less GPIO HAT on a
Pi 5 (Waveshare F, G, H, J) from a 3 A USB-C splitter, and on a 3B+/4 an
EEPROM-less, I2C-less HAT (Waveshare C, D, E) from any splitter -- and the
(C) *is* a splitter electrically, feeding the Pi's USB power input from a
USB-A socket. Those need the switch's 802.3af class or a look at the board.

Other boards: the Orange Pi PC (Xunlong, Allwinner H3, Armbian)
    The same probe runs on the fleet's Orange Pi PCs and the summary keeps
    its shape: the board is just another `model`, with no revision code, an
    empty header and an undetermined power class. Identity comes from the
    device tree: /proc/device-tree/model ("Xunlong Orange Pi PC") and
    compatible ("xunlong,orangepi-pc allwinner,sun8i-h3"). The serial is
    the SoC's: U-Boot reads the Allwinner SID e-fuses, builds serial# from
    them and writes it to /serial-number in the device tree it hands the
    kernel (common/fdt_support.c fdt_root), which the 32-bit kernel also
    prints as /proc/cpuinfo's Serial line. The SID words themselves are
    readable from the sunxi-sid nvmem under /sys/bus/nvmem/devices/, and
    the probe records them and reproduces U-Boot's rule (chip-id word, then
    a CRC-32 of the other three) as a fallback and a cross-check. U-Boot
    derives eth0's MAC from the same serial: 02, the serial's fourth byte,
    then its last four (02:81:2e:b7:a3:4e from 02c000812eb7a34e on
    pi-sw2-p22), so the MAC is not independent evidence. RAM is MemTotal
    rounded up to the fitted size and the Armbian release is
    /etc/armbian-release. The Pi-only pokes (dtparam, vcgencmd) are
    skipped: an H3 has no PMIC and no firmware power report, so unless a
    HAT names the supply nothing here can say what powers it.

    The 40-pin header, though, is probed exactly as a Pi's. A HAT does not
    know what it is plugged into: pi-sw2-p22 carries a Digilent Pmod HAT
    Adaptor whose ID EEPROM answers at 0x50 with the same R-Pi magic and
    the same atoms it would on a Pi (measured 2026-09-13). Only the bus
    numbering differs, and that is what HEADER_BUSES is for -- the Orange
    Pi PC's header pins 27/28 are TWI1 (i2c-1) and pins 3/5 are TWI0
    (i2c-0), the reverse of the Pi. Its i2c-2 is the R_TWI the SY8106A
    regulator sits on, and its i2c-3 is the HDMI DDC line, which
    acknowledges every address: neither is declared, so neither is read.
"""
import fcntl
import glob
import json
import os
import re
import struct
import subprocess
import sys
import time
import uuid
import zlib

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


def dt_u32(path):
    try:
        with open(path, "rb") as f:
            return struct.unpack(">I", f.read(4))[0]
    except (OSError, struct.error):
        return None


def dt_strings(path):
    """A device-tree string-list property (NUL-separated), as a list."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    return [s.decode("ascii", "replace") for s in raw.split(b"\0") if s]


# --- which board --------------------------------------------------------------

def board_kind(model, compatible):
    """"rpi" for a Raspberry Pi, "opi" for a Xunlong Orange Pi, else "other";
    from the device tree's compatible list first, the model string second."""
    if any(c.startswith("raspberrypi,") for c in compatible) or model.startswith("Raspberry Pi"):
        return "rpi"
    if any(c.startswith("xunlong,") for c in compatible) or "Orange Pi" in model:
        return "opi"
    return "other"


NOMINAL_MEMORY = ((256, "256 MB"), (512, "512 MB"), (1024, "1 GB"), (2048, "2 GB"),
                  (4096, "4 GB"), (8192, "8 GB"), (16384, "16 GB"), (32768, "32 GB"),
                  (65536, "64 GB"))


def nominal_memory(mem_kb):
    """The fitted RAM size from MemTotal, which always reads a little under
    it (the firmware, and on a Pi the GPU, take their share first): the
    smallest nominal size MemTotal fits in."""
    if not mem_kb:
        return None
    mib = mem_kb / 1024.0
    for size, name in NOMINAL_MEMORY:
        if mib <= size:
            return name
    size = NOMINAL_MEMORY[-1][0]             # beyond the table: the next
    while size < mib:                        # power of two that fits it
        size *= 2
    return "%d GB" % (size // 1024)


def mem_total_kb():
    m = re.search(r"^MemTotal:\s*(\d+) kB", read(ROOT + "/proc/meminfo") or "", re.M)
    return int(m.group(1)) if m else None


def armbian_release():
    """/etc/armbian-release as a dict (KEY=value lines, values may be
    quoted); None where the file does not exist."""
    text = read(ROOT + "/etc/armbian-release")
    if text is None:
        return None
    out = {}
    for line in text.split("\n"):
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def sunxi_sid():
    """The first four words of the Allwinner SID e-fuses, from the sunxi-sid
    nvmem (little-endian 32-bit words: the chip id, then the unique part);
    None where there is no such nvmem or it cannot be read."""
    for p in sorted(glob.glob(ROOT + "/sys/bus/nvmem/devices/sunxi-sid*/nvmem")):
        try:
            with open(p, "rb") as f:
                raw = f.read(16)
        except OSError:
            continue
        if len(raw) == 16:
            return ["0x%08x" % w for w in struct.unpack("<4I", raw)]
    return None


def sunxi_serial(sid):
    """U-Boot's serial# from an Allwinner SID's words: the chip-id word,
    then a CRC-32 of words 1-3, with the low 24 bits forced non-zero
    because they also become the MAC's NIC bytes. H3 and later; the CRC is
    skipped on sun4i/5i/6i/7i and on the A23 and A33, which are sun8i too.
    (board/sunxi/board.c: get_unique_sid in current U-Boot,
    setup_environment in older trees.) The same string U-Boot writes to
    the device tree's /serial-number -- so this stands in only when that
    property is missing. None when the chip-id word is zero, which is what
    a board whose U-Boot sets no serial at all reads as."""
    words = [int(w, 16) for w in sid]
    if not words[0]:
        return None
    tail = zlib.crc32(struct.pack("<3I", *words[1:4])) & 0xffffffff
    if tail & 0xffffff == 0:
        tail |= 0x800000
    return "%08x%08x" % (words[0], tail)


# --- I2C ----------------------------------------------------------------------
#
# Spoken directly to /dev/i2c-N rather than through i2c-tools. The ioctl the
# tools wrap needs no more privilege than the device node itself, which is
# group i2c on every board here with the login in that group, so the header
# reads with no sudo and no packages at all -- which is the point of the
# probe being one dependency-free file, and it holds on a board where
# i2c-tools is not installed.
#
# It is installed on this fleet, in /usr/sbin (which is not on a login PATH,
# only on sudo's). The two agree: checked against i2c-tools 4.3 on the Orange
# Pi on 2026-09-13, the bus scans match i2cdetect -r on both header buses and
# eeprom_read returns bytes identical to i2ctransfer's 256.

I2C_SLAVE = 0x0703                 # <linux/i2c-dev.h>: bind this fd to an address


def i2c_open(bus, addr):
    """An fd on /dev/i2c-`bus` bound to the 7-bit `addr`, or None when the
    bus is absent, the address is already claimed by a kernel driver (what
    i2cdetect shows as "UU"), or this user is not in the i2c group."""
    try:
        fd = os.open(ROOT + "/dev/i2c-%d" % bus, os.O_RDWR)
    except OSError:
        return None
    try:
        fcntl.ioctl(fd, I2C_SLAVE, addr)
    except (OSError, IOError):     # 3.5: ioctl on a non-device raises IOError
        os.close(fd)
        return None
    return fd


def i2c_present(bus, addr):
    """Whether `addr` answers a one-byte read. That read is what
    `i2cdetect -r` does, and it is the safer of its two probes: a quick
    write can disturb a write-only device, a read cannot."""
    fd = i2c_open(bus, addr)
    if fd is None:
        return False
    try:
        os.read(fd, 1)
        return True
    except (OSError, IOError):
        return False
    finally:
        os.close(fd)


def i2c_scan(bus, first=0x03, last=0x77):
    """The addresses answering on `bus`, lower-case hex without the 0x, the
    way i2cdetect prints them and the way the rest of this file expects
    them."""
    return ["%02x" % a for a in range(first, last + 1) if i2c_present(bus, a)]


# --- HAT EEPROM -------------------------------------------------------------

def eeprom_read(bus, addr, length=256):
    """The first `length` bytes of an EEPROM with a 16-bit word address:
    seek to 0 by writing the two offset bytes, then read. None when the
    address does not answer or the read fails part way."""
    fd = i2c_open(bus, addr)
    if fd is None:
        return None
    out = b""
    try:
        os.write(fd, b"\x00\x00")
        while len(out) < length:
            chunk = os.read(fd, min(128, length - len(out)))
            if not chunk:
                break
            out += chunk
    except (OSError, IOError):
        return None
    finally:
        os.close(fd)
    return out if len(out) == length else None


def eeprom_decode(blob):
    if not blob or blob[:4] != b"R-Pi":
        return None
    ver, _, numatoms, eeplen = struct.unpack_from("<BBHI", blob, 4)
    pos, info = 12, {"version": ver, "atoms": []}
    for _ in range(numatoms):
        if pos + 8 > len(blob):
            break
        atype, count, dlen = struct.unpack_from("<HHI", blob, pos)
        data = blob[pos + 8:pos + 8 + dlen - 2]
        if atype == 1 and len(data) >= 22:
            pid, pver, vslen, pslen = struct.unpack_from("<HHBB", data, 16)
            info.update(uuid=str(uuid.UUID(bytes_le=data[0:16])), pid="0x%04x" % pid,
                        pver="0x%04x" % pver,
                        vendor=data[22:22 + vslen].decode("ascii", "replace"),
                        product=data[22 + vslen:22 + vslen + pslen].decode("ascii", "replace"))
        elif atype == 3:
            info["dt_blob"] = data.decode("ascii", "replace")
        pos += 8 + dlen
    return info


# Where the 40-pin header's two I2C pairs land, per board. The roles are the
# header's, not the SoC's: "id" is pins 27/28 (ID_SD/ID_SC, where the HAT
# spec puts the ID EEPROM) and "user" is pins 3/5 (SDA1/SCL1, where a HAT
# puts anything else it carries). The bus numbers are each board's own and
# do not agree between boards -- measured on the fleet on 2026-09-13 by
# reading the R-Pi magic off the EEPROM of a HAT known to be fitted:
#   Raspberry Pi 4, Pi 5    id 0, user 1
#   Xunlong Orange Pi PC    id 1 (i2c@1c2b000, PA18/PA19), user 0
#                           (i2c@1c2ac00, PA11/PA12) -- the reverse of the Pi
# "enable" brings a bus up when it is not already there, and is the board's
# own command: the Pi's dtparam does not exist on Armbian, which needs a
# reboot to add an overlay, so an Orange Pi takes the buses it has. Both
# buses get one. Most of the fleet leaves the user bus off -- 12 of the 35
# Pis had no /dev/i2c-1 -- and without an enable those hosts were never
# looked at, so a Waveshare PoE HAT (B), which is known only by the devices
# it puts there, could not have been seen on any of them. Whatever this
# brings up is put back afterwards: the host is left as it was found.
#
# Only a declared bus is ever scanned. An undeclared one may be an HDMI DDC
# line -- the Orange Pi's i2c-3 acknowledges all 117 addresses -- and
# scanning it would invent a HAT out of an empty socket.
HEADER_BUSES = {
    "rpi": {"id": 0, "user": 1,
            "enable": ["sudo", "dtparam", "i2c_vc=on"],
            "enable_user": ["sudo", "dtparam", "i2c_arm=on"]},
    "opi": {"id": 1, "user": 0, "enable": None, "enable_user": None},
}


# How long a node may take to appear after its overlay is applied. udev
# makes /dev/i2c-N after dtparam has returned, and on a Pi Zero late enough
# that looking straight away missed it (rpiz-4, 2026-09-26).
BUS_SETTLE_S = 5.0


def open_bus(bus, enable=None):
    """Make /dev/i2c-<bus> readable, as (there now, brought up by this call).

    The second half is what says to put it back: a bus the board was
    already carrying is left alone, and an overlay this applied is taken
    out again -- whether or not its node ever turned up."""
    path = ROOT + "/dev/i2c-%d" % bus
    if os.path.exists(path):
        return True, False
    if not enable:
        return False, False
    sh(enable)
    deadline = time.time() + BUS_SETTLE_S
    while not os.path.exists(path) and time.time() < deadline:
        time.sleep(0.1)
    return os.path.exists(path), True


def id_bus_scan(bus, enable=None):
    """HAT ID EEPROMs on `bus`, as (found, whether the bus could be read).

    Keyed by address, as the HAT spec allows 0x50 through 0x57. Only a blob
    carrying the R-Pi magic counts, so a bus that answers at every address
    yields nothing rather than eight HATs -- and nothing found is reported
    apart from no bus to look at, because only the first rules a HAT out."""
    present, mine = open_bus(bus, enable)
    if not present:
        if mine:
            sh(["sudo", "dtparam", "-r"])
        return {}, False
    found = {}
    for addr in range(0x50, 0x58):
        info = eeprom_decode(eeprom_read(bus, addr))
        if info:
            found["0x%02x" % addr] = info
    if mine:
        sh(["sudo", "dtparam", "-r"])
    return found, True


def user_bus_scan(bus, enable=None):
    """What answers on the header's user bus, as (addresses, could be read).

    The same distinction the ID bus makes: a HAT that carries devices
    rather than an EEPROM is invisible on a bus that was never opened."""
    present, mine = open_bus(bus, enable)
    if not present:
        if mine:
            sh(["sudo", "dtparam", "-r"])
        return None, False
    devices = i2c_scan(bus)
    if mine:
        sh(["sudo", "dtparam", "-r"])
    return devices, True


# Soldered-down wired ports: the Pi's own controllers (macb on a Pi 5,
# bcmgenet on a Pi 4, the 3B+'s LAN7800), the Allwinner H3's dwmac-sun8i,
# and stmmaceth, the name the generic DesignWare MAC platform driver
# registers under (drivers/net/ethernet/stmicro/stmmac) on the sunxi
# boards whose device tree does not bind the sun8i glue -- read from the
# kernel source, not from a board here.
SOC_ETHERNET_DRIVERS = ("macb", "bcmgenet", "lan78xx", "dwmac-sun8i", "stmmaceth")
ONBOARD_DRIVERS = SOC_ETHERNET_DRIVERS + ("brcmfmac",)

# The Broadcom-OUI boards (3B+, Zero W and earlier) derive both of their own
# MACs from their serial number, which rpi_hwid.revision says the same way
# for the collecting side and records where it was established.
BROADCOM_OUI = (0xB8, 0x27, 0xEB)


def board_macs(serial):
    """The MACs this board derives for itself, as mac -> "eth" or "wlan".

    This is how an interface on an internal USB bus is told from a dongle
    on an external one. Driver names cannot do it: every Pi before the 3B+
    reaches Ethernet through a USB chip soldered beside the SoC (the
    LAN9512/9514, driver smsc95xx), and that same driver serves removable
    SMSC dongles, so naming it would sweep them in too. A MAC the board
    computes from its own serial is the one thing no dongle can be wearing.

    Safe to ask of any board, without first asking which board it is: a
    derived MAC that no interface has classifies nothing, so a Pi 5 (whose
    MACs follow no such rule) and an Allwinner board (whose own rule is
    U-Boot's, and whose wired port its driver already names) are simply
    left alone. Empty for a serial that is not hex.
    """
    try:
        tail = bytes.fromhex(serial or "")[-3:]
    except ValueError:
        return {}
    if len(tail) != 3:
        return {}
    fmt = "%02x:%02x:%02x:%02x:%02x:%02x"
    return {
        fmt % (BROADCOM_OUI + tuple(bytearray(tail))): "eth",
        fmt % (BROADCOM_OUI + tuple(b ^ 0x55 for b in bytearray(tail))): "wlan",
    }


def net_interfaces(serial=None):
    """Every non-loopback interface: name, MAC, driver, whether onboard.

    Each one also carries `signal`: which piece of evidence settled
    `onboard`, because the same boolean is not equally well established on
    every board and a consumer asserting against it deserves to know which
    it has got.

    derived-mac      the MAC is one this board computes from its serial, so
                     the port is provably the board's own, and provably the
                     eth or wlan one whatever it ended up being called.
    driver           the driver is one that only ever serves something
                     soldered down. Weaker: it is a closed list, so a board
                     with an unusual onboard part falls off the end of it.
    unmatched-mac    the board's derivation rule is in evidence here -- some
                     other interface wears a MAC it produces -- and this is
                     not one of them, which positively establishes a
                     removable adapter.
    no-derived-macs  no interface wears a derived MAC, so the rule is not in
                     evidence and "not onboard" rests on the driver list
                     alone: an absence of evidence, not evidence of absence.

    That last distinction needs the whole list before any one interface can
    be judged, hence the second pass. board_macs() is speculative by design
    -- it will derive a pair from a Pi 5's serial, which follows no such
    rule -- so a miss only means anything once a hit has shown the rule
    applies to this board at all.
    """
    own = board_macs(serial)
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/class/net/*")):
        name = os.path.basename(p)
        if name == "lo":
            continue
        drv = os.path.basename(os.path.realpath(p + "/device/driver")) \
            if os.path.exists(p + "/device/driver") else None
        dev = os.path.realpath(p + "/device") if os.path.exists(p + "/device") else ""
        usb_dev = None
        # the interface directory is "<device>:<config>.<iface>"; the device
        # is what carries the descriptors (a hub in between is not it)
        m = re.search(r"/(\d+-[\d.]+):\d+\.\d+(?:/|$)", dev)
        if m:
            usb_dev = m.group(1)
        mac = read(p + "/address")
        onboard = drv in ONBOARD_DRIVERS
        kind = ("eth" if name.startswith("eth") or drv in SOC_ETHERNET_DRIVERS
                else "wlan" if name.startswith("wl") or drv == "brcmfmac"
                else "other")
        # A MAC this board derives for itself settles both questions at
        # once, and better than the name does: it says the port is the
        # board's own, and which of its two ports it is, whatever the
        # interface ended up being called.
        if mac in own:
            onboard, kind = True, own[mac]
        out.append({"name": name, "mac": mac, "driver": drv,
                    "onboard": onboard, "kind": kind, "usb": usb_dev,
                    "signal": None, "speed": read(p + "/speed")})
    derived = any(i["mac"] in own for i in out)
    for i in out:
        if i["mac"] in own:
            i["signal"] = "derived-mac"
        elif i["onboard"]:
            i["signal"] = "driver"
        else:
            i["signal"] = "unmatched-mac" if derived else "no-derived-macs"
    return out


def usb_net_adapters(ifaces):
    """Removable USB network adapters, with the descriptors a label needs."""
    out = []
    for i in ifaces:
        if i["onboard"] or not i["usb"]:
            continue
        p = ROOT + "/sys/bus/usb/devices/" + i["usb"]
        out.append({
            "iface": i["name"], "mac": i["mac"], "driver": i["driver"],
            "vidpid": "%s:%s" % (read(p + "/idVendor"), read(p + "/idProduct")),
            "manufacturer": read(p + "/manufacturer"), "product": read(p + "/product"),
            "usb_serial": read(p + "/serial"), "bcd_usb": read(p + "/version"),
            "usb_speed": read(p + "/speed"), "kind": "wifi" if i["kind"] == "wlan" else "ethernet",
            # how firmly this was established as removable rather than the
            # board's own; see net_interfaces
            "signal": i["signal"],
        })
    return out


# --- collect ------------------------------------------------------------------

def collect():
    d = {}
    d["model"] = read(ROOT + "/proc/device-tree/model") or ""
    compatible = dt_strings(ROOT + "/proc/device-tree/compatible")
    d["compatible"] = compatible
    d["board"] = board_kind(d["model"], compatible)
    is_pi = d["board"] == "rpi"
    cpuinfo = read(ROOT + "/proc/cpuinfo") or ""
    d["serial"] = read(ROOT + "/proc/device-tree/serial-number")
    m = re.search(r"^Serial\s*:\s*([0-9a-fA-F]+)", cpuinfo, re.M)
    d["cpuinfo_serial"] = m.group(1) if m else None
    # The Allwinner SID: recorded whenever the nvmem is there, and the
    # serial U-Boot builds from it stands in when the device tree and
    # cpuinfo carry nothing (or the all-zero placeholder).
    d["sid"] = sunxi_sid()
    d["sid_serial"] = sunxi_serial(d["sid"]) if d["sid"] else None
    if not d["serial"] or set(d["serial"]) == {"0"}:
        if d["cpuinfo_serial"] and set(d["cpuinfo_serial"]) != {"0"}:
            d["serial"] = d["cpuinfo_serial"]
        elif d["sid_serial"]:
            d["serial"] = d["sid_serial"]
    # The revision code is the Pi firmware's; a device-tree boot on
    # anything else prints a meaningless 0000 there.
    m = re.search(r"^Revision\s*:\s*(\S+)", cpuinfo, re.M)
    d["revision"] = m.group(1) if m and is_pi else None
    d["mem_kb"] = mem_total_kb()
    d["armbian"] = armbian_release()
    d["hat_fw"] = None
    if os.path.isdir(ROOT + "/proc/device-tree/hat"):
        d["hat_fw"] = {k: read(ROOT + "/proc/device-tree/hat/" + k)
                       for k in ("vendor", "product", "product_id", "product_ver", "uuid")}
    # The header's two I2C pairs are HAT evidence on any board that has a
    # 40-pin header, not only a Pi: the fleet's Orange Pi PC carries a
    # Digilent Pmod HAT Adaptor whose ID EEPROM reads exactly like the one
    # on a Pi. Which bus is which is the board's to say (HEADER_BUSES); a
    # board that declares nothing is left alone rather than guessed at.
    header_buses = HEADER_BUSES.get(d["board"])
    if header_buses:
        d["hat_eeproms"], id_read = id_bus_scan(header_buses["id"], header_buses["enable"])
        d["header_i2c"], user_read = user_bus_scan(header_buses["user"],
                                                   header_buses["enable_user"])
        d["header_buses_read"] = {"id": id_read, "user": user_read}
    else:
        d["hat_eeproms"], d["header_i2c"] = {}, None
        # A board that declares no header is not a board whose header went
        # unread: there is nothing here to have missed.
        d["header_buses_read"] = {}
    usb = {}
    for p in glob.glob(ROOT + "/sys/bus/usb/devices/*"):
        v, pr = read(p + "/idVendor"), read(p + "/idProduct")
        if v:
            usb[os.path.basename(p)] = "%s:%s" % (v, pr)
    d["usb"] = usb
    # What the power port itself reports. Every Pi model: the firmware's
    # throttle flags, whose bit 0 is under-voltage now and bit 16
    # under-voltage since boot -- the only thing a 3B+, Zero or Pi 4 can say
    # about the supply on its micro-USB or USB-C. Pi 5: the firmware's USB-C
    # judgement, below. Other boards have no vcgencmd and nothing like it.
    thr = sh(["vcgencmd", "get_throttled"]) if is_pi else ""
    m = re.search(r"0x([0-9a-f]+)", thr)
    t = int(m.group(1), 16) if m else None
    d["throttled"] = ("0x%x" % t) if t is not None else None
    d["undervoltage_now"] = bool(t & 0x1) if t is not None else None
    d["undervoltage_since_boot"] = bool(t & 0x10000) if t is not None else None
    d["interfaces"] = net_interfaces(d["serial"])
    d["usb_net"] = usb_net_adapters(d["interfaces"])
    pi5 = "Pi 5" in d["model"]
    d["pi5"] = pi5
    if pi5:
        d["max_current_ma"] = dt_u32(ROOT + "/proc/device-tree/chosen/power/max_current")
        try:
            with open(ROOT + "/proc/device-tree/chosen/power/usbpd_power_data_objects", "rb") as f:
                raw = f.read()
            pdos = [struct.unpack_from(">I", raw, i)[0] for i in range(0, len(raw) - 3, 4)]
            d["usbpd_pdos"] = ["0x%08x" % x for x in pdos if x]
        except OSError:
            d["usbpd_pdos"] = None
        adc = sh(["sudo", "vcgencmd", "pmic_read_adc"])
        m = re.search(r"EXT5V_V volt\(\d+\)=([0-9.]+)V", adc)
        d["ext5v_v"] = float(m.group(1)) if m else None
        m = re.search(r"BATT_V volt\(\d+\)=([0-9.]+)V", adc)
        d["rtc_batt_v"] = float(m.group(1)) if m else None
        d["fan_dt"] = read(ROOT + "/proc/device-tree/cooling_fan/status")
        d["fan_rpm"] = None
        for h in glob.glob(ROOT + "/sys/class/hwmon/hwmon*"):
            if read(h + "/name") == "pwmfan":
                d["fan_rpm"] = int(read(h + "/fan1_input") or 0)
    return d


# --- verdict ------------------------------------------------------------------

def verdict(d):
    ev, header, power = [], [], None
    is_pi = d.get("board", "rpi") == "rpi"
    if d.get("compatible"):
        ev.append("device tree: compatible %s; %s (MemTotal %s kB)" % (
            " ".join(d["compatible"]), nominal_memory(d.get("mem_kb")) or "memory not read",
            d.get("mem_kb")))
    if d.get("sid"):
        ev.append("Allwinner SID %s -> serial %s%s" % (
            " ".join(d["sid"]), d["sid_serial"],
            "" if d["sid_serial"] == d["serial"] else " (device tree says %s)" % d["serial"]))
    if d.get("armbian"):
        a = d["armbian"]
        ev.append("Armbian %s on board id %s (%s)" % (
            a.get("VERSION", "?"), a.get("BOARD", "?"), a.get("LINUXFAMILY", "?")))
    for addr, e in d["hat_eeproms"].items():
        header.append("%s (HAT EEPROM at %s, pid %s%s)" % (
            e.get("product", "?").strip(), addr, e.get("pid"),
            "" if addr == "0x50" else ", firmware does not read it"))
        if "PoE" in e.get("product", ""):
            power = "PoE HAT on the GPIO header: " + e["product"].strip()
    if d["hat_fw"]:
        fw_name = "%s %s" % (d["hat_fw"]["vendor"], d["hat_fw"]["product"])
        header.append(fw_name + " (firmware-read HAT EEPROM)")
        if "PoE" in (d["hat_fw"]["product"] or ""):
            power = "PoE HAT on the GPIO header: " + fw_name
    # A HAT that carries devices rather than an EEPROM, read off the header's
    # user bus (pins 3/5) whichever bus number that is on this board. The
    # pair is the HAT's own silicon, so it identifies the HAT on any board
    # the HAT fits: a Waveshare PoE HAT (B) is one on an Orange Pi too.
    devices = d.get("header_i2c") or []
    named = None
    if "3c" in devices and "20" in devices:
        named = ("Waveshare PoE HAT (B) "
                 "(SSD1306 at 0x3c + PCF8574 at 0x20 on the header's user bus)")
        power = "PoE HAT on the GPIO header: Waveshare PoE HAT (B)"
    elif set(["29", "40", "70"]) <= set(devices):
        # All three, not the obvious two: 0x40 is the PCA9685's default and
        # also the INA219's, so it discriminates badly on its own, and 0x29
        # is the TSL2591's and also the VL53L0X's. What settles it is 0x70,
        # the PCA9685's All Call address, which it answers *in addition to*
        # its own -- measured on rpi5-pantilt 2026-09-15, where 0x40 and
        # 0x70 return the same MODE1 (0x21) because they are one chip, and
        # 0x29 returns 0x50 from the TSL2591's ID register. A board that has
        # All Call switched off falls through to the raw address list and
        # goes unnamed, which is the right way to be wrong here: a HAT
        # without a name is recoverable, a sticker with the wrong name is not.
        named = ("Waveshare 2-DOF Pan-Tilt HAT "
                 "(PCA9685 at 0x40 answering All Call 0x70, TSL2591 at 0x29)")
    if named:
        header.append(named)
    elif devices:
        ev.append("header user bus devices: " + " ".join(devices))
    usb = d["usb"]
    roots = [k for k, v in usb.items() if v == "1a40:0101" and k.count(".") == 0 and "-" in k]
    for r in roots:
        if usb.get(r + ".4") == "0bda:8152":
            header.append("Waveshare PoE-ETH-USB-HUB-HAT (1a40:0101 hub with RTL8152 on port 4)")
            power = "PoE through the Waveshare PoE-ETH-USB-HUB-HAT bonnet"
    if d["pi5"]:
        mc = d["max_current_ma"]
        ev.append("USB-C as the firmware sees it: max_current %s mA, %s; 5 V input %.2f V" % (
            mc,
            ("PD objects " + " ".join(d["usbpd_pdos"])) if d.get("usbpd_pdos")
            else "no PD contract",
            d["ext5v_v"] or 0))
        if mc in (900, 1500):
            power = power or ("external supply on USB-C advertising %d mA by resistor: "
                              "a PoE splitter or a USB-A lead" % mc)
        elif mc == 5000:
            power = power or "USB-C source with a PD contract: a PD supply or a PD splitter"
        elif mc == 3000 and not power:
            lean = ("5 V input above 5.1 V leans HAT" if (d["ext5v_v"] or 0) > 5.1
                    else "5 V input at or below 5.0 V leans splitter")
            power = ("ambiguous: no PD contract, so either a GPIO-fed HAT without an ID EEPROM "
                     "(Waveshare F/G/H/J) or a 3 A USB-C splitter; " + lean)
        ev.append("fan header: %s%s" % (d["fan_dt"] or "no node",
                  ", %d rpm" % d["fan_rpm"] if d["fan_rpm"] is not None else ""))
        batt = d["rtc_batt_v"] or 0
        ev.append("RTC battery: %s" % ("fitted, %.2f V" % batt if batt > 1.0
                                       else "none (%.2f V)" % batt))
    if d.get("throttled") is not None:
        ev.append("power port: throttled=%s%s%s" % (
            d["throttled"], ", under-voltage NOW" if d["undervoltage_now"] else "",
            ", under-voltage since boot" if d["undervoltage_since_boot"] else ""))
    # "Nothing on the header" and "the header could not be read" are
    # different answers and only one of them rules a HAT out, so a bus that
    # stayed shut is said out loud rather than passed off as an empty one.
    buses = HEADER_BUSES.get(d.get("board", "rpi")) or {}
    read_ok = d.get("header_buses_read") or {}
    unread = [role for role in ("id", "user") if role in buses and not read_ok.get(role)]
    if unread:
        ev.append("header %s bus could not be read (i2c-%s): a HAT known only "
                  "by what answers there cannot be ruled out" % (
                      " and ".join(unread), ", i2c-".join(str(buses[r]) for r in unread)))
    if not power and is_pi:
        power = ("nothing on the Pi distinguishes it: a HAT with no ID EEPROM and no I2C devices "
                 "(Waveshare C, D, E) or an external splitter; use the switch's PD class or look")
    elif not power:
        # A HAT found above still names the supply; this is only what is left
        # when it did not. An H3 has no PMIC and no firmware to ask.
        power = "no power sensing on this board: nothing on it reports its supply"
    if not header:
        header = ["nothing identifiable on the header" if not unread else
                  "nothing identifiable on the header, and it was not fully read"]
    return {"header": header, "power": power,
            "evidence": ev, "summary": summary(d, header, power)}


# HATs identified by what they carry rather than by an ID EEPROM. verdict()
# writes a long line naming the evidence; the summary carries only the short
# name, taken as the prefix of that line. A new signature MUST be added here
# as well as to verdict(), or the HAT is named in the verdict and silently
# absent from the summary, which is the half that labels and the ansible
# assert actually read.
HEADER_SHORT_NAMES = (
    "Waveshare PoE-ETH-USB-HUB-HAT",
    "Waveshare PoE HAT (B)",
    "Waveshare 2-DOF Pan-Tilt HAT",
)


def summary(d, header, power):
    """The verdict in a fixed shape for the audit and the ansible assert:
    short names only, and None where the signal does not exist on this
    model rather than a guess."""
    items = []
    for addr, e in d["hat_eeproms"].items():
        items.append(e.get("product", "?").strip())
    if d["hat_fw"] and (d["hat_fw"]["product"] or "").strip() not in items:
        items.append("%s %s" % (d["hat_fw"]["vendor"], d["hat_fw"]["product"]))
    for h in header:
        for name in HEADER_SHORT_NAMES:
            if h.startswith(name) and name not in items:
                items.append(name)
    if power.startswith("PoE HAT on the GPIO header"):
        pclass = "gpio-poe-hat"
    elif power.startswith("PoE through the Waveshare PoE-ETH-USB-HUB-HAT"):
        pclass = "bonnet-poe"
    elif power.startswith("external supply on USB-C"):
        pclass = "usbc-supply"
    elif power.startswith("USB-C source with a PD contract"):
        pclass = "usbc-pd-supply"
    elif power.startswith("ambiguous"):
        pclass = "ambiguous"
    else:
        pclass = "undetermined"
    macs = [{"kind": i["kind"], "mac": i["mac"], "signal": i.get("signal")}
            for i in d.get("interfaces", [])
            if i["onboard"] and i["kind"] in ("eth", "wlan")]
    usb_net = list(d.get("usb_net", []))
    # Waveshare's PoE-ETH-USB-HUB-HAT gives a Zero its wired port: that
    # RTL8152 (port 4 of the bonnet's hub) is the Pi's eth, not a dongle.
    bonnet_ports = [r + ".4" for r, v in d["usb"].items()
                    if v == "1a40:0101" and "." not in r and "-" in r
                    and d["usb"].get(r + ".4") == "0bda:8152"]
    by_iface = {i["name"]: i for i in d.get("interfaces", [])}
    for u in list(usb_net):
        if by_iface.get(u["iface"], {}).get("usb") in bonnet_ports:
            # "bonnet-hub", not the interface's own signal: this part is the
            # board's own port only because of where it sits in this HAT's
            # hub, and the identical part anywhere else is a dongle.
            macs.insert(0, {"kind": "eth", "mac": u["mac"], "signal": "bonnet-hub"})
            usb_net.remove(u)
    hat_uuid = None
    if d["hat_fw"] and d["hat_fw"].get("uuid"):
        hat_uuid = d["hat_fw"]["uuid"]
    elif d["hat_eeproms"]:
        hat_uuid = list(d["hat_eeproms"].values())[0].get("uuid")
    return {
        "model": d["model"], "serial": d["serial"], "revision": d["revision"],
        "compatible": " ".join(d.get("compatible") or []),
        "memory": nominal_memory(d.get("mem_kb")),
        "header": items, "hat_uuid": hat_uuid, "power_class": pclass,
        "macs": macs, "usb_net": usb_net,
        "rtc_battery": ((d.get("rtc_batt_v") or 0) > 1.0) if d["pi5"] else None,
        "fan": (d.get("fan_dt") == "okay") if d["pi5"] else None,
        "max_current_ma": d.get("max_current_ma") if d["pi5"] else None,
        "ext5v_v": d.get("ext5v_v") if d["pi5"] else None,
    }


def headline(d):
    """The first line of the text report: model, serial, and the revision
    code where the board has one."""
    line = "%s  serial %s" % (d["model"], d["serial"])
    if d.get("revision"):
        line += "  rev %s" % d["revision"]
    return line


def main():
    d = collect()
    v = verdict(d)
    if "--json" in sys.argv:
        d["verdict"] = v
        print(json.dumps(d, indent=1))
        return
    print(headline(d))
    for h in v["header"]:
        print("  header  : " + h)
    for e in v["evidence"]:
        # labelled for its JSON key, so that "signal" is left to mean the
        # one thing it means on an interface line below
        print("  evidence: " + e)
    print("  power   : " + v["power"])
    for i in d["interfaces"]:
        if i["onboard"]:
            print("  onboard : %-6s %s  %s (%s)" % (
                i["kind"], i["mac"], i["driver"], i["signal"]))
    for u in d["usb_net"]:
        print("  usb net : %s %s %s  %s  %s (%s)" % (
            u["vidpid"], u["manufacturer"] or "", u["product"] or "",
            u["mac"], u["kind"], u["signal"]))


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
