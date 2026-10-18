"""rpi-hwid collect --read-tt-rom HOST: a Tiny Tapeout chip ROM the board's
SDK would not trust (a TT04's: address 129 does not give 0x00) is read by
the probe in the same REPL session, only on the hosts named and only where
the SDK reported the shuttle as unknown."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from rpi_hwid import cli, collect, tinytapeout
from rpi_hwid.collect import probe_source
from test_collect_force_offline import reply

TT04_TEXT = "shuttle=tt04\nrepo=TinyTapeout/tinytapeout-04\ncommit=a1b2c3d4\n"

UNKNOWN = {"machine": "Raspberry Pi Pico with RP2040", "sdk": "2.0.4", "demoboard": "TT04",
           "carrier_present": True, "rom": {"shuttle": "unknown", "repo": "", "commit": ""}}


def test_a_tt04_descriptor_becomes_the_rom():
    answer = dict(UNKNOWN)
    read = {"magic0": 0x78, "magic129": 0x56, "text": TT04_TEXT, "restored": "tt_um_factory_test"}
    tinytapeout.rom_from_read(answer, read)
    assert answer["rom"] == {"shuttle": "tt04", "repo": "TinyTapeout/tinytapeout-04",
                             "commit": "a1b2c3d4"}
    assert answer["rom_read_by_probe"] is True
    assert answer["rom_text"] == TT04_TEXT
    assert answer["rom_probe"] == read


@pytest.mark.parametrize("text", ["", "\x01\x02garbage", "repo=TinyTapeout/x\ncommit=1\n"])
def test_a_read_that_does_not_name_a_shuttle_leaves_the_rom_unknown(text):
    """Bytes that are not printable key=value text naming a shuttle are kept
    as evidence and nothing more."""
    answer = dict(UNKNOWN)
    tinytapeout.rom_from_read(answer, {"magic0": 0x78, "text": text})
    assert answer["rom"]["shuttle"] == "unknown"
    assert "rom_read_by_probe" not in answer
    assert answer["rom_probe"]["text"] == text


def repl(monkeypatch, first):
    """repl_on against a board whose REPL_SNIPPET answer is `first`; returns
    the snippets run, in order, and what repl_on gives."""
    ran = []

    def exec_(fd, code, timeout):
        ran.append(code)
        if code == tinytapeout.ROM_SNIPPET:
            return json.dumps({"magic0": 0x78, "magic129": 0x56, "text": TT04_TEXT}), ""
        return json.dumps(first), ""
    monkeypatch.setattr(tinytapeout, "raw_repl_exec", exec_)
    return ran


@pytest.mark.parametrize(("read_rom", "first", "rom_read"), [
    (True, UNKNOWN, True),
    (False, UNKNOWN, False),
    (True, dict(UNKNOWN, rom={"shuttle": "tt06", "repo": "r", "commit": "c"}), False),
])
def test_the_rom_is_read_only_when_asked_and_the_sdk_said_unknown(monkeypatch, read_rom, first,
                                                                  rom_read):
    ran = repl(monkeypatch, first)
    answer = tinytapeout.repl_on(os.open(os.devnull, os.O_RDWR), "/dev/ttyACM0", 5, read_rom)
    assert (tinytapeout.ROM_SNIPPET in ran) is rom_read
    assert ran[0] == tinytapeout.REPL_SNIPPET
    assert answer.get("rom_read_by_probe", False) is rom_read


def test_the_verdict_names_the_shuttle_and_says_who_read_it():
    answer = dict(UNKNOWN)
    tinytapeout.rom_from_read(answer, {"magic0": 0x78, "text": TT04_TEXT})
    usb = {"id": "2e8a:0005", "path": "1-1.4", "serial": "E66", "tty": "/dev/ttyACM0",
           "manufacturer": "MicroPython", "product": "Board in FS mode"}
    board, = tinytapeout.tinytapeout_verdict({"usb": [usb], "repl": {"1-1.4": answer}})
    assert board["shuttle"] == "tt04"
    assert "read by this probe" in board["how"]


@pytest.mark.parametrize(("kw", "call"), [
    ({}, "collect_tinytapeout()"),
    ({"read_tt_rom": True}, "collect_tinytapeout(read_rom=True)"),
    ({"read_tt_rom": True, "take_port": False},
     "collect_tinytapeout(take_port=False, read_rom=True)"),
])
def test_the_script_asks_for_the_rom_only_when_told(kw, call):
    assert f"merge_tinytapeout(_doc, {call})" in probe_source(tinytapeout=True, **kw)


def test_read_tt_rom_goes_to_the_host_named_and_implies_tinytapeout(monkeypatch, tmp_path):
    scripts: dict[str, str] = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        scripts[cmd[-2]] = input
        return subprocess.CompletedProcess(cmd, 0, stdout=reply(), stderr="")
    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--read-tt-rom", "b", "a", "b"]) == 0
    assert "collect_tinytapeout(read_rom=True)" in scripts["pi@b"]
    assert "collect_tinytapeout" not in scripts["pi@a"]
