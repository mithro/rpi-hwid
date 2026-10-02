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
    calls, buses, overlays = [], [], []

    def fake_sh(args, timeout=15):
        calls.append(args)
        if args == ["sudo", "dtparam", "i2c_vc=on"]:
            _w(tmp_path, "/dev/i2c-0", "")
            overlays.append("dtparam i2c_vc=on")
        elif args == ["sudo", "dtparam", "-l"]:
            return "\n".join(f"{i}:  {o}" for i, o in enumerate(overlays))
        elif args[:3] == ["sudo", "dtparam", "-r"]:
            overlays.pop(int(args[3]))
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
    assert calls.index(["sudo", "dtparam", "i2c_vc=on"]) < \
        calls.index(["sudo", "dtparam", "-r", "0"])
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
    # not probed, so none listed: no FPGA or Tiny Tapeout label comes of it
    assert s["fpga"] == []
    assert s["tinytapeout"] == []
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


# --- --user-bus (contract 18) ------------------------------------------------

def test_user_bus_scans_it_and_puts_back_what_it_brought_up(pi5, monkeypatch):
    root, calls, buses = pi5
    (root / "dev/i2c-1").unlink()                     # off, as most of the fleet has it

    def sh(args, timeout=15):
        calls.append(args)
        if args == ["sudo", "dtparam", "i2c_arm=on"]:
            _w(root, "/dev/i2c-1", "")
            overlays.append("dtparam i2c_arm=on")
        elif args == ["sudo", "dtparam", "i2c_vc=on"]:
            _w(root, "/dev/i2c-0", "")
            overlays.append("dtparam i2c_vc=on")
        elif args == ["sudo", "dtparam", "-l"]:
            return "\n".join(f"{i}:  {o}" for i, o in enumerate(overlays))
        elif args[:3] == ["sudo", "dtparam", "-r"]:
            gone = overlays.pop(int(args[3]))
            (root / ("dev/i2c-1" if gone.endswith("arm=on") else "dev/i2c-0")).unlink()
        elif args[:2] == ["sudo", "vcgencmd"]:
            return "EXT5V_V volt(24)=5.33990000V\nBATT_V volt(25)=3.26000000V\n"
        return ""
    overlays = []
    monkeypatch.setattr(probe, "sh", sh)
    doc = cli.pi_only_label_input("p48", user_bus=True)
    assert 1 in buses, "the user bus was scanned"
    assert ["sudo", "dtparam", "i2c_arm=on"] in calls
    assert overlays == []
    assert not (root / "dev/i2c-1").exists(), "left as it was found"
    # read, and nothing there: a bare header, which the Pi label can say
    assert doc["summary"]["header"] == []


def test_user_bus_names_a_hat_known_only_by_its_chips(pi5, monkeypatch):
    _ = pi5
    monkeypatch.setattr(probe, "i2c_scan", lambda bus, **kw: ["20", "3c"] if bus == 1 else [])
    assert cli.pi_only_label_input("p48", user_bus=True)["summary"]["header"] == [
        "Waveshare PoE HAT (B)"]


def test_without_user_bus_nothing_changes(pi5):
    _, calls, buses = pi5
    cli.pi_only_label_input("p48")
    assert 1 not in buses
    assert ["sudo", "dtparam", "i2c_arm=on"] not in calls


def test_user_bus_goes_with_pi_only(capsys, tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}")
    assert cli.main(["label-input", "--from", str(path), "--user-bus"]) == 2
    assert "--user-bus goes with --pi-only" in capsys.readouterr().err


def test_the_cli_passes_user_bus_on(pi5, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "pi_only_label_input",
                        lambda host, user_bus=False: seen.append(user_bus) or
                        label_input.build(host, {"model": "m"}))
    assert cli.main(["label-input", "--pi-only", "--user-bus", "--host", "h"]) == 0
    assert seen == [True]


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
# gives them (sections 13 and 17), to show the Pi's document survives the
# trip byte for byte:
#   a field that was not read      left out (the Pi-only probe's header,
#                                  when nothing it may look at named one)
#   a scalar read, and none        "-" (a Pi 4's fan, a HAT with no uuid)
#   a list read, and empty         "[]"

UNREAD = ("header",)       # the one field --pi-only writes as null for "not read"


def encode(summary):
    out = {}
    for key, value in summary.items():
        if value is None:
            if key not in UNREAD:
                out[key] = "-"
            continue
        if isinstance(value, (list, dict)):
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
    assert details["fpga"] == "[]"
    # contract 19: the adapters go too, so the site's usb_net matches
    assert json.loads(details["usb_net"])[0]["vidpid"] == "0bda:8153"
    assert details["hat_uuid"] == "-"          # this HAT has none
    site = label_input.build("p48", decode(details), {"serial": "registration"})
    assert label_input.comparable(site) == label_input.comparable(pi)


def test_a_document_whose_header_is_unread_survives_it_too(pi5):
    """An unread header is left out of the event, not sent as "-", and the
    label input the site builds without it has it as not read."""
    pi = cli.pi_only_label_input("p48")
    assert pi["summary"]["header"] is None
    details = encode(pi["summary"])
    assert "header" not in details
    site = label_input.build("p48", decode(details))
    assert site["summary"]["header"] is None
    assert label_input.comparable(site) == label_input.comparable(pi)


def test_a_pi_4s_fan_is_sent_as_read_and_none():
    """No fan header on a Pi 4: the probe says so with null, which goes out
    as "-" and comes back as null."""
    import copy

    from conftest import RAW

    pi4 = label_input.from_probe("rpi4-tt", copy.deepcopy(RAW["rpi4-tt"]))
    details = encode(pi4["summary"])
    assert details["fan"] == details["rtc_battery"] == "-"
    site = label_input.build("rpi4-tt", decode(details))
    assert site["summary"]["fan"] is None
    assert label_input.comparable(site) == label_input.comparable(pi4)
