"""names, revision decoding, and the probe/fpga verdicts on captured evidence."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from rpi_hwid import fpga, names, probe, revision
from rpi_hwid.collect import load_collected, parse_probe_json, probe_source

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
    with pytest.raises(ValueError):
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


def test_decode_revision_rejects_old_style():
    with pytest.raises(ValueError):
        revision.decode_revision("0002")


def test_broadcom_macs_from_serial():
    assert revision.broadcom_macs("000000009bc0bdaf") == ("b8:27:eb:c0:bd:af", "b8:27:eb:95:e8:fa")
    macs = [{"kind": "eth", "mac": "b8:27:eb:e3:e7:e4"}]
    assert revision.derived_wlan_mac("000000004fe3e7e4", macs) == "b8:27:eb:b6:b2:b1"
    # a Pi 5 OUI has no rule; a present wlan needs none
    assert revision.derived_wlan_mac("d88100008543dc30", [{"kind": "eth", "mac": "2c:cf:67:16:bd:98"}]) is None
    assert revision.derived_wlan_mac("000000009bc0bdaf", macs + [{"kind": "wlan", "mac": "x"}]) is None


# --- probe verdict on captured evidence -------------------------------------------


def _evidence(**over):
    base = {
        "model": "Raspberry Pi 5 Model B Rev 1.1", "serial": "c36b093f773d46b8",
        "revision": "a04171", "hat_fw": None, "hat_eeproms": {}, "i2c1": [],
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
    assert v["summary"]["macs"] == [{"kind": "wlan", "mac": "b8:27:eb:02:a3:24"}]
    assert v["summary"]["rtc_battery"] is None


def test_verdict_poe_hat_b_by_i2c_devices():
    d = _evidence(model="Raspberry Pi 4 Model B Rev 1.5", pi5=False, i2c1=["20", "3c"])
    v = probe.verdict(d)
    assert v["summary"]["power_class"] == "gpio-poe-hat"
    assert "Waveshare PoE HAT (B)" in v["summary"]["header"]


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


# --- collector --------------------------------------------------------------------------


def test_probe_source_embeds_fpga_and_both_files_run_standalone(tmp_path):
    src = probe_source(fpga=True, jtag=False, flash=False)
    assert src.startswith("RPI_HWID_EMBEDDED = True")
    assert "merge_fpga(_doc" in src
    # both probes must be plain scripts: no f-strings, compile clean
    for name in ("probe.py", "fpga.py"):
        text = probe_source() if name == "probe.py" else fpga.__file__ and open(fpga.__file__).read()
        assert "f\"" not in text and "f'" not in text
    r = subprocess.run([sys.executable, "-c", "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)",
                        probe.__file__], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_parse_probe_json_skips_banner():
    doc = {"verdict": {"summary": {}}}
    assert parse_probe_json("Password set for pi\n" + json.dumps(doc)) == doc
    with pytest.raises(ValueError):
        parse_probe_json("no json")
    with pytest.raises(ValueError):
        parse_probe_json(json.dumps({"x": 1}))


def test_load_collected(data_dir):
    docs = load_collected(data_dir)
    assert set(docs) == {"rpi5-netv2", "pi-sw1-p10", "pi-sw2-p16", "rpiz-serial", "pi-sw2-p47"}
    (data_dir / "bad.json").write_text("{}")
    with pytest.raises(ValueError, match="not a probe document"):
        load_collected(data_dir)
