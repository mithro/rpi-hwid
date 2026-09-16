"""Run the probes' collectors against a fake /proc, /sys and /dev tree, with
every external command stubbed, so the sysfs-walking code is exercised
without a Pi (and without sudo)."""

from __future__ import annotations

import errno
import json
import os
import pty
import struct
import subprocess
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


def _iface(root, name, mac, driver, bus="platform"):
    """One interface in a fake sysfs, with `driver` bound to it."""
    _w(root, f"/sys/class/net/{name}/address", mac + "\n")
    dev = root / f"sys/devices/fake/{name}"
    dev.mkdir(parents=True, exist_ok=True)
    (root / f"sys/class/net/{name}/device").symlink_to(dev)
    drv = root / f"sys/bus/{bus}/drivers/{driver}"
    drv.mkdir(parents=True, exist_ok=True)
    (dev / "driver").symlink_to(drv)


def test_signal_says_which_evidence_settled_each_interface(tmp_path, monkeypatch):
    """The same `onboard` boolean is not equally well established.

    On a Broadcom-OUI board both verdicts are positive: the wired port's MAC
    is one the board derives, and the dongle's provably is not. That is the
    distinction a consumer asserting against `onboard` needs, and it is
    invisible in the boolean.
    """
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    serial = "000000004fe3e7e4"                    # pi-sw1-p10, a 3B+
    own_eth = probe.board_macs(serial)
    eth = next(m for m, k in own_eth.items() if k == "eth")
    _iface(tmp_path, "eth0", eth, "lan78xx")
    _iface(tmp_path, "eth1", "00:e0:4c:68:01:03", "r8152", bus="usb")

    by_name = {i["name"]: i for i in probe.net_interfaces(serial)}
    assert by_name["eth0"]["onboard"] is True
    assert by_name["eth0"]["signal"] == "derived-mac"
    assert by_name["eth1"]["onboard"] is False
    assert by_name["eth1"]["signal"] == "unmatched-mac"


def test_a_board_that_derives_no_macs_says_so_rather_than_implying_proof(
        tmp_path, monkeypatch):
    """A Pi 5's dongle is removable on the driver list's word alone.

    Same boolean as the 3B+ case above, weaker grounds: nothing here
    positively established anything, so the signal must not read the same.
    """
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    _iface(tmp_path, "eth0", "98:fe:54:13:f5:75", "macb")
    _iface(tmp_path, "eth1", "00:e0:4c:68:01:03", "r8152", bus="usb")

    by_name = {i["name"]: i for i in probe.net_interfaces("d88100008543dc30")}
    assert by_name["eth0"]["signal"] == "driver"      # onboard, but not proven
    assert by_name["eth1"]["signal"] == "no-derived-macs"
    assert by_name["eth1"]["onboard"] is False


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
    """The pool's Orange Pi PC, as captured from pi-sw2-p22 on 2026-09-11:
    the device tree U-Boot hands the kernel (model, compatible, the serial#
    it built from the SID), the 32-bit kernel's cpuinfo (which repeats that
    serial and prints a meaningless Revision), MemTotal, the sunxi-sid
    nvmem, the H3's EMAC on dwmac-sun8i, and a CDC-ECM gadget interface.

    The SID words are the ones actually in the board's e-fuses, so the
    CRC-32 rule below is checked against hardware rather than against
    itself: U-Boot's derivation reproduces the device-tree serial exactly.
    There is no /etc/armbian-release: this board netboots the pool's
    Raspbian armhf root, the same one the Pi rigs use."""
    _w(root, "/proc/device-tree/model", "Xunlong Orange Pi PC\0")
    _w(root, "/proc/device-tree/compatible", "xunlong,orangepi-pc\0allwinner,sun8i-h3\0")
    _w(root, "/proc/device-tree/serial-number", "02c000812eb7a34e\0")
    _w(root, "/proc/cpuinfo",
       "processor\t: 0\nmodel name\t: ARMv7 Processor rev 5 (v7l)\nCPU part\t: 0xc07\n"
       "Hardware\t: Allwinner sun8i Family\nRevision\t: 0000\nSerial\t\t: 02c000812eb7a34e\n")
    _w(root, "/proc/meminfo", "MemTotal:        1016504 kB\nMemFree:          612340 kB\n")
    # the e-fuses as read off the board, little-endian words
    _w(root, "/sys/bus/nvmem/devices/sunxi-sid0/nvmem",
       bytes.fromhex("8100c0022046d03514880579940a1c40") + b"\0" * 240)
    _w(root, "/sys/class/net/eth0/address", "02:81:2e:b7:a3:4e\n")
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
    assert d["serial"] == "02c000812eb7a34e"
    assert d["cpuinfo_serial"] == "02c000812eb7a34e"
    assert d["sid"] == ["0x02c00081", "0x35d04620", "0x79058814", "0x401c0a94"]
    assert d["sid_serial"] == "02c000812eb7a34e", "U-Boot's rule reproduces the DT serial"
    assert d["revision"] is None, "cpuinfo's 0000 is not a revision code"
    assert d["mem_kb"] == 1016504
    assert d["armbian"] is None, "the pool's Orange Pis netboot Raspbian, not Armbian"
    assert d["hat_fw"] is None
    assert d["hat_eeproms"] == {}
    assert d["header_i2c"] is None, "this fake tree has no /dev/i2c-0 to scan"
    assert d["throttled"] is None
    assert d["pi5"] is False
    assert calls == [], "no sudo, dtparam or vcgencmd on a board without them"
    ifaces = {i["name"]: i for i in d["interfaces"]}
    assert ifaces["eth0"]["onboard"] is True
    assert ifaces["eth0"]["driver"] == "dwmac-sun8i"
    assert ifaces["eth0"]["kind"] == "eth"
    assert ifaces["usb0"]["onboard"] is False
    assert ifaces["usb0"]["kind"] == "other"
    assert ifaces["usb0"]["usb"] is None, "a gadget is not a USB device on the host side"
    assert d["usb_net"] == []
    assert d["header_buses_read"] == {"id": False, "user": False}
    v = probe.verdict(d)
    # An Orange Pi declares both header buses but has no command to bring
    # one up (Armbian needs a reboot for an overlay), so a tree without
    # them is a header that went unlooked-at -- which is not the same
    # answer as an empty one, and says so.
    assert v["header"] == ["nothing identifiable on the header, and it was not fully read"]
    assert any("header id and user bus could not be read (i2c-1, i2c-0)" in e
               for e in v["evidence"])
    assert "no power sensing" in v["power"]
    assert any(e.startswith("Allwinner SID 0x02c00081") for e in v["evidence"])
    assert not any(e.startswith("Armbian") for e in v["evidence"])
    s = v["summary"]
    assert s["model"] == "Xunlong Orange Pi PC"
    assert s["serial"] == "02c000812eb7a34e"
    assert s["revision"] is None
    assert s["compatible"] == "xunlong,orangepi-pc allwinner,sun8i-h3"
    assert s["memory"] == "1 GB"
    assert s["header"] == []
    assert s["hat_uuid"] is None
    assert s["power_class"] == "undetermined"
    # an Allwinner board derives none either: U-Boot's rule, not Broadcom's
    assert s["macs"] == [
        {"kind": "eth", "mac": "02:81:2e:b7:a3:4e", "signal": "driver"}]
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
    assert d["serial"] == "02c000812eb7a34e"
    assert d["cpuinfo_serial"] == "0000000000000000"
    v = probe.verdict(d)
    assert v["summary"]["serial"] == "02c000812eb7a34e"
    assert probe.headline(d) == "Xunlong Orange Pi PC  serial 02c000812eb7a34e"


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
    assert d["header_i2c"] is None
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
    # a Pi 5 derives no MACs, so the driver list is all that settled this
    assert v["summary"]["macs"] == [
        {"kind": "eth", "mac": "98:fe:54:13:f5:75", "signal": "driver"}]
    assert ifaces["eth1"]["signal"] == "no-derived-macs"
    assert adapter["signal"] == "no-derived-macs"
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


def test_fpga_jtag_without_either_tool_is_an_error_record(fake_root, monkeypatch):
    # Stubbed rather than left to the real `which`: with openocd now a
    # fallback, a developer who happens to have it installed would otherwise
    # have the suite run `sudo openocd` against their own machine's GPIO.
    monkeypatch.setattr(fpga, "sh", lambda *a, **k: "")
    f = fpga.collect_fpga(jtag=True)
    assert f["jtag"] == {"error": "openFPGALoader not installed"}


# The two raw shifts below were taken off real boards, each on a host that
# also had openFPGALoader, and each openFPGALoader answer is what the
# transform has to reproduce. They pin the bit order: a DNA that is subtly
# wrong still looks like a DNA, and names.netv2_name is a pure function of it,
# so a wrong transform would mint a plausible but permanently wrong name onto
# a printed sticker.
DNA_SHIFTS = [
    # rpi5-netv2's NeTV2 (XC7A100T) over the GPIO harness
    ("3a109dc672342e63", "0x00742c4e63b9085c"),
    # an Arty A7-35T (210319B301DE) over its own Digilent FT2232
    ("3A1578A440A14647", "0x00628502251ea85c"),
]


@pytest.mark.parametrize(("raw", "expected"), DNA_SHIFTS)
def test_openocd_dna_transform_matches_openfpgaloader(raw, expected, fake_root, monkeypatch):
    monkeypatch.setattr(fpga, "sh", lambda *a, **k: "/usr/bin/openocd")
    monkeypatch.setattr(fpga, "openocd_adapter", lambda *a, **k: ["adapter driver dummy"])
    monkeypatch.setattr(fpga, "sh_all", lambda *a, **k: (
        "Info : JTAG tap: fpga.tap tap/device found: 0x0362d093 (mfg: 0x049 (Xilinx))\n"
        f"RAWDNA={raw}\n"))
    assert fpga.openocd_probe()["dna"] == expected


def test_openocd_names_the_cable_it_used(fake_root, monkeypatch):
    # fpga_verdict splits netv2 from arty on this key, so it has to be right.
    monkeypatch.setattr(fpga, "sh", lambda *a, **k: "/usr/bin/openocd")
    monkeypatch.setattr(fpga, "openocd_adapter", lambda *a, **k: ["adapter driver dummy"])
    monkeypatch.setattr(fpga, "sh_all", lambda *a, **k:
                        "tap/device found: 0x0362d093 (mfg: 0x049 (Xilinx))")
    assert fpga.openocd_probe()["cable"] == "gpio"
    assert fpga.openocd_probe("210319B301DE")["cable"] == "digilent"


@pytest.mark.parametrize("raw", ["0000000000000000", "ffffffffffffffff"])
def test_openocd_refuses_a_dead_chains_dna(raw, fake_root, monkeypatch):
    # All-zero or all-ones is an absent or unpowered chain. The idcode still
    # stands; only the DNA is withheld, so the board is labelled but unnamed.
    monkeypatch.setattr(fpga, "sh", lambda *a, **k: "/usr/bin/openocd")
    monkeypatch.setattr(fpga, "openocd_adapter", lambda *a, **k: ["adapter driver dummy"])
    monkeypatch.setattr(fpga, "sh_all", lambda *a, **k: (
        f"tap/device found: 0x0362d093 (mfg: 0x049 (Xilinx))\nRAWDNA={raw}\n"))
    res = fpga.openocd_probe()
    assert res["idcode"] == "0x0362d093"
    assert res["dna"] is None


def test_openocd_drives_a_digilent_cable_when_openfpgaloader_is_absent(fake_root, monkeypatch):
    # openocd speaks to the FT2232 too, so an Arty is not left unread either.
    seen = {}

    def fake_sh_all(argv, **k):
        seen["argv"] = argv
        return "tap/device found: 0x0362d093 (mfg: 0x049 (Xilinx))\nRAWDNA=3A1578A440A14647\n"

    monkeypatch.setattr(fpga, "sh", lambda a, **k: (
        "" if "openFPGALoader" in a else "/usr/bin/openocd"))
    monkeypatch.setattr(fpga, "digilent_cables", lambda: [{"serial": "210319B301DE"}])
    monkeypatch.setattr(fpga, "sh_all", fake_sh_all)
    res = fpga.jtag_probe()
    assert res["cable"] == "digilent"
    assert res["dna"] == "0x00628502251ea85c"
    joined = " ".join(seen["argv"])
    assert "digilent-hs1.cfg" in joined
    assert "210319B301DE" in joined


# What GPIO_GET_CHIPINFO returned on the fleet, 2026-09-16.
PI3_CHIPS = [(0, "pinctrl-bcm2835"), (1, "raspberrypi-exp-gpio")]
PI4_CHIPS = [(0, "pinctrl-bcm2711"), (1, "raspberrypi-exp-gpio")]
PI5_CHIPS = [(11, "gpio-brcmstb@107d517c00"), (12, "gpio-brcmstb@107d517c20"),
             (13, "gpio-brcmstb@107d508500"), (14, "gpio-brcmstb@107d508520"),
             (15, "pinctrl-rp1")]


@pytest.mark.parametrize(("chips", "expected"), [
    (PI3_CHIPS, 0), (PI4_CHIPS, 0), (PI5_CHIPS, 15), ([(3, "some-other-gpio")], None)])
def test_the_header_chip_is_found_by_its_driver_label(chips, expected):
    assert fpga.header_gpiochip(chips) == expected


def _linuxgpiod_openocd(monkeypatch):
    """An openocd that has the linuxgpiod driver."""
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15: "Info : Linux GPIOD")


@pytest.mark.parametrize(("model", "chips", "chip"), [
    ("Raspberry Pi 3 Model B Plus Rev 1.3", PI3_CHIPS, 0),
    ("Raspberry Pi 5 Model B Rev 1.1", PI5_CHIPS, 15),
])
def test_every_harness_pin_names_its_chip(fake_root, monkeypatch, model, chips, chip):
    """openocd 0.12 leaves a gpio given without -chip unassigned, and then
    refuses to scan with "Require tck, tms, tdi and tdo gpios". That shipped
    because every other openocd test mocks this function away whole; this one
    reads the commands it actually emits."""
    _w(fake_root, "/proc/device-tree/model", model + "\0")
    _linuxgpiod_openocd(monkeypatch)
    monkeypatch.setattr(fpga, "gpiochips", lambda: chips)
    cmds = fpga.openocd_adapter()
    for sig, pin in (("tck", 4), ("tms", 17), ("tdi", 27), ("tdo", 22)):
        assert f"catch {{adapter gpio {sig} -chip {chip} {pin}}}" in cmds
    # the explicit form comes after the deprecated one, so it is what sticks
    last_legacy = max(i for i, c in enumerate(cmds) if "linuxgpiod_" in c)
    first_modern = min(i for i, c in enumerate(cmds) if "adapter gpio" in c)
    assert last_legacy < first_modern


def test_a_pi5_whose_header_chip_cannot_be_found_is_not_guessed_at(fake_root, monkeypatch):
    """Anywhere else chip 0 is the header; on a Pi 5 it is something else,
    and driving the harness pins on the wrong controller is not a read."""
    _w(fake_root, "/proc/device-tree/model", "Raspberry Pi 5 Model B Rev 1.1\0")
    _linuxgpiod_openocd(monkeypatch)
    monkeypatch.setattr(fpga, "gpiochips", list)
    assert fpga.openocd_adapter() is None

    _w(fake_root, "/proc/device-tree/model", "Raspberry Pi 3 Model B Plus Rev 1.3\0")
    assert "catch {adapter gpio tck -chip 0 4}" in fpga.openocd_adapter()


def test_openocd_reads_the_chain_when_openfpgaloader_is_installed_but_cannot(
        fake_root, monkeypatch):
    """The sw1 rigs' openFPGALoader was built without libgpiod. It is
    installed, so the old fallback (only when absent) never fired, and it said
    why on stderr, which was discarded -- five NeTV2s vanished as raw ""."""
    monkeypatch.setattr(fpga, "digilent_cables", list)
    monkeypatch.setattr(fpga, "sh", lambda args, timeout=15:
                        "/usr/bin/openFPGALoader" if args[:1] == ["which"] else "")
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15: "error : libgpiod not found")
    monkeypatch.setattr(fpga, "openocd_probe", lambda serial=None: {
        "idcode": "0x0362d093", "tool": "openocd", "dna": "0x0038a44663258854", "cable": "gpio"})
    res = fpga.jtag_probe()
    assert res["tool"] == "openocd"
    assert res["dna"] == "0x0038a44663258854"          # netv2-basil, pi-sw1-p10


@pytest.mark.parametrize(("idcode", "profile"), [
    ("0x362d093", "arty_a7_35t"),
    ("0x3631093", "arty_a7_100t"),
    ("0x13631093", "arty_a7_100t"),    # a later silicon revision of the 100T
    ("0x23631093", "arty_a7_100t"),    # one no string list had written into it
])
def test_the_arty_flash_profile_follows_the_die_not_the_spelling(fake_root, monkeypatch,
                                                                idcode, profile):
    """The profile names the part the spiOverJtag bridge is built for; loading
    the 35T bridge onto a 100T is how a flash read goes wrong. It used to match
    a list of idcode strings, so a revision not written into the list got the
    35T bridge."""
    monkeypatch.setattr(fpga, "digilent_cables", lambda: [{"serial": "210319B301DE"}])
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15:
                        f"idcode {idcode}\nfamily artix a7")
    seen = []

    def fake_sh(args, timeout=15):
        seen.append(args)
        if args[:1] == ["which"]:
            return "/usr/bin/openFPGALoader"
        return ""
    monkeypatch.setattr(fpga, "sh", fake_sh)
    fpga.jtag_probe(want_flash=True)
    flash = [a for a in seen if "-f" in a]
    assert flash
    assert flash[0][flash[0].index("-b") + 1] == profile


def test_a_chain_neither_tool_can_read_says_why_twice(fake_root, monkeypatch):
    monkeypatch.setattr(fpga, "digilent_cables", list)
    monkeypatch.setattr(fpga, "sh", lambda args, timeout=15:
                        "/usr/bin/openFPGALoader" if args[:1] == ["which"] else "")
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15: "error : libgpiod not found")
    monkeypatch.setattr(fpga, "openocd_probe", lambda serial=None: {
        "idcode": None, "tool": "openocd", "raw": "scan chain interrogation failed: all zeroes"})
    res = fpga.jtag_probe()
    assert res["idcode"] is None
    assert "libgpiod not found" in res["raw"]
    assert "all zeroes" in res["openocd"]


def test_peripheral_base_follows_the_board(fake_root, monkeypatch, tmp_path):
    for model, base in [("Raspberry Pi 4 Model B Rev 1.4", "0xFE000000"),
                        ("Raspberry Pi 3 Model B Plus Rev 1.3", "0x3F000000"),
                        ("Raspberry Pi Model B Rev 2", "0x20000000")]:
        _w(tmp_path, "/proc/device-tree/model", model)
        monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
        assert fpga.peripheral_base() == base


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

    def fake_sh(args, timeout=15):
        if args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x10000"
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    # The I2C seam: a Pi's user bus is 1, and the Waveshare PoE HAT (B)'s
    # two chips answer there. Nothing answers on the ID bus (0).
    monkeypatch.setattr(probe, "i2c_scan", lambda bus, **kw: ["20", "3c"] if bus == 1 else [])
    monkeypatch.setattr(probe, "eeprom_read", lambda bus, addr, length=256: None)
    d = probe.collect()
    assert d["hat_fw"]["product"] == "Pmod HAT Adaptor"
    assert d["header_i2c"] == ["20", "3c"]
    assert d["undervoltage_since_boot"] is True
    v = probe.verdict(d)
    assert "Digilent Pmod HAT Adaptor" in v["summary"]["header"]
    assert "Waveshare PoE HAT (B)" in v["summary"]["header"]
    assert v["summary"]["hat_uuid"] == "6bcd3833-3d1d-4b3e-9ab1-945c71845f3a"


def test_a_user_bus_that_is_off_is_brought_up_for_the_scan_and_put_back(fake_root, monkeypatch):
    """Most of the fleet leaves the header's user bus disabled, and a HAT
    known only by the devices it puts there is invisible without it. So the
    bus gets the same on-demand enable the ID bus has -- and the same
    tidying up, because the host is not ours to reconfigure."""
    _w(fake_root, "/dev/i2c-0", "")                  # the ID bus is already up
    calls = []

    def fake_sh(args, timeout=15):
        calls.append(args)
        if args == ["sudo", "dtparam", "i2c_arm=on"]:
            _w(fake_root, "/dev/i2c-1", "")          # the overlay creates it
        elif args == ["sudo", "dtparam", "-r"]:
            (fake_root / "dev/i2c-1").unlink()       # and removing it takes it away
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    monkeypatch.setattr(probe, "i2c_scan", lambda bus, **kw: ["20", "3c"] if bus == 1 else [])
    monkeypatch.setattr(probe, "eeprom_read", lambda bus, addr, length=256: None)

    d = probe.collect()
    assert d["header_i2c"] == ["20", "3c"], "the HAT's two chips, on a bus that was off"
    assert d["header_buses_read"] == {"id": True, "user": True}
    assert ["sudo", "dtparam", "i2c_arm=on"] in calls
    assert ["sudo", "dtparam", "-r"] in calls, "brought up here, so put back here"
    assert not (fake_root / "dev/i2c-1").exists(), "left as it was found"
    # The ID bus was already up, so it is not taken down with it.
    assert calls.count(["sudo", "dtparam", "-r"]) == 1
    assert "Waveshare PoE HAT (B)" in probe.verdict(d)["summary"]["header"]


def test_probe_main_prints_text_and_json(fake_root, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["probe.py"])
    probe.main()
    out = capsys.readouterr().out
    assert "power   :" in out
    assert "onboard : eth" in out
    # the line says not just what was decided but what decided it
    assert "(no-derived-macs)" in out
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


# --- taking the port from the service that holds it ---------------------------

def test_unit_for_pid_names_only_a_system_service(tmp_path, monkeypatch):
    monkeypatch.setattr(tinytapeout, "ROOT", str(tmp_path))
    _w(tmp_path, "/proc/11559/cgroup", "0::/system.slice/fpgas-tt.service\n")
    assert tinytapeout.unit_for_pid("11559") == "fpgas-tt.service"
    # cgroup v1 writes a line per controller; the systemd one carries the unit
    _w(tmp_path, "/proc/2/cgroup",
       "12:devices:/system.slice/foo.service\n1:name=systemd:/system.slice/foo.service\n")
    assert tinytapeout.unit_for_pid("2") == "foo.service"
    # anything under a user's manager is that user's login, never a rig's
    # bridge: not even a user's own service is named
    _w(tmp_path, "/proc/3/cgroup",
       "0::/user.slice/user-1000.slice/user@1000.service/app.slice/bridge.service\n")
    assert tinytapeout.unit_for_pid("3") is None
    # a tmux pane, as captured on ten64 on 2026-09-13: the only .service in
    # its path is the user manager, and stopping that ended every login,
    # terminal and tmux session the user had
    _w(tmp_path, "/proc/5/cgroup",
       "0::/user.slice/user-1001.slice/user@1001.service/app.slice/"
       "tmux-spawn-61c6286b-b978-44e3-b06c-0cddf877b052.scope\n")
    assert tinytapeout.unit_for_pid("5") is None
    _w(tmp_path, "/proc/6/cgroup", "0::/user.slice/user-1001.slice/user@1001.service/init.scope\n")
    assert tinytapeout.unit_for_pid("6") is None
    # a scope beneath a system service is not the service's own process either
    _w(tmp_path, "/proc/7/cgroup", "0::/system.slice/foo.service/payload.scope\n")
    assert tinytapeout.unit_for_pid("7") is None
    # a login session is a scope: nothing here to stop and start again
    _w(tmp_path, "/proc/4/cgroup", "0::/user.slice/user-1000.slice/session-3.scope\n")
    assert tinytapeout.unit_for_pid("4") is None
    assert tinytapeout.unit_for_pid("999") is None          # no such process


def test_on_raspberry_pi_reads_the_device_tree_model(tmp_path, monkeypatch):
    monkeypatch.setattr(tinytapeout, "ROOT", str(tmp_path))
    assert tinytapeout.on_raspberry_pi() is False, "no device tree: not a Pi"
    _w(tmp_path, "/proc/device-tree/model", "Traverse Ten64\0")
    assert tinytapeout.on_raspberry_pi() is False
    _w(tmp_path, "/proc/device-tree/model", "Xunlong Orange Pi PC\0")
    assert tinytapeout.on_raspberry_pi() is False
    _w(tmp_path, "/proc/device-tree/model", "Raspberry Pi 4 Model B Rev 1.4\0")
    assert tinytapeout.on_raspberry_pi() is True


def test_own_pids_include_this_process_and_its_parent():
    pids = tinytapeout.own_pids()
    assert str(os.getpid()) in pids
    assert str(os.getppid()) in pids


@pytest.fixture
def systemctl(monkeypatch):
    """Record every command the service control runs, in order, and let a
    test declare which of them fail. Yields (calls, fails); a key of `fails`
    that appears in a command's text gives that command its result."""
    calls: list[list[str]] = []
    fails: dict[str, tuple[int, str]] = {}

    def fake_run_cmd(args, timeout=20):
        calls.append(list(args))
        for needle, result in fails.items():
            if needle in " ".join(args):
                return result
        return (0, "")
    monkeypatch.setattr(tinytapeout, "run_cmd", fake_run_cmd)
    return calls, fails


@pytest.fixture
def busy_board(monkeypatch):
    """A port that is busy on the first open and answers on the next, held
    by pid 11559 of fpgas-tt.service."""
    opens = []

    def open_tty(path):
        opens.append(path)
        if len(opens) == 1:
            raise OSError(errno.EBUSY, "Device or resource busy")
        return os.open("/dev/null", os.O_RDWR)
    monkeypatch.setattr(tinytapeout, "open_tty", open_tty)
    monkeypatch.setattr(tinytapeout, "port_holder_info", lambda tty: ("python3", "11559"))
    monkeypatch.setattr(tinytapeout, "unit_for_pid", lambda pid: "fpgas-tt.service")
    monkeypatch.setattr(tinytapeout, "on_raspberry_pi", lambda: True)
    monkeypatch.setattr(tinytapeout, "raw_repl_exec",
                        lambda fd, code, timeout: (json.dumps(TT06_ANSWER), ""))
    return opens


def test_a_busy_port_is_taken_from_its_service_and_given_back(busy_board, systemctl):
    calls, _ = systemctl
    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=5)

    assert answer["sdk"] == TT06_ANSWER["sdk"], "the board was read once the port was free"
    assert answer["service"] == {"unit": "fpgas-tt.service", "stopped": True,
                                 "deadman": True, "restored": True}
    ran = [" ".join(c) for c in calls]
    armed = next(i for i, c in enumerate(ran) if "systemd-run" in c)
    # matched exactly, not by suffix: what the deadman is armed *with* is
    # itself "systemctl start fpgas-tt.service", so a loose match here
    # would find the arming and call it the restart
    stopped = ran.index("sudo -n systemctl stop fpgas-tt.service")
    started = ran.index("sudo -n systemctl start fpgas-tt.service")
    # the deadman is armed before the stop, never after: the window it
    # covers has to begin where the risk does
    assert armed < stopped < started
    assert f"--on-active={tinytapeout.RESTORE_DELAY}s" in ran[armed]
    assert "--unit=" + tinytapeout.RESTORE_UNIT in ran[armed]
    # and it is cancelled only once the service is back by the normal path
    assert ran[-1].endswith(f"stop {tinytapeout.RESTORE_UNIT}.timer")
    assert all(c[:2] == ["sudo", "-n"] for c in calls), "every one of these needs root"


def test_a_failed_restart_leaves_the_deadman_armed(busy_board, systemctl):
    calls, fails = systemctl
    fails["start fpgas-tt.service"] = (1, "Job for fpgas-tt.service failed")

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=5)

    assert answer["service"]["restored"] is False
    assert answer["service"]["restore_error"].startswith("Job for")
    # the timer is now the only thing left that will bring the service
    # back, so it must not be cancelled: the one mention of it is the
    # stale-timer clear that arming does first
    assert sum(tinytapeout.RESTORE_UNIT in " ".join(c) for c in calls) == 2
    assert "systemd-run" in " ".join(calls[2])


def test_nothing_is_stopped_without_passwordless_sudo(busy_board, systemctl):
    calls, fails = systemctl
    fails["true"] = (1, "sudo: a password is required")

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=5)

    assert "no passwordless sudo" in answer["error"]
    assert "fpgas-tt.service" in answer["error"]
    assert not any("systemctl stop" in " ".join(c) for c in calls)


def test_nothing_is_stopped_when_the_holder_is_not_a_service(busy_board, systemctl,
                                                             monkeypatch):
    calls, _ = systemctl
    monkeypatch.setattr(tinytapeout, "unit_for_pid", lambda pid: None)

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=5)

    assert "python3 (pid 11559) is not part of a system service" in answer["error"]
    assert calls == []


def test_nothing_is_stopped_on_a_machine_that_is_not_a_pi(busy_board, systemctl, monkeypatch):
    calls, _ = systemctl
    monkeypatch.setattr(tinytapeout, "on_raspberry_pi", lambda: False)

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1)

    assert "not a Raspberry Pi" in answer["error"]
    assert "service" not in answer
    assert calls == [], "not even sudo is asked about off a Pi"


@pytest.mark.parametrize("unit", ["user@1001.service", "tmux-server.service", "ssh.service",
                                  "getty@ttyACM0.service", "fpgas-tt-other.service"])
def test_only_the_tiny_tapeout_bridge_is_ever_stopped(busy_board, systemctl, monkeypatch, unit):
    calls, _ = systemctl
    monkeypatch.setattr(tinytapeout, "unit_for_pid", lambda pid: unit)

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1)

    assert f"{unit} holds it and is left alone" in answer["error"]
    assert "service" not in answer
    assert calls == [], "not even sudo is asked about"


def test_a_port_held_by_the_probe_itself_is_never_taken(busy_board, systemctl, monkeypatch):
    calls, _ = systemctl
    monkeypatch.setattr(tinytapeout, "port_holder_info",
                        lambda tty: ("python3", str(os.getppid())))

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1)

    assert "is this probe itself" in answer["error"]
    assert calls == []


@pytest.mark.parametrize(("unit", "pi"), [("user@1001.service", True),
                                          ("fpgas-tt.service", False)])
def test_take_port_from_refuses_on_its_own(systemctl, monkeypatch, unit, pi):
    calls, _ = systemctl
    monkeypatch.setattr(tinytapeout, "on_raspberry_pi", lambda: pi)
    with pytest.raises(ValueError, match="refusing to stop"):
        tinytapeout.take_port_from("/dev/ttyACM0", 1, unit)
    assert calls == []


@pytest.mark.parametrize(("args", "shell"), [
    (["sudo", "-n", "true"], False), (["/usr/bin/systemctl", "stop", "x"], False),
    ("sudo -n true", True), ("echo hi; systemd-run true", True),
])
def test_tests_cannot_start_privileged_commands_here(args, shell):
    # the guard in conftest.py fails the test before the process exists,
    # so none of these ever reaches the machine running the suite
    with pytest.raises(pytest.fail.Exception, match="tried to run"):
        subprocess.run(args, shell=shell)


def test_the_guard_cannot_be_swallowed_by_run_cmd():
    # run_cmd turns every OSError into a return code; the guard's failure
    # is not an OSError, so it still ends the test
    with pytest.raises(pytest.fail.Exception, match="tried to run"):
        tinytapeout.run_cmd(["sudo", "-n", "true"])
    with pytest.raises(pytest.fail.Exception, match="tried to run"):
        tinytapeout.can_sudo()


def test_no_stop_service_leaves_a_busy_port_alone(busy_board, systemctl):
    """--no-stop-service leaves the service running AND leaves its port
    alone. It does not open the port instead: the bridge does not hold it
    exclusively, so the open would succeed and the two readers would split
    the board's answers between them."""
    calls, _ = systemctl
    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1, take_port=False)
    assert "fpgas-tt.service holds /dev/ttyACM0" in answer["error"]
    assert "--no-stop-service" in answer["error"]
    assert answer["holder"] == "python3 (pid 11559)"
    assert "service" not in answer
    assert calls == [], "the service was never even asked about"


def test_a_port_that_stays_busy_after_the_stop_is_reported(busy_board, systemctl,
                                                           monkeypatch):
    def always_busy(path):
        raise OSError(errno.EBUSY, "Device or resource busy")
    monkeypatch.setattr(tinytapeout, "open_tty", always_busy)

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1)

    assert "was stopped but /dev/ttyACM0 stayed busy" in answer["error"]
    # still handed back, even though the read got nothing out of it
    assert answer["service"]["restored"] is True


def test_service_note_says_what_happened_to_the_unit():
    note = tinytapeout.service_note
    assert note({"unit": "x.service", "stopped": True, "restored": True}) == (
        "x.service was stopped for the read and started again")
    assert note({"unit": "x.service", "stopped": False}) == (
        "x.service holds the port and was left running")
    assert note({"unit": "x.service", "stopped": True, "restored": False,
                 "restore_error": "boom", "deadman": True}) == (
        "x.service was stopped for the read and DID NOT restart: boom (a timer will retry)")


def test_the_verdict_carries_what_was_done_to_the_service(busy_board, systemctl):
    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=5)
    d = {"usb": [{"id": "2e8a:0005", "path": "1-1.2", "serial": "E6", "tty": "/dev/ttyACM0",
                  "manufacturer": "MicroPython", "product": "Board in FS mode"}],
         "repl": {"1-1.2": answer}}
    board = tinytapeout.tinytapeout_verdict(d)[0]
    assert "fpgas-tt.service was stopped for the read and started again" in board["how"]


def test_run_cmd_never_raises():
    assert tinytapeout.run_cmd(["/nonexistent/binary"])[0] == 127
    assert tinytapeout.run_cmd(["sleep", "5"], timeout=1) == (124, "timed out after 1 s")
    assert tinytapeout.run_cmd(["echo", "hello"]) == (0, "hello")


def test_tinytapeout_collect_walks_the_usb_tree(fake_root, monkeypatch):
    asked = []

    def fake_read_repl(tty, timeout=10, take_port=True):
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
    monkeypatch.setattr(tinytapeout, "read_repl",
                        lambda tty, timeout=10, take_port=True: TT06_ANSWER)
    d = probe.collect()
    d["verdict"] = probe.verdict(d)
    tinytapeout.merge_tinytapeout(d, tinytapeout.collect_tinytapeout())
    assert d["verdict"]["summary"]["tinytapeout"][0]["usb_serial"] == "E6614C311B7A7A37"
    assert d["tinytapeout"]["repl"]["1-1.2"]["sdk"] == "2.0.4"
    assert d["verdict"]["tinytapeout"][0]["chip_url"] == "https://tinytapeout.com/chips/tt06/"


def test_tinytapeout_main_prints_text_and_json(fake_root, capsys, monkeypatch):
    monkeypatch.setattr(tinytapeout, "read_repl",
                        lambda tty, timeout=10, take_port=True: TT06_ANSWER)
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


def test_armbian_release_is_read_when_a_board_has_one(opi_root):
    """The pool's Orange Pis netboot Raspbian, so none of them carries
    /etc/armbian-release; a board that does is still reported, since
    Armbian is what an Orange Pi normally runs."""
    root, _calls = opi_root
    _w(root, "/etc/armbian-release",
       "# PLEASE DO NOT EDIT THIS FILE\nBOARD=orangepipc\nBOARD_NAME=\"Orange Pi PC\"\n"
       "BOARDFAMILY=sun8i\nLINUXFAMILY=sunxi\nARCH=arm\nBOARD_TYPE=csc\nBRANCH=current\n"
       "VERSION=26.8.0-trunk.170\n")
    d = probe.collect()
    assert d["armbian"]["VERSION"] == "26.8.0-trunk.170"
    assert d["armbian"]["BOARD_NAME"] == "Orange Pi PC"
    assert any(e.startswith("Armbian 26.8.0-trunk.170 on board id orangepipc")
               for e in probe.verdict(d)["evidence"])


# The first 64 bytes of the ID EEPROM on the Digilent Pmod HAT Adaptor fitted
# to pi-sw2-p22, the fleet's Orange Pi PC, read off i2c-1 on 2026-09-13. A
# HAT does not know what it is plugged into: this is byte for byte the
# format a Pi would read, which is the whole reason the probe can share one
# code path across the two boards.
OPI_PMOD_EEPROM = bytes.fromhex(
    "522d5069010002006c0000000100000030000000"
    "aaff3b3624884da972423c0955f9126c01000100"
    "0810446967696c656e74506d6f64204841542041646170746f72"
)


def test_orange_pi_reads_a_hat_eeprom_off_its_own_id_bus(opi_root, monkeypatch):
    """An Orange Pi is probed exactly as a Pi is. Its header's ID pins are
    i2c-1, not the Pi's i2c-0, so a scan of the wrong bus would find
    nothing: this pins the bus the board declares as much as the decode."""
    root, _calls = opi_root
    _w(root, "/dev/i2c-0", "")
    _w(root, "/dev/i2c-1", "")

    read_from = []

    def fake_eeprom_read(bus, addr, length=256):
        read_from.append((bus, addr))
        if bus == 1 and addr == 0x50:
            return OPI_PMOD_EEPROM.ljust(length, b"\xff")
        return None

    monkeypatch.setattr(probe, "eeprom_read", fake_eeprom_read)
    monkeypatch.setattr(probe, "i2c_scan", lambda bus, **kw: [])
    d = probe.collect()

    assert {bus for bus, _addr in read_from} == {1}, "only the declared ID bus is read"
    assert list(d["hat_eeproms"]) == ["0x50"]
    assert d["hat_eeproms"]["0x50"]["product"] == "Pmod HAT Adaptor"
    assert d["hat_eeproms"]["0x50"]["vendor"] == "Digilent"
    v = probe.verdict(d)
    assert "Pmod HAT Adaptor" in v["summary"]["header"]


def test_a_board_with_no_declared_header_buses_is_not_scanned(fake_root, monkeypatch):
    """An unknown board declares no buses, so nothing is read. Scanning
    whatever /dev/i2c-* happens to exist would be unsafe: the Orange Pi's
    i2c-3 is an HDMI DDC line that acknowledges all 117 addresses."""
    _w(fake_root, "/proc/device-tree/model", "Some Other SBC\0")
    _w(fake_root, "/proc/device-tree/compatible", "vendor,other-sbc\0")
    _w(fake_root, "/dev/i2c-0", "")
    _w(fake_root, "/dev/i2c-3", "")

    def boom(*a, **k):
        raise AssertionError("an undeclared bus must never be touched")

    monkeypatch.setattr(probe, "eeprom_read", boom)
    monkeypatch.setattr(probe, "i2c_scan", boom)
    d = probe.collect()
    assert d["board"] == "other"
    assert d["hat_eeproms"] == {}
    assert d["header_i2c"] is None


def test_a_held_port_is_never_shared(busy_board, systemctl, monkeypatch):
    """A port someone else holds is taken or left alone, never shared.

    The holder does not have to hold it exclusively -- the rig's bridge
    does not -- so open() would succeed and the two readers would split the
    board's answers, which reads as a mute board and writes into a stream
    someone else is reading. So nothing is opened when the port cannot be
    had outright, whether that is because --no-stop-service was given or
    because the holder is not one this probe may stop.
    """
    calls, _ = systemctl
    opened = []
    monkeypatch.setattr(tinytapeout, "open_tty",
                        lambda path: opened.append(path) or os.open("/dev/null", os.O_RDWR))

    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1, take_port=False)
    assert opened == [], "--no-stop-service must not open a port the bridge is streaming"
    assert "--no-stop-service" in answer["error"]
    assert answer["holder"] == "python3 (pid 11559)"
    assert calls == [], "and it asks nothing of systemd"

    monkeypatch.setattr(tinytapeout, "unit_for_pid", lambda pid: "something-else.service")
    answer = tinytapeout.read_repl("/dev/ttyACM0", timeout=1)
    assert opened == [], "a holder that may not be stopped is not shared with either"
    assert "something-else.service holds it and is left alone" in answer["error"]


def test_a_port_this_probe_itself_holds_is_still_read(monkeypatch):
    """The probe's own fd is not a competing reader: the pty tests hold one
    end themselves, and a rig where the probe's own parent has the port is
    not a port being streamed by anyone else."""
    monkeypatch.setattr(tinytapeout, "port_holder_info",
                        lambda tty: ("pytest", str(os.getpid())))
    assert tinytapeout.foreign_holder("/dev/ttyACM0") is None
