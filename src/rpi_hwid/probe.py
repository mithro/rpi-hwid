#!/usr/bin/env python3
"""What is this Raspberry Pi wearing, and what powers it?

This module is deliberately a single, dependency-free file that runs on any
Raspberry Pi with python3 >= 3.5 (needs sudo and i2c-tools), so it can be
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
I2C devices on bus 1
    Waveshare PoE HAT (B) for 3B+/4B has an SSD1306 OLED at 0x3c and a
    PCF8574 fan controller at 0x20. Nothing else in the line-up puts
    anything on bus 1.
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
    then its last four (02:81:e1:ce:7d:46 from 02c00181e1ce7d46 on
    opi1pc-b), so the MAC is not independent evidence. RAM is MemTotal
    rounded up to the fitted size, the Armbian release is
    /etc/armbian-release, and the Pi-only pokes (dtparam, vcgencmd, the ID
    bus, the I2C-1 scan, the bonnet rule) are skipped: an H3 has no PMIC,
    no firmware power report and no HAT convention, so nothing here can
    say what powers it.
"""
import glob
import json
import os
import re
import struct
import subprocess
import sys
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
    return "%d GB" % -(-mib // 1024)          # beyond the table: rounded up


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
    """U-Boot's serial# for a sun8i board from its SID words: the chip-id
    word, then a CRC-32 of words 1-3 (board/sunxi/board.c,
    setup_environment; the low 24 bits are forced non-zero because they
    also become the MAC's NIC bytes). The same string U-Boot writes to the
    device tree's /serial-number."""
    words = [int(w, 16) for w in sid]
    tail = zlib.crc32(struct.pack("<3I", *words[1:4])) & 0xffffffff
    if tail & 0xffffff == 0:
        tail |= 0x800000
    return "%08x%08x" % (words[0], tail)


# --- HAT EEPROM -------------------------------------------------------------

def eeprom_read(bus, addr, length=256):
    out = b""
    for off in range(0, length, 128):
        r = sh(["sudo", "i2ctransfer", "-y", str(bus), "w2@0x%02x" % addr,
                "0x%02x" % (off >> 8), "0x%02x" % (off & 0xff), "r128"])
        if not r or "Error" in r:
            return None
        out += bytes(int(x, 16) for x in r.split())
    return out


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


def id_bus_scan():
    """Find HAT EEPROMs on the ID bus, enabling it at runtime if needed."""
    enabled_here = False
    if not os.path.exists(ROOT + "/dev/i2c-0"):
        sh(["sudo", "dtparam", "i2c_vc=on"])
        enabled_here = os.path.exists(ROOT + "/dev/i2c-0")
        if not enabled_here:
            return {}
    found = {}
    for addr in range(0x50, 0x58):
        info = eeprom_decode(eeprom_read(0, addr))
        if info:
            found["0x%02x" % addr] = info
    if enabled_here:
        sh(["sudo", "dtparam", "-r"])
    return found


# Soldered-down interface drivers: the SoC Ethernet (macb on Pi 5,
# bcmgenet on Pi 4, dwmac-sun8i for the Allwinner H3's EMAC and stmmaceth
# for other Synopsys DWMAC platforms), the onboard SDIO radio (brcmfmac),
# and the 3B+'s LAN7800, which is on an internal USB bus but cannot be
# unplugged.
SOC_ETHERNET_DRIVERS = ("macb", "bcmgenet", "lan78xx", "dwmac-sun8i", "stmmaceth")
ONBOARD_DRIVERS = SOC_ETHERNET_DRIVERS + ("brcmfmac",)


def net_interfaces():
    """Every non-loopback interface: name, MAC, driver, whether onboard."""
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
        onboard = drv in ONBOARD_DRIVERS
        kind = ("eth" if name.startswith("eth") or drv in SOC_ETHERNET_DRIVERS
                else "wlan" if name.startswith("wl") or drv == "brcmfmac"
                else "other")
        out.append({"name": name, "mac": read(p + "/address"), "driver": drv,
                    "onboard": onboard, "kind": kind, "usb": usb_dev,
                    "speed": read(p + "/speed")})
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
        })
    return out


def parse_i2cdetect(text):
    """Addresses that answered, from i2cdetect's grid: each row is
    "R0: xx xx -- ..." and only the cells after the colon count."""
    found = []
    for line in text.split("\n"):
        if ":" not in line:
            continue
        for cell in line.split(":", 1)[1].split():
            if re.match(r"^[0-7][0-9a-f]$", cell):
                found.append(cell)
    return sorted(set(found))


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
    # The ID bus and bus 1 are HAT evidence, and HATs are a Pi convention:
    # on another board a device on bus 1 is the board's own (an Orange Pi
    # PC's CPU regulator), so the scans are skipped rather than misread.
    d["hat_eeproms"] = id_bus_scan() if is_pi else {}
    if is_pi and os.path.exists(ROOT + "/dev/i2c-1"):
        d["i2c1"] = parse_i2cdetect(sh(["sudo", "i2cdetect", "-y", "1"]))
    else:
        d["i2c1"] = None
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
    d["interfaces"] = net_interfaces()
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
    if not is_pi:
        # HATs are a Pi convention, and the supply is unsensed: an H3 has
        # no PMIC and no firmware to report what feeds the board.
        return {"header": ["40-pin header not probed: no HAT ID EEPROM convention on this board"],
                "power": "no power sensing on this board: nothing on it reports its supply",
                "evidence": ev, "summary": summary(d, [], "undetermined")}
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
    i2c1 = d.get("i2c1") or []
    if "3c" in i2c1 and "20" in i2c1:
        header.append("Waveshare PoE HAT (B) (SSD1306 at 0x3c + PCF8574 at 0x20 on bus 1)")
        power = "PoE HAT on the GPIO header: Waveshare PoE HAT (B)"
    elif i2c1:
        ev.append("bus 1 devices: " + " ".join(i2c1))
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
    if not power:
        power = ("nothing on the Pi distinguishes it: a HAT with no ID EEPROM and no I2C devices "
                 "(Waveshare C, D, E) or an external splitter; use the switch's PD class or look")
    return {"header": header or ["nothing identifiable on the header"], "power": power,
            "evidence": ev, "summary": summary(d, header, power)}


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
        bonnet = "Waveshare PoE-ETH-USB-HUB-HAT"
        if h.startswith(bonnet) and bonnet not in items:
            items.append(bonnet)
        if h.startswith("Waveshare PoE HAT (B)") and "Waveshare PoE HAT (B)" not in items:
            items.append("Waveshare PoE HAT (B)")
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
    macs = [{"kind": i["kind"], "mac": i["mac"]} for i in d.get("interfaces", [])
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
            macs.insert(0, {"kind": "eth", "mac": u["mac"]})
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
        print("  header : " + h)
    for e in v["evidence"]:
        print("  signal : " + e)
    print("  power  : " + v["power"])
    for i in d["interfaces"]:
        if i["onboard"]:
            print("  onboard: %-6s %s  %s" % (i["kind"], i["mac"], i["driver"]))
    for u in d["usb_net"]:
        print("  usb net: %s %s %s  %s  %s" % (u["vidpid"], u["manufacturer"] or "",
                                              u["product"] or "", u["mac"], u["kind"]))


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
