#!/usr/bin/env python3
"""Which software-defined radio is attached to this host?

A separate, dependency-free file like rpi_hwid.fpga and rpi_hwid.tinytapeout,
for the same reason (python3 >= 3.5 on the host, sent over ssh, nothing
installed). Standalone:

    ssh pi@host 'python3 -' --json < src/rpi_hwid/sdr.py

or appended to the Pi probe by ``rpi-hwid collect --sdr``, which merges its
findings into that document's ``verdict.summary.sdr``.

Every radio here is usually held by a live service -- OpenWebRX, the KrakenSDR
DoA software, readsb -- so by default nothing in this file opens one. It reads
sysfs, and the one tool it runs, ``iio_attr``, it runs against a Pluto's
*network* IIO context, a second client of the Pluto's own iiod that leaves the
USB IIO interface OpenWebRX streams through alone. With --sdr-open, and only
on a card nothing holds, it also opens a usdr card the way its own tools do
(usdr_dm_sensors, usdr_flash: both read-only) for what only an open card says.
Established on the fleet's four radios, 2026-09-26:

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
            "busnum": sdr_read(p + "/busnum"), "devnum": sdr_read(p + "/devnum"),
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
        cfg = sdr_read_bytes(p + "/config", 8)
        out.append({
            "slot": os.path.basename(p), "id": pid,
            "health": usdr_health(cfg, bool(drv)),
            "usdr_node": usdr_node(os.path.basename(p)),
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


def sdr_read_bytes(path, n):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return None


def usdr_health(cfg, bound):
    """Why a usdr card is not answering, from the first bytes of its config
    space (user-readable in sysfs), or None when it looks alive.

    Measured on rpi-sdr-xsdr 2026-09-26: the card silently dropped off its
    link some days after the driver had brought it up -- no dmesg line, the
    endpoint still listed, but its command register back to Mem- BusMaster-
    and the library reading HWID ffffffff. A reboot brought it back. A
    config read of a dead link answers all ones.
    """
    if not cfg or len(cfg) < 6:
        return "config space unreadable"
    vendor = cfg[0] | (cfg[1] << 8)
    command = cfg[4] | (cfg[5] << 8)
    if vendor == 0xFFFF:
        return ("card not answering: its config space reads all ones (link down); "
                "reboot the host, or power-cycle it if that does not bring it back")
    if bound and not command & 0x6:
        return ("card not answering: the driver is bound but the card's memory and "
                "bus-master enables are clear, so it has reset or dropped its link since "
                "the driver brought it up; reboot the host, or power-cycle it")
    return None


def usdr_node(slot):
    """The /dev/usdrN the driver made for the card at `slot`, or None."""
    for d in sorted(glob.glob(SDR_ROOT + "/sys/class/usdr/usdr*")):
        if os.path.basename(os.path.realpath(d + "/device")) == slot:
            return "/dev/" + os.path.basename(d)
    return None


USDR_HWID = re.compile(r"\[XDEV\]\s+HWID\s+([0-9a-fA-F]{8})")
USDR_FLASH_ID = re.compile(r"Flash ID id ([0-9a-fA-F]{8})")
USDR_FW = re.compile(r"Actual firmware in use:\s+FirmwareID ([0-9a-fA-F]{8})")
USDR_IMAGE = re.compile(r"(Golden|Master) image: DEVID ([0-9a-fA-F]{8}) "
                        r"FirmwareID ([0-9a-fA-F]{8})")


def usdr_open(p):
    """Open the card the way its own tools do and read what only an open
    card says: the HWID register (which LMS7002M card this is), the
    configuration flash's JEDEC id and the images in it. Only with
    --sdr-open, only on a healthy card, and only when nothing holds it:
    OpenWebRX opens the card when a listener connects, and a second open
    would take it from under them."""
    if p["health"]:
        return {"error": p["health"]}
    if not p["usdr_node"]:
        return {"error": "no /dev/usdr node for %s: driver not bound" % p["slot"]}
    rc, out, err = sdr_sh(["sudo", "-n", "fuser", p["usdr_node"]])
    if rc == 0:
        return {"error": "%s is held by pid %s: not opened" % (p["usdr_node"], out.split())}
    if err.strip() and "sudo" in err:
        return {"error": "sudo -n fuser: %s" % err.strip()}
    res = {}
    rc, out, err = sdr_sh(["sudo", "-n", "usdr_dm_sensors", "-l", "3"], timeout=60)
    m = USDR_HWID.search(out + err)
    if m:
        res["hwid"] = m.group(1).lower()
    rc, out, err = sdr_sh(["sudo", "-n", "usdr_flash"], timeout=60)
    text = out + err
    m = USDR_FLASH_ID.search(text)
    if m:
        # usdr reads RDID as a little-endian word: 1f16421f is 1f 42 16
        raw = m.group(1).lower()
        res["flash_jedec"] = "0x" + raw[6:8] + raw[4:6] + raw[2:4]
    m = USDR_FW.search(text)
    if m:
        res["firmware_id"] = m.group(1).lower()
    for kind, devid, fwid in USDR_IMAGE.findall(text):
        res[kind.lower() + "_image"] = {"devid": devid.lower(), "firmware_id": fwid.lower()}
    if res.get("flash_jedec") == "0x1f4216" and res.get("hwid") != "ffffffff":
        usdr_esn(res)
    if res.get("hwid") == "ffffffff":
        res["error"] = ("card not answering: HWID reads ffffffff; reboot the host, or "
                        "power-cycle it if that does not bring it back")
        del res["hwid"]
    return res


RTL_TUNER = re.compile(r"Found (.+?) tuner")
RTL_EEPROM_FIELD = re.compile(r"^(Manufacturer|Product|Serial number|Serial number enabled|"
                              r"IR endpoint enabled|Remote wakeup enabled):\s*(.*?)\s*$", re.M)


def usb_node(u):
    try:
        return "/dev/bus/usb/%03d/%03d" % (int(u["busnum"]), int(u["devnum"]))
    except (TypeError, ValueError, KeyError):
        return None


# Every RTL2832U librtlsdr can see, in its own index order, by serial. The
# tools' `-d` goes through verbose_device_search, which takes an all-digit
# string for an index before it tries serials -- and every serial on this
# fleet is all digits: "00000001" asked for device #1 on a host with one
# dongle (rpi-sdr-rtlsdr-v3, 2026-09-26), and a KrakenSDR's "1000" would ask
# for device #1000. So the serial is resolved to an index here, through
# librtlsdr itself, and only the index is passed on.
RTL_INDEX_READER = r"""
import ctypes, ctypes.util, json
lib = ctypes.CDLL(ctypes.util.find_library("rtlsdr") or "librtlsdr.so.0")
serials = []
for i in range(lib.rtlsdr_get_device_count()):
    m, p, s = (ctypes.create_string_buffer(256) for _ in range(3))
    lib.rtlsdr_get_device_usb_strings(i, m, p, s)
    serials.append(s.value.decode("ascii", "replace"))
print(json.dumps({"serials": serials}))
"""


def rtl_index(serial):
    """(librtlsdr's index for the one dongle with `serial`, None) or
    (None, why)."""
    rc, out, err = sdr_sh(["python3", "-c", RTL_INDEX_READER], timeout=30)
    try:
        serials = json.loads(out.strip().splitlines()[-1])["serials"]
    except (ValueError, IndexError, KeyError):
        return None, "librtlsdr's device list: %s" % ((err.strip() or "no answer")[-200:])
    found = [i for i, s in enumerate(serials) if s == serial]
    if len(found) != 1:
        return None, ("%d dongles answer serial %s, so it names none of them" % (
            len(found), serial))
    return found[0], None


def rtl_open(u, kraken=False):
    """Ask librtlsdr about one RTL2832U -- `rtl_eeprom -d SERIAL`, which with
    no write flag only reads -- for the tuner and the EEPROM's fields. Only
    with --sdr-open, and only when nothing holds the device: readsb, OpenWebRX
    and Heimdall keep theirs open for as long as they run."""
    node = usb_node(u)
    if not node or not u.get("serial"):
        return {"error": "no usbfs node or no serial to select it by"}
    rc, out, err = sdr_sh(["sudo", "-n", "fuser", node])
    if rc == 0:
        return {"error": "%s is held by pid %s: not opened" % (node, out.split())}
    idx, why = rtl_index(u["serial"])
    if idx is None:
        return {"error": why}
    rc, out, err = sdr_sh(["rtl_eeprom", "-d", str(idx)], timeout=30)
    text = out + err
    res = {}
    m = RTL_TUNER.search(text)
    if m:
        res["tuner"] = m.group(1)
    fields = dict(RTL_EEPROM_FIELD.findall(text))
    if fields:
        res["eeprom"] = fields
    if res.get("tuner") == "Rafael Micro R820T" and not kraken:
        # the V3's own feature: HF wired into the Q branch (see sdr_verdict)
        res["direct_sampling"] = rtl_direct_sampling(idx)
    if not res:
        res["error"] = "rtl_eeprom -d %d: %s" % (idx, (text.strip() or "rc %d" % rc)
                                                 .splitlines()[-1])
    return res

# The XSDR configuration flash's ESN, read through libusdr's own espi core.
# Run as root in a process of its own, so the device is closed on exit
# whatever happens. Mirrors usdr_flash's own id read; see rpi_hwid.sdr.
USDR_ESN_READER = r"""
import ctypes, json, sys
L = ctypes.CDLL("libusdr.so.0")
V = ctypes.c_void_p
L.lowlevel_create.argtypes = [ctypes.c_uint, V, V, ctypes.POINTER(V), ctypes.c_uint, V,
                              ctypes.c_size_t]
L.lowlevel_get_ops.restype = V
L.lowlevel_get_ops.argtypes = [V]
L.lowlevel_get_device.restype = V
L.lowlevel_get_device.argtypes = [V]
L.usdr_device_vfs_obj_val_get_u64.argtypes = [V, ctypes.c_char_p,
                                               ctypes.POINTER(ctypes.c_uint64)]
L.espi_flash_get_id.argtypes = [V, ctypes.c_uint64, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_uint32), ctypes.c_char_p,
                                ctypes.c_size_t]
L.espi_flash_read.argtypes = [V, ctypes.c_uint64, ctypes.c_uint, ctypes.c_uint,
                              ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p]
LSOP = ctypes.CFUNCTYPE(ctypes.c_int, V, ctypes.c_uint64, ctypes.c_uint, ctypes.c_uint,
                        ctypes.c_size_t, V, ctypes.c_size_t, V)
out = {}
dev = V()
if L.lowlevel_create(0, None, None, ctypes.byref(dev), 0, None, 0):
    print(json.dumps({"error": "lowlevel_create failed"})); sys.exit(0)
# struct lowlevel_ops: generic_get, then ls_op
ls_op = LSOP(ctypes.cast(L.lowlevel_get_ops(dev), ctypes.POINTER(V))[1])
base = ctypes.c_uint64(10)
L.usdr_device_vfs_obj_val_get_u64(L.lowlevel_get_device(dev), b"/ll/qspi_flash/base",
                                  ctypes.byref(base))
base = base.value
def wr(addr, v):
    w = ctypes.c_uint32(v)
    return ls_op(dev, 0, 0, addr, 0, None, 4, V(ctypes.addressof(w)))
def rd(addr):
    r = ctypes.c_uint32(0)
    res = ls_op(dev, 0, 0, addr, 4, V(ctypes.addressof(r)), 0, None)
    return res, r.value
def done():
    for _ in range(100000):
        res, st = rd(base + 0)
        if res or not st & 1:
            return res
    return -110
def cmd(op, sz):
    # MAKE_ESPI_CORE_CMD(op, sz, 0, 0, 0, 0): espi_flash.c's WREN/RDSR shape
    return wr(base + 0, (op << 24) | (sz << 16)) or done()
def reg8(op):
    res = cmd(op, 4)
    res2, v = rd(base + 1)
    return res or res2, v & 0xFF
def flash(off, n):
    b = ctypes.create_string_buffer(n)
    return L.espi_flash_read(dev, 0, base, 512, off, n, b), bytearray(b.raw)
fid = ctypes.c_uint32(0)
name = ctypes.create_string_buffer(64)
L.espi_flash_get_id(dev, 0, base, ctypes.byref(fid), name, 64)
out["rdid32"] = "%08x" % fid.value
if fid.value & 0xFFFFFF != 0x16421F:
    out["error"] = "not an AT25SL321: ESN not read"
    print(json.dumps(out)); sys.exit(0)
res, sr = reg8(0x05)
res2, scur = reg8(0x2B)
if res or res2 or sr & 1:
    out["error"] = "status %02x: busy or unreadable, ESN not read" % sr
    print(json.dumps(out)); sys.exit(0)
out["security"] = "%02x" % scur
r0, before = flash(0, 16)
e = cmd(0xB1, 0)                                   # Enter Secured OTP
r1, esn = flash(0, 16) if not e else (e, b"")
x = cmd(0xC1, 0)                                   # Exit Secured OTP, always
r2, after = flash(0, 16)
out.update(enso=e, exso=x, read=r1)
if r0 or r2 or before != after:
    out["error"] = "MAIN ARRAY DOES NOT READ BACK AS BEFORE: power-cycle the card"
elif not (e or r1 or x):
    out["esn"] = "".join("%02x" % c for c in esn)
print(json.dumps(out))
"""


def usdr_esn(res):
    """The AT25SL321's 128-bit ESN, from its secured OTP area, into `res`.

    The part (JEDEC 1f 42 16, "at25sl321" in Linux's spi-nor atmel.c) has no
    Read Unique ID command. Its datasheet (Renesas DS-AT25SL321-112 Rev. K,
    8.41 and Table 17) puts a "128-bit ESN (Electrical Serial Number)" at
    000000-00000F of a separate 4-kbit secured OTP area, reached by Enter
    Secured OTP (B1h), a normal read and Exit Secured OTP (C1h), and says
    security register (2Bh) bit 0 shows whether the factory locked it. Only
    the mode switch is sent -- volatile, no write-enable, no program, no
    erase -- and the reader proves the main array reads back unchanged
    after it. rpi-sdr-xsdr's, 2026-09-26: 19 04 02 03 09 0e 97 69 then
    eight bytes of ff, factory lock 0.
    """
    rc, out, err = sdr_sh(["sudo", "-n", "python3", "-c", USDR_ESN_READER], timeout=60)
    try:
        got = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        res["flash_uid_error"] = "ESN reader: %s" % (err.strip() or "no answer")[-300:]
        return
    if got.get("error") or not got.get("esn"):
        res["flash_uid_error"] = got.get("error") or "ESN reader gave no ESN"
        return
    scur = int(got["security"], 16)
    res["flash_uid"] = got["esn"]
    res["flash_uid_state"] = "read"
    res["flash_uid_note"] = ("AT25SL321 secured-OTP ESN; security register 0x%02x: factory "
                             "lock %d, customer lock %d" % (scur, scur & 1, (scur >> 1) & 1))

# One RTL2832U's direct-sampling inputs, compared: half a second from the I
# branch (mode 1) and half from the Q branch (mode 2), at 14 MHz, through
# librtlsdr's rtlsdr_read_sync with a fixed byte count. In a process of its
# own so a stuck USB read is killed by the timeout, not waited on.
RTL_DS_READER = r"""
import ctypes, ctypes.util, json, math, sys
lib = ctypes.CDLL(ctypes.util.find_library("rtlsdr") or "librtlsdr.so.0")
idx = int(sys.argv[1])
dev = ctypes.c_void_p()
if idx < 0 or lib.rtlsdr_open(ctypes.byref(dev), idx):
    print(json.dumps({"error": "could not open %s" % sys.argv[1]})); sys.exit(0)
out = {}
try:
    lib.rtlsdr_set_sample_rate(dev, 2400000)
    for mode, name in ((1, "i_rms"), (2, "q_rms")):
        lib.rtlsdr_set_direct_sampling(dev, mode)
        lib.rtlsdr_set_center_freq(dev, 14000000)
        lib.rtlsdr_reset_buffer(dev)
        n = ctypes.c_int(0)
        buf = ctypes.create_string_buffer(2400000)
        lib.rtlsdr_read_sync(dev, buf, 2400000, ctypes.byref(n))
        v = bytearray(buf.raw[:n.value])[600000::2]
        if len(v) < 100000:
            out["error"] = "short read in mode %d" % mode
            break
        mean = sum(v) / float(len(v))
        out[name] = round(math.sqrt(sum((x - mean) ** 2 for x in v) / len(v)), 3)
    lib.rtlsdr_set_direct_sampling(dev, 0)
finally:
    lib.rtlsdr_close(dev)
print(json.dumps(out))
"""
# The Q branch's signal must stand this far clear of the unconnected I
# branch's ADC noise to count as wired. Measured on rpi-sdr-rtlsdr-v3,
# 2026-09-26, at 14 MHz: I 0.46, Q 2.32 (and at 1 and 7.1 MHz, Q 1.14 and
# 1.76 against I 0.50 and 0.49 -- rising with frequency, as antenna noise
# through a 24 MHz low-pass does, where the I branch stays flat).
DS_Q_OVER_I = 3.0


def rtl_direct_sampling(idx):
    rc, out, err = sdr_sh(["python3", "-c", RTL_DS_READER, str(idx)], timeout=30)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": "direct-sampling read: %s" % ((err.strip() or "no answer")[-200:])}


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


def rtl_tuner(d, members):
    """The tuner the open read found behind these RTL2832Us, when every one
    of them answered and they agree; else None."""
    read = [((d.get("rtl_open") or {}).get(u["path"]) or {}).get("tuner") for u in members]
    return read[0] if read and read[0] and len(set(read)) == 1 else None


def rtl_model(d, u):
    """'rtl-sdr-blog-v3' where the evidence says so, else None.

    A Blog V3's EEPROM is the generic one, so it is told by its hardware.
    Its datasheet (rtl-sdr.com RTL-SDR-Blog-V3-Datasheet.pdf): "The V3 has
    direct sampling mode implemented in hardware already, so no hardware
    mods are required", HF diplexed off the SMA into the RTL2832U's Q
    branch, where "on typical R820T RTL-SDR dongles one can enable direct
    sampling mode by soldering a wire to the Q-branch pins". So an R820T
    dongle whose Q branch carries signal and whose I branch does not is a
    V3 (or one modified to be like one)."""
    read = (d.get("rtl_open") or {}).get(u["path"]) or {}
    ds = read.get("direct_sampling") or {}
    if read.get("tuner") != "Rafael Micro R820T" or not ds.get("i_rms") or not ds.get("q_rms"):
        return None
    return "rtl-sdr-blog-v3" if ds["q_rms"] >= DS_Q_OVER_I * ds["i_rms"] else None


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
                "tuner": rtl_tuner(d, members),
                "how": "five RTL2832U on hub %s (%s), serials 1000-1004" % (
                    hub, members[0]["parent_id"])})
    for u in rtl:
        if u["path"] in kraken:
            continue
        devices.append({
            "kind": "rtl-sdr", "usb": u["path"], "vidpid": u["id"],
            "usb_serial": u["serial"], "manufacturer": u["manufacturer"],
            "product": u["product"], "tuner": rtl_tuner(d, [u]),
            "rtl_model": rtl_model(d, u),
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
        opened = (d.get("usdr_open") or {}).get(p["slot"]) or {}
        golden = opened.get("golden_image") or {}
        devices.append({
            "usdr_hwid": opened.get("hwid"), "flash_jedec": opened.get("flash_jedec"),
            "flash_uid": opened.get("flash_uid"),
            "flash_uid_state": opened.get("flash_uid_state"),
            "flash_uid_note": opened.get("flash_uid_note"),
            "flash_uid_error": opened.get("flash_uid_error"),
            "fpga_devid": golden.get("devid"), "usdr_images": {
                k: opened[k] for k in ("firmware_id", "golden_image", "master_image")
                if k in opened} or None,
            "usdr_error": p["health"] or opened.get("error"),
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
    "rx_channels", "tx_channels", "adc_bits", "usdr_hwid", "flash_jedec", "fpga_devid",
    "usdr_error", "tuner", "flash_uid", "flash_uid_state", "flash_uid_note", "rtl_model")


def sdr_summary(devices):
    """Only the identity and capability keys, and only those with a value."""
    out = []
    for dev in devices:
        out.append({k: dev[k] for k in SDR_SUMMARY_KEYS
                    if dev.get(k) is not None and dev.get(k) != ""})
    return out


def collect_sdr(open_radios=False):
    s = {"usb": sdr_usb_devices(), "pcie": sdr_pcie_devices(), "iio": {}}
    s["usdr_open"] = {p["slot"]: usdr_open(p) for p in s["pcie"]} if open_radios else {}
    # a KrakenSDR's channels are asked their EEPROM only, never streamed:
    # rpi-sdr-kraken browns out under load on its 15 W supply
    kraken_paths = set()
    rtl = [u for u in s["usb"] if u["id"] in RTL_IDS]
    for hub in set(u["parent"] for u in rtl):
        members = [u for u in rtl if u["parent"] == hub]
        if hub and tuple(sorted(u["serial"] or "" for u in members)) == KRAKEN_SERIALS:
            kraken_paths.update(u["path"] for u in members)
    s["rtl_open"] = {u["path"]: rtl_open(u, u["path"] in kraken_paths)
                     for u in rtl} if open_radios else {}
    for u in s["usb"]:
        if u["id"] in PLUTO_IDS:
            addr, why = pluto_address(u)
            s["iio"][u["path"]] = pluto_iio("ip:" + addr) if addr else {"error": why}
    s["devices"] = sdr_verdict(s)
    s["summary"] = sdr_summary(s["devices"])
    return s


def merge_sdr(doc, s):
    """Fold an sdr document into a Pi probe document (in place)."""
    doc["sdr"] = {k: s[k] for k in ("usb", "pcie", "usdr_open", "rtl_open") if k in s}
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
    s = collect_sdr("--sdr-open" in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(s, indent=1))
        return
    sdr_describe(s["devices"])


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    sdr_main()
