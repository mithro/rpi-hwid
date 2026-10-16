"""`rpi-hwid label-input --pi-only`: the Pi's facts for the labels, read
while the board under test may be in use, so never through the header's
user bus (GPIO2/3), the FPGA or a Tiny Tapeout board."""

from __future__ import annotations

import json
import typing

import pytest

from id_bus_fixtures import SimBus, eeprom_bus
from rpi_hwid import cli, fpga, label_input, probe, tinytapeout
from rpi_hwid.model import Summary
from test_probe_collect import _pi5_tree, _w


@pytest.fixture
def pi5(tmp_path, monkeypatch):
    """A Pi 5 with no I2C bus on the ID pins (GPIO0/1) and its user bus
    up, with every command and every I2C access recorded: a /dev/i2c-N
    opened as N, the ID bus's GPIO lines requested as 0."""
    _pi5_tree(tmp_path)
    _w(tmp_path, "/dev/i2c-1", "")          # the user bus is up: tempting
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "BUS_SETTLE_S", 0.0)
    calls, buses = [], []

    def fake_sh(args, timeout=15):
        calls.append(args)
        if args[-1:] == ["pmic_read_adc"]:
            return "EXT5V_V volt(24)=5.33990000V\nBATT_V volt(25)=3.26000000V\n"
        if args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x0"
        return ""

    def no_bus(bus, addr):
        buses.append(bus)  # and answer as a bus with nothing on it

    def id_lines(chip, offsets):
        assert offsets == (0, 1)
        buses.append(0)
        return SimBus()    # pulled up, and nothing on it

    monkeypatch.setattr(probe, "sh", fake_sh)
    monkeypatch.setattr(probe, "i2c_open", no_bus)
    monkeypatch.setattr(probe, "open_id_lines", id_lines)
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


def test_the_id_bus_is_read_with_no_command_at_all(pi5):
    """No overlay and no module for the ID bus: removing `dtparam i2c_vc=on`
    hangs in the kernel on a Pi 3B+ (mithro/rpi-hwid issue #91). Its
    GPIO lines are read from user space instead, and nothing is left to
    put back."""
    root, calls, buses = pi5
    cli.pi_only_label_input("p48")
    assert buses == [0]
    assert [c for c in calls if c[0] == "sudo" and "pmic_read_adc" not in c] == []
    assert not (root / "dev/i2c-0").exists()


def test_a_hat_on_the_id_bus_is_named(pi5, monkeypatch):
    """A HAT EEPROM the firmware does not read (Waveshare's PoE M.2 HAT+ (B)
    at 0x52), found through the GPIO lines."""
    monkeypatch.setattr(probe, "open_id_lines", lambda chip, offsets: eeprom_bus(0x52))
    s = cli.pi_only_label_input("p48")["summary"]
    assert s["header"] == ["Waveshare PoE M.2 HAT+ (B)"]
    assert s["hat_uuid"] == "9729525c-eeee-98e9-f348-a0720f4c16eb"


def test_the_cameras_are_not_looked_for(pi5, monkeypatch):
    """The site's pi-identified event carries no cameras (contract 13), so
    the two documents agree only if this one has none either: not read."""
    monkeypatch.setattr(probe, "collect_cameras", lambda: pytest.fail("cameras probed"))
    assert cli.pi_only_label_input("p48")["summary"]["cameras"] is None


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
        elif args == ["sudo", "dtparam", "-l"]:
            return "\n".join(f"{i}:  {o}" for i, o in enumerate(overlays))
        elif args[:3] == ["sudo", "dtparam", "-r"]:
            overlays.pop(int(args[3]))
            (root / "dev/i2c-1").unlink()
        elif args[-1:] == ["pmic_read_adc"]:
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



# --- label-input fails loud (mithro/rpi-hwid issue #91) ----------------------


def _user_bus_host(root, calls, overlays):
    """A Pi whose user bus is off and comes up with `dtparam i2c_arm=on`."""
    (root / "dev/i2c-1").unlink()

    def sh(args, timeout=15):
        calls.append(list(args))
        if args == ["sudo", "dtparam", "i2c_arm=on"]:
            _w(root, "/dev/i2c-1", "")
            overlays.append("dtparam i2c_arm=on")
        elif args == ["sudo", "dtparam", "-l"]:
            return "\n".join(f"{i}:  {o}" for i, o in enumerate(overlays))
        elif args[:3] == ["sudo", "dtparam", "-r"]:
            overlays.pop(int(args[3]))
            (root / "dev/i2c-1").unlink()
        elif args[-1:] == ["pmic_read_adc"]:
            return "EXT5V_V volt(24)=5.33990000V\nBATT_V volt(25)=3.26000000V\n"
        return ""
    return sh


def test_an_undo_that_hangs_makes_label_input_fail_and_say_so(pi5, monkeypatch, capsys):
    """What happened on the Pi 3B+ in issue #91, with the user bus's
    overlay: its removal timed out. No document, exit 1, and stderr names
    the command and says the host is not as it was found."""
    root, calls, _ = pi5
    overlays = []
    sh = _user_bus_host(root, calls, overlays)

    def sh_checked(args, timeout=15):
        if args[:3] == ["sudo", "dtparam", "-r"]:
            raise probe.CommandFailedError(
                f"`{' '.join(args)}` did not finish within {timeout} s; it was killed, "
                "and what it started may still be running")
        return sh(args, timeout)
    monkeypatch.setattr(probe, "sh", sh)
    monkeypatch.setattr(probe, "sh_checked", sh_checked)
    rc = cli.main(["label-input", "--pi-only", "--user-bus", "--host", "p48"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert err.startswith("label-input: the host is not as it was found, after "
                          "`sudo modprobe i2c-dev`, `sudo dtparam i2c_arm=on`: ")
    assert "`sudo dtparam -r 0` did not finish within 15 s" in err
    assert 'dtparam -l was [] and is now ["dtparam i2c_arm=on"]' in err


def test_a_host_changed_meanwhile_makes_label_input_fail(pi5, monkeypatch, capsys):
    """Every undo step succeeded, and the host still differs from how it was
    found: that is said too, never passed off as put back."""
    root, calls, _ = pi5
    overlays = []
    monkeypatch.setattr(probe, "sh", _user_bus_host(root, calls, overlays))

    def scan(bus, **kw):
        overlays.append("dtoverlay spi0-1cs")      # someone else's, meanwhile
        return []
    monkeypatch.setattr(probe, "i2c_scan", scan)
    assert cli.main(["label-input", "--pi-only", "--user-bus", "--host", "p48"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert 'dtparam -l was [] and is now ["dtoverlay spi0-1cs"]' in err


def test_a_host_put_back_exits_zero(pi5, monkeypatch, capsys):
    root, calls, _ = pi5
    overlays = []
    monkeypatch.setattr(probe, "sh", _user_bus_host(root, calls, overlays))
    assert cli.main(["label-input", "--pi-only", "--user-bus", "--host", "p48"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert json.loads(out)["summary"]["header"] == []
    assert overlays == []


def test_an_id_bus_in_use_makes_label_input_fail(pi5, monkeypatch, capsys):
    """GPIO0/1 held by a kernel driver or another program: the read is not
    made, and not passed off as a header with nothing on it."""
    def busy(chip, offsets):
        raise probe.IdBusError("GPIO0/1 are in use by i2c-gpio: the HAT ID bus was not read")
    monkeypatch.setattr(probe, "open_id_lines", busy)
    assert cli.main(["label-input", "--pi-only", "--host", "p48"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "label-input: GPIO0/1 are in use by i2c-gpio: the HAT ID bus was not read\n"


def test_label_input_this_host_fails_the_same_way(monkeypatch, capsys):
    from rpi_hwid import this_host

    def changed(host):
        raise probe.HostNotAsFoundError("the host is not as it was found, after `x`: y")
    monkeypatch.setattr(this_host, "label_input_document", changed)
    assert cli.main(["label-input", "--this-host", "--host", "h"]) == 1
    assert capsys.readouterr().err == (
        "label-input: the host is not as it was found, after `x`: y\n")


def test_labels_this_host_fails_the_same_way(monkeypatch, capsys, tmp_path):
    from rpi_hwid import labels, this_host

    def changed(host):
        raise probe.HostNotAsFoundError("the host is not as it was found, after `x`: y")
    monkeypatch.setattr(this_host, "label_input_document", changed)
    assert labels.main(["--this-host", "--host", "h", "--out", str(tmp_path / "l.pdf")]) == 1
    assert capsys.readouterr().err == "h: the host is not as it was found, after `x`: y\n"
    assert not (tmp_path / "l.pdf").exists()


def test_a_hat_the_firmware_read_settles_it_even_then(pi5, monkeypatch):
    root, _, _ = pi5
    monkeypatch.setattr(probe, "sh", lambda args, timeout=15: "")
    for key, value in (("vendor", "Digilent"), ("product", "Pmod HAT Adaptor")):
        _w(root, f"/proc/device-tree/hat/{key}", value + "\0")
    doc = cli.pi_only_label_input("p48", user_bus=True)
    assert doc["summary"]["header"] == ["Digilent Pmod HAT Adaptor"]


@pytest.mark.parametrize(("buses", "user_bus", "header", "want"), [
    ({"id": True, "user": True}, True, [], []),
    ({"id": True, "user": True}, True, ["Waveshare PoE HAT (B)"], ["Waveshare PoE HAT (B)"]),
    ({"id": False, "user": True}, True, ["Waveshare PoE HAT (B)"], None),
    ({"id": True, "user": False}, True, [], None),
    ({"id": True, "user": False}, False, [], None),
    ({"id": True, "user": False}, False, ["Waveshare PoE M.2 HAT+ (B)"],
     ["Waveshare PoE M.2 HAT+ (B)"]),
    ({"id": False, "user": False}, False, [], None),
    ({}, False, ["no HAT header on this board"], ["no HAT header on this board"]),
])
def test_the_header_is_null_wherever_a_bus_it_needed_went_unread(buses, user_bus, header, want):
    d = {"header_buses_read": buses, "hat_fw": None}
    assert cli.header_as_read(header, d, user_bus) == want


def _cm5(root):
    _w(root, "/proc/device-tree/model", "Raspberry Pi Compute Module 5 Lite Rev 1.0\0")
    _w(root, "/proc/device-tree/compatible", "raspberrypi,5-compute-module\0brcm,bcm2712\0")


def _bus_work(calls):
    return [c for c in calls if "dtparam" in c or "modprobe" in c]


def test_a_compute_module_has_no_header_bus_touched_by_any_default(pi5):
    """The safety property of mithro/rpi-hwid issue #83, at the level of the
    commands and the bus opens themselves: GPIO2/GPIO3 are JTAG wires on a
    Compute Blade wired to an Acorn."""
    root, calls, buses = pi5
    _cm5(root)
    doc = cli.pi_only_label_input("pi16")
    assert buses == [], "no /dev/i2c-N was opened"
    assert _bus_work(calls) == [], "no dtparam, no modprobe"
    # contract 17: a header nobody looked at is null, never "HAT none"
    assert doc["summary"]["header"] is None
    calls.clear()
    d = probe.collect(cameras=False)             # what a bare probe run does
    assert buses == []
    assert _bus_work(calls) == []
    assert d["header_buses_skipped"] == "compute-module"


def test_a_compute_modules_buses_are_read_when_asked(pi5):
    root, calls, buses = pi5
    _cm5(root)
    cli.pi_only_label_input("pi16", user_bus=True)
    assert set(buses) == {0, 1}
    assert not any("i2c_vc=on" in c for c in calls)


def test_rpi_hwid_probe_fails_the_same_way(monkeypatch, capsys):
    def changed(*a, **k):
        raise probe.HostNotAsFoundError("the host is not as it was found, after `x`: y")
    monkeypatch.setattr(probe, "collect", changed)
    assert cli.main(["probe", "--json"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "probe: the host is not as it was found, after `x`: y\n"


def test_the_probe_script_fails_the_same_way(monkeypatch, capsys):
    """`python3 - < probe.py` over ssh, as collect runs it."""
    def busy(*a, **k):
        raise probe.IdBusError("GPIO0/1 are in use by i2c-gpio: the HAT ID bus was not read")
    monkeypatch.setattr(probe, "collect", busy)
    monkeypatch.setattr("sys.argv", ["probe.py", "--json"])
    with pytest.raises(SystemExit) as exit_:
        probe.main()
    assert exit_.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "probe: GPIO0/1 are in use by i2c-gpio: the HAT ID bus was not read\n"
