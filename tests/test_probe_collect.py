"""Run the probes' collectors against a fake /proc, /sys and /dev tree, with
every external command stubbed, so the sysfs-walking code is exercised
without a Pi (and without sudo)."""

from __future__ import annotations

import struct

import pytest

from rpi_hwid import fpga, probe


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
    _w(root, "/proc/device-tree/serial-number", "c36b093f773d46b8\0")
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


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    _pi5_tree(tmp_path)
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
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
    assert d["serial"] == "c36b093f773d46b8"
    assert d["revision"] == "a04171"
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
