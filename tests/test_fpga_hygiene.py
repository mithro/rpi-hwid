"""What the FPGA module's own reads leave behind (issue #55's hardware
runs): a BAR read with memory decoding off, the wrong harness's pins,
harness pins left driven, a masked IDCODE -- and the locks that keep it
out of fpgas-verify's way."""

from __future__ import annotations

import fcntl
import threading

import pytest

from rpi_hwid import fpga
from test_probe_collect import _w

SLOT = "0001:01:00.0"
# the real lock paths, read before conftest empties them for every test
REAL_LOCKS = dict(fpga.BOARD_LOCKS)


def _config(root, command):
    _w(root, f"/sys/bus/pci/devices/{SLOT}/config",
       bytes([0xEE, 0x10, 0x21, 0x70, command & 0xFF, command >> 8]) + bytes(58))


@pytest.fixture
def ran(tmp_path, monkeypatch):
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(fpga, "sh", lambda args, timeout=15: calls.append(args) or "")
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15: (
        calls.append(args[:2]), "IDENT=\nDNA=0054b48664b04854\n")[1])
    return tmp_path, calls


def test_a_bar_read_turns_memory_decoding_on_and_back_off(ran):
    root, calls = ran
    _config(root, 0x0000)                         # Mem-: no driver bound
    fpga.soc_probe(SLOT)
    # value:mask: the Memory Space bit alone is written, both ways
    assert calls == [["sudo", "setpci", "-s", SLOT, "COMMAND=0002:0002"],
                     ["sudo", fpga.sys.executable or "python3"],
                     ["sudo", "setpci", "-s", SLOT, "COMMAND=0000:0002"]]


def test_memory_decoding_already_on_is_left_alone(ran):
    root, calls = ran
    _config(root, 0x0006)
    fpga.soc_probe(SLOT)
    assert not [c for c in calls if "setpci" in c]


def test_the_memory_bit_is_turned_off_again_when_the_read_fails(ran, monkeypatch):
    root, calls = ran
    _config(root, 0x0400)

    def boom(args, timeout=15):
        raise OSError("gone")
    monkeypatch.setattr(fpga, "sh_all", boom)
    with pytest.raises(OSError, match="gone"):
        fpga.soc_probe(SLOT)
    assert calls[-1] == ["sudo", "setpci", "-s", SLOT, "COMMAND=0000:0002"]


# --- the boards' locks -----------------------------------------------------------

@pytest.fixture
def locks(tmp_path, monkeypatch):
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "BOARD_LOCKS", REAL_LOCKS)
    monkeypatch.setattr(fpga, "LOCK_WAIT_S", 0.2)
    (tmp_path / "run/lock").mkdir(parents=True)
    (tmp_path / "run/fpgas-online").mkdir(parents=True)
    return tmp_path


def _hold(root, path):
    held = open(str(root) + path, "a")  # noqa: SIM115 -- the open file is the lock
    fcntl.flock(held, fcntl.LOCK_EX)
    return held


def test_a_bar_read_waits_for_fpgas_verify_and_says_board_busy(locks, monkeypatch):
    _config(locks, 0x0000)
    monkeypatch.setattr(fpga, "sh", lambda *a, **k: pytest.fail("touched a held board"))
    monkeypatch.setattr(fpga, "sh_all", lambda *a, **k: pytest.fail("touched a held board"))
    held = _hold(locks, "/run/lock/fpgas-acorn.lock")
    try:
        res = fpga.soc_probe(SLOT)
    finally:
        held.close()
    assert res["error"].startswith("board busy: /run/lock/fpgas-acorn.lock held for")


def test_a_lock_let_go_within_the_wait_is_taken(locks, monkeypatch):
    monkeypatch.setattr(fpga, "LOCK_WAIT_S", 5)
    held = _hold(locks, "/run/lock/fpgas-acorn.lock")
    threading.Timer(0.3, held.close).start()
    got = fpga.take_lock("acorn")
    assert got is not None
    fpga.release_lock(got)


@pytest.mark.parametrize(("pins", "lock"), [
    ("10:9:11:8", "/run/lock/fpgas-acorn.lock"),
    ("2:3:4:14", "/run/lock/fpgas-acorn.lock"),
    ("27:22:4:17", "/run/fpgas-online/netv2.lock")])
def test_a_chain_read_takes_its_boards_lock(locks, monkeypatch, pins, lock):
    monkeypatch.setattr(fpga, "digilent_cables", list)
    monkeypatch.setattr(fpga, "ch347_cables", list)
    monkeypatch.setattr(fpga, "jtag_probe_chain",
                        lambda *a: pytest.fail("read a chain someone holds"))
    held = _hold(locks, lock)
    try:
        res = fpga.jtag_probe(pins=pins)
    finally:
        held.close()
    assert res["idcode"] is None
    assert res["error"].startswith("board busy: " + lock)


def test_an_artys_cable_takes_the_artys_lock(locks, monkeypatch):
    monkeypatch.setattr(fpga, "digilent_cables", lambda: [{"serial": "210319B301DE"}])
    monkeypatch.setattr(fpga, "jtag_probe_chain", lambda *a: pytest.fail("read"))
    held = _hold(locks, "/run/fpgas-online/arty.lock")
    try:
        assert fpga.jtag_probe()["error"].startswith("board busy")
    finally:
        held.close()


def test_no_fpgas_verify_no_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "BOARD_LOCKS", REAL_LOCKS)
    assert fpga.take_lock("acorn") is None


# --- the harness -----------------------------------------------------------------

ACORN = [{"slot": SLOT, "id": "1e24:021f", "subsystem": "1e24:021f", "bars": []}]


@pytest.mark.parametrize(("pcie", "model", "pins"), [
    (ACORN, "Raspberry Pi 5 Model B Rev 1.1", "10:9:11:8"),
    (ACORN, "Raspberry Pi 500 Rev 1.0", "10:9:11:8"),
    (ACORN, "Raspberry Pi Compute Module 4 Rev 1.1", "2:3:4:14"),
    (ACORN, "Raspberry Pi Compute Module 5 Rev 1.0", "2:3:4:14"),
    (ACORN, "Raspberry Pi 4 Model B Rev 1.5", "27:22:4:17"),
    ([{"slot": SLOT, "id": "10ee:7024", "subsystem": "", "bars": []}],
     "Raspberry Pi 5 Model B Rev 1.0", "27:22:4:17"),
    ([], "Raspberry Pi 4 Model B Rev 1.5", "27:22:4:17"),
    ([{"slot": SLOT, "id": "10ee:7011", "subsystem": "0000:0000", "bars": []}],
     "Raspberry Pi 5 Model B Rev 1.1", "10:9:11:8"),
])
def test_the_harness_is_the_boards(pcie, model, pins):
    assert fpga.default_pins(pcie, model) == pins


def test_collect_drives_the_acorns_harness_by_default(tmp_path, monkeypatch):
    _w(tmp_path, "/proc/device-tree/model", "Raspberry Pi 5 Model B Rev 1.1\0")
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "pcie_devices", lambda: ACORN)
    monkeypatch.setattr(fpga, "ftdi_devices", list)
    monkeypatch.setattr(fpga, "cynthion_devices", list)
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: None)
    seen = []
    monkeypatch.setattr(fpga, "jtag_probe", lambda flash, pins, *a: seen.append(pins) or {})
    fpga.collect_fpga(jtag=True)
    assert seen == ["10:9:11:8"]


PINCTRL = ("10: a0    pn | lo // GPIO10 = SPI0_MOSI\n"
           " 9: ip    pd | lo // GPIO9 = input\n"
           "11: op dh pu | hi // GPIO11 = output\n"
           " 8: ip    pu | hi // GPIO8 = input\n")
DRIVEN = ("10: op dl pn | lo // GPIO10 = output\n"
          " 9: ip    pd | lo // GPIO9 = input\n"
          "11: op dl pu | lo // GPIO11 = output\n"
          " 8: op dl pu | lo // GPIO8 = output\n")


def _harness(monkeypatch, chain=None, after=PINCTRL):
    calls, gets = [], []

    def sh(args, timeout=15):
        calls.append(args)
        if args[:4] == ["sudo", "-n", "pinctrl", "get"]:
            gets.append(1)
            return PINCTRL if len(gets) == 1 else after
        return ""
    monkeypatch.setattr(fpga, "sh", sh)
    monkeypatch.setattr(fpga, "digilent_cables", list)
    monkeypatch.setattr(fpga, "ch347_cables", list)
    monkeypatch.setattr(fpga, "jtag_probe_chain", chain or (lambda *a: {"idcode": "0x13636093"}))
    return calls


def test_the_harness_pins_are_put_back_and_checked(monkeypatch):
    calls = _harness(monkeypatch)
    res = fpga.jtag_probe(pins="10:9:11:8")
    assert calls[0] == ["sudo", "-n", "pinctrl", "get", "10,9,11,8"]
    assert calls[1:5] == [["sudo", "-n", "pinctrl", "set", "8", "ip", "pu"],
                          ["sudo", "-n", "pinctrl", "set", "9", "ip", "pd"],
                          ["sudo", "-n", "pinctrl", "set", "10", "a0", "pn"],
                          ["sudo", "-n", "pinctrl", "set", "11", "op", "dh", "pu"]]
    assert calls[5] == ["sudo", "-n", "pinctrl", "get", "10,9,11,8"]
    assert res["pins_restored"] == ["8", "9", "10", "11"]
    assert "pins_not_restored" not in res


def test_pins_that_did_not_come_back_are_named(monkeypatch):
    _harness(monkeypatch, after=DRIVEN)
    res = fpga.jtag_probe(pins="10:9:11:8")
    assert res["pins_not_restored"] == ["8", "10", "11"]
    assert res["pins_restored"] == ["9"]


def test_they_are_put_back_when_the_read_fails(monkeypatch):
    def boom(*a):
        raise RuntimeError("libgpiod assertion")
    calls = _harness(monkeypatch, boom)
    with pytest.raises(RuntimeError, match="libgpiod"):
        fpga.jtag_probe(pins="10:9:11:8")
    assert len([c for c in calls if c[3] == "set"]) == 4


def test_without_pinctrl_the_pins_are_said_not_to_be_put_back(monkeypatch):
    calls = _harness(monkeypatch)
    monkeypatch.setattr(fpga, "sh", lambda args, timeout=15: calls.append(args) or "")
    res = fpga.jtag_probe(pins="10:9:11:8")
    assert not [c for c in calls if c[3] == "set"]
    assert "pins_restored" not in res
    assert res["pins_not_restored"].startswith("pinctrl could not read")


def test_a_cable_of_its_own_drives_no_pi_pin(monkeypatch):
    calls = _harness(monkeypatch)
    monkeypatch.setattr(fpga, "digilent_cables", lambda: [{"serial": "210319B301DE"}])
    fpga.jtag_probe()
    assert calls == []


# --- the whole IDCODE ----------------------------------------------------------

# openFPGALoader --detect --verbose-level 2 on p48's Acorn, as fpgas-verify's
# #77 reads it: the part table's key masked, the raw scan whole.
DETECT_P48 = """\
JTAG init
- 0 -> 0x13636093
- 1 -> 0xffffffff
index 0:
\tidcode 0x3636093
\tmanufacturer xilinx
\tfamily artix a7 200t
\tmodel  xc7a200
\tirlength 6
"""


def _chain(monkeypatch, detect):
    seen = []
    monkeypatch.setattr(fpga, "digilent_cables", list)
    monkeypatch.setattr(fpga, "ch347_cables", list)
    monkeypatch.setattr(fpga, "gpiochips", list)
    monkeypatch.setattr(fpga, "openfpgaloader_tool", lambda download=True: {
        "argv": ["sudo", "openFPGALoader"], "source": "host", "flash_info": True})
    monkeypatch.setattr(fpga, "sh", lambda args, timeout=15: (
        "/usr/bin/openFPGALoader" if args[0] == "which" else '{"dna": "0x0054b48664b04854"}'))
    monkeypatch.setattr(fpga, "sh_all", lambda args, timeout=15: seen.append(args) or detect)
    return seen


def test_the_idcode_is_the_raw_scans_whole_value(monkeypatch):
    seen = _chain(monkeypatch, DETECT_P48)
    res = fpga.jtag_probe_chain(pins="10:9:11:8")
    assert res["idcode"] == "0x13636093"
    assert seen[0][-3:] == ["--detect", "--verbose-level", "2"]


def test_without_the_raw_scan_the_masked_one_stands(monkeypatch):
    _chain(monkeypatch, "index 0:\n\tidcode 0x3636093\n\tfamily artix a7 200t\n")
    assert fpga.jtag_probe_chain(pins="10:9:11:8")["idcode"] == "0x3636093"
