#!/usr/bin/env python3
"""Which software-defined radio is attached to this host?

A separate, dependency-free file like rpi_hwid.fpga and rpi_hwid.tinytapeout,
for the same reason (python3 >= 3.5 on the host, sent over ssh, nothing
installed). Standalone:

    ssh pi@host 'python3 -' --json < src/rpi_hwid/sdr.py

or appended to the Pi probe by ``rpi-hwid collect --sdr``, which merges its
findings into that document's ``verdict.summary.sdr``.

Every radio here is usually held by a live service -- OpenWebRX, the KrakenSDR
DoA software, readsb -- so nothing in this file opens one. It reads sysfs, and
the one tool it runs, ``iio_attr``, it runs against a Pluto's *network* IIO
context, a second client of the Pluto's own iiod that leaves the USB IIO
interface OpenWebRX streams through alone. Established on the fleet's four
radios, 2026-09-26:

RTL2832U (0bda:2838)
    Realtek's demodulator, the RTL-SDR. Its EEPROM gives the USB strings and
    the serial and nothing else; which tuner sits behind it is known only to a
    program that opens it. The RTL-SDR Blog V4 names itself -- librtlsdr
    (rtlsdrblog/rtl-sdr-blog, src/librtlsdr.c) checks for manufacturer
    "RTLSDRBlog", product "Blog V4" -- but a Blog V3 ships with the generic
    strings ("Realtek", "RTL2838UHIDIR", serial "00000001") that every other
    RTL2832U dongle carries, so from here a V3 is an RTL2832U and no more.
KrakenSDR
    Five RTL2832Us on one hub (rpi-sdr-kraken: a Microchip USB2517,
    0424:2517) carrying serials 1000 to 1004. Those five serials are the
    KrakenSDR's convention, not an identity: Heimdall's own EEPROM script
    (krakenrf/heimdall_daq_fw, util/kerberos_eeprom_init.sh) writes
    serial=$((i+1000)), and every configuration it ships names 1000 as the
    control channel (ctr_channel_serial_no = 1000). So five dongles on one hub
    numbered 1000-1004 are one KrakenSDR, and every KrakenSDR reads the same.
ADALM-Pluto (0456:b673)
    The USB serial is the Pluto's own, and the network IIO context says the
    rest: hw_model, hw_serial, fw_version, the AD936x variant and its
    reference clock, and the tuning, sampling and bandwidth ranges the
    driver will accept.
Wavelet Lab usdr family (PCIe 10ee:7049 and kin)
    The usdr_pcie_uram driver's own id table (wavelet-lab/usdr-lib,
    src/lib/lowlevel/pcie_uram/driver/usdr_pcie_uram.c, usdr_pci_table)
    lists every id its cards present, with the device class each maps to in
    driver_data: 7049 and 9049 are class 4, M2_LM7_1_DEVICE_ID in
    src/lib/device/device_ids.h -- the LMS7002M M.2 cards, of which the
    XSDR is one (XTRX and SSDR are the others; only the card's HWID
    register, read by opening it, says which). They are Xilinx ids, so
    rpi_hwid.fpga takes care not to call one an unknown FPGA. The PCIe
    Device Serial Number is reported as read, but on rpi-sdr-xsdr it is
    00-00-00-00-12-34-56-78: the Xilinx core's placeholder, the same on
    every card.
"""
import glob
import json
import os
import re
import subprocess
import sys

# Prefix for every absolute path read; the tests point it at a fake tree.
SDR_ROOT = ""

RTL_IDS = ("0bda:2838", "0bda:2832")
PLUTO_IDS = ("0456:b673",)
# The KrakenSDR's channel serials, in channel order (see above).
KRAKEN_SERIALS = ("1000", "1001", "1002", "1003", "1004")

# usdr_pcie_uram's usdr_pci_table: PCIe device id -> driver_data, the index
# into its s_uuid table. Only the classes usdr-lib names in device_ids.h are
# given a family; the rest are the driver's all the same.
USDR_PCIE = {
    "10ee:7031": 0, "10ee:7032": 0, "10ee:7044": 1, "10ee:7045": 2,
    "10ee:7046": 3, "10ee:7049": 4, "10ee:9049": 4, "10ee:9034": 5,
    "10ee:9044": 5, "10ee:7071": 6,
}
USDR_FAMILY = {1: "m2_lm6_1", 4: "m2_lm7_1", 5: "m2_dsdr", 6: "pe_sync"}

# Where a Pluto's USB network link finds it when nobody changed config.txt.
PLUTO_DEFAULT_ADDR = "192.168.2.1"


def sdr_sh(args, timeout=15):
    """Run a fixed argument list (never a shell): (returncode, out, err)."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)   # 3.5-safe
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 127, "", str(exc)


def sdr_read(path):
    try:
        with open(path, "rb") as f:
            return f.read().rstrip(b"\0").decode("ascii", "replace").strip()
    except OSError:
        return None


def sdr_usb_devices():
    """Every USB device, with its descriptors and the hub it hangs off."""
    out = []
    base = SDR_ROOT + "/sys/bus/usb/devices/"
    for p in sorted(glob.glob(base + "*")):
        name = os.path.basename(p)
        vid, pid = sdr_read(p + "/idVendor"), sdr_read(p + "/idProduct")
        if not vid or not pid or ":" in name:
            continue
        parent = name.rsplit(".", 1)[0] if "." in name else None
        pvid = sdr_read(base + parent + "/idVendor") if parent else None
        ppid = sdr_read(base + parent + "/idProduct") if parent else None
        out.append({
            "path": name, "id": "%s:%s" % (vid, pid),
            "manufacturer": sdr_read(p + "/manufacturer"),
            "product": sdr_read(p + "/product"), "serial": sdr_read(p + "/serial"),
            "bcd_device": sdr_read(p + "/bcdDevice"), "speed": sdr_read(p + "/speed"),
            "parent": parent,
            "parent_id": "%s:%s" % (pvid, ppid) if pvid and ppid else None,
            "net": sorted(os.path.basename(n) for n in glob.glob(p + ":*/net/*")),
        })
    return out


def sdr_pcie_devices():
    """The PCIe endpoints the usdr driver serves, with their link and DSN."""
    out = []
    for p in sorted(glob.glob(SDR_ROOT + "/sys/bus/pci/devices/*")):
        vend, dev = sdr_read(p + "/vendor") or "", sdr_read(p + "/device") or ""
        pid = "%s:%s" % (vend[2:], dev[2:])
        if pid not in USDR_PCIE:
            continue
        drv = os.path.realpath(p + "/driver") if os.path.exists(p + "/driver") else None
        # sysfs serial_number is root-only (0400); sudo -n, so a host without
        # passwordless sudo reports it unread rather than prompting
        rc, dsn, err = sdr_sh(["sudo", "-n", "cat", p + "/serial_number"])
        out.append({
            "slot": os.path.basename(p), "id": pid,
            "subsystem": "%s:%s" % ((sdr_read(p + "/subsystem_vendor") or "0x????")[2:],
                                    (sdr_read(p + "/subsystem_device") or "0x????")[2:]),
            "class": sdr_read(p + "/class"), "revision": sdr_read(p + "/revision"),
            "driver": os.path.basename(drv) if drv else None,
            "dsn": dsn.strip() if rc == 0 and dsn.strip() else None,
            "dsn_error": None if rc == 0 else (err.strip() or "rc %d" % rc),
            "link_speed": sdr_read(p + "/current_link_speed"),
            "link_width": sdr_read(p + "/current_link_width"),
            "max_link_speed": sdr_read(p + "/max_link_speed"),
            "max_link_width": sdr_read(p + "/max_link_width"),
        })
    return out


def iio_range(text):
    """'[325000000 1 3800000000]' -> [325000000, 3800000000]; else None."""
    m = re.match(r"^\s*\[\s*(\d+)\s+\d+\s+(\d+)\s*\]\s*$", text or "")
    return [int(m.group(1)), int(m.group(2))] if m else None


def iio_context_attrs(text):
    """The `name: value` lines of `iio_attr -C`, as a dict."""
    out = {}
    for line in (text or "").splitlines()[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            out[k.strip()] = v.strip()
    return out


IIO_CHANNEL = re.compile(r"channel '(voltage\d+)' \((input|output), index: \d+, "
                         r"format: [bl]e:[SU](\d+)/")


def iio_channels(text):
    """Scan-element voltage channels in an `iio_attr -c DEV` listing:
    [(name, direction, bits)]."""
    return [m.groups() for m in IIO_CHANNEL.finditer(text or "")]


def pluto_address(dev):
    """The Pluto's IPv4 address on its USB network link, from the host's
    neighbour table: (address, None) or (None, why)."""
    if not dev["net"]:
        return None, "no network interface on the Pluto's USB device"
    iface = dev["net"][0]
    rc, out, err = sdr_sh(["ip", "-4", "neigh", "show", "dev", iface])
    for line in out.splitlines():
        addr = line.split()[0] if line.split() else ""
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", addr) and "FAILED" not in line:
            return addr, None
    return None, "no IPv4 neighbour on %s%s" % (iface, (": " + err.strip()) if err.strip()
                                               else "")


def pluto_iio(uri):
    """What a Pluto's network IIO context says of itself: attributes only,
    never a buffer."""
    def ask(*args):
        rc, out, err = sdr_sh(["iio_attr", "-u", uri] + list(args))
        return out if rc == 0 else None

    ctx = ask("-C")
    if ctx is None:
        return {"error": "iio_attr -u %s -C gave no answer" % uri}
    attrs = iio_context_attrs(ctx)
    res = {"uri": uri, "context": attrs}
    for key, args in (
            ("rx_lo_hz", ("-c", "ad9361-phy", "altvoltage0", "frequency_available")),
            ("tx_lo_hz", ("-c", "ad9361-phy", "altvoltage1", "frequency_available")),
            ("rx_rate_hz", ("-c", "-i", "ad9361-phy", "voltage0",
                            "sampling_frequency_available")),
            ("tx_rate_hz", ("-c", "-o", "ad9361-phy", "voltage0",
                            "sampling_frequency_available")),
            ("rx_bw_hz", ("-c", "-i", "ad9361-phy", "voltage0", "rf_bandwidth_available")),
            ("tx_bw_hz", ("-c", "-o", "ad9361-phy", "voltage0", "rf_bandwidth_available"))):
        res[key] = iio_range(ask(*args))
    rx = [c for c in iio_channels(ask("-c", "cf-ad9361-lpc")) if c[1] == "input"]
    tx = [c for c in iio_channels(ask("-c", "cf-ad9361-dds-core-lpc")) if c[1] == "output"]
    # the scan elements are I and Q: two to a channel
    res["rx_channels"] = len(rx) // 2 if rx else None
    res["tx_channels"] = len(tx) // 2 if tx else None
    res["adc_bits"] = int(rx[0][2]) if rx else None
    return res


def link_text(p):
    """'5.0 GT/s x1 (card x2)': the link as trained, and the card's own
    width where the slot narrowed it."""
    speed = (p.get("link_speed") or "").replace(" PCIe", "")
    if not speed:
        return None
    text = "%s x%s" % (speed, p.get("link_width") or "?")
    if p.get("max_link_width") and p["max_link_width"] != p.get("link_width"):
        text += " (card x%s)" % p["max_link_width"]
    return text


def sdr_verdict(d):
    """Name the radios this host has, from USB and PCIe."""
    devices = []
    rtl = [u for u in d["usb"] if u["id"] in RTL_IDS]
    # A KrakenSDR: five RTL2832Us sharing one hub, numbered 1000-1004.
    by_hub = {}
    for u in rtl:
        by_hub.setdefault(u["parent"], []).append(u)
    kraken = set()
    for hub, members in sorted(by_hub.items(), key=lambda kv: kv[0] or ""):
        serials = sorted(u["serial"] or "" for u in members)
        if hub and tuple(serials) == KRAKEN_SERIALS:
            kraken.update(u["path"] for u in members)
            devices.append({
                "kind": "krakensdr", "usb": hub, "vidpid": members[0]["id"],
                "hub": members[0]["parent_id"],
                "channel_serials": list(KRAKEN_SERIALS),
                "channels": {u["serial"]: u["path"] for u in members},
                "how": "five RTL2832U on hub %s (%s), serials 1000-1004" % (
                    hub, members[0]["parent_id"])})
    for u in rtl:
        if u["path"] in kraken:
            continue
        devices.append({
            "kind": "rtl-sdr", "usb": u["path"], "vidpid": u["id"],
            "usb_serial": u["serial"], "manufacturer": u["manufacturer"],
            "product": u["product"],
            "how": "USB %s %s %s at %s" % (u["id"], u["manufacturer"] or "",
                                          u["product"] or "", u["path"])})
    for u in d["usb"]:
        if u["id"] not in PLUTO_IDS:
            continue
        dev = {"kind": "pluto", "usb": u["path"], "vidpid": u["id"],
               "usb_serial": u["serial"], "manufacturer": u["manufacturer"],
               "product": u["product"],
               "how": "USB %s %s at %s" % (u["id"], u["product"] or "", u["path"])}
        iio = (d.get("iio") or {}).get(u["path"]) or {}
        if iio.get("error"):
            dev["iio_error"] = iio["error"]
        elif iio:
            ctx = iio["context"]
            dev.update({
                "iio_uri": iio["uri"], "hw_model": ctx.get("hw_model"),
                "hw_model_variant": ctx.get("hw_model_variant"),
                "hw_serial": ctx.get("hw_serial"), "fw_version": ctx.get("fw_version"),
                "rf_chip": ctx.get("ad9361-phy,model"),
                "xo_hz": int(ctx["ad9361-phy,xo_correction"])
                if (ctx.get("ad9361-phy,xo_correction") or "").isdigit() else None})
            for k in ("rx_lo_hz", "tx_lo_hz", "rx_rate_hz", "tx_rate_hz", "rx_bw_hz",
                      "tx_bw_hz", "rx_channels", "tx_channels", "adc_bits"):
                dev[k] = iio.get(k)
            dev["how"] += "; IIO context %s" % iio["uri"]
        devices.append(dev)
    for p in d["pcie"]:
        family = USDR_FAMILY.get(USDR_PCIE[p["id"]])
        devices.append({
            "kind": "usdr", "slot": p["slot"], "pcie_id": p["id"],
            "pcie_subsystem": p["subsystem"], "usdr_family": family,
            "driver": p["driver"], "pcie_dsn": p["dsn"], "pcie_link": link_text(p),
            "how": "PCIe %s at %s (usdr_pcie_uram class %d%s)%s" % (
                p["id"], p["slot"], USDR_PCIE[p["id"]],
                (", " + family) if family else "",
                (", bound to " + p["driver"]) if p["driver"] else ", no driver bound")})
    return devices


SDR_SUMMARY_KEYS = (
    "kind", "vidpid", "usb_serial", "manufacturer", "product", "channel_serials", "hub",
    "pcie_id", "pcie_subsystem", "usdr_family", "driver", "pcie_dsn", "pcie_link",
    "iio_uri", "hw_model", "hw_model_variant", "hw_serial", "fw_version", "rf_chip",
    "xo_hz", "rx_lo_hz", "tx_lo_hz", "rx_rate_hz", "tx_rate_hz", "rx_bw_hz", "tx_bw_hz",
    "rx_channels", "tx_channels", "adc_bits")


def sdr_summary(devices):
    """Only the identity and capability keys, and only those with a value."""
    out = []
    for dev in devices:
        out.append({k: dev[k] for k in SDR_SUMMARY_KEYS
                    if dev.get(k) is not None and dev.get(k) != ""})
    return out


def collect_sdr():
    s = {"usb": sdr_usb_devices(), "pcie": sdr_pcie_devices(), "iio": {}}
    for u in s["usb"]:
        if u["id"] in PLUTO_IDS:
            addr, why = pluto_address(u)
            s["iio"][u["path"]] = pluto_iio("ip:" + addr) if addr else {"error": why}
    s["devices"] = sdr_verdict(s)
    s["summary"] = sdr_summary(s["devices"])
    return s


def merge_sdr(doc, s):
    """Fold an sdr document into a Pi probe document (in place)."""
    doc["sdr"] = {k: s[k] for k in ("usb", "pcie") if k in s}
    if "iio" in s:
        doc["sdr"]["iio"] = s["iio"]
    doc["verdict"]["sdr"] = s["devices"]
    doc["verdict"]["summary"]["sdr"] = s["summary"]
    return doc


def sdr_describe(devices):
    for dev in devices:
        print("  sdr    : %s (%s)" % (dev["kind"], dev["how"]))
    if not devices:
        print("  sdr    : none found")


def sdr_main():
    s = collect_sdr()
    if "--json" in sys.argv:
        print(json.dumps(s, indent=1))
        return
    sdr_describe(s["devices"])


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    sdr_main()
