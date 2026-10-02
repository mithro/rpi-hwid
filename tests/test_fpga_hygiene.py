"""What the FPGA module's own reads leave behind (issue #55's hardware
runs): a BAR read with memory decoding off, the wrong harness's pins, and
harness pins left driven."""

from __future__ import annotations

import pytest

from rpi_hwid import fpga
from test_probe_collect import _w

SLOT = "0001:01:00.0"


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
    assert calls == [["sudo", "setpci", "-s", SLOT, "COMMAND=0002"],
                     ["sudo", fpga.sys.executable or "python3"],
                     ["sudo", "setpci", "-s", SLOT, "COMMAND=0000"]]


def test_memory_decoding_already_on_is_left_alone(ran):
    root, calls = ran
    _config(root, 0x0006)
    fpga.soc_probe(SLOT)
    assert not [c for c in calls if "setpci" in c]


def test_the_command_register_is_put_back_when_the_read_fails(ran, monkeypatch):
    root, calls = ran
    _config(root, 0x0400)

    def boom(args, timeout=15):
        raise OSError("gone")
    monkeypatch.setattr(fpga, "sh_all", boom)
    with pytest.raises(OSError, match="gone"):
        fpga.soc_probe(SLOT)
    assert calls[-1] == ["sudo", "setpci", "-s", SLOT, "COMMAND=0400"]


ACORN = [{"slot": SLOT, "id": "1e24:021f", "subsystem": "1e24:021f", "bars": []}]


@pytest.mark.parametrize(("pcie", "model", "pins"), [
    (ACORN, "Raspberry Pi 5 Model B Rev 1.1", "10:9:11:8"),
    (ACORN, "Raspberry Pi Compute Module 4 Rev 1.1", "2:3:4:14"),
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
    monkeypatch.setattr(fpga, "identity_probe", lambda: None)
    seen = []
    monkeypatch.setattr(fpga, "jtag_probe", lambda flash, pins, *a: seen.append(pins) or {})
    fpga.collect_fpga(jtag=True)
    assert seen == ["10:9:11:8"]


PINCTRL = ("10: a0    pn | lo // GPIO10 = SPI0_MOSI\n"
           " 9: ip    pd | lo // GPIO9 = input\n"
           "11: op dh pu | hi // GPIO11 = output\n"
           " 8: ip    pu | hi // GPIO8 = input\n")


def _harness(monkeypatch, chain=None):
    calls = []

    def sh(args, timeout=15):
        calls.append(args)
        return PINCTRL if args[:4] == ["sudo", "-n", "pinctrl", "get"] else ""
    monkeypatch.setattr(fpga, "sh", sh)
    monkeypatch.setattr(fpga, "digilent_cables", list)
    monkeypatch.setattr(fpga, "ch347_cables", list)
    monkeypatch.setattr(fpga, "jtag_probe_chain", chain or (lambda *a: {"idcode": "0x13636093"}))
    return calls


def test_the_harness_pins_are_put_back_as_they_were_found(monkeypatch):
    calls = _harness(monkeypatch)
    res = fpga.jtag_probe(pins="10:9:11:8")
    assert calls[0] == ["sudo", "-n", "pinctrl", "get", "10,9,11,8"]
    assert calls[1:] == [["sudo", "-n", "pinctrl", "set", "8", "ip", "pu"],
                         ["sudo", "-n", "pinctrl", "set", "9", "ip", "pd"],
                         ["sudo", "-n", "pinctrl", "set", "10", "a0", "pn"],
                         ["sudo", "-n", "pinctrl", "set", "11", "op", "dh", "pu"]]
    assert res["pins_restored"] == ["8", "9", "10", "11"]


def test_they_are_put_back_when_the_read_fails(monkeypatch):
    def boom(*a):
        raise RuntimeError("libgpiod assertion")
    calls = _harness(monkeypatch, boom)
    with pytest.raises(RuntimeError, match="libgpiod"):
        fpga.jtag_probe(pins="10:9:11:8")
    assert len([c for c in calls if c[3] == "set"]) == 4


def test_without_pinctrl_nothing_is_set(monkeypatch):
    calls = _harness(monkeypatch)
    monkeypatch.setattr(fpga, "sh", lambda args, timeout=15: calls.append(args) or "")
    res = fpga.jtag_probe(pins="10:9:11:8")
    assert not [c for c in calls if c[3] == "set"]
    assert "pins_restored" not in res


def test_a_cable_of_its_own_drives_no_pi_pin(monkeypatch):
    calls = _harness(monkeypatch)
    monkeypatch.setattr(fpga, "digilent_cables", lambda: [{"serial": "210319B301DE"}])
    fpga.jtag_probe()
    assert calls == []
