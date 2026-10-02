"""`rpi-hwid label-input --pi-only`: the Pi's facts for the labels, read
while the board under test may be in use, so never through the header's
user bus (GPIO2/3), the FPGA or a Tiny Tapeout board."""

from __future__ import annotations

import json
import typing

import pytest

from rpi_hwid import cli, fpga, label_input, probe, tinytapeout
from rpi_hwid.model import Summary
from test_probe_collect import _pi5_tree, _w


@pytest.fixture
def pi5(tmp_path, monkeypatch):
    """A Pi 5 whose ID bus is down and user bus is up, with every command
    and every I2C access recorded."""
    _pi5_tree(tmp_path)
    _w(tmp_path, "/dev/i2c-1", "")          # the user bus is up: tempting
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "BUS_SETTLE_S", 0.0)
    calls, buses = [], []

    def fake_sh(args, timeout=15):
        calls.append(args)
        if args == ["sudo", "dtparam", "i2c_vc=on"]:
            _w(tmp_path, "/dev/i2c-0", "")
        elif args == ["sudo", "dtparam", "-r"]:
            (tmp_path / "dev/i2c-0").unlink()
        elif args[:2] == ["sudo", "vcgencmd"]:
            return "EXT5V_V volt(24)=5.33990000V\nBATT_V volt(25)=3.26000000V\n"
        elif args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x0"
        return ""

    def no_bus(bus, addr):
        buses.append(bus)  # and answer as a bus with nothing on it

    monkeypatch.setattr(probe, "sh", fake_sh)
    monkeypatch.setattr(probe, "i2c_open", no_bus)
    monkeypatch.setattr(fpga, "collect_fpga", lambda *a, **k: pytest.fail("FPGA probed"))
    monkeypatch.setattr(tinytapeout, "collect_tinytapeout",
                        lambda *a, **k: pytest.fail("Tiny Tapeout board probed"))
    return tmp_path, calls, buses


def test_the_user_bus_is_never_opened_enabled_or_scanned(pi5):
    _, calls, buses = pi5
    cli.pi_only_label_input("p48")
    assert 1 not in buses, "GPIO2/3 carry the board under test"
    assert buses, "the ID bus was read"
    assert set(buses) == {0}
    assert ["sudo", "dtparam", "i2c_arm=on"] not in calls


def test_what_it_brings_up_on_the_id_bus_it_puts_back(pi5):
    root, calls, _ = pi5
    cli.pi_only_label_input("p48")
    assert calls.index(["sudo", "dtparam", "i2c_vc=on"]) < calls.index(["sudo", "dtparam", "-r"])
    assert not (root / "dev/i2c-0").exists(), "left as it was found"


def test_it_writes_the_pi_facts_and_nothing_of_the_boards(pi5):
    doc = cli.pi_only_label_input("p48")
    s = doc["summary"]
    assert doc["host"] == "p48"
    assert s["serial"] == "c36b093f773d46b8"
    assert s["fan"] is True
    assert s["rtc_battery"] is True
    assert s["power_class"] == "ambiguous"
    assert s["macs"] == [{"kind": "eth", "mac": "98:fe:54:13:f5:75", "signal": "driver"}]
    assert s["fpga"] is None
    assert s["tinytapeout"] is None
    assert set(doc["sources"].values()) == {"rpi-hwid"}


def test_an_empty_header_with_the_user_bus_unread_is_not_read(pi5):
    """"HAT none" on a Pi wearing a Waveshare PoE HAT (B), known only by its
    chips on the user bus, would be a wrong fact on a sticker."""
    assert cli.pi_only_label_input("p48")["summary"]["header"] is None


def test_a_hat_the_firmware_read_is_still_named(pi5):
    root, _, _ = pi5
    for key, value in (("vendor", "Digilent"), ("product", "Pmod HAT Adaptor"),
                       ("uuid", "6bcd3833-3d1d-4b3e-9ab1-945c71845f3a")):
        _w(root, f"/proc/device-tree/hat/{key}", value + "\0")
    s = cli.pi_only_label_input("p48")["summary"]
    assert s["header"] == ["Digilent Pmod HAT Adaptor"]
    assert s["hat_uuid"] == "6bcd3833-3d1d-4b3e-9ab1-945c71845f3a"


def test_the_cli_prints_the_document(pi5, capsys):
    assert cli.main(["label-input", "--pi-only", "--host", "p48"]) == 0
    out = capsys.readouterr().out
    assert out == label_input.dumps(cli.pi_only_label_input("p48"))


def test_from_and_pi_only_are_one_or_the_other(capsys):
    with pytest.raises(SystemExit):
        cli.main(["label-input", "--pi-only", "--from", "x.json"])
    with pytest.raises(SystemExit):
        cli.main(["label-input"])


# --- the pi-identified event (label contract section 13) ----------------------
#
# fpgas-verify sends this document's summary as flat k=v strings and the
# site rebuilds it. These are that encoding's two halves, as the contract
# gives them, to show the Pi's document survives the trip byte for byte.

def encode(summary):
    out = {}
    for key, value in summary.items():
        if value is None:
            out[key] = "-"
        elif isinstance(value, (list, dict)):
            out[key] = json.dumps(value, separators=(",", ":"), sort_keys=True)
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def decode(details):
    hints = typing.get_type_hints(Summary)
    out = {}
    for key, text in details.items():
        hint = hints[key]
        if type(None) in typing.get_args(hint):         # X | None: X
            (hint,) = [h for h in typing.get_args(hint) if h is not type(None)]
        kind = typing.get_origin(hint) or hint
        if text == "-":
            out[key] = None
        elif kind in (tuple, dict):
            out[key] = json.loads(text)
        elif kind is bool:
            out[key] = text == "true"
        elif kind in (int, float):
            out[key] = kind(text)
        else:
            out[key] = text
    return out


def test_the_document_survives_the_event_encoding(pi5):
    root, _, _ = pi5
    _w(root, "/proc/device-tree/hat/product", "Pmod HAT Adaptor\0")
    _w(root, "/proc/device-tree/hat/vendor", "Digilent\0")
    pi = cli.pi_only_label_input("p48")
    details = encode(pi["summary"])
    assert details["macs"] == '[{"kind":"eth","mac":"98:fe:54:13:f5:75","signal":"driver"}]'
    assert details["fpga"] == "-"
    site = label_input.build("p48", decode(details), {"serial": "registration"})
    assert label_input.comparable(site) == label_input.comparable(pi)


def test_a_document_whose_header_is_unread_survives_it_too(pi5):
    pi = cli.pi_only_label_input("p48")
    assert pi["summary"]["header"] is None
    site = label_input.build("p48", decode(encode(pi["summary"])))
    assert label_input.comparable(site) == label_input.comparable(pi)
