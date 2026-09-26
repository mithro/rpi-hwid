"""The SDR probe (rpi_hwid.sdr): what it finds in a sysfs tree and in what
the tools it runs answer. Every tool answer here was captured from the
fleet's own radios on 2026-09-26 -- rpi-sdr-kraken, rpi-sdr-pluto,
rpi-sdr-xsdr and rpi-sdr-rtlsdr-v3 -- read without opening a device."""

from __future__ import annotations

import json
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


def _pci(root, slot, vendor, device, driver=None, sub=("0x10ee", "0x0007"),
         command=0x0006):
    d = root / "sys/bus/pci/devices" / slot
    d.mkdir(parents=True)
    # the head of config space: vendor, device, command (Mem+ BusMaster+)
    (d / "config").write_bytes(int(vendor, 16).to_bytes(2, "little")
                               + int(device, 16).to_bytes(2, "little")
                               + command.to_bytes(2, "little") + b"\x10\x00")
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
    if driver == "usdr":
        node = root / "sys/class/usdr/usdr0"
        node.mkdir(parents=True)
        (node / "device").symlink_to(d)
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


# `rtl_eeprom -d 1000` on rpi-sdr-kraken, OpenWebRX stopped, 2026-09-26
KRAKEN_EEPROM = """Found 5 device(s):
  0:  Generic RTL2832U OEM
  1:  Generic RTL2832U OEM
  2:  Generic RTL2832U OEM
  3:  Generic RTL2832U OEM
  4:  Generic RTL2832U OEM
Using device 1: Generic RTL2832U OEM
Found Rafael Micro R820T tuner
Current configuration:
__________________________________________
Vendor ID:\t\t0x0bda
Product ID:\t\t0x2838
Manufacturer:\t\tRealtek
Product:\t\tRTL2838UHIDIR
Serial number:\t\t1000
Serial number enabled:\tyes
IR endpoint enabled:\tyes
Remote wakeup enabled:\tno
__________________________________________
"""


class FakeLibrtlsdr:
    """The tools the open read runs, answering the way librtlsdr's do.

    `rtl_eeprom -d` parses its argument with atoi (rtl_eeprom.c): it is an
    index and only an index, so "00000001" is device #1 and "1000" is device
    #1000 -- the bug rpi-sdr-rtlsdr-v3 showed live on 2026-09-26, "Failed to
    open rtlsdr device #1." The device list comes from the index reader,
    in librtlsdr's order, which need not be the ports' order.
    """

    def __init__(self, serials, eeprom, ds=None, held=False):
        self.serials, self.eeprom, self.ds, self.held = serials, eeprom, ds, held
        self.calls = []

    def __call__(self, args, timeout=15):
        self.calls.append(list(args))
        if args[:3] == ["sudo", "-n", "fuser"]:
            return (0, " 4242", "") if self.held else (1, "", "")
        if args[:3] == ["python3", "-c", sdr.RTL_INDEX_READER]:
            return 0, json.dumps({"serials": self.serials}) + "\n", ""
        if args[:2] == ["rtl_eeprom", "-d"]:
            idx = int(args[2]) if args[2].isdigit() else 0     # atoi
            if idx >= len(self.serials):
                return 1, "", (f"Found {len(self.serials)} device(s):\n"
                               f"Failed to open rtlsdr device #{idx}.\n")
            return 0, "", self.eeprom.replace("Serial number:\t\t1000",
                                              "Serial number:\t\t" + self.serials[idx])
        if args[:3] == ["python3", "-c", sdr.RTL_DS_READER]:
            assert 0 <= int(args[3]) < len(self.serials)
            return 0, (self.ds or "{}") + "\n", ""
        return 127, "", "not here"

    def eeprom_indices(self):
        return [c[2] for c in self.calls if c[:2] == ["rtl_eeprom", "-d"]]


# librtlsdr's order on rpi-sdr-kraken, from rtl_eeprom's own device list
KRAKEN_ORDER = ["1004", "1000", "1001", "1002", "1003"]


def test_the_open_read_finds_every_krakens_tuner(kraken_root, monkeypatch):
    for n, port in enumerate(("2", "3", "4", "5", "6")):
        (kraken_root / "sys/bus/usb/devices" / ("1-1." + port) / "busnum").write_text("1\n")
        (kraken_root / "sys/bus/usb/devices" / ("1-1." + port) / "devnum").write_text(
            f"{n + 3}\n")
    lib = FakeLibrtlsdr(KRAKEN_ORDER, KRAKEN_EEPROM)
    monkeypatch.setattr(sdr, "sdr_sh", lib)
    s = sdr.collect_sdr(open_radios=True)
    (k,) = s["summary"]
    assert k["tuner"] == "Rafael Micro R820T"
    # every channel asked by its librtlsdr index, never by its all-digit serial
    assert sorted(lib.eeprom_indices()) == ["0", "1", "2", "3", "4"]
    # 1-1.5 is serial 1000, which librtlsdr lists second
    assert s["rtl_open"]["1-1.5"]["eeprom"]["Serial number"] == "1000"
    assert s["rtl_open"]["1-1.5"]["eeprom"]["IR endpoint enabled"] == "yes"


def test_a_dongle_readsb_holds_is_not_opened(kraken_root, monkeypatch):
    for port in ("2", "3", "4", "5", "6"):
        (kraken_root / "sys/bus/usb/devices" / ("1-1." + port) / "busnum").write_text("1\n")
        (kraken_root / "sys/bus/usb/devices" / ("1-1." + port) / "devnum").write_text("3\n")
    monkeypatch.setattr(sdr, "sdr_sh", lambda args, timeout=15: (0, " 4242", "")
                        if args[:3] == ["sudo", "-n", "fuser"] else (127, "", "not here"))
    s = sdr.collect_sdr(open_radios=True)
    assert "tuner" not in s["summary"][0]
    assert "held by" in s["rtl_open"]["1-1.2"]["error"]


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
    # the serial is the QSPI flash's unique id, and is recorded as one
    assert p["flash_uid"] == "10447354119600022000120009f61e2b82"
    assert p["flash_uid_state"] == "read"
    assert p["flash_source"] == "pluto-firmware"


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


def test_a_radio_round_trips_through_the_model():
    from rpi_hwid.model import Summary

    radio = {"kind": "pluto", "usb_serial": "10447354119600022000120009f61e2b82",
             "rx_lo_hz": [325000000, 3800000000], "rx_channels": 1}
    kraken = {"kind": "krakensdr", "channel_serials": ["1000", "1001", "1002", "1003", "1004"]}
    s = Summary.from_dict({"model": "m", "serial": "s", "revision": "r", "power_class": "p",
                           "sdr": [radio, kraken]})
    assert s.sdr[0].rx_lo_hz == (325000000, 3800000000)
    assert s.sdr[1].channel_serials == ("1000", "1001", "1002", "1003", "1004")
    d = s.to_dict()
    assert d["sdr"][0]["rx_lo_hz"] == [325000000, 3800000000]
    assert d["sdr"][1]["channel_serials"] == ["1000", "1001", "1002", "1003", "1004"]
    assert Summary.from_dict(d) == s


def test_a_field_the_model_does_not_know_is_an_error():
    from rpi_hwid.model import SdrDevice

    with pytest.raises(TypeError):
        SdrDevice.from_dict({"kind": "pluto", "colour": "blue"})


def test_collect_appends_the_sdr_module_on_request():
    from rpi_hwid.collect import probe_source

    src = probe_source(sdr=True)
    assert src.startswith("RPI_HWID_EMBEDDED = True")
    assert "merge_sdr(_doc, collect_sdr())" in src
    assert "collect_fpga" not in src
    assert "merge_sdr" not in probe_source()
    assert "collect_sdr(open_radios=True)" in probe_source(sdr=True, sdr_open=True)
    both = probe_source(fpga=True, sdr=True)
    assert both.index("merge_fpga(_doc") < both.index("merge_sdr(_doc")



# `sudo usdr_dm_sensors -l 3` and `sudo usdr_flash` on rpi-sdr-xsdr after its
# reboot, 2026-09-26 (the lines the probe reads; the tools print more)
XSDR_SENSORS = "14:47:21.100010 ERROR:  [XDEV] HWID 8030012d\n"
XSDR_FLASH = """Device was created: `usdr0`!
Flash ID id 1f16421f (Adesto SPI/QPI series 32 Mb)!
It looks like the FPGA M image is corrupted! res=-22
Actual firmware in use:      FirmwareID 83355581 (20260616212201)
Golden image: DEVID 0362c093 FirmwareID 83355581 (20260616212201)
Master image: DEVID 00000000 FirmwareID 00000000 (0)
"""


def _xsdr_tools(root, held=False):
    def fake(args, timeout=15):
        if args[:3] == ["sudo", "-n", "cat"]:
            return 0, "00-00-00-00-12-34-56-78\n", ""
        if args == ["sudo", "-n", "fuser", "/dev/usdr0"]:
            return (0, " 659", "/dev/usdr0:        ") if held else (1, "", "")
        if args == ["sudo", "-n", "usdr_dm_sensors", "-l", "3"]:
            return 0, "", XSDR_SENSORS
        if args == ["sudo", "-n", "usdr_flash"]:
            return 0, "", XSDR_FLASH
        return 127, "", "not here"
    return fake


def test_the_open_read_names_the_card_and_its_flash(xsdr_root, monkeypatch):
    monkeypatch.setattr(sdr, "sdr_sh", _xsdr_tools(xsdr_root))
    s = sdr.collect_sdr(open_radios=True)
    (x,) = s["summary"]
    assert x["usdr_hwid"] == "8030012d"
    assert x["flash_jedec"] == "0x1f4216"
    assert x["fpga_devid"] == "0362c093"
    (dev,) = s["devices"]
    # the images are evidence, not identity: gateware changes
    assert dev["usdr_images"]["master_image"] == {"devid": "00000000",
                                                  "firmware_id": "00000000"}


def test_a_card_someone_holds_is_not_opened(xsdr_root, monkeypatch):
    calls = []
    tools = _xsdr_tools(xsdr_root, held=True)

    def spy(args, timeout=15):
        calls.append(args)
        return tools(args, timeout)

    monkeypatch.setattr(sdr, "sdr_sh", spy)
    (x,) = sdr.collect_sdr(open_radios=True)["summary"]
    assert "held by" in x["usdr_error"]
    assert not [c for c in calls if "usdr_flash" in c or "usdr_dm_sensors" in c]


def test_without_the_flag_nothing_is_opened(xsdr_root, monkeypatch):
    calls = []
    tools = _xsdr_tools(xsdr_root)
    monkeypatch.setattr(sdr, "sdr_sh", lambda a, timeout=15: calls.append(a) or tools(a))
    sdr.collect_sdr()
    assert [c[:3] for c in calls] == [["sudo", "-n", "cat"]]


def test_a_card_that_dropped_off_its_link_says_so(tmp_path, monkeypatch):
    """rpi-sdr-xsdr, 2026-09-26: bound, listed, and its command register
    back to Mem- BusMaster-; the library read HWID ffffffff."""
    _pci(tmp_path, "0001:01:00.0", "0x10ee", "0x7049", driver="usdr", command=0)
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))
    monkeypatch.setattr(sdr, "sdr_sh", _xsdr_tools(tmp_path))
    (x,) = sdr.collect_sdr(open_radios=True)["summary"]
    assert x["usdr_error"].startswith("card not answering")
    assert "usdr_hwid" not in x


def test_a_dead_link_reads_all_ones():
    assert sdr.usdr_health(b"\xff" * 8, True).startswith("card not answering")
    assert sdr.usdr_health(b"\xee\x10\x49\x70\x06\x00\x10\x00", True) is None
    assert sdr.usdr_health(None, True) == "config space unreadable"


def test_the_open_read_takes_the_flash_esn(xsdr_root, monkeypatch):
    """rpi-sdr-xsdr's AT25SL321, 2026-09-26: the secured OTP's first eight
    bytes programmed, the rest erased, factory lock clear."""
    tools = _xsdr_tools(xsdr_root)
    esn = '{"rdid32": "1f16421f", "security": "00", "enso": 0, "exso": 0, "read": 0, ' \
          '"esn": "190402030' '90e9769ffffffffffffffff"}'

    def fake(args, timeout=15):
        if args[:4] == ["sudo", "-n", "python3", "-c"]:
            assert args[4] == sdr.USDR_ESN_READER
            return 0, esn + "\n", ""
        return tools(args, timeout)

    monkeypatch.setattr(sdr, "sdr_sh", fake)
    (x,) = sdr.collect_sdr(open_radios=True)["summary"]
    # recorded as an FPGA board's flash is: the programmed id, its width,
    # its state, the chip's own word on it, and how it was read
    assert x["flash_uid"] == "19040203090e9769"
    assert x["flash_uid_bits"] == 64
    assert x["flash_uid_state"] == "read"
    assert x["flash_source"] == "usdr-espi"
    assert "factory lock 0" in x["flash_uid_note"]
    assert "19040203090e9769ffffffffffffffff" in x["flash_uid_note"]


def test_the_esn_reader_is_python35_source():
    compile(sdr.USDR_ESN_READER, "usdr_esn_reader", "exec")
    assert "f\"" not in sdr.USDR_ESN_READER


# `rtl_eeprom` on rpi-sdr-rtlsdr-v3, ultrafeeder stopped, 2026-09-26
V3_EEPROM = KRAKEN_EEPROM.replace("Found 5 device(s)", "Found 1 device(s)").replace(
    "Serial number:\t\t1000", "Serial number:\t\t00000001")


def _v3_root(tmp_path, monkeypatch, ds):
    _usb(tmp_path, "1-1", "2109", "3431", product="USB2.0 Hub", bcd="0421")
    d = _usb(tmp_path, "1-1.2", "0bda", "2838", "Realtek", "RTL2838UHIDIR", "00000001")
    (d / "busnum").write_text("1\n")
    (d / "devnum").write_text("3\n")
    monkeypatch.setattr(sdr, "SDR_ROOT", str(tmp_path))

    lib = FakeLibrtlsdr(["00000001"], KRAKEN_EEPROM, ds)
    monkeypatch.setattr(sdr, "sdr_sh", lib)
    return lib


def test_a_v3_is_told_by_its_hf_path(tmp_path, monkeypatch):
    # the measurement at 14 MHz: I 0.46, Q 2.32
    lib = _v3_root(tmp_path, monkeypatch, '{"i_rms": 0.46, "q_rms": 2.32}')
    (r,) = sdr.collect_sdr(open_radios=True)["summary"]
    assert r["tuner"] == "Rafael Micro R820T"
    assert r["rtl_model"] == "rtl-sdr-blog-v3"
    # the serial "00000001" is atoi 1: the dongle is index 0, and both reads say so
    assert lib.eeprom_indices() == ["0"]
    assert [c[3] for c in lib.calls if c[:3] == ["python3", "-c", sdr.RTL_DS_READER]] == ["0"]


def test_two_dongles_with_one_serial_are_not_guessed_between(tmp_path, monkeypatch):
    lib = _v3_root(tmp_path, monkeypatch, "{}")
    lib.serials = ["00000001", "00000001"]
    s = sdr.collect_sdr(open_radios=True)
    assert "2 dongles answer serial 00000001" in s["rtl_open"]["1-1.2"]["error"]
    assert lib.eeprom_indices() == []


def test_a_dongle_with_no_hf_path_is_not_a_v3(tmp_path, monkeypatch):
    _v3_root(tmp_path, monkeypatch, '{"i_rms": 0.46, "q_rms": 0.5}')
    (r,) = sdr.collect_sdr(open_radios=True)["summary"]
    assert "rtl_model" not in r


def test_a_krakens_channels_are_never_streamed(kraken_root, monkeypatch):
    for port in ("2", "3", "4", "5", "6"):
        (kraken_root / "sys/bus/usb/devices" / ("1-1." + port) / "busnum").write_text("1\n")
        (kraken_root / "sys/bus/usb/devices" / ("1-1." + port) / "devnum").write_text("3\n")
    lib = FakeLibrtlsdr(KRAKEN_ORDER, KRAKEN_EEPROM, '{"i_rms": 1, "q_rms": 9}')
    monkeypatch.setattr(sdr, "sdr_sh", lib)
    sdr.collect_sdr(open_radios=True)
    assert not [c for c in lib.calls if c[:3] == ["python3", "-c", sdr.RTL_DS_READER]]
