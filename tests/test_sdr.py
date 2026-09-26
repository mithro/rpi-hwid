"""The SDR probe (rpi_hwid.sdr): what it finds in a sysfs tree and in what
the tools it runs answer. Every tool answer here was captured from the
fleet's own radios on 2026-09-26 -- rpi-sdr-kraken, rpi-sdr-pluto,
rpi-sdr-xsdr and rpi-sdr-rtlsdr-v3 -- read without opening a device."""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tokenize

import pytest

from rpi_hwid import fpga, sdr

# `iio_attr -u ip:192.168.2.1 -C` on rpi-sdr-pluto
PLUTO_CONTEXT = """IIO context with 9 attributes:
hw_model: Analog Devices PlutoSDR Rev.B (Z7010-AD9363A)
hw_model_variant: 1
hw_serial: 10447354119600022000120009f61e2b82
fw_version: v0.39
ad9361-phy,xo_correction: 40000000
ad9361-phy,model: ad9363a
local,kernel: 6.1.0-gf3da30df6004
uri: ip:192.168.2.1
ip,ip-addr: 192.168.2.1
"""
PLUTO_RX_CHANNELS = """\
dev 'cf-ad9361-lpc', channel 'voltage0' (input, index: 0, format: le:S12/16>>0), found 6 channel-specific attributes
dev 'cf-ad9361-lpc', channel 'voltage1' (input, index: 1, format: le:S12/16>>0), found 6 channel-specific attributes
"""  # noqa: E501
PLUTO_TX_CHANNELS = """\
dev 'cf-ad9361-dds-core-lpc', channel 'voltage0' (output, index: 0, format: le:S16/16>>0), found 4 channel-specific attributes
dev 'cf-ad9361-dds-core-lpc', channel 'voltage1' (output, index: 1, format: le:S16/16>>0), found 4 channel-specific attributes
dev 'cf-ad9361-dds-core-lpc', channel 'altvoltage3', id 'TX1_Q_F2' (output), found 6 channel-specific attributes
dev 'cf-ad9361-dds-core-lpc', channel 'altvoltage1', id 'TX1_I_F2' (output), found 6 channel-specific attributes
dev 'cf-ad9361-dds-core-lpc', channel 'altvoltage0', id 'TX1_I_F1' (output), found 6 channel-specific attributes
dev 'cf-ad9361-dds-core-lpc', channel 'altvoltage2', id 'TX1_Q_F1' (output), found 6 channel-specific attributes
"""  # noqa: E501
PLUTO_ANSWERS = {
    ("-C",): PLUTO_CONTEXT,
    ("-c", "cf-ad9361-lpc"): PLUTO_RX_CHANNELS,
    ("-c", "cf-ad9361-dds-core-lpc"): PLUTO_TX_CHANNELS,
    ("-c", "ad9361-phy", "altvoltage0", "frequency_available"): "[325000000 1 3800000000]\n",
    ("-c", "ad9361-phy", "altvoltage1", "frequency_available"): "[325000000 1 3800000000]\n",
    ("-c", "-i", "ad9361-phy", "voltage0", "sampling_frequency_available"):
        "[520833 1 30720000]\n",
    ("-c", "-o", "ad9361-phy", "voltage0", "sampling_frequency_available"):
        "[520833 1 30720000]\n",
    ("-c", "-i", "ad9361-phy", "voltage0", "rf_bandwidth_available"): "[200000 1 56000000]\n",
    ("-c", "-o", "ad9361-phy", "voltage0", "rf_bandwidth_available"): "[200000 1 40000000]\n",
}


def _usb(root, path, vid, pid, manufacturer=None, product=None, serial=None,
         bcd="0100", speed="480"):
    d = root / "sys/bus/usb/devices" / path
    d.mkdir(parents=True)
    for name, value in (("idVendor", vid), ("idProduct", pid), ("manufacturer", manufacturer),
                        ("product", product), ("serial", serial), ("bcdDevice", bcd),
                        ("speed", speed)):
        if value is not None:
            (d / name).write_text(value + "\n")
    return d


def _pci(root, slot, vendor, device, driver=None, sub=("0x10ee", "0x0007")):
    d = root / "sys/bus/pci/devices" / slot
    d.mkdir(parents=True)
    for name, value in (("vendor", vendor), ("device", device), ("class", "0x058000"),
                        ("subsystem_vendor", sub[0]), ("subsystem_device", sub[1]),
                        ("revision", "0x00"), ("current_link_speed", "5.0 GT/s PCIe"),
                        ("current_link_width", "1"), ("max_link_speed", "5.0 GT/s PCIe"),
                        ("max_link_width", "2")):
        (d / name).write_text(value + "\n")
    if driver:
        drv = root / "sys/bus/pci/drivers" / driver
        drv.mkdir(parents=True, exist_ok=True)
        (d / "driver").symlink_to(drv)
    return d


@pytest.fixture
def kraken_root(tmp_path, monkeypatch):
    """rpi-sdr-kraken's USB: the KrakenSDR's own USB2517 hub and its five
    RTL2832Us, serials 1000-1004 on ports 5, 4, 3, 2, 6."""
    _usb(tmp_path, "1-1", "0424", "2517", bcd="0002")
    for port, serial in (("2", "1003"), ("3", "1002"), ("4", "1001"), ("5", "1000"),
                         ("6", "1004")):
        _usb(tmp_path, "1-1." + port, "0bda", "2838", "Realtek", "RTL2838UHIDIR", serial)
    _usb(tmp_path, "usb1", "1d6b", "0002", "Linux xhci-hcd", "xHCI Host Controller",
         "xhci-hcd.0", bcd="0618")
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))
    return tmp_path


def test_the_kraken_is_one_radio_of_five_channels(kraken_root, monkeypatch):
    monkeypatch.setattr(sdr, "sdr_sh", lambda args, timeout=15: (127, "", "not here"))
    s = sdr.collect_sdr()
    (k,) = s["summary"]
    assert k["kind"] == "krakensdr"
    # in channel order, which is serial order, not port order
    assert k["channel_serials"] == ["1000", "1001", "1002", "1003", "1004"]
    assert k["vidpid"] == "0bda:2838"
    assert k["hub"] == "0424:2517"
    assert "usb_serial" not in k
    (board,) = s["devices"]
    assert "1-1" in board["how"]
    assert "1000-1004" in board["how"]


def test_a_lone_rtl2832u_is_an_rtl_sdr_and_keeps_its_serial(tmp_path, monkeypatch):
    # rpi-sdr-rtlsdr-v3: a VIA hub, the dongle, and a u-blox GPS beside it
    _usb(tmp_path, "1-1", "2109", "3431", product="USB2.0 Hub", bcd="0421")
    _usb(tmp_path, "1-1.2", "0bda", "2838", "Realtek", "RTL2838UHIDIR", "00000001")
    _usb(tmp_path, "1-1.4", "1546", "01a7", "u-blox AG - www.u-blox.com",
         "u-blox 7 - GPS/GNSS Receiver", speed="12")
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))
    monkeypatch.setattr(sdr, "sdr_sh", lambda args, timeout=15: (127, "", "not here"))
    (r,) = sdr.collect_sdr()["summary"]
    assert r == {"kind": "rtl-sdr", "vidpid": "0bda:2838", "usb_serial": "00000001",
                 "manufacturer": "Realtek", "product": "RTL2838UHIDIR"}


def test_five_rtl_dongles_that_are_not_a_kraken_stay_five_dongles(tmp_path, monkeypatch):
    # serials a Kraken would never carry: five separate radios
    _usb(tmp_path, "1-1", "0424", "2517")
    for port in range(1, 6):
        _usb(tmp_path, f"1-1.{port}", "0bda", "2838", "Realtek", "RTL2838UHIDIR",
             f"0000000{port}")
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))
    monkeypatch.setattr(sdr, "sdr_sh", lambda args, timeout=15: (127, "", "not here"))
    kinds = [d["kind"] for d in sdr.collect_sdr()["summary"]]
    assert kinds == ["rtl-sdr"] * 5


@pytest.fixture
def pluto_root(tmp_path, monkeypatch):
    """rpi-sdr-pluto: the Pluto on USB, its RNDIS link named eth-pluto."""
    _usb(tmp_path, "3-1", "0456", "b673", "Analog Devices Inc.", "PlutoSDR (ADALM-PLUTO)",
         "10447354119600022000120009f61e2b82", bcd="0601")
    net = tmp_path / "sys/bus/usb/devices/3-1:1.0/net/eth-pluto"
    net.mkdir(parents=True)
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))
    calls = []

    def fake(args, timeout=15):
        calls.append(list(args))
        if args[:4] == ["ip", "-4", "neigh", "show"]:
            return 0, "192.168.2.1 lladdr 00:05:f7:45:4d:bb REACHABLE\n", ""
        if args[:3] == ["iio_attr", "-u", "ip:192.168.2.1"]:
            out = PLUTO_ANSWERS.get(tuple(args[3:]))
            return (0, out, "") if out is not None else (1, "", "no such attribute")
        return 127, "", "not here"

    monkeypatch.setattr(sdr, "sdr_sh", fake)
    return calls


def test_the_pluto_is_read_over_its_network_context(pluto_root):
    s = sdr.collect_sdr()
    (p,) = s["summary"]
    assert p["kind"] == "pluto"
    assert p["usb_serial"] == "10447354119600022000120009f61e2b82"
    assert p["hw_serial"] == p["usb_serial"]
    assert p["hw_model"] == "Analog Devices PlutoSDR Rev.B (Z7010-AD9363A)"
    assert p["fw_version"] == "v0.39"
    assert p["rf_chip"] == "ad9363a"
    assert p["xo_hz"] == 40000000
    assert p["iio_uri"] == "ip:192.168.2.1"
    assert p["rx_lo_hz"] == [325000000, 3800000000]
    assert p["tx_lo_hz"] == [325000000, 3800000000]
    assert p["rx_rate_hz"] == [520833, 30720000]
    assert p["rx_bw_hz"] == [200000, 56000000]
    assert p["tx_bw_hz"] == [200000, 40000000]
    # one I/Q pair each way: a Rev B's one RX and one TX
    assert p["rx_channels"] == 1
    assert p["tx_channels"] == 1
    assert p["adc_bits"] == 12


def test_the_pluto_is_never_asked_over_usb(pluto_root):
    """The IIO interface on USB is the one OpenWebRX streams through; the
    network context is a second client of iiod and leaves it alone."""
    sdr.collect_sdr()
    iio = [c for c in pluto_root if c[0] == "iio_attr"]
    assert iio
    assert all(c[1:3] == ["-u", "ip:192.168.2.1"] for c in iio)


def test_a_pluto_with_no_network_context_says_why(pluto_root, monkeypatch):
    monkeypatch.setattr(sdr, "sdr_sh", lambda args, timeout=15: (0, "", "")
                        if args[0] == "ip" else (127, "", "not here"))
    s = sdr.collect_sdr()
    (p,) = s["summary"]
    assert p["usb_serial"] == "10447354119600022000120009f61e2b82"
    assert "hw_serial" not in p
    (dev,) = s["devices"]
    assert "no IPv4 neighbour on eth-pluto" in dev["iio_error"]


@pytest.fixture
def xsdr_root(tmp_path, monkeypatch):
    """rpi-sdr-xsdr's PCIe: the RP1 and the XSDR on usdr_pcie_uram."""
    _pci(tmp_path, "0001:01:00.0", "0x10ee", "0x7049", driver="usdr")
    _pci(tmp_path, "0002:01:00.0", "0x1de4", "0x0001", driver="rp1", sub=("0x0000", "0x0000"))
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))

    def fake(args, timeout=15):
        if args == ["sudo", "-n", "cat", str(tmp_path) +
                    "/sys/bus/pci/devices/0001:01:00.0/serial_number"]:
            return 0, "00-00-00-00-12-34-56-78\n", ""
        return 127, "", "not here"

    monkeypatch.setattr(sdr, "sdr_sh", fake)
    return tmp_path


def test_the_xsdr_is_found_on_pcie(xsdr_root):
    (x,) = sdr.collect_sdr()["summary"]
    assert x["kind"] == "usdr"
    assert x["pcie_id"] == "10ee:7049"
    assert x["usdr_family"] == "m2_lm7_1"
    assert x["driver"] == "usdr"
    assert x["pcie_dsn"] == "00-00-00-00-12-34-56-78"
    assert x["pcie_link"] == "5.0 GT/s x1 (card x2)"


def test_the_xsdr_is_not_an_unknown_fpga(xsdr_root):
    """It is a Xilinx id, and it was once named an unknown FPGA for it."""
    f = {"pcie": fpga.pcie_devices(), "ftdi": [], "cynthion": []}
    assert fpga.fpga_verdict(f) == []


def test_other_xilinx_endpoints_are_still_fpgas(xsdr_root):
    _pci(xsdr_root, "0003:01:00.0", "0x10ee", "0x7022")
    f = {"pcie": fpga.pcie_devices(), "ftdi": [], "cynthion": []}
    assert [b["kind"] for b in fpga.fpga_verdict(f)] == ["unknown-fpga"]


def test_the_two_modules_agree_on_the_usdr_ids():
    assert set(fpga.USDR_PCIE_IDS) == set(sdr.USDR_PCIE)


def test_a_range_is_its_ends():
    assert sdr.iio_range("[325000000 1 3800000000]") == [325000000, 3800000000]
    assert sdr.iio_range("[1 2 3 4]") is None
    assert sdr.iio_range("") is None


def test_merge_puts_the_summary_where_the_model_reads_it():
    doc = {"verdict": {"summary": {}}}
    s = {"usb": [], "pcie": [], "devices": [{"kind": "rtl-sdr"}],
         "summary": [{"kind": "rtl-sdr"}]}
    sdr.merge_sdr(doc, s)
    assert doc["verdict"]["summary"]["sdr"] == [{"kind": "rtl-sdr"}]
    assert doc["verdict"]["sdr"] == [{"kind": "rtl-sdr"}]
    assert doc["sdr"] == {"usb": [], "pcie": []}


def test_the_sdr_probe_is_a_plain_python35_script():
    with pathlib.Path(sdr.__file__).open("rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    assert not [t for t in tokens if tokenize.tok_name[t.type] == "FSTRING_START"]
    prefixes = {t.string[:2].lower() for t in tokens if t.type == tokenize.STRING}
    assert not [p for p in prefixes if p.startswith(("f", "rf", "fr")) and p[-1] in "'\""]
    r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", sdr.__file__],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_the_xsdr_is_no_endpoint_for_fpga_flash_or_soc_reads(xsdr_root):
    """--flash detaches and rescans FPGA endpoints and --soc maps their BAR;
    neither may touch a radio's."""
    assert fpga.fpga_endpoints(fpga.pcie_devices()) == []
