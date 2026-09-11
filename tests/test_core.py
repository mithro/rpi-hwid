"""names, revision decoding, and the probe/fpga/tinytapeout verdicts on captured
evidence."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tokenize

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


def test_decode_revision_rejects_old_style():
    with pytest.raises(ValueError, match="old-style"):
        revision.decode_revision("0002")


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
    opi = boards.identify(docs["opi1pc-b"].summary)
    assert (opi.kind, opi.short, opi.title) == ("opi", "Orange Pi PC", "Orange Pi PC")
    assert opi.subtitle == "1 GB  ·  Allwinner H3  ·  dt orangepi-pc"
    assert opi.mark == "orange-pi.png"
    assert (opi.wired, opi.radio, opi.radio_derivable) == (True, False, False)


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
    # opi1pc-b, both captured: the one full check the fleet allows
    assert boards.sunxi_mac("02c00181e1ce7d46") == "02:81:e1:ce:7d:46"
    # opi1pc-a: only the MAC was captured (02:81:3c:1a:db:71); the rule says
    # its serial ends in 3c1adb71 and, like every H3, has 0x81 in byte 3
    assert boards.sunxi_mac("02c001813c1adb71") == "02:81:3c:1a:db:71"
    with pytest.raises(ValueError, match="sunxi serial"):
        boards.sunxi_mac("abcd")


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
                                              "idcode": None, "flash": None, "flash_jedec": None}]
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
                         "opi1pc-b", "rpi4-tt"}
    assert docs["opi1pc-b"].summary.compatible == "xunlong,orangepi-pc allwinner,sun8i-h3"
    assert docs["opi1pc-b"].summary.memory == "1 GB"
    assert docs["opi1pc-b"].summary.revision == ""
    assert docs["rpi5-netv2"].summary.fpga[0].identity == "0x00742c4e63b9085c"
    assert [b.shuttle for b in docs["rpi4-tt"].summary.tinytapeout] == ["tt06", "ttihp25a"]
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
    assert "tinytapeout tt06, ttihp25a" in out
    assert ProbeDocument.from_json("x", (tmp_path / "rpi4-tt.json").read_text()).summary \
        .tinytapeout[1].shuttle == "ttihp25a"

    monkeypatch.setattr(probe, "collect", lambda: {k: v for k, v in raw.items()
                                                    if k not in ("verdict", "tinytapeout")})
    monkeypatch.setattr(probe, "verdict", lambda d: copy.deepcopy(raw["verdict"]))
    monkeypatch.setattr(tinytapeout, "collect_tinytapeout", lambda repl=True, timeout=10: {
        "usb": raw["tinytapeout"]["usb"], "repl": raw["tinytapeout"]["repl"],
        "boards": raw["verdict"]["tinytapeout"],
        "summary": raw["verdict"]["summary"]["tinytapeout"]})
    assert cli.main(["probe", "--tinytapeout"]) == 0
    out = capsys.readouterr().out
    assert "tt     : TT06 on demo board TT06+" in out
    assert "tt     : TTIHP25a on demo board TTDBv3 [3.2]" in out
    assert "DemoBoard.get" not in tinytapeout.REPL_SNIPPET, "the snippet must not init the board"
    assert ".contents" not in tinytapeout.REPL_SNIPPET, "ChipROM.contents drives the chip's pins"
    assert cli.main(["tinytapeout"]) == 0
    assert "TTIHP25a" in capsys.readouterr().out
    assert cli.main(["tinytapeout", "--json"]) == 0
    assert '"shuttle": "ttihp25a"' in capsys.readouterr().out
