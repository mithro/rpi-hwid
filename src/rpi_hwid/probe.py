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
"""
import glob
import json
import os
import re
import struct
import subprocess
import sys
import uuid

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
# bcmgenet on Pi 4), the onboard SDIO radio (brcmfmac), and the 3B+'s
# LAN7800, which is on an internal USB bus but cannot be unplugged.
ONBOARD_DRIVERS = ("macb", "bcmgenet", "brcmfmac", "lan78xx")


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
        kind = ("eth" if name.startswith("eth") or drv in ("macb", "bcmgenet", "lan78xx")
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
    d["serial"] = read(ROOT + "/proc/device-tree/serial-number")
    m = re.search(r"^Revision\s*:\s*(\S+)", read(ROOT + "/proc/cpuinfo") or "", re.M)
    d["revision"] = m.group(1) if m else None
    d["hat_fw"] = None
    if os.path.isdir(ROOT + "/proc/device-tree/hat"):
        d["hat_fw"] = {k: read(ROOT + "/proc/device-tree/hat/" + k)
                       for k in ("vendor", "product", "product_id", "product_ver", "uuid")}
    d["hat_eeproms"] = id_bus_scan()
    if os.path.exists(ROOT + "/dev/i2c-1"):
        d["i2c1"] = parse_i2cdetect(sh(["sudo", "i2cdetect", "-y", "1"]))
    else:
        d["i2c1"] = None
    usb = {}
    for p in glob.glob(ROOT + "/sys/bus/usb/devices/*"):
        v, pr = read(p + "/idVendor"), read(p + "/idProduct")
        if v:
            usb[os.path.basename(p)] = "%s:%s" % (v, pr)
    d["usb"] = usb
    # What the power port itself reports. Every model: the firmware's
    # throttle flags, whose bit 0 is under-voltage now and bit 16
    # under-voltage since boot -- the only thing a 3B+, Zero or Pi 4 can say
    # about the supply on its micro-USB or USB-C. Pi 5: the firmware's USB-C
    # judgement, below.
    thr = sh(["vcgencmd", "get_throttled"])
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
        "header": items, "hat_uuid": hat_uuid, "power_class": pclass,
        "macs": macs, "usb_net": usb_net,
        "rtc_battery": ((d.get("rtc_batt_v") or 0) > 1.0) if d["pi5"] else None,
        "fan": (d.get("fan_dt") == "okay") if d["pi5"] else None,
        "max_current_ma": d.get("max_current_ma") if d["pi5"] else None,
        "ext5v_v": d.get("ext5v_v") if d["pi5"] else None,
    }


def main():
    d = collect()
    v = verdict(d)
    if "--json" in sys.argv:
        d["verdict"] = v
        print(json.dumps(d, indent=1))
        return
    print("%s  serial %s  rev %s" % (d["model"], d["serial"], d["revision"]))
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
