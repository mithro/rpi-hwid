"""The ESP32 probe module: the USB tree from sysfs, and the esptool read."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tokenize

import pytest

from rpi_hwid import esp32

# rpi5-433mhz's USB tree as sysfs showed it on 2026-09-26 (read with cat,
# nothing opened): three ESP32-C3 SuperMinis on their own USB-Serial-JTAG,
# a LilyGO board behind a CH9102, an Ebyte radio behind a CH340 and a
# Realtek Wi-Fi adapter with no tty at all.
RPI5_433MHZ = {
    "3-1.2": {"idVendor": "303a", "idProduct": "1001", "bcdDevice": "0101", "speed": "12",
              "manufacturer": "Espressif", "product": "USB JTAG/serial debug unit",
              "serial": "E8:3D:C1:8C:5C:88", "tty": ("acm", "ttyACM0")},
    "3-1.3": {"idVendor": "303a", "idProduct": "1001", "bcdDevice": "0101", "speed": "12",
              "manufacturer": "Espressif", "product": "USB JTAG/serial debug unit",
              "serial": "44:1B:F6:2E:B3:80", "tty": ("acm", "ttyACM2")},
    "3-1.4": {"idVendor": "303a", "idProduct": "1001", "bcdDevice": "0101", "speed": "12",
              "manufacturer": "Espressif", "product": "USB JTAG/serial debug unit",
              "serial": "E8:3D:C1:8C:3E:B8", "tty": ("acm", "ttyACM3")},
    "1-1.3": {"idVendor": "1a86", "idProduct": "55d4", "bcdDevice": "0444", "speed": "12",
              "product": "USB Single Serial", "serial": "591B031339",
              "tty": ("acm", "ttyACM1")},
    "1-1.4": {"idVendor": "1a86", "idProduct": "7523", "bcdDevice": "8233", "speed": "12",
              "product": "USB Serial", "tty": ("usb-serial", "ttyUSB0")},
    "1-2": {"idVendor": "0bda", "idProduct": "c811", "bcdDevice": "0200", "speed": "480",
            "manufacturer": "Realtek", "product": "802.11ac NIC", "serial": "123456"},
}
LINKS = {
    "serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_E8:3D:C1:8C:5C:88-if00": "ttyACM0",
    "serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_44:1B:F6:2E:B3:80-if00": "ttyACM2",
    "serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_E8:3D:C1:8C:3E:B8-if00": "ttyACM3",
    "radio-esp32-E8:3D:C1:8C:5C:88": "ttyACM0",
    "radio-cc1101-blue": "ttyACM3",
    "radio-sx1278-ra02": "ttyACM2",
    "radio-lilygo": "ttyACM1",
    "radio-e22": "ttyUSB0",
}


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    base = tmp_path / "sys/bus/usb/devices"
    base.mkdir(parents=True)
    for name, attrs in RPI5_433MHZ.items():
        d = base / name
        d.mkdir()
        for k, v in attrs.items():
            if k != "tty":
                (d / k).write_text(v + "\n")
        if "tty" in attrs:
            how, tty = attrs["tty"]
            iface = d / f"{name}:1.0"
            (iface / "tty" / tty if how == "acm" else iface / tty).mkdir(parents=True)
    dev = tmp_path / "dev"
    (dev / "serial/by-id").mkdir(parents=True)
    for link, tty in LINKS.items():
        (dev / tty).touch()
        target = ("../../" if link.startswith("serial/") else "") + tty
        (dev / link).symlink_to(target)
    monkeypatch.setattr(esp32, "ROOT", str(tmp_path))
    return tmp_path


def test_the_usb_tree_finds_the_espressif_ports_and_the_bridges(sysfs):
    e = esp32.collect_esp32()
    assert [(d["tty"], d["mac"]) for d in e["devices"]] == [
        ("/dev/ttyACM0", "e8:3d:c1:8c:5c:88"),
        ("/dev/ttyACM2", "44:1b:f6:2e:b3:80"),
        ("/dev/ttyACM3", "e8:3d:c1:8c:3e:b8"),
    ]
    assert all(d["transport"] == "usb-serial-jtag" for d in e["devices"])
    assert all(d["mac_source"] == "usb-serial-jtag serial" for d in e["devices"])
    # a bridge is only a candidate: the chip behind it is invisible
    assert [(d["bridge"], d["usb_serial"], d["tty"]) for d in e["candidates"]] == [
        ("CH9102", "591B031339", "/dev/ttyACM1"), ("CH340", None, "/dev/ttyUSB0")]
    assert all(d["mac"] is None for d in e["candidates"])
    assert e["reads"] == []


def test_the_udev_names_travel_with_the_port(sysfs):
    (blue,) = [d for d in esp32.collect_esp32()["devices"] if d["tty"] == "/dev/ttyACM3"]
    assert "/dev/radio-cc1101-blue" in blue["tty_links"]


def test_nothing_is_opened_without_a_read_being_asked_for(sysfs, monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("a serial port was opened")

    monkeypatch.setattr(esp32, "run_read", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    esp32.collect_esp32()


# The shape the read returns. Synthetic values (the uid especially): the
# real reads are the ESP32 fixtures in conftest.
READ = {
    "port": "/dev/radio-cc1101-blue", "esptool": "4.7.0",
    "chip_description": "ESP32-C3 (QFN32) (revision v0.4)",
    "features": ["WiFi", "BLE", "Embedded Flash 4MB (XMC)"], "crystal_mhz": 40,
    "mac": "e8:3d:c1:8c:3e:b8", "flash_jedec": "0x204016",
    "flash_uid": "0123456789abcdef",
    "efuse": {"MAC": "e8:3d:c1:8c:3e:b8 (OK)", "WAFER_VERSION_MINOR_LO": 4},
    "boot_after": "ESP-ROM:esp32c3-api1-20210207\nrst:0x15 (USB_UART_CHIP_RESET)\n",
}


def test_only_the_named_ports_are_read(sysfs, monkeypatch):
    asked = []

    def fake(port, timeout=90):
        asked.append(port)
        return dict(READ, port=port), None, "RESULT ..."

    monkeypatch.setattr(esp32, "run_read", fake)
    e = esp32.collect_esp32(["/dev/radio-cc1101-blue"])
    assert asked == ["/dev/radio-cc1101-blue"]
    (blue,) = [d for d in e["devices"] if d["tty"] == "/dev/ttyACM3"]
    assert blue["chip"] == "ESP32-C3"
    assert blue["package"] == "QFN32"
    assert blue["revision"] == "v0.4"
    assert blue["crystal_mhz"] == 40
    assert blue["flash_jedec"] == "0x204016"
    assert blue["mac_source"] == "usb-serial-jtag serial; efuse"
    assert "mac_conflict" not in blue
    others = [d for d in e["devices"] if d["tty"] != "/dev/ttyACM3"]
    assert all(d["chip"] is None for d in others)


def test_a_mac_that_disagrees_is_recorded_not_resolved(sysfs, monkeypatch):
    monkeypatch.setattr(esp32, "run_read", lambda p, timeout=90: (
        dict(READ, mac="e8:3d:c1:8c:3e:b9"), None, ""))
    (blue,) = [d for d in esp32.collect_esp32(["/dev/ttyACM3"])["devices"]
               if d["tty"] == "/dev/ttyACM3"]
    assert blue["mac_conflict"] == {"usb": "e8:3d:c1:8c:3e:b8", "efuse": "e8:3d:c1:8c:3e:b9"}


def test_a_bridge_whose_chip_answers_becomes_a_device(sysfs, monkeypatch):
    monkeypatch.setattr(esp32, "run_read", lambda p, timeout=90: (
        dict(READ, chip_description="ESP32-D0WD-V3 (revision v3.1)",
             mac="a4:f0:0f:74:2e:68"), None, ""))
    e = esp32.collect_esp32(["/dev/ttyUSB0"])
    (d,) = [d for d in e["devices"] if d["tty"] == "/dev/ttyUSB0"]
    assert d["mac"] == "a4:f0:0f:74:2e:68"
    assert d["mac_source"] == "efuse"
    assert d["bridge"] == "CH340"
    assert d["chip"] == "ESP32-D0WD-V3"
    assert [c["tty"] for c in e["candidates"]] == ["/dev/ttyACM1"]


def test_a_failed_read_is_kept_with_its_reason(sysfs, monkeypatch):
    monkeypatch.setattr(esp32, "run_read", lambda p, timeout=90: (
        None, "esptool read failed: Failed to connect to Espressif device", "..."))
    e = esp32.collect_esp32(["/dev/ttyACM0"])
    (d,) = [d for d in e["devices"] if d["tty"] == "/dev/ttyACM0"]
    assert d["read_error"].startswith("esptool read failed")
    assert e["reads"][0]["ok"] is False


@pytest.mark.parametrize(("text", "want"), [
    ("ESP32-C3 (QFN32) (revision v0.4)", ("ESP32-C3", "QFN32", "v0.4")),
    ("ESP32-D0WD-V3 (revision v3.1)", ("ESP32-D0WD-V3", None, "v3.1")),
    ("ESP32-S3 (QFN56) (revision v0.2)", ("ESP32-S3", "QFN56", "v0.2")),
    ("", (None, None, None)),
])
def test_chip_facts(text, want):
    assert esp32.chip_facts(text) == want


def test_parse_read_takes_the_result_line():
    assert esp32.parse_read("banner\nRESULT {\"a\": 1}\n") == {"a": 1}
    assert esp32.parse_read("A fatal error occurred: Failed to connect") is None


def test_merge_puts_devices_in_the_verdict():
    doc = {"verdict": {"summary": {}}}
    esp32.merge_esp32(doc, {"usb": [], "reads": [], "devices": [{"mac": "m"}],
                            "candidates": []})
    assert doc["verdict"]["esp32"] == [{"mac": "m"}]
    assert doc["esp32"]["reads"] == []
    json.dumps(doc)


def test_the_module_is_a_plain_python35_script():
    with pathlib.Path(esp32.__file__).open("rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    assert not [t for t in tokens if tokenize.tok_name[t.type] == "FSTRING_START"]
    r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", esp32.__file__],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    # the read script is run by the host's own python3: it must compile too
    compile(esp32.READ_SCRIPT, "READ_SCRIPT", "exec")


# --- the read script, against a fake esptool ------------------------------------
#
# Enough of esptool, espefuse and pyserial for READ_SCRIPT to run in a real
# child python. The flash sits behind a fake of the C3's SPI controller
# registers, which is how the read reaches it: it answers RDID, the status
# registers, Read Unique ID (a 128-bit id ending in 0xFF, as an XMC's does)
# and SFDP, and -- like a real C3 -- reads nothing back from a command that
# has a MOSI phase.

FAKE_ESPTOOL = '''
import os
__version__ = os.environ.get("FAKE_VERSION", "4.7.0")
from . import cmds
'''
FAKE_CMDS = '''
import os
UID = bytes.fromhex("c1a2b3d4e5f607180150ffffffffffff")
SFDP = bytes.fromhex("53464450060100ff00060110300000ff") + bytes([255]) * 32 + bytes.fromhex(
    "e520f9ffffffff01") + bytes([255]) * 200
BASE = 0x60002000
USR, USR1, USR2, MISO, MOSI, W0 = (BASE + o for o in (0x18, 0x1C, 0x20, 0x28, 0x24, 0x58))
LOG = os.environ["FAKE_LOG"]

def log(s):
    with open(LOG, "a") as f:
        f.write(s + "\\n")

class FatalError(RuntimeError):
    pass

class Port:
    name = "fake"
    timeout = None
    def __init__(self):
        self.sent = False
    def fileno(self):
        raise OSError("not a tty")
    def read(self, n):
        if self.timeout is None and os.environ.get("FAKE_QUIET"):
            import time
            time.sleep(60)      # a quiet app and a blocking port: read never fills
        if self.sent:
            return b""
        self.sent = True
        return b"ESP-ROM:esp32c3-api1-20210207\\nrst:0x15 (USB_UART_CHIP_RESET)\\n"
    def close(self):
        log("close")

class ESP:
    CHIP_NAME = "ESP32-C3"
    SPI_REG_BASE, SPI_USR_OFFS, SPI_USR1_OFFS, SPI_USR2_OFFS = BASE, 0x18, 0x1C, 0x20
    SPI_MOSI_DLEN_OFFS, SPI_MISO_DLEN_OFFS, SPI_W0_OFFS = 0x24, 0x28, 0x58
    def __init__(self):
        self._port = Port()
        self.regs = {}
    def read_reg(self, a):
        return self.regs.get(a, 0)
    def write_reg(self, a, v):
        self.regs[a] = v
        if a == BASE and v & (1 << 18):
            self.run()
            self.regs[BASE] = 0
    def run(self):
        cmd, usr = self.regs[USR2] & 0xFF, self.regs[USR]
        n = (self.regs[MISO] + 1) // 8
        log("spi 0x%02x %d" % (cmd, n))
        if cmd not in (0x9F, 0x05, 0x35, 0x15, 0x4B, 0x5A):
            raise FatalError("the fake flash was sent 0x%02x" % cmd)
        if cmd == 0x4B and os.environ.get("FAKE_UID") == "broken":
            raise FatalError("SPI command did not complete in time")
        if usr & (1 << 27):
            data = bytes(n)                 # a C3 reads nothing after MOSI
        elif cmd == 0x4B:
            # four dummy bytes as address and dummy clocks, then the id
            assert usr & (1 << 30) and usr & (1 << 29), "0x4B without its dummies"
            data = (UID + bytes([255]) * 64)[:n]
        elif cmd == 0x5A:
            a = self.regs[BASE + 4]
            data = (SFDP + bytes([255]) * 64)[a:a + n]
        else:
            data = ({0x9F: bytes.fromhex("204016") * 22, 0x05: bytes([0]), 0x35: bytes([2]),
                     0x15: bytes([0x20])}[cmd] * 64)[:n]
        data += bytes(-len(data) % 4)
        for i in range(0, len(data), 4):
            self.regs[W0 + i] = int.from_bytes(data[i:i + 4], "little")
    def get_chip_description(self):
        return "ESP32-C3 (QFN32) (revision v0.4)"
    def get_chip_features(self):
        return ["WiFi", "BLE", "Embedded Flash 4MB (XMC)"]
    def get_crystal_freq(self):
        return 40
    def read_mac(self):
        return (0xe8, 0x3d, 0xc1, 0x8c, 0x3e, 0xb8)
    def flash_spi_attach(self, arg):
        pass
    def flash_id(self):
        return 0x164020
    def hard_reset(self):
        log("hard_reset")

def detect_chip(port, baud, mode):
    log("detect " + mode)
    if os.environ.get("FAKE_CONNECT") == "fail":
        raise FatalError("Failed to connect to Espressif device")
    return ESP()
'''
FAKE_FIELDS = '''
class F:
    def __init__(self, name, v):
        self.name, self.v = name, v
    def get(self):
        return self.v

class EspEfuses:
    def __init__(self, esp, skip_connect=False):
        self.f = [F("MAC", "e8:3d:c1:8c:3e:b8 (OK)"), F("BLOCK_KEY0", "secret"),
                  F("OPTIONAL_UNIQUE_ID", "00112233445566778899aabbccddeeff"),
                  F("WAFER_VERSION_MINOR_LO", 4)]
    def __iter__(self):
        return iter(self.f)
'''
FAKE_SERIAL = '''
import os
class Serial:
    def __init__(self, port, baud, timeout=None):
        self.log("open")
    def log(self, s):
        with open(os.environ["FAKE_LOG"], "a") as f:
            f.write(s + "\\n")
    def __setattr__(self, k, v):
        if k in ("rts", "dtr"):
            self.log("%s=%s" % (k, v))
        object.__setattr__(self, k, v)
    def fileno(self):
        raise OSError("not a tty")
    def read(self, n):
        return b""
    def close(self):
        pass
'''


@pytest.fixture
def fake_esptool(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    for rel, text in (("esptool/__init__.py", FAKE_ESPTOOL), ("esptool/cmds.py", FAKE_CMDS),
                      ("espefuse/__init__.py", ""), ("espefuse/efuse/__init__.py", ""),
                      ("espefuse/efuse/esp32c3/__init__.py", ""),
                      ("espefuse/efuse/esp32c3/fields.py", FAKE_FIELDS),
                      ("serial/__init__.py", FAKE_SERIAL)):
        (lib / rel).parent.mkdir(parents=True, exist_ok=True)
        (lib / rel).write_text(text)
    log = tmp_path / "log"
    monkeypatch.setenv("PYTHONPATH", str(lib))
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("RPI_HWID_ESP32_LISTEN", "0.2")
    monkeypatch.setattr(esp32, "esptool_pythons", lambda: [sys.executable])
    return log


def test_the_read_takes_the_whole_uid_and_sfdp_through_the_registers(fake_esptool):
    result, error, _text = esp32.run_read("/dev/ttyACM3")
    assert error is None
    assert result["flash_uid_raw"] == "c1a2b3d4e5f607180150ffffffffffff" + "ff" * 16
    assert result["flash_rdid"] == "2040162040162040"
    assert result["flash_status"] == {"05": "00", "35": "02", "15": "20"}
    assert result["flash_sfdp_raw"].startswith("53464450060100ff")
    assert len(result["flash_sfdp_raw"]) == 512
    assert result["flash_jedec"] == "0x204016"
    assert result["chip_description"] == "ESP32-C3 (QFN32) (revision v0.4)"
    assert result["efuse"]["OPTIONAL_UNIQUE_ID"] == "00112233445566778899aabbccddeeff"
    assert "BLOCK_KEY0" not in result["efuse"], "no key block is ever printed"
    assert result["errors"] == {"hupcl": "OSError: not a tty"}
    assert "USB_UART_CHIP_RESET" in result["boot_after"]
    lines = fake_esptool.read_text().splitlines()
    assert lines[0] == "detect default_reset"
    assert lines[-2:] == ["hard_reset", "close"]
    assert set(lines[1:-2]) == {"spi 0x4b 32", "spi 0x9f 8", "spi 0x05 1", "spi 0x35 1",
                                "spi 0x15 1", "spi 0x5a 64"}


def test_a_quiet_application_does_not_hold_the_read_open(fake_esptool, monkeypatch):
    """E8:3D:C1:8C:5C:88 runs an app that prints little, and esptool's port
    blocked until 4096 bytes came: the read timed out and lost everything."""
    import time

    monkeypatch.setenv("FAKE_QUIET", "1")
    t0 = time.time()
    result, error, _ = esp32.run_read("/dev/ttyACM0", timeout=30)
    assert error is None
    assert result["flash_uid_raw"].startswith("c1a2b3d4e5f60718")
    assert time.time() - t0 < 20


def test_esptool_5_gets_its_own_spelling_of_the_reset_mode(fake_esptool, monkeypatch):
    monkeypatch.setenv("FAKE_VERSION", "5.2.0")
    _result, error, _ = esp32.run_read("/dev/ttyUSB1")
    assert error is None
    assert fake_esptool.read_text().splitlines()[0] == "detect default-reset"


def test_a_failed_step_keeps_the_rest_and_still_resets(fake_esptool, monkeypatch):
    monkeypatch.setenv("FAKE_UID", "broken")
    result, error, _ = esp32.run_read("/dev/ttyACM3")
    assert error is None
    assert "flash_uid_raw" not in result
    assert result["errors"]["flash_uid_raw"].startswith("FatalError: SPI command")
    assert result["flash_sfdp_raw"].startswith("53464450")
    assert result["mac"] == "e8:3d:c1:8c:3e:b8"
    assert result["efuse"]["MAC"].startswith("e8:3d")
    assert "hard_reset" in fake_esptool.read_text()


def test_a_failed_connect_still_pulses_the_reset(fake_esptool, monkeypatch):
    monkeypatch.setenv("FAKE_CONNECT", "fail")
    result, _error, _ = esp32.run_read("/dev/ttyACM3")
    assert result["errors"]["connect"].startswith("FatalError: Failed to connect")
    assert fake_esptool.read_text().splitlines()[1:] == [
        "open", "dtr=False", "rts=True", "rts=False"]


def test_apply_read_keeps_what_was_read_and_says_what_was_not():
    d = esp32.device_from_usb(
        {"path": "3-1.4", "vidpid": "303a:1001", "manufacturer": "Espressif",
         "product": "USB JTAG/serial debug unit", "serial": "E8:3D:C1:8C:3E:B8",
         "bcd_device": "0101", "speed": "12", "tty": ["/dev/ttyACM3"]}, {})
    esp32.apply_read(d, dict(READ, flash_uid_raw=None,
                             errors={"flash_uid_raw": "FatalError: no"}))
    assert d["chip"] == "ESP32-C3"
    assert d["flash_uid"] is None
    assert d["flash_uid_state"] is None
    assert d["read_errors"] == {"flash_uid_raw": "FatalError: no"}


# The Read Unique ID reads of 2026-09-27, 32 bytes each: an XMC in an
# ESP32-C3 (128 bits, the last six 0xFF, then 0xFF), a devkit's GigaDevice
# (64 bits, then 0xFF) and an ESP32-CAM's Boya (128 bits, then again).
@pytest.mark.parametrize(("raw", "want"), [
    ("240c1119088539540150ffffffffffff" + "ff" * 16,
     ("240c1119088539540150ffffffffffff", 128, "read")),
    ("3130343531118566" + "ff" * 8 + "c801" + "ff" * 14, ("3130343531118566", 64, "read")),
    ("343738393844fa77fffcffff968f1f11" * 2, ("343738393844fa77fffcffff968f1f11", 128, "read")),
    ("0123456789abcdef" * 4, ("0123456789abcdef", 64, "read")),
    ("00" * 32, (None, None, "blank")),
    ("ff" * 32, (None, None, "blank")),
    ("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
     (None, None, "unbounded")),
    (None, (None, None, None)),
    ("abcd", (None, None, None)),
])
def test_the_uid_is_as_long_as_the_part_says(raw, want):
    assert esp32.flash_uid_from_raw(raw) == want


# A C3 SuperMini's XMC, as read on 2026-09-27 (the header and basic table)
C3_SFDP = ("53464450060102ff00060110300000ff46000104d00000ff84000102c00000ff"
           + "ff" * 16 + "e520f9ffffffff01")


def test_the_sfdp_summary():
    got = esp32.sfdp_summary(C3_SFDP)
    assert got == {"revision": "1.6", "density_bytes": 4 << 20, "tables": [
        {"id": "0xff00", "revision": "1.6", "dwords": 16, "pointer": "0x30"},
        {"id": "0xff46", "revision": "1.0", "dwords": 4, "pointer": "0xd0"},
        {"id": "0xff84", "revision": "1.0", "dwords": 2, "pointer": "0xc0"}]}
    assert esp32.sfdp_summary("ff" * 16) == {"revision": "none"}
    assert esp32.sfdp_summary(None) is None


def test_the_error_line_is_the_one_that_says_what_went_wrong():
    text = ("Detecting chip type... ESP32-C3\nConnecting...Traceback (most recent call last):\n"
            "  File \"<string>\", line 15, in <module>\n"
            "esptool.util.FatalError: Reading more than 32 bits back\n\n"
            "Detecting chip type... ESP32-C3\n")
    assert esp32.error_line(text) == "esptool.util.FatalError: Reading more than 32 bits back"
    assert esp32.error_line("") == "no output"


def test_the_collector_embeds_the_module_and_reads_only_what_it_is_told():
    from rpi_hwid.collect import probe_source

    assert "collect_esp32" not in probe_source()
    src = probe_source(esp32=True)
    assert src.startswith("RPI_HWID_EMBEDDED = True")
    assert "merge_esp32(_doc, collect_esp32([]))" in src
    src = probe_source(esp32=True, esp32_read=("/dev/radio-cc1101-blue",))
    assert "merge_esp32(_doc, collect_esp32(['/dev/radio-cc1101-blue']))" in src
    compile(src, "probe", "exec")


def test_collect_hands_each_host_its_own_ports(monkeypatch, tmp_path):
    from rpi_hwid import collect

    seen = {}

    def fake(host, users, jump, fpga, jtag, flash, tinytapeout=False, take_port=True,
             esp32=False, esp32_read=()):
        seen[host] = (esp32, tuple(esp32_read))
        return collect.Result(host, False, error="not really")

    monkeypatch.setattr(collect, "probe_host", fake)
    collect.collect(["a", "b", "c"], tmp_path, esp32_read=("a=/dev/ttyACM0", "a=/dev/ttyACM3",
                                                            "b=/dev/ttyUSB1"), workers=1)
    assert seen == {"a": (True, ("/dev/ttyACM0", "/dev/ttyACM3")),
                    "b": (True, ("/dev/ttyUSB1",)), "c": (False, ())}


def test_a_read_names_its_host_with_or_without_the_user(monkeypatch, tmp_path):
    """`--esp32-read host=PORT` for a host given as `tim@host` did nothing at
    all on 2026-09-26: the reads were silently dropped."""
    from rpi_hwid import collect

    seen = {}

    def fake(host, users, jump, fpga, jtag, flash, tinytapeout=False, take_port=True,
             esp32=False, esp32_read=()):
        seen[host] = tuple(esp32_read)
        return collect.Result(host, False, error="not really")

    monkeypatch.setattr(collect, "probe_host", fake)
    collect.collect(["tim@rpi5", "rpi4"], tmp_path, workers=1,
                    esp32_read=("rpi5=/dev/ttyACM0", "tim@rpi4=/dev/ttyUSB1"))
    assert seen == {"tim@rpi5": ("/dev/ttyACM0",), "rpi4": ("/dev/ttyUSB1",)}


def test_a_read_for_a_host_not_being_collected_is_an_error(tmp_path):
    from rpi_hwid import collect

    with pytest.raises(ValueError, match="nosuchhost"):
        collect.collect(["rpi5"], tmp_path, esp32_read=("nosuchhost=/dev/ttyACM0",))
    with pytest.raises(ValueError, match="HOST=PORT"):
        collect.collect(["rpi5"], tmp_path, esp32_read=("rpi5",))


def test_the_read_runs_under_a_python_that_has_esptool(monkeypatch, tmp_path):
    """rpi4-esp has no system esptool but Tim's own venv at ~/.venvs/esptool."""
    venv = tmp_path / ".venvs/esptool/bin"
    venv.mkdir(parents=True)
    (venv / "python3").symlink_to(sys.executable)
    monkeypatch.setenv("HOME", str(tmp_path))
    found = esp32.esptool_pythons()
    assert found[0] == sys.executable
    assert str(venv / "python3") in found


def test_the_esp32_command(sysfs, capsys):
    from rpi_hwid.cli import main as cli_main

    assert cli_main(["esp32"]) == 0
    out = capsys.readouterr().out
    assert "e8:3d:c1:8c:5c:88" in out
    assert "would reset it" in out
    assert cli_main(["esp32", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["devices"]) == 3


def test_collect_refuses_an_unmatched_read_before_probing_anything(tmp_path, monkeypatch,
                                                                   capsys):
    from rpi_hwid import collect
    from rpi_hwid.cli import main as cli_main

    monkeypatch.setattr(collect, "probe_host", lambda *a, **k: pytest.fail("probed"))
    assert cli_main(["collect", "--out", str(tmp_path), "--esp32-read",
                     "rpi5=/dev/ttyACM0", "tim@rpi4"]) == 2
    assert "rpi5 is not one of the hosts being collected" in capsys.readouterr().err
