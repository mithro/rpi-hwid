"""Run the probes' collectors against a fake /proc, /sys and /dev tree, with
every external command stubbed, so the sysfs-walking code is exercised
without a Pi (and without sudo)."""

from __future__ import annotations

import json
import os
import pty
import struct
import termios
import threading

import pytest

from rpi_hwid import fpga, probe, tinytapeout


def _w(root, rel, content):
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


def _pi5_tree(root):
    _w(root, "/proc/device-tree/model", "Raspberry Pi 5 Model B Rev 1.1\0")
    _w(root, "/proc/device-tree/compatible", "raspberrypi,5-model-b\0brcm,bcm2712\0")
    _w(root, "/proc/device-tree/serial-number", "c36b093f773d46b8\0")
    _w(root, "/proc/meminfo", "MemTotal:        1006740 kB\n")
    _w(root, "/proc/cpuinfo", "processor\t: 0\nRevision\t: a04171\nSerial\t\t: c36b093f773d46b8\n")
    _w(root, "/proc/device-tree/chosen/power/max_current", struct.pack(">I", 3000))
    _w(root, "/proc/device-tree/chosen/power/usbpd_power_data_objects", b"\0" * 28)
    _w(root, "/proc/device-tree/cooling_fan/status", "okay\0")
    _w(root, "/sys/class/hwmon/hwmon2/name", "pwmfan\n")
    _w(root, "/sys/class/hwmon/hwmon2/fan1_input", "2471\n")
    # onboard ethernet (macb) and a Realtek dongle on usb 2-1
    _w(root, "/sys/class/net/eth0/address", "98:fe:54:13:f5:75\n")
    _w(root, "/sys/class/net/eth0/speed", "1000\n")
    (root / "sys/class/net/eth0/device").mkdir(parents=True)
    _w(root, "/sys/devices/platform/axi/1000120000.pcie/1f00100000.ethernet/x", "")
    (root / "sys/class/net/eth0/device/driver").symlink_to(
        root / "sys/bus/platform/drivers/macb")
    (root / "sys/bus/platform/drivers/macb").mkdir(parents=True)
    _w(root, "/sys/class/net/eth1/address", "00:e0:4c:68:01:03\n")
    _w(root, "/sys/class/net/eth1/speed", "100\n")
    dev = root / ("sys/devices/platform/axi/1000120000.pcie/1f00200000.usb/"
                  "xhci-hcd.0/usb2/2-1/2-1:1.0")
    dev.mkdir(parents=True)
    (root / "sys/class/net/eth1/device").symlink_to(dev)
    (root / "sys/bus/usb/drivers/r8152").mkdir(parents=True)
    (dev / "driver").symlink_to(root / "sys/bus/usb/drivers/r8152")
    for k, v in (("idVendor", "0bda"), ("idProduct", "8153"), ("manufacturer", "Realtek"),
                 ("product", "USB 10/100/1000 LAN"), ("serial", "001000001"),
                 ("version", " 3.00"), ("speed", "5000")):
        _w(root, "/sys/bus/usb/devices/2-1/" + k, v + "\n")
    _w(root, "/sys/bus/usb/devices/usb2/idVendor", "1d6b\n")
    _w(root, "/sys/bus/usb/devices/usb2/idProduct", "0003\n")
    # a Tiny Tapeout demo board (MicroPython RP2040) on usb 1-1.2, its CDC
    # ACM interface bound to ttyACM0, and a Pico in BOOTSEL mode on 1-1.3
    for k, v in (("idVendor", "2e8a"), ("idProduct", "0005"), ("manufacturer", "MicroPython"),
                 ("product", "Board in FS mode"), ("serial", "E6614C311B7A7A37")):
        _w(root, "/sys/bus/usb/devices/1-1.2/" + k, v + "\n")
    (root / "sys/bus/usb/devices/1-1.2:1.0/tty/ttyACM0").mkdir(parents=True)
    (root / "sys/bus/usb/devices/1-1.2:1.1").mkdir(parents=True)
    for k, v in (("idVendor", "2e8a"), ("idProduct", "0003"), ("product", "RP2 Boot"),
                 ("serial", "E0C9125B0D9B")):
        _w(root, "/sys/bus/usb/devices/1-1.3/" + k, v + "\n")
    # the Acorn on PCIe, plus the RP1 which is not a board
    for slot, vend, dev_id, cls, res, sub in (
        ("0001:01:00.0", "0x1e24", "0x021f", "0x120000",
         "0x1b00000000 0x1b0001ffff 0x40200\n0x1b00100000 0x1b0010ffff 0x40200\n0 0 0\n",
         ("0x1e24", "0x021f")),
        ("0000:01:00.0", "0x1de4", "0x0001", "0x020000",
         "0x1f00000000 0x1f00003fff 0x40200\n0x1f00400000 0x1f007fffff 0x40200\n",
         ("0x1de4", "0x0001")),
        ("0001:00:00.0", "0x14e4", "0x2712", "0x060400", "", ("0x0000", "0x0000")),
    ):
        _w(root, f"/sys/bus/pci/devices/{slot}/vendor", vend + "\n")
        _w(root, f"/sys/bus/pci/devices/{slot}/device", dev_id + "\n")
        _w(root, f"/sys/bus/pci/devices/{slot}/class", cls + "\n")
        _w(root, f"/sys/bus/pci/devices/{slot}/resource", res)
        _w(root, f"/sys/bus/pci/devices/{slot}/subsystem_vendor", sub[0] + "\n")
        _w(root, f"/sys/bus/pci/devices/{slot}/subsystem_device", sub[1] + "\n")


def _orange_pi_pc_tree(root):
    """An Orange Pi PC on Armbian trixie, as captured from opi1pc-b on
    2026-07-08: the device tree U-Boot hands the kernel (model, compatible,
    the serial# it built from the SID), the 32-bit kernel's cpuinfo (which
    repeats that serial and prints a meaningless Revision), MemTotal, the
    Armbian release file, the sunxi-sid nvmem, the H3's EMAC on
    dwmac-sun8i, and the CDC-ECM gadget interface. The SID words 1-3 are
    chosen so U-Boot's CRC-32 rule lands on the captured serial (the real
    words were never read; the board is off the network)."""
    _w(root, "/proc/device-tree/model", "Xunlong Orange Pi PC\0")
    _w(root, "/proc/device-tree/compatible", "xunlong,orangepi-pc\0allwinner,sun8i-h3\0")
    _w(root, "/proc/device-tree/serial-number", "02c00181e1ce7d46\0")
    _w(root, "/proc/cpuinfo",
       "processor\t: 0\nmodel name\t: ARMv7 Processor rev 5 (v7l)\nCPU part\t: 0xc07\n"
       "Hardware\t: Allwinner sun8i Family\nRevision\t: 0000\nSerial\t\t: 02c00181e1ce7d46\n")
    _w(root, "/proc/meminfo", "MemTotal:        1015636 kB\nMemFree:          612340 kB\n")
    _w(root, "/etc/armbian-release",
       "# PLEASE DO NOT EDIT THIS FILE\nBOARD=orangepipc\nBOARD_NAME=\"Orange Pi PC\"\n"
       "BOARDFAMILY=sun8i\nLINUXFAMILY=sunxi\nARCH=arm\nBOARD_TYPE=csc\nBRANCH=current\n"
       "VERSION=26.8.0-trunk.170\n")
    _w(root, "/sys/bus/nvmem/devices/sunxi-sid0/nvmem",
       bytes.fromhex("8101c0025f4a0b0c9e8c1e8c274e02f8") + b"\0" * 240)
    _w(root, "/sys/class/net/eth0/address", "02:81:e1:ce:7d:46\n")
    _w(root, "/sys/class/net/eth0/speed", "100\n")
    emac = root / "sys/devices/platform/soc/1c30000.ethernet"
    emac.mkdir(parents=True)
    (root / "sys/class/net/eth0/device").symlink_to(emac)
    (root / "sys/bus/platform/drivers/dwmac-sun8i").mkdir(parents=True)
    (emac / "driver").symlink_to(root / "sys/bus/platform/drivers/dwmac-sun8i")
    _w(root, "/sys/class/net/usb0/address", "aa:8f:2b:0c:11:3e\n")
    gadget = root / "sys/devices/platform/soc/1c19000.usb/musb-hdrc.2.auto/gadget.0"
    gadget.mkdir(parents=True)
    (root / "sys/class/net/usb0/device").symlink_to(gadget)
    _w(root, "/sys/bus/usb/devices/usb1/idVendor", "1d6b\n")
    _w(root, "/sys/bus/usb/devices/usb1/idProduct", "0002\n")


@pytest.fixture
def opi_root(tmp_path, monkeypatch):
    _orange_pi_pc_tree(tmp_path)
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    calls = []

    def fake_sh(args, timeout=15):
        calls.append(args)
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    return tmp_path, calls


def test_collect_orange_pi_pc(opi_root):
    _root, calls = opi_root
    d = probe.collect()
    assert d["board"] == "opi"
    assert d["model"] == "Xunlong Orange Pi PC"
    assert d["compatible"] == ["xunlong,orangepi-pc", "allwinner,sun8i-h3"]
    assert d["serial"] == "02c00181e1ce7d46"
    assert d["cpuinfo_serial"] == "02c00181e1ce7d46"
    assert d["sid"] == ["0x02c00181", "0x0c0b4a5f", "0x8c1e8c9e", "0xf8024e27"]
    assert d["sid_serial"] == "02c00181e1ce7d46", "U-Boot's rule reproduces the DT serial"
    assert d["revision"] is None, "cpuinfo's 0000 is not a revision code"
    assert d["mem_kb"] == 1015636
    assert d["armbian"]["VERSION"] == "26.8.0-trunk.170"
    assert d["armbian"]["BOARD_NAME"] == "Orange Pi PC"
    assert d["hat_fw"] is None
    assert d["hat_eeproms"] == {}
    assert d["i2c1"] is None
    assert d["throttled"] is None
    assert d["pi5"] is False
    assert calls == [], "no sudo, dtparam, i2cdetect or vcgencmd on a board without them"
    ifaces = {i["name"]: i for i in d["interfaces"]}
    assert ifaces["eth0"]["onboard"] is True
    assert ifaces["eth0"]["driver"] == "dwmac-sun8i"
    assert ifaces["eth0"]["kind"] == "eth"
    assert ifaces["usb0"]["onboard"] is False
    assert ifaces["usb0"]["kind"] == "other"
    assert ifaces["usb0"]["usb"] is None, "a gadget is not a USB device on the host side"
    assert d["usb_net"] == []
    v = probe.verdict(d)
    assert v["header"] == ["40-pin header not probed: no HAT ID EEPROM convention on this board"]
    assert "no power sensing" in v["power"]
    assert any(e.startswith("Allwinner SID 0x02c00181") for e in v["evidence"])
    assert any(e.startswith("Armbian 26.8.0-trunk.170 on board id orangepipc")
               for e in v["evidence"])
    s = v["summary"]
    assert s["model"] == "Xunlong Orange Pi PC"
    assert s["serial"] == "02c00181e1ce7d46"
    assert s["revision"] is None
    assert s["compatible"] == "xunlong,orangepi-pc allwinner,sun8i-h3"
    assert s["memory"] == "1 GB"
    assert s["header"] == []
    assert s["hat_uuid"] is None
    assert s["power_class"] == "undetermined"
    assert s["macs"] == [{"kind": "eth", "mac": "02:81:e1:ce:7d:46"}]
    assert s["usb_net"] == []
    assert s["rtc_battery"] is None
    assert s["fan"] is None
    assert s["max_current_ma"] is None
    assert s["ext5v_v"] is None


def test_orange_pi_serial_falls_back_to_the_sid(opi_root):
    """A device tree without /serial-number and a cpuinfo Serial of zeros
    (an older U-Boot) leave the SID as the only source."""
    root, _calls = opi_root
    (root / "proc/device-tree/serial-number").unlink()
    _w(root, "/proc/cpuinfo", "Hardware\t: Allwinner sun8i Family\nRevision\t: 0000\n"
                              "Serial\t\t: 0000000000000000\n")
    d = probe.collect()
    assert d["serial"] == "02c00181e1ce7d46"
    assert d["cpuinfo_serial"] == "0000000000000000"
    v = probe.verdict(d)
    assert v["summary"]["serial"] == "02c00181e1ce7d46"
    assert probe.headline(d) == "Xunlong Orange Pi PC  serial 02c00181e1ce7d46"


@pytest.mark.parametrize(("kb", "want"), [
    (None, None), (1015636, "1 GB"), (443212, "512 MB"), (4143120, "4 GB"),
    (8167424, "8 GB"), (16523456, "16 GB"), (31171676, "32 GB"), (70000000, "128 GB"),
])
def test_nominal_memory(kb, want):
    assert probe.nominal_memory(kb) == want


def test_board_kind():
    assert probe.board_kind("Raspberry Pi 5 Model B Rev 1.0",
                            ["raspberrypi,5-model-b", "brcm,bcm2712"]) == "rpi"
    assert probe.board_kind("Raspberry Pi Zero W Rev 1.1", []) == "rpi"
    assert probe.board_kind("Xunlong Orange Pi PC",
                            ["xunlong,orangepi-pc", "allwinner,sun8i-h3"]) == "opi"
    assert probe.board_kind("", ["xunlong,orangepi-zero", "allwinner,sun8i-h2-plus"]) == "opi"
    assert probe.board_kind("MinnowBoard Turbot", []) == "other"


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    _pi5_tree(tmp_path)
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
    monkeypatch.setattr(tinytapeout, "ROOT", str(tmp_path))
    # no sudo, no vcgencmd, no openFPGALoader here: every command answers
    # what it would on a machine lacking the tool, and the PMIC line is fed
    # by the one stub that matters
    def fake_sh(args, timeout=15):
        if args[:2] == ["sudo", "vcgencmd"]:
            return ("EXT5V_V volt(24)=5.33990000V\nBATT_V volt(25)=3.26000000V\n")
        if args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x0"
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    monkeypatch.setattr(fpga, "sh", fake_sh)
    return tmp_path


def test_collect_walks_the_tree(fake_root):
    d = probe.collect()
    assert d["model"] == "Raspberry Pi 5 Model B Rev 1.1"
    assert d["board"] == "rpi"
    assert d["serial"] == "c36b093f773d46b8"
    assert d["revision"] == "a04171"
    assert d["sid"] is None
    assert d["pi5"] is True
    assert d["max_current_ma"] == 3000
    assert d["usbpd_pdos"] == []
    assert d["ext5v_v"] == pytest.approx(5.3399)
    assert d["rtc_batt_v"] == pytest.approx(3.26)
    assert d["fan_dt"] == "okay"
    assert d["fan_rpm"] == 2471
    assert d["hat_fw"] is None
    assert d["hat_eeproms"] == {}
    assert d["i2c1"] is None
    assert d["throttled"] == "0x0"
    ifaces = {i["name"]: i for i in d["interfaces"]}
    assert ifaces["eth0"]["onboard"] is True
    assert ifaces["eth0"]["driver"] == "macb"
    assert ifaces["eth1"]["onboard"] is False
    assert ifaces["eth1"]["usb"] == "2-1"
    (adapter,) = d["usb_net"]
    assert adapter["vidpid"] == "0bda:8153"
    assert adapter["bcd_usb"] == "3.00"
    assert adapter["kind"] == "ethernet"
    v = probe.verdict(d)
    assert v["summary"]["macs"] == [{"kind": "eth", "mac": "98:fe:54:13:f5:75"}]
    assert v["summary"]["power_class"] == "ambiguous"
    assert v["summary"]["rtc_battery"] is True
    assert v["summary"]["compatible"] == "raspberrypi,5-model-b brcm,bcm2712"
    assert v["summary"]["memory"] == "1 GB"


def test_fpga_collect_finds_the_acorn_not_the_rp1(fake_root):
    f = fpga.collect_fpga()
    ids = {p["id"]: p for p in f["pcie"]}
    assert "1e24:021f" in ids
    assert ids["1e24:021f"]["bars"] == [128 << 10, 64 << 10]
    assert "14e4:2712" not in ids, "the root port is a bridge, not an endpoint"
    assert f["boards"][0]["kind"] == "acorn"
    assert f["summary"] == [{"kind": "acorn"}]
    assert f["jtag"] is None


def test_fpga_jtag_without_tool_is_an_error_record(fake_root):
    f = fpga.collect_fpga(jtag=True)
    assert f["jtag"] == {"error": "openFPGALoader not installed"}


def test_merge_fpga_into_probe_document(fake_root):
    d = probe.collect()
    d["verdict"] = probe.verdict(d)
    fpga.merge_fpga(d, fpga.collect_fpga())
    assert d["verdict"]["summary"]["fpga"] == [{"kind": "acorn"}]
    assert d["fpga"]["pcie"]


def test_hat_firmware_dir_and_id_bus(fake_root, monkeypatch):
    for k, v in (("vendor", "Digilent"), ("product", "Pmod HAT Adaptor"),
                 ("product_id", "0x0001"), ("product_ver", "0x0001"),
                 ("uuid", "6bcd3833-3d1d-4b3e-9ab1-945c71845f3a")):
        _w(fake_root, "/proc/device-tree/hat/" + k, v + "\0")
    _w(fake_root, "/dev/i2c-1", "")
    _w(fake_root, "/dev/i2c-0", "")
    i2cdetect = ("     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n"
                 "00:                         -- -- -- -- -- -- -- --\n"
                 "20: 20 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --\n"
                 "30: -- -- -- -- -- -- -- -- -- -- -- -- 3c -- -- --\n")

    def fake_sh(args, timeout=15):
        if args[:3] == ["sudo", "i2cdetect", "-y"]:
            return i2cdetect
        if args[:2] == ["sudo", "i2ctransfer"]:
            return ""       # no EEPROM answers on the ID bus
        if args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x10000"
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    d = probe.collect()
    assert d["hat_fw"]["product"] == "Pmod HAT Adaptor"
    assert d["i2c1"] == ["20", "3c"]
    assert d["undervoltage_since_boot"] is True
    v = probe.verdict(d)
    assert "Digilent Pmod HAT Adaptor" in v["summary"]["header"]
    assert "Waveshare PoE HAT (B)" in v["summary"]["header"]
    assert v["summary"]["hat_uuid"] == "6bcd3833-3d1d-4b3e-9ab1-945c71845f3a"


def test_probe_main_prints_text_and_json(fake_root, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["probe.py"])
    probe.main()
    out = capsys.readouterr().out
    assert "power  :" in out
    assert "onboard: eth" in out
    monkeypatch.setattr("sys.argv", ["probe.py", "--json"])
    probe.main()
    assert '"verdict"' in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["fpga.py"])
    fpga.main()
    assert "acorn" in capsys.readouterr().out


# --- tinytapeout: the USB tree, the raw REPL on a pty, and the merge ----------------

TT06_ANSWER = {"machine": "Raspberry Pi Pico with RP2040", "micropython": "1.24.0",
               "sdk": "2.0.4", "sdk_revision": None, "demoboard": "TT06+",
               "carrier_present": True, "carrier_version": None,
               "rom": {"shuttle": "tt06", "repo": "TinyTapeout/tinytapeout-06",
                       "commit": "0f5a1b2c"}, "rom_cached": True,
               "rom_text": "shuttle=tt06\nrepo=TinyTapeout/tinytapeout-06\ncommit=0f5a1b2c\n"}


def _fake_micropython(master, answer, seen):
    """Speak MicroPython's raw REPL protocol on the master side of a pty:
    Ctrl-A gets the banner, code + Ctrl-D gets OK, output, Ctrl-D, Ctrl-D,
    '>' and Ctrl-B the friendly prompt. Records what the host sent."""
    buf = b""
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:
            return
        if not data:
            return
        buf += data
        seen.append(data)
        if b"\x01" in buf:
            os.write(master, b"\r\nraw REPL; CTRL-B to exit\r\n>")
            buf = buf[buf.index(b"\x01") + 1:]
        if b"\x04" in buf:
            code = buf[:buf.index(b"\x04")]
            buf = buf[buf.index(b"\x04") + 1:]
            os.write(master, b"OK")
            if b"import ttboard" in code:
                os.write(master, json.dumps(answer).encode() + b"\r\n")
            os.write(master, b"\x04\x04>")
        if b"\x02" in buf:
            os.write(master, b"\r\nMicroPython v1.24.0 on 2024-10-25\r\n>>> ")
            buf = buf[buf.index(b"\x02") + 1:]


@pytest.fixture
def fake_board():
    """A pty whose far end behaves like a demo board at the REPL; yields
    the tty path and the list of writes the board saw."""
    master, slave = pty.openpty()
    seen: list[bytes] = []
    th = threading.Thread(target=_fake_micropython, args=(master, TT06_ANSWER, seen),
                          daemon=True)
    th.start()
    yield os.ttyname(slave), seen
    os.close(slave)
    os.close(master)


def test_read_repl_speaks_raw_repl(fake_board):
    tty, seen = fake_board
    answer = tinytapeout.read_repl(tty, timeout=5)
    assert answer == TT06_ANSWER
    sent = b"".join(seen)
    assert sent.startswith(b"\r\x03\x03")           # interrupt first
    assert b"\r\x01" in sent
    assert b"\x04" in sent
    assert sent.endswith(b"\r\x02")                  # and back to the friendly REPL
    assert b"_shuttle_props" in sent                 # the cached ROM, never a fresh read


def test_read_repl_never_hangs():
    master, slave = pty.openpty()               # nobody answers on the far end
    try:
        r = tinytapeout.read_repl(os.ttyname(slave), timeout=1)
    finally:
        os.close(slave)
        os.close(master)
    # the pty's other end is this very process, so the holder is named too
    assert r["error"].startswith("no raw REPL prompt (not MicroPython, or busy)")
    assert "has the port open" in r["error"]
    assert r["holder"].endswith(f"(pid {os.getpid()})")
    assert tinytapeout.read_repl("/nonexistent/ttyACM9", timeout=1)["error"].startswith(
        "cannot open /nonexistent/ttyACM9")
    assert tinytapeout.read_repl("/dev/null", timeout=1)["error"].startswith("cannot open")


def test_read_repl_survives_termios_error_and_a_stalled_write(monkeypatch):
    # a tty that vanishes between open and tcsetattr raises termios.error,
    # which is not an OSError; it must become an error record, not a crash
    def gone(path):
        raise termios.error(5, "Input/output error")
    real_open = tinytapeout.open_tty
    monkeypatch.setattr(tinytapeout, "open_tty", gone)
    assert tinytapeout.read_repl("/dev/ttyACM7") == {
        "error": "cannot open /dev/ttyACM7: (5, 'Input/output error')"}
    monkeypatch.setattr(tinytapeout, "open_tty", real_open)
    # a board that never drains its CDC buffer: os.write keeps saying EAGAIN
    master, slave = pty.openpty()
    try:
        monkeypatch.setattr(tinytapeout.os, "write", lambda fd, data: (_ for _ in ()).throw(
            BlockingIOError(11, "Resource temporarily unavailable")))
        r = tinytapeout.read_repl(os.ttyname(slave), timeout=0.5)
    finally:
        os.close(slave)
        os.close(master)
    assert r["error"].startswith("timed out writing to the board")


def test_read_repl_reports_a_traceback_and_junk(monkeypatch):
    def exec_raises(fd, code, timeout):
        return ("", "Traceback (most recent call last):\n  ...\nOSError: boom\n")
    monkeypatch.setattr(tinytapeout, "open_tty", lambda path: os.open("/dev/null", os.O_RDWR))
    monkeypatch.setattr(tinytapeout, "raw_repl_exec", exec_raises)
    assert tinytapeout.read_repl("x")["error"].startswith("snippet raised: Traceback")
    monkeypatch.setattr(tinytapeout, "raw_repl_exec", lambda fd, c, t: ("nothing here", ""))
    assert tinytapeout.read_repl("x")["error"].startswith("no JSON")
    monkeypatch.setattr(tinytapeout, "raw_repl_exec", lambda fd, c, t: ("{bad", ""))
    assert tinytapeout.read_repl("x")["error"].startswith("bad JSON")


def test_tinytapeout_collect_walks_the_usb_tree(fake_root, monkeypatch):
    asked = []

    def fake_read_repl(tty, timeout=10):
        asked.append(tty)
        return TT06_ANSWER
    monkeypatch.setattr(tinytapeout, "read_repl", fake_read_repl)
    t = tinytapeout.collect_tinytapeout()
    assert [u["id"] for u in t["usb"]] == ["2e8a:0005", "2e8a:0003"]
    assert t["usb"][0]["tty"] == "/dev/ttyACM0"
    assert t["usb"][0]["serial"] == "E6614C311B7A7A37"
    assert asked == ["/dev/ttyACM0"], "only the MicroPython device is asked, not BOOTSEL"
    assert [b["kind"] for b in t["boards"]] == ["tinytapeout"]
    assert t["summary"][0]["shuttle"] == "tt06"
    assert t["summary"][0]["demoboard_version"] == "v2.0.1"
    # without the REPL the same device is a candidate only
    t = tinytapeout.collect_tinytapeout(repl=False)
    assert t["repl"] is None
    assert [b["kind"] for b in t["boards"]] == ["rp2-micropython"]
    assert t["summary"] == []


def test_merge_tinytapeout_into_probe_document(fake_root, monkeypatch):
    monkeypatch.setattr(tinytapeout, "read_repl", lambda tty, timeout=10: TT06_ANSWER)
    d = probe.collect()
    d["verdict"] = probe.verdict(d)
    tinytapeout.merge_tinytapeout(d, tinytapeout.collect_tinytapeout())
    assert d["verdict"]["summary"]["tinytapeout"][0]["usb_serial"] == "E6614C311B7A7A37"
    assert d["tinytapeout"]["repl"]["1-1.2"]["sdk"] == "2.0.4"
    assert d["verdict"]["tinytapeout"][0]["chip_url"] == "https://tinytapeout.com/chips/tt06/"


def test_tinytapeout_main_prints_text_and_json(fake_root, capsys, monkeypatch):
    monkeypatch.setattr(tinytapeout, "read_repl", lambda tty, timeout=10: TT06_ANSWER)
    monkeypatch.setattr("sys.argv", ["tinytapeout.py"])
    tinytapeout.main()
    out = capsys.readouterr().out
    assert "tt     : TT06 on demo board TT06+" in out
    monkeypatch.setattr("sys.argv", ["tinytapeout.py", "--json", "--no-repl"])
    tinytapeout.main()
    out = capsys.readouterr().out
    assert '"repl": null' in out
    monkeypatch.setattr("sys.argv", ["tinytapeout.py", "--no-repl"])
    tinytapeout.main()
    assert "candidate (MicroPython RP2 2e8a:0005" in capsys.readouterr().out
    fpga_answer = dict(TT06_ANSWER, rom={"shuttle": "FPGA", "repo": "", "commit": ""})
    tinytapeout.describe(tinytapeout.tinytapeout_verdict(
        {"usb": tinytapeout.usb_candidates(), "repl": {"1-1.2": fpga_answer}}))
    assert "tt     : FPGA breakout" in capsys.readouterr().out
    tinytapeout.describe([])
    assert "none found" in capsys.readouterr().out
