"""A Tiny Tapeout board whose REPL could not be read is named from
fpgas-verify's identity document, matched on its USB serial. pi-sw2-p48's
TT03.5 (2026-10-18) has no chip ROM and its demo board answered nothing on
its REPL, not even Ctrl-C; fpgas-verify had read it at boot."""

from __future__ import annotations

from rpi_hwid import tinytapeout

SERIAL = "de636c65c34d6a25"

CANDIDATE = {
    "kind": "rp2-micropython", "usb": "1-1.4", "usb_serial": SERIAL, "tty": "/dev/ttyACM0",
    "how": "MicroPython RP2 2e8a:0005 on USB 1-1.4; REPL: no raw REPL prompt"}

IDENTITY = {
    "schema": "fpgas-verify/identity", "identity_version": 1, "source": "live",
    "read_at": "2026-10-18T02:52:31+00:00",
    "boards": [{
        "board": "tt", "kind": "tt", "chip": "asic", "commit": None, "repo": None,
        "demoboard": "TT06+", "demoboard_version": "v1.2.1", "mcu": "RP2040",
        "sdk": "1.2.2", "serial": SERIAL, "usb_serial": SERIAL, "shuttle": "tt03p5",
        "usb": "1-1.4", "variant": "tt-asic"}],
}


def merge(boards, identity):
    doc = {"verdict": {"summary": {}}, "fpga": {"fpgas_verify": {"document": identity}}}
    tinytapeout.merge_tinytapeout(doc, {"usb": [], "repl": {}, "boards": boards,
                                        "summary": tinytapeout.tinytapeout_summary(boards)})
    return doc


def test_an_unread_board_is_named_from_the_identity_with_its_serial():
    doc = merge([CANDIDATE], IDENTITY)
    board, = doc["verdict"]["tinytapeout"]
    assert board["kind"] == "tinytapeout"
    assert board["shuttle"] == "tt03p5" and board["chip"] == "asic"
    assert board["mcu"] == "RP2040" and board["sdk"] == "1.2.2"
    assert board["demoboard_version"] == "v1.2.1"
    assert "fpgas-verify identity" in board["how"] and "no raw REPL prompt" in board["how"]
    assert doc["verdict"]["summary"]["tinytapeout"] == [{
        "usb_serial": SERIAL, "mcu": "RP2040", "shuttle": "tt03p5", "chip": "asic",
        "repo": None, "commit": None, "demoboard": "TT06+", "demoboard_version": "v1.2.1",
        "sdk": "1.2.2"}]


def test_another_serial_is_not_named():
    other = dict(CANDIDATE, usb_serial="0000000000000000")
    doc = merge([other], IDENTITY)
    assert doc["verdict"]["tinytapeout"] == [other]
    assert doc["verdict"]["summary"]["tinytapeout"] == []


def test_a_board_its_repl_named_is_left_as_read():
    read = {"kind": "tinytapeout", "usb_serial": SERIAL, "shuttle": "tt06", "how": "REPL"}
    assert merge([read], IDENTITY)["verdict"]["tinytapeout"] == [read]


def test_without_a_readable_identity_nothing_is_named():
    for identity in (None, {}, dict(IDENTITY, schema="other"), dict(IDENTITY, identity_version=2)):
        assert merge([CANDIDATE], identity)["verdict"]["tinytapeout"] == [CANDIDATE]
