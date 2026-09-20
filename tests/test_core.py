"""names, revision decoding, and the probe/fpga/tinytapeout verdicts on captured
evidence."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tokenize
from dataclasses import replace

import pytest

from rpi_hwid import boards, fpga, names, probe, revision, tinytapeout
from rpi_hwid.collect import load_collected, probe_source
from rpi_hwid.model import FpgaBoard, Mac, ProbeDocument, Summary, TinyTapeoutBoard

# --- names ----------------------------------------------------------------------


def test_netv2_names_are_pure_and_decorrelated():
    # the fleet's eight, including three pairs one hex digit apart
    known = {
        "0x0058a44663258854": "netv2-amber", "0x0038a44663258854": "netv2-basil",
        "0x00704c4e63b90854": "netv2-cedar", "0x0054ec4663258854": "netv2-coral",
        "0x0054ec466325885c": "netv2-flint", "0x00742c4e63b9085c": "netv2-grove",
        "0x01744426133b885c": "netv2-indigo", "0x00382426133b885c": "netv2-violet",
    }
    for dna, name in known.items():
        assert names.netv2_name(dna) == name
        assert names.netv2_name(dna.upper().replace("0X", "")) == name


def test_netv2_name_rejects_junk():
    with pytest.raises(ValueError, match="not a hex"):
        names.netv2_name("not-hex")


def test_arty_names_follow_the_chain_and_honour_the_registry():
    serials = ["210319B301DE", "210319B0C238", "210319B301E1", "210319A764F5"]
    got = names.arty_names(serials)
    assert got["210319B301DE"] == "arty-hawk"
    assert got["210319B0C238"] == "arty-serin"
    # B301E1 and A764F5 collide on the first candidate; the later one moves on
    assert got["210319B301E1"] == "arty-goose"
    assert got["210319A764F5"] != "arty-goose"
    assert len(set(got.values())) == 4
    # a pinned registry wins and blocks its word for newcomers
    pinned = {"210319B301DE": "arty-otter"}
    again = names.arty_names(serials, pinned)
    assert again["210319B301DE"] == "arty-otter"
    assert "arty-otter" not in [v for k, v in again.items() if k != "210319B301DE"]


# --- revision -------------------------------------------------------------------


@pytest.mark.parametrize(("code", "model", "memory", "rev"), [
    ("c04170", "5", "4 GB", "1.0"),
    ("b04171", "5", "2 GB", "1.1"),
    ("a04171", "5", "1 GB", "1.1"),
    ("d03115", "4 Model B", "8 GB", "1.5"),
    ("b03115", "4 Model B", "2 GB", "1.5"),
    ("a020d3", "3 Model B+", "1 GB", "1.3"),
    ("9000c1", "Zero W", "512 MB", "1.1"),
])
def test_decode_revision(code, model, memory, rev):
    r = revision.decode_revision(code)
    assert (r.model, r.memory, r.revision) == (model, memory, rev)


@pytest.mark.parametrize(("code", "model", "memory", "rev"), [
    ("0002", "Model B", "256 MB", "1.0"),
    ("000f", "Model B", "512 MB", "2.0"),          # rpib-serial
    ("0011", "Compute Module 1", "512 MB", "1.0"),  # rpicm1-serial
    ("0013", "Model B+", "512 MB", "1.2"),
    ("0015", "Model A+", "256 MB / 512 MB", "1.1"),  # the one code that does not say
    ("1000000f", "Model B", "512 MB", "2.0"),      # flag bits above the code itself
])
def test_decode_revision_old_style(code, model, memory, rev):
    r = revision.decode_revision(code)
    assert (r.model, r.memory, r.revision, r.soc) == (model, memory, rev, "BCM2835")


def test_decode_revision_refuses_an_unlisted_old_style_code():
    with pytest.raises(ValueError, match="no model is listed"):
        revision.decode_revision("000a")


def test_broadcom_macs_from_serial():
    assert revision.broadcom_macs("000000009bc0bdaf") == ("b8:27:eb:c0:bd:af", "b8:27:eb:95:e8:fa")
    macs = [{"kind": "eth", "mac": "b8:27:eb:e3:e7:e4"}]
    assert revision.derived_wlan_mac("000000004fe3e7e4", macs) == "b8:27:eb:b6:b2:b1"
    # a Pi 5 OUI has no rule; a present wlan needs none
    pi5 = [{"kind": "eth", "mac": "2c:cf:67:16:bd:98"}]
    assert revision.derived_wlan_mac("d88100008543dc30", pi5) is None
    both = [*macs, {"kind": "wlan", "mac": "x"}]
    assert revision.derived_wlan_mac("000000009bc0bdaf", both) is None


# --- boards ---------------------------------------------------------------------


def test_identify_pi_from_revision_and_orange_pi_from_device_tree(docs):
    pi = boards.identify(docs["rpi5-netv2"].summary)
    assert (pi.kind, pi.short, pi.title) == ("rpi", "Pi 5", "Raspberry Pi 5")
    assert pi.subtitle == "4 GB  ·  Rev 1.0  ·  rev code c04170"
    assert pi.mark == "raspberry-pi.svg"
    assert pi.wired is True
    assert pi.radio_derivable is False
    zero = boards.identify(docs["rpiz-serial"].summary)
    assert zero.wired is False
    assert zero.radio_derivable is True
    opi = boards.identify(docs["pi-sw2-p22"].summary)
    assert (opi.kind, opi.short, opi.title) == ("opi", "Orange Pi PC", "Orange Pi PC")
    assert opi.subtitle == "1 GB  ·  Allwinner H3  ·  dt orangepi-pc"
    assert opi.mark == "orange-pi.png"
    assert (opi.wired, opi.radio, opi.radio_derivable) == (True, False, False)


def test_identify_the_boards_older_than_the_packed_revision_code(docs):
    """The two pre-2012 boards, and the ports they are missing. Getting the
    ports wrong here is not cosmetic: the wlan MAC is derivable from the
    serial on both, so a board left merely "not known" would be labelled
    with a radio MAC when it has no radio."""
    b = boards.identify(docs["rpib-serial"].summary)
    assert (b.kind, b.short, b.title) == ("rpi", "Pi Model B", "Raspberry Pi Model B")
    assert b.subtitle == "512 MB  ·  Rev 2.0  ·  rev code 000f"
    assert (b.wired, b.radio) == (True, False)      # a wired port, no radio
    cm = boards.identify(docs["rpicm1-serial"].summary)
    assert cm.title == "Raspberry Pi Compute Module 1"
    assert (cm.wired, cm.radio) == (False, False)   # neither, on the module


def test_identify_settles_the_one_ambiguous_memory_size_from_memtotal():
    """0015 covers an A+ that shipped with either size and does not say
    which; the measurement does."""
    s = Summary(model="Raspberry Pi Model A Plus Rev 1.1", serial="s", revision="0015",
                power_class="undetermined", memory="512 MB")
    assert boards.identify(s).memory == "512 MB"
    assert "512 MB" in boards.identify(s).subtitle
    # and where nothing was measured, the code's own answer is kept whole
    assert boards.identify(replace(s, memory=None)).memory == "256 MB / 512 MB"


def test_identify_orange_pi_without_captured_details():
    """An older document, or a Xunlong board this table has not met: the
    title still comes off the model string and the die off the compatible."""
    s = Summary(model="Xunlong Orange Pi Zero", serial="s", revision="",
                power_class="undetermined",
                compatible="xunlong,orangepi-zero allwinner,sun8i-h2-plus")
    b = boards.identify(s)
    assert b.title == "Orange Pi Zero"
    assert b.subtitle == "RAM not read  ·  Allwinner H2+  ·  dt orangepi-zero"
    assert (b.wired, b.radio) == (None, None)
    s = Summary(model="Xunlong Orange Pi 3", serial="s", revision="", power_class="undetermined",
                compatible="xunlong,orangepi-3 allwinner,sun50i-h6-x", memory="2 GB")
    assert boards.identify(s).subtitle == "2 GB  ·  sun50i-h6-x  ·  dt orangepi-3"
    # a board with no label design of its own: identified as None, so the
    # label run skips it rather than dying on it
    other = Summary(model="MinnowBoard Turbot", serial="s", revision="",
                    power_class="undetermined")
    assert boards.board_kind(other) == "other"
    assert boards.identify(other) is None


def test_sunxi_mac_follows_uboot_rule():
    # pi-sw2-p22, read off the board: SID -> serial -> MAC, all three agree
    assert boards.sunxi_mac("02c000812eb7a34e") == "02:81:2e:b7:a3:4e"
    with pytest.raises(ValueError, match="sunxi serial"):
        boards.sunxi_mac("abcd")


# --- probe verdict on captured evidence -------------------------------------------


def _evidence(**over):
    base = {
        "model": "Raspberry Pi 5 Model B Rev 1.1", "serial": "c36b093f773d46b8",
        "revision": "a04171", "hat_fw": None, "hat_eeproms": {}, "header_i2c": [],
        "usb": {"usb1": "1d6b:0002"}, "interfaces": [], "usb_net": [],
        "throttled": "0x0", "undervoltage_now": False, "undervoltage_since_boot": False,
        "pi5": True, "max_current_ma": 3000, "usbpd_pdos": [], "ext5v_v": 5.34,
        "rtc_batt_v": 3.26, "fan_dt": "okay", "fan_rpm": 2471,
    }
    base.update(over)
    return base


def test_verdict_waveshare_m2_hat_at_0x52():
    d = _evidence(hat_eeproms={"0x52": {"product": "Waveshare PoE M.2 HAT+ (B)", "pid": "0x6d87",
                                        "uuid": "9729525c-eeee-98e9-f348-a0720f4c16eb"}})
    v = probe.verdict(d)
    assert v["summary"]["power_class"] == "gpio-poe-hat"
    assert v["summary"]["header"] == ["Waveshare PoE M.2 HAT+ (B)"]
    assert v["summary"]["hat_uuid"] == "9729525c-eeee-98e9-f348-a0720f4c16eb"
    assert v["summary"]["rtc_battery"] is True
    assert v["summary"]["fan"] is True
    assert any("firmware does not read it" in h for h in v["header"])


def test_verdict_usbc_supply_from_900ma():
    d = _evidence(max_current_ma=900, ext5v_v=4.83, rtc_batt_v=0.0, fan_dt="disabled",
                  fan_rpm=None)
    v = probe.verdict(d)
    assert v["summary"]["power_class"] == "usbc-supply"
    assert v["summary"]["rtc_battery"] is False
    assert v["summary"]["fan"] is False


def test_verdict_ambiguous_at_3a_without_eeprom():
    v = probe.verdict(_evidence(ext5v_v=5.09, rtc_batt_v=0.01))
    assert v["summary"]["power_class"] == "ambiguous"
    assert "leans splitter" in v["power"]


def test_verdict_pd_contract():
    v = probe.verdict(_evidence(max_current_ma=5000, usbpd_pdos=["0x0a01912c"]))
    assert v["summary"]["power_class"] == "usbc-pd-supply"


def test_verdict_zero_bonnet_and_onboard_macs():
    d = _evidence(model="Raspberry Pi Zero W Rev 1.1", pi5=False,
                  usb={"1-1": "1a40:0101", "1-1.4": "0bda:8152", "usb1": "1d6b:0002"},
                  interfaces=[{"name": "eth0", "mac": "00:e0:4c:36:0b:0a", "driver": "r8152",
                               "onboard": False, "kind": "eth", "usb": "1-1.4", "speed": "100"},
                              {"name": "wlan0", "mac": "b8:27:eb:02:a3:24", "driver": "brcmfmac",
                               "onboard": True, "kind": "wlan", "usb": None, "speed": None}])
    for k in ("max_current_ma", "usbpd_pdos", "ext5v_v", "rtc_batt_v", "fan_dt", "fan_rpm"):
        d.pop(k)
    v = probe.verdict(d)
    assert v["summary"]["power_class"] == "bonnet-poe"
    assert v["summary"]["header"] == ["Waveshare PoE-ETH-USB-HUB-HAT"]
    assert v["summary"]["macs"] == [
        {"kind": "wlan", "mac": "b8:27:eb:02:a3:24", "signal": None}]
    assert v["summary"]["rtc_battery"] is None


def test_verdict_poe_hat_b_by_i2c_devices():
    d = _evidence(model="Raspberry Pi 4 Model B Rev 1.5", pi5=False, header_i2c=["20", "3c"])
    v = probe.verdict(d)
    assert v["summary"]["power_class"] == "gpio-poe-hat"
    assert "Waveshare PoE HAT (B)" in v["summary"]["header"]


def test_verdict_pan_tilt_hat_needs_the_all_call_address():
    """0x29+0x40 is not enough, and deliberately so.

    Both are contended -- 0x40 is the PCA9685's default and the INA219's,
    0x29 the TSL2591's and the VL53L0X's -- so a robotics bonnet carrying
    a current monitor and a rangefinder would wear this HAT's name. 0x70
    is the PCA9685 answering All Call as well as its own address, which is
    what makes the set a fingerprint rather than a coincidence.
    """
    # as measured on rpi5-pantilt: 0x28 answers the scan with nothing fitted
    d = _evidence(model="Raspberry Pi 5 Model B Rev 1.1",
                  header_i2c=["28", "29", "40", "70"])
    v = probe.verdict(d)
    # the summary is the half labels and the ansible assert read, so the
    # short name has to reach it, not just the verbose verdict line
    assert v["summary"]["header"] == ["Waveshare 2-DOF Pan-Tilt HAT"]
    assert any("All Call 0x70" in h for h in v["header"])
    # the phantom is not evidence and must not be named as a device
    assert not any("0x28" in h for h in v["header"])

    two = _evidence(model="Raspberry Pi 5 Model B Rev 1.1", header_i2c=["29", "40"])
    v = probe.verdict(two)
    assert not any("Pan-Tilt" in h for h in v["summary"]["header"])
    # unnamed, but not unrecorded: the addresses still reach the evidence
    assert any("29 40" in e for e in v["evidence"])


def test_verdict_undetermined_on_a_bare_3bplus():
    d = _evidence(model="Raspberry Pi 3 Model B Plus Rev 1.3", pi5=False)
    v = probe.verdict(d)
    assert v["summary"]["power_class"] == "undetermined"


def test_hat_eeprom_decode():
    # the first bytes of the Waveshare PoE M.2 HAT+ (B) EEPROM, as read at 0x52
    blob = bytes.fromhex(
        "522d5069" "0200" "0400" "ee000000"
        "0100" "0000" "39000000"
        "5c522997eeeee998f348a0720f4c16eb" "876d" "0100" "07" "1a"
    ) + b"vendor " + b"Waveshare PoE M.2 HAT+ (B)" + b"\x00\x00"
    info = probe.eeprom_decode(blob)
    assert info["product"] == "Waveshare PoE M.2 HAT+ (B)"
    assert info["pid"] == "0x6d87"
    assert info["vendor"] == "vendor "
    assert probe.eeprom_decode(b"junk") is None


# --- fpga verdict -------------------------------------------------------------------


# pi-sw1-p38 on welland.fpgas.online, a Pi 5 carrying a pcileech-fpga board,
# exactly as the probe read it on 2026-09-16 (the second PCIe device is the
# Pi 5's own RP1, which is always there).
PCILEECH_HOST = {
    "pcie": [{"slot": "0001:01:00.0", "id": "10ee:0666", "class": "0x020000",
              "bars": [4096], "subsystem": "10ee:0007"},
             {"slot": "0002:01:00.0", "id": "1de4:0001", "class": "0x020000",
              "bars": [16384, 4194304, 65536], "subsystem": "0000:0000"}],
    "ftdi": [{"path": "1-2", "id": "0403:601f", "manufacturer": "FTDI",
              "product": "FTDI SuperSpeed-FIFO Bridge", "serial": "000000000001"}],
    # the GPIO harness is not wired to it: openocd read an all-zero chain
    "jtag": {"idcode": None, "raw": "error : libgpiod not found"},
}


def test_fpga_verdict_names_a_pcileech_board_rather_than_an_unknown_one():
    (board,) = fpga.fpga_verdict(PCILEECH_HOST)
    assert board["kind"] == "pcileech"
    assert "FT601" in board["how"]
    # the FT601's serial is the part's default, shared by every unit, so it
    # must not become the board's identity the way a Digilent serial does
    assert fpga.fpga_summary([board]) == [{"kind": "pcileech"}]


# The FT601's reply to pcileech_request(), exactly as read from pi-sw1-p38 on
# 2026-09-16: five filler words, then two records of core-register replies.
PCILEECH_REPLY = bytes.fromhex(
    "6666555566665555666655556666555566665555333333e3000089ab0008040e000a0900"
    "0010087100129b450014030d00160000f3ffffef00220300ffffffffffffffffffffffff"
    "ffffffffffffffffffffffff")


def test_pcileech_request_is_reads_of_the_core_register_space_and_nothing_else():
    req = fpga.pcileech_request([0x0008, 0x000A])
    assert req[:16] == b"\x66\x66\x55\x55" * 4
    assert req[16:] == bytes.fromhex("0000000000081377" "00000000000a1377")
    # every command is a read (0x10) of core space (0x03); no write bit (0x20)
    # and no read-write address (bit 15), so it cannot change the gateware
    for i in range(16, len(fpga.pcileech_request()), 8):
        cmd = fpga.pcileech_request()[i:i + 8]
        assert cmd[6] == 0x13
        assert cmd[7] == 0x77
        assert cmd[4] & 0x80 == 0


def test_pcileech_identity_from_the_bytes_a_real_board_sent():
    ident = fpga.pcileech_identity(fpga.pcileech_parse(PCILEECH_REPLY))
    assert ident == {"version": "4.14", "fpga_id": 9, "uptime_s": 143077, "pcie_present": True}


def test_a_reply_without_the_magic_is_not_taken_for_pcileech():
    # the same records with the magic's bytes changed: something else answered
    assert fpga.pcileech_identity(fpga.pcileech_parse(PCILEECH_REPLY.replace(
        bytes.fromhex("000089ab"), bytes.fromhex("00000000")))) is None
    assert fpga.pcileech_identity(fpga.pcileech_parse(b"\x66\x66\x55\x55" * 8)) is None
    assert fpga.pcileech_identity({}) is None


def test_pcileech_probe_reports_why_it_read_nothing(monkeypatch):
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15:
                        "ERROR=interface 1 in use: [Errno 16] Device or resource busy")
    assert fpga.pcileech_probe() == {
        "error": "interface 1 in use: [Errno 16] Device or resource busy"}
    # the reader parses on the host and hands back staged register bytes; this
    # is a STAGED line the FT601 on pi-sw1-p38 produced on 2026-09-16 (magic
    # 0xAB89 at 0/1, version 4.14 at 8/9, FPGA id 9 at 10, PRSNT# at 34)
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15:
                        "STAGED=0:137,1:171,8:4,9:14,10:9,34:3,35:0")
    ident = fpga.pcileech_probe()
    assert (ident["version"], ident["fpga_id"], ident["pcie_present"]) == ("4.14", 9, True)
    # a reply the reader could not stage into anything is reported, not crashed on
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15: "STAGED=")
    assert "not pcileech gateware" in fpga.pcileech_probe()["error"]


def test_fpga_verdict_carries_the_gateware_it_read():
    host = dict(PCILEECH_HOST, pcileech={"version": "4.14", "fpga_id": 9})
    (board,) = fpga.fpga_verdict(host)
    assert (board["gateware"], board["gateware_id"]) == ("4.14", 9)
    assert fpga.fpga_summary([board]) == [{"kind": "pcileech", "gateware": "4.14",
                                           "gateware_id": 9}]
    # FPGA id 0 is a real class (SP605_FT601), not an absent one
    zero = fpga.fpga_verdict(dict(PCILEECH_HOST, pcileech={"version": "4.9", "fpga_id": 0}))
    assert fpga.fpga_summary(zero)[0]["gateware_id"] == 0


@pytest.mark.parametrize(("pcie", "ftdi", "asked"), [
    (PCILEECH_HOST["pcie"], PCILEECH_HOST["ftdi"], True),
    (PCILEECH_HOST["pcie"], [], False),                   # no FT601 to ask
    (PCILEECH_HOST["pcie"][1:], PCILEECH_HOST["ftdi"], False),  # an FT601, but not this board's
])
def test_the_gateware_is_only_asked_when_both_signatures_are_there(monkeypatch, pcie, ftdi, asked):
    """Register reads sent into some other device's FT601 would be writes into
    whatever that device is."""
    calls = []
    monkeypatch.setattr(fpga, "pcie_devices", lambda: pcie)
    monkeypatch.setattr(fpga, "ftdi_devices", lambda: ftdi)
    monkeypatch.setattr(fpga, "jtag_probe", lambda flash=False: None)
    monkeypatch.setattr(fpga, "pcileech_probe", lambda: calls.append(1) or {"version": "4.14",
                                                                             "fpga_id": 9})
    fpga.collect_fpga(jtag=True)
    assert bool(calls) is asked
    calls.clear()
    fpga.collect_fpga(jtag=False)
    assert not calls, "and never without --jtag"


def test_a_pcileech_board_does_not_need_its_usb_bridge_to_be_named():
    host = dict(PCILEECH_HOST, ftdi=[])
    (board,) = fpga.fpga_verdict(host)
    assert board["kind"] == "pcileech"
    assert "FT601" not in board["how"]
    # but a Xilinx id with some other BAR layout is still not claimed
    other = dict(host, pcie=[dict(PCILEECH_HOST["pcie"][0], bars=[1 << 20])])
    assert fpga.fpga_verdict(other)[0]["kind"] == "unknown-fpga"


def test_fpga_verdict_by_pcie_bars_and_ftdi():
    f = {"pcie": [{"slot": "0001:01:00.0", "id": "10ee:7024", "class": "0x058000",
                   "bars": [1 << 20], "subsystem": "10ee:0007"}],
         "ftdi": [], "jtag": {"idcode": "0x3631093", "family": "artix a7 100t",
                              "dna": "0x00704c4e63b90854", "cable": "gpio"}}
    boards = fpga.fpga_verdict(f)
    assert len(boards) == 1
    assert boards[0]["kind"] == "netv2"
    assert boards[0]["dna"] == "0x00704c4e63b90854"

    f = {"pcie": [{"slot": "0001:01:00.0", "id": "1e24:021f", "class": "0x120000",
                   "bars": [128 << 10, 64 << 10], "subsystem": "1e24:021f"}],
         "ftdi": [], "jtag": None}
    assert fpga.fpga_verdict(f)[0]["kind"] == "acorn"

    f = {"pcie": [{"slot": "0000:01:00.0", "id": "1106:3483", "class": "0x0c0330",
                   "bars": [4096], "subsystem": "1106:3483"}],
         "ftdi": [{"path": "1-1.4", "id": "0403:6010", "manufacturer": "Digilent",
                   "product": "Digilent USB Device", "serial": "210319B301DE"}],
         "jtag": {"idcode": "0x362d093", "family": "artix a7 35t",
                  "dna": "0x00628502251ea85c", "cable": "digilent",
                  "flash_jedec": "0x012018", "flash": "spansion S25FL128S"}}
    boards = fpga.fpga_verdict(f)
    assert [b["kind"] for b in boards] == ["arty"]
    assert boards[0]["serial"] == "210319B301DE"
    assert boards[0]["flash_jedec"] == "0x012018"
    assert fpga.fpga_summary(boards) == [{
        "kind": "arty", "serial": "210319B301DE", "dna": "0x00628502251ea85c",
        "idcode": "0x362d093", "flash": "spansion S25FL128S", "flash_jedec": "0x012018"}]


def test_fpga_gpio_chain_without_pcie_board_is_a_netv2():
    # a Pi 5 always lists the RP1 as a PCIe endpoint; that is not a board
    f = {"pcie": [{"slot": "0000:01:00.0", "id": "1de4:0001", "class": "0x020000",
                   "bars": [16384, 4194304], "subsystem": "1de4:0001"}],
         "ftdi": [], "jtag": {"idcode": "0x3631093", "dna": "0x00742c4e63b9085c", "cable": "gpio"}}
    boards = fpga.fpga_verdict(f)
    assert [b["kind"] for b in boards] == ["netv2"]


# --- cynthion -------------------------------------------------------------------------

# The production Cynthion on rpi5-netv2.iot.welland.mithis.com, exactly as
# sysfs reported it on 2026-09-21: analyzer gateware sharing its USB port with
# the Apollo stub, so subclass 0x10 and subclass 0x00 side by side.
CYNTHION_ANALYZER = {
    "path": "1-1.4", "id": "1d50:615b", "manufacturer": "Cynthion Project",
    "product": "USB Analyzer", "serial": "267125df30c460de",
    "bcd_device": "0104", "subclasses": ["10", "00"],
}


def test_cynthion_mode_comes_from_the_interface_subclass():
    # cynthion/shared/usb.toml: the vid:pid is the same whatever is loaded,
    # which is exactly why the subclass exists
    assert fpga.cynthion_mode(CYNTHION_ANALYZER) == "analyzer"
    assert fpga.cynthion_mode(dict(CYNTHION_ANALYZER, subclasses=["20"])) == "moondancer"
    assert fpga.cynthion_mode(dict(CYNTHION_ANALYZER, id="1d50:615c",
                                   subclasses=[])) == "apollo"
    assert fpga.cynthion_mode(dict(CYNTHION_ANALYZER, subclasses=[])) is None


def test_the_usb_serial_is_the_flash_uid_only_when_gateware_published_it():
    # In Apollo mode the serial belongs to the debug controller, not to the
    # ECP5's configuration flash; recording it as a flash UID would key a
    # board's permanent name on the wrong chip.
    assert fpga.cynthion_flash_uid(CYNTHION_ANALYZER) == "267125df30c460de"
    apollo = dict(CYNTHION_ANALYZER, id="1d50:615c", subclasses=[], serial="deadbeef")
    assert fpga.cynthion_flash_uid(apollo) is None


def test_cynthion_revision_decodes_bcddevice_not_a_gateware_version():
    # apollo_fpga/__init__.py:260 -- major is the high byte, minor the low
    assert fpga.cynthion_revision("0104") == "1.4"
    assert fpga.cynthion_revision("0007") == "0.7"
    assert fpga.cynthion_revision(None) is None
    # 0xFF is an external Apollo board (Daisho, Pergola) and 0xFE a subdevice;
    # neither is a Cynthion revision, and "r255.1" would be a lie on a label
    assert fpga.cynthion_revision("ff01") is None
    assert fpga.cynthion_revision("fe00") is None


def test_fpga_verdict_names_a_cynthion_from_usb_alone():
    f = {"pcie": [], "ftdi": [], "jtag": None, "cynthion": [CYNTHION_ANALYZER]}
    (board,) = fpga.fpga_verdict(f)
    assert board["kind"] == "cynthion"
    assert board["serial"] == "267125df30c460de"
    assert board["hw_rev"] == "1.4"
    assert board["mode"] == "analyzer"
    assert fpga.fpga_summary([board]) == [{
        "kind": "cynthion", "serial": "267125df30c460de",
        "hw_rev": "1.4", "mode": "analyzer"}]


# --- tinytapeout verdict -------------------------------------------------------------

TT_USB = {"path": "1-1.2", "id": "2e8a:0005", "manufacturer": "MicroPython",
          "product": "Board in FS mode", "serial": "E6614C311B7A7A37", "tty": "/dev/ttyACM0"}
TT06_ANSWER = {"machine": "Raspberry Pi Pico with RP2040", "micropython": "1.24.0",
               "sdk": "2.0.4", "sdk_revision": None, "demoboard": "TT06+",
               "carrier_present": True, "carrier_version": None,
               "rom": {"shuttle": "tt06", "repo": "TinyTapeout/tinytapeout-06",
                       "commit": "0f5a1b2c"}, "rom_cached": True}


def test_tinytapeout_verdict_reads_the_rom_and_the_table():
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": TT06_ANSWER}})
    assert b["kind"] == "tinytapeout"
    assert (b["shuttle"], b["chip"], b["commit"]) == ("tt06", "asic", "0f5a1b2c")
    assert b["demoboard"] == "TT06+"
    assert b["demoboard_version"] == "v2.0.1"          # from the table, not the board
    assert b["chip_url"] == "https://tinytapeout.com/chips/tt06/"
    assert b["usb_serial"] == "E6614C311B7A7A37"
    assert b["mcu"] == "RP2040"
    assert "chip ROM shuttle=tt06; demo board TT06+" in b["how"]
    assert tinytapeout.tinytapeout_summary([b]) == [{
        "usb_serial": "E6614C311B7A7A37", "mcu": "RP2040", "shuttle": "tt06", "chip": "asic",
        "repo": "TinyTapeout/tinytapeout-06", "commit": "0f5a1b2c", "demoboard": "TT06+",
        "demoboard_version": "v2.0.1", "sdk": "2.0.4"}]


def test_tinytapeout_verdict_fpga_breakout_and_no_rom():
    fpga_answer = dict(TT06_ANSWER, demoboard="TTDBv3 [3.2]", sdk="3.1.1", carrier_version=2,
                       rom={"shuttle": "FPGA", "repo": "", "commit": ""})
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": fpga_answer}})
    assert (b["shuttle"], b["chip"], b["commit"]) == (None, "fpga", None)
    assert b["chip_url"] is None
    # a TT04 chip has no ROM: the v2 SDK reports 'unknown' and 'TT04/TT05'
    tt04 = dict(TT06_ANSWER, demoboard="TT04/TT05", carrier_present=None,
                rom={"shuttle": "unknown", "repo": "", "commit": ""})
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": tt04}})
    assert (b["shuttle"], b["chip"], b["demoboard"]) == (None, None, "TT04/TT05")
    # ... and a v2 SDK that saw a TT06+ carrier but read no ROM is still an ASIC
    tt06_blank = dict(tt04, demoboard="TT06+", carrier_present=True)
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": tt06_blank}})
    assert (b["shuttle"], b["chip"]) == (None, "asic")
    # the boot never read the ROM: nothing is driven, and the verdict says why
    cold = dict(TT06_ANSWER, machine="TinyTapeout RP2350B Core with RP2350", rom=None,
                rom_cached=False, err_rom="chip ROM not read by the boot")
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": cold}})
    assert (b["shuttle"], b["chip"], b["mcu"]) == (None, "asic", "RP2350")
    assert "chip ROM not cached on the board; rom: chip ROM not read by the boot" in b["how"]
    no_tt = dict(cold, err_rom="tt not defined", err_demoboard="ImportError('x')",
                 demoboard=None, carrier_present=None)
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": no_tt}})
    assert "rom: tt not defined; demoboard: ImportError('x'); demo board not detected" in b["how"]
    forced = dict(TT06_ANSWER, rom_forced=True)
    (b,) = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": forced}})
    assert "shuttle=tt06 (forced in config.ini)" in b["how"]


def test_tinytapeout_verdict_candidates_and_other_picos():
    # REPL not read: a candidate, listed but not summarised
    boards = tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": None})
    assert [b["kind"] for b in boards] == ["rp2-micropython"]
    assert "REPL not read" in boards[0]["how"]
    assert tinytapeout.tinytapeout_summary(boards) == []
    # REPL failed: still a candidate, with the reason
    boards = tinytapeout.tinytapeout_verdict(
        {"usb": [TT_USB], "repl": {"1-1.2": {"error": "cannot open /dev/ttyACM0: busy"}}})
    assert "REPL: cannot open" in boards[0]["how"]
    # a Pico running plain MicroPython answered without the SDK: not a TT board
    plain = {"machine": "Raspberry Pi Pico with RP2040", "err_sdk": "ImportError('ttboard')"}
    assert tinytapeout.tinytapeout_verdict({"usb": [TT_USB], "repl": {"1-1.2": plain}}) == []
    # a BOOTSEL-mode RP2040 (2e8a:0003) is not a candidate at all
    boot = dict(TT_USB, id="2e8a:0003", tty=None)
    assert tinytapeout.tinytapeout_verdict({"usb": [boot], "repl": {}}) == []


def test_shuttle_table_and_names():
    info = tinytapeout.shuttle_info("TT05")
    assert (info["chip_colour"], info["chip_silk"]) == ("yellow", "black")
    assert (info["demoboard_colour"], info["demoboard_silk"]) == ("black", "white")
    assert info["demoboard_version"] == "v1.2.3"
    assert tinytapeout.shuttle_info("nope") == {
        "chip_colour": None, "chip_silk": None, "demoboard_colour": None,
        "demoboard_silk": None, "demoboard_version": None, "url": None}
    assert tinytapeout.shuttle_title("tt06") == "Tiny Tapeout 6"
    assert tinytapeout.shuttle_title("tt03p5") == "Tiny Tapeout 3.5"
    assert tinytapeout.shuttle_title("ttihp25a") == "Tiny Tapeout IHP 25a"
    assert tinytapeout.shuttle_title("ttgf0p2") == "Tiny Tapeout GF 0.2"
    assert tinytapeout.shuttle_short("ttsky26c") == "TTSKY26c"
    assert tinytapeout.shuttle_short("tt06") == "TT06"
    assert tinytapeout.shuttle_pdk("ttihp0p4") == "ihp-sg13g2"
    assert tinytapeout.shuttle_pdk("FPGA") is None
    assert "ttihp0p1" not in tinytapeout.SHUTTLES        # unsourced rows are not listed
    for colour in {c for row in tinytapeout.SHUTTLES.values() for c in row[:4] if c}:
        assert colour in tinytapeout.COLOURS
    for row in tinytapeout.SHUTTLES.values():
        assert row[5] is None or row[5].startswith("https://")


def test_shuttle_marks_name_the_operator_then_the_foundry():
    # one of each kind: chipIgnite under Efabless, then under ChipFoundry,
    # IHP's own shuttles (operator and foundry are the same mark), and
    # wafer.space's GlobalFoundries runs
    assert tinytapeout.shuttle_marks("tt06") == ("efabless", "skywater")
    assert tinytapeout.shuttle_marks("ttsky25b") == ("chipfoundry", "skywater")
    assert tinytapeout.shuttle_marks("ttihp25a") == ("ihp",)
    assert tinytapeout.shuttle_marks("ttgf26a") == ("wafer-space", "globalfoundries")
    # ttcad25a went through neither: a Cadence shuttle, so the foundry only
    assert tinytapeout.shuttle_marks("ttcad25a") == ("skywater",)
    assert tinytapeout.shuttle_marks("TTSKY26C") == ("chipfoundry", "skywater")
    # an unknown shuttle (and no shuttle at all) yields nothing, never raises
    assert tinytapeout.shuttle_marks("ttxyz99z") == ()
    assert tinytapeout.shuttle_marks("") == ()
    assert tinytapeout.shuttle_marks(None) == ()
    # tt10 was cancelled: no silicon, nothing to mark
    assert tinytapeout.shuttle_marks("tt10") == ()
    # the two tables describe the same shuttles, and every mark is shipped
    assert set(tinytapeout.SHUTTLE_MARKS) == set(tinytapeout.SHUTTLES)
    artwork = pathlib.Path(tinytapeout.__file__).parent / "artwork"
    for marks in tinytapeout.SHUTTLE_MARKS.values():
        for mark in marks:
            assert list(artwork.glob(mark + ".*")), mark


# --- collector --------------------------------------------------------------------------


def test_probe_source_embeds_fpga_and_both_files_run_standalone():
    src = probe_source(fpga=True, jtag=False, flash=False)
    assert src.startswith("RPI_HWID_EMBEDDED = True")
    assert "merge_fpga(_doc" in src
    assert "merge_tinytapeout" not in src
    both = probe_source(fpga=True, tinytapeout=True)
    assert both.index("merge_fpga(_doc") < both.index("merge_tinytapeout(_doc")
    assert both.count("RPI_HWID_EMBEDDED = True") == 1
    alone = probe_source(tinytapeout=True)
    assert "merge_tinytapeout(_doc, collect_tinytapeout())" in alone
    assert "collect_fpga" not in alone
    # every probe must be a plain script: no f-strings, compile clean under -W error
    for module in (probe, fpga, tinytapeout):
        with pathlib.Path(module.__file__).open("rb") as fh:
            tokens = list(tokenize.tokenize(fh.readline))
        prefixes = {t.string[:2].lower() for t in tokens if t.type == tokenize.STRING}
        fstring_starts = [t for t in tokens if tokenize.tok_name[t.type] == "FSTRING_START"]
        assert not fstring_starts, module.__name__
        fprefixed = [p for p in prefixes if p.startswith(("f", "rf", "fr")) and p[-1] in "'\""]
        assert not fprefixed, module.__name__
        r = subprocess.run(
            [sys.executable, "-W", "error", "-m", "py_compile", module.__file__],
            capture_output=True, text=True, check=False,
        )
        assert r.returncode == 0, r.stderr


def test_probe_document_from_json_skips_banner():
    raw = {"verdict": {"summary": {"model": "Raspberry Pi 5 Model B Rev 1.1", "serial": "s",
                                   "revision": "a04171", "power_class": "gpio-poe-hat",
                                   "fpga": [{"kind": "acorn"}],
                                   "macs": [{"kind": "eth", "mac": "m"}]}}}
    doc = ProbeDocument.from_json("h", "Password set for pi\n" + json.dumps(raw))
    assert doc.summary.fpga == (FpgaBoard(kind="acorn"),)
    assert doc.summary.macs == (Mac("eth", "m"),)
    assert doc.summary.tinytapeout == ()
    assert doc.summary.rtc_battery is None
    assert doc.summary.to_dict()["fpga"] == [{"kind": "acorn", "serial": None, "dna": None,
                                              "idcode": None, "flash": None, "flash_jedec": None,
                                              "gateware": None, "gateware_id": None}]
    with pytest.raises(ValueError, match="no JSON"):
        ProbeDocument.from_json("h", "no json")
    with pytest.raises(ValueError, match=r"verdict\.summary"):
        ProbeDocument.from_json("h", json.dumps({"x": 1}))
    with pytest.raises(ValueError, match="does not know"):
        Summary.from_dict({"model": "m", "serial": "s", "revision": "r",
                           "power_class": "p", "surprise": 1})


def test_summary_round_trips_tinytapeout_boards():
    raw = {"model": "m", "serial": "s", "revision": "r", "power_class": "p",
           "tinytapeout": [{"usb_serial": "E6614C311B7A7A37", "mcu": "RP2040", "shuttle": "tt06",
                            "chip": "asic", "repo": "TinyTapeout/tinytapeout-06",
                            "commit": "0f5a1b2c", "demoboard": "TT06+",
                            "demoboard_version": "v2.0.1", "sdk": "2.0.4"}]}
    s = Summary.from_dict(raw)
    (b,) = s.tinytapeout
    assert isinstance(b, TinyTapeoutBoard)
    assert b.identity == "E6614C311B7A7A37"
    assert s.to_dict()["tinytapeout"] == raw["tinytapeout"]


def test_load_collected(data_dir):
    docs = load_collected(data_dir)
    assert set(docs) == {"rpi5-netv2", "pi-sw1-p10", "pi-sw2-p16", "rpiz-serial", "pi-sw2-p47",
                         "pi-sw2-p22", "rpi4-tt", "pi-sw2-p33", "pi-sw2-p37", "rpi5-433mhz",
                         "rpib-serial", "rpicm1-serial"}
    assert docs["pi-sw2-p22"].summary.compatible == "xunlong,orangepi-pc allwinner,sun8i-h3"
    assert docs["pi-sw2-p22"].summary.memory == "1 GB"
    assert docs["pi-sw2-p22"].summary.revision == ""
    assert docs["rpi5-netv2"].summary.fpga[0].identity == "0x00742c4e63b9085c"
    assert [b.shuttle for b in docs["rpi4-tt"].summary.tinytapeout] == ["tt06", "ttgf0p2"]
    (data_dir / "bad.json").write_text("{}")
    with pytest.raises(ValueError, match="not a probe document"):
        load_collected(data_dir)


def test_cli_probe_and_collect_summary_with_tinytapeout(monkeypatch, capsys, tmp_path):
    """The CLI paths that carry the Tiny Tapeout findings, with the probes
    stubbed: `probe --tinytapeout` prints the tt line, and `collect
    --tinytapeout` feeds the flag through to probe_host and prints the
    shuttle in its summary line."""
    import copy

    import conftest
    from rpi_hwid import cli, collect
    from rpi_hwid.model import ProbeDocument

    raw = copy.deepcopy(conftest.TT_HOST)
    doc_json = json.dumps(raw)

    def fake_run(cmd, input, capture_output, text, timeout):
        assert "merge_tinytapeout(_doc" in input
        return subprocess.CompletedProcess(cmd, 0, stdout=doc_json, stderr="")
    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    rc = cli.main(["collect", "--out", str(tmp_path), "--tinytapeout", "rpi4-tt"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tinytapeout tt06, ttgf0p2" in out
    assert ProbeDocument.from_json("x", (tmp_path / "rpi4-tt.json").read_text()).summary \
        .tinytapeout[1].shuttle == "ttgf0p2"

    monkeypatch.setattr(probe, "collect", lambda: {k: v for k, v in raw.items()
                                                    if k not in ("verdict", "tinytapeout")})
    monkeypatch.setattr(probe, "verdict", lambda d: copy.deepcopy(raw["verdict"]))
    monkeypatch.setattr(tinytapeout, "collect_tinytapeout", lambda repl=True, timeout=10, **kw: {
        "usb": raw["tinytapeout"]["usb"], "repl": raw["tinytapeout"]["repl"],
        "boards": raw["verdict"]["tinytapeout"],
        "summary": raw["verdict"]["summary"]["tinytapeout"]})
    assert cli.main(["probe", "--tinytapeout"]) == 0
    out = capsys.readouterr().out
    assert "tt     : TT06 on demo board TT06+" in out
    assert "tt     : TTGF0p2 on demo board TTDBv3 [3.2]" in out
    assert "DemoBoard.get" not in tinytapeout.REPL_SNIPPET, "the snippet must not init the board"
    assert ".contents" not in tinytapeout.REPL_SNIPPET, "ChipROM.contents drives the chip's pins"
    assert cli.main(["tinytapeout"]) == 0
    assert "TTGF0p2" in capsys.readouterr().out
    assert cli.main(["tinytapeout", "--json"]) == 0
    assert '"shuttle": "ttgf0p2"' in capsys.readouterr().out


def test_no_stop_service_reaches_every_way_the_tinytapeout_module_runs(
        monkeypatch, capsys, tmp_path):
    """--no-stop-service must reach collect_tinytapeout however the module
    is run: on the Pi (`tinytapeout`, `probe --tinytapeout`) and over ssh
    (`collect --tinytapeout`, which writes the call into the script it
    sends). Without it the module takes a port from the service holding
    it, as designed; a flag that silently did nothing on one of those
    paths would stop a rig's bridge the operator had asked to leave be."""
    from rpi_hwid import cli, collect

    seen = []

    def fake_collect_tt(repl=True, timeout=10, take_port=True):
        seen.append(take_port)
        return {"usb": [], "repl": None, "boards": [], "summary": []}

    monkeypatch.setattr(tinytapeout, "collect_tinytapeout", fake_collect_tt)
    assert cli.main(["tinytapeout", "--json"]) == 0
    assert cli.main(["tinytapeout", "--json", "--no-stop-service"]) == 0
    assert seen == [True, False]

    seen.clear()
    monkeypatch.setattr(probe, "collect", lambda: {"model": "x"})
    monkeypatch.setattr(probe, "verdict", lambda d: {"summary": {}})
    monkeypatch.setattr(tinytapeout, "merge_tinytapeout", lambda doc, t: None)
    assert cli.main(["probe", "--json", "--tinytapeout"]) == 0
    assert cli.main(["probe", "--json", "--tinytapeout", "--no-stop-service"]) == 0
    assert seen == [True, False]
    capsys.readouterr()

    # Over ssh the call is text in the script, so the script is what to check.
    assert "merge_tinytapeout(_doc, collect_tinytapeout())" in probe_source(tinytapeout=True)
    assert "collect_tinytapeout(take_port=False)" in probe_source(
        tinytapeout=True, take_port=False)
    sent = []

    def fake_run(cmd, input, capture_output, text, timeout):
        sent.append(input)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"model": "m", "verdict": {"summary": {}}}), stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    cli.main(["collect", "--out", str(tmp_path), "--tinytapeout", "--no-stop-service", "h"])
    assert sent, "collect sent no script"
    assert "collect_tinytapeout(take_port=False)" in sent[0]


def test_the_suite_cannot_run_privileged_commands_on_this_machine():
    """tests/conftest.py fails any test that starts sudo, systemctl and the
    like. This test's code once stopped user@1001.service on a workstation
    with passwordless sudo -- every terminal and the tmux server with it --
    so the guard is proven here rather than trusted. Only harmless commands
    are tried (`true`, `--version`), so a guard that had broken could still
    not do damage while this test found out."""
    attempts = (
        ["sudo", "-n", "true"],
        ["/usr/bin/sudo", "-n", "true"],
        ["systemctl", "--version"],
        ["systemd-run", "--version"],
    )
    for args in attempts:
        with pytest.raises(pytest.fail.Exception, match="a test tried to run"):
            subprocess.run(args, capture_output=True, check=False)
    with pytest.raises(pytest.fail.Exception, match="a test tried to run"):
        subprocess.run("sudo -n true", shell=True, capture_output=True, check=False)
    with pytest.raises(pytest.fail.Exception, match=r"os\.system"):
        os.system("true")
    assert subprocess.run(["true"], check=False).returncode == 0, "ordinary commands still run"


# --- the Tiny Tapeout board spreadsheet ---------------------------------------

def test_tt_boards_json_ships_with_the_package():
    """The generated data is package data, so `pip install rpi-hwid` has it
    without a network round trip at label time."""
    from rpi_hwid import tt_boards

    doc = tt_boards.load()
    assert doc["asics"], "no asic rows: was tools/fetch_tt_boards.py run?"
    assert doc["demoboards"]
    for key, row in doc["asics"].items():
        assert key == key.lower(), key
        assert " " not in key, key
        for field in ("colour_hex", "silk_hex"):
            value = row.get(field)
            assert value is None or (len(value) == 7 and value[0] == "#"), (key, field, value)


def test_tt_boards_joins_a_shuttle_to_both_its_boards():
    """A shuttle's carrier is its own row; its demo board is whichever board
    lists the shuttle under "Used by". TT09 and the ETR board it ships with
    are the case the hand-written table never had."""
    from rpi_hwid import tt_boards

    found = tt_boards.shuttle_boards("tt09")
    assert found["chip"]["colour"] == "Yellow"
    assert found["demoboard"]["version"] == "v3.2"
    assert "tt09" in found["demoboard"]["used_by"]
    assert tt_boards.demoboard_version("tt09") == "v3.2"
    assert tt_boards.chip_page("tt09") == "https://tinytapeout.com/chips/tt09/"
    # A shuttle the sheet does not carry asks without a special case.
    assert tt_boards.shuttle_boards("nosuchshuttle") == {"chip": None, "demoboard": None}
    assert tt_boards.demoboard_version(None) is None


def test_tt_boards_colours_prefer_the_sheet_then_the_palette():
    """The sheet's own hex wins, the name is lower-cased for the label, and a
    colour the sheet names but has no hex for falls back to the palette."""
    from rpi_hwid import tinytapeout as tt_data
    from rpi_hwid import tt_boards

    c = tt_boards.colours("tt06", tt_data.COLOURS)
    assert (c["chip"], c["chip_name"]) == ("#c98599", "pink")
    # TT06's teal silkscreen has a Pantone in the sheet but no hex, so the
    # palette supplies one and the sheet still supplies the word.
    assert c["chip_silk_name"] == "teal"
    assert c["chip_silk"] == tt_data.COLOURS.get("teal")
    blank = tt_boards.colours(None, tt_data.COLOURS)
    assert set(blank.values()) == {None}
