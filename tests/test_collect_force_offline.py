"""rpi-hwid collect --force-offline HOST: a Cynthion's ECP5 TraceID, read
on the hosts named and no others, and said when it went wrong."""

from __future__ import annotations

import copy
import json
import subprocess

import pytest

from conftest import RAW
from rpi_hwid import cli, collect
from rpi_hwid.collect import probe_source

OFFLINE = "merge_fpga(_doc, collect_fpga(False, False, True))"


def test_the_script_asks_for_the_traceid_only_when_told():
    assert "collect_fpga(False, False)" in probe_source(fpga=True)
    assert "collect_fpga(False, False, True)" in probe_source(fpga=True, force_offline=True)
    # with --jtag and --flash too, all three are passed
    assert "collect_fpga(True, True, True)" in probe_source(
        fpga=True, jtag=True, flash=True, force_offline=True)


def reply(read="ok"):
    """A real document (rpi5-netv2's, which has a Cynthion) with the offline
    read as `read` says: "ok", None (never run), "stuck" (the analyzer did
    not come back), or an error message."""
    doc = copy.deepcopy(RAW["rpi5-netv2"])
    fpga = doc.setdefault("fpga", {})
    if read == "ok":
        fpga["cynthion_jtag"] = {"trace_id": "0x1b808604604e0e", "restored": True}
    elif read is None:
        fpga["cynthion_jtag"] = None
    elif read == "stuck":
        fpga["cynthion_jtag"] = {"trace_id": "0x1b808604604e0e", "restored": False}
    else:
        fpga["cynthion_jtag"] = {"error": read, "restored": True}
    return json.dumps(doc)


@pytest.fixture
def ssh(monkeypatch):
    """The script each host was sent, keyed by the user@host ssh was given,
    and the reply each host gives (by bare host; a good read by default)."""
    sent: dict[str, str] = {}
    replies: dict[str, str] = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        target = cmd[-2]
        sent[target] = input
        return subprocess.CompletedProcess(
            cmd, 0, stdout=replies.get(target.split("@", 1)[1], reply()), stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    return sent, replies


def offline_targets(sent):
    return sorted(t for t, script in sent.items() if OFFLINE in script)


def test_force_offline_goes_to_the_host_named_and_no_other(ssh, tmp_path):
    """It stops a Cynthion's capture for a few seconds and may drop power to
    its TARGET port, so it goes to the host named and to no other."""
    sent, _ = ssh
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--force-offline", "b", "a", "b"]) == 0
    assert offline_targets(sent) == ["pi@b"]
    assert "collect_fpga" not in sent["pi@a"]


def test_force_offline_with_fpga_everywhere_still_only_names_its_host(ssh, tmp_path):
    sent, _ = ssh
    cli.main(["collect", "--out", str(tmp_path), "--users", "pi", "--fpga",
              "--force-offline", "b", "a", "b"])
    assert offline_targets(sent) == ["pi@b"]
    assert "merge_fpga(_doc, collect_fpga(False, False))" in sent["pi@a"]


@pytest.mark.parametrize(("named", "host"), [("pi@b", "b"), ("b", "pi@b")])
def test_a_host_is_matched_with_or_without_its_user(ssh, tmp_path, named, host):
    sent, _ = ssh
    cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
              "--force-offline", named, "a", host])
    assert offline_targets(sent) == ["pi@b"]


def test_a_host_not_being_collected_is_an_error_before_anything_is_read(ssh, tmp_path,
                                                                         capsys):
    """A read that silently does nothing looks just like one that found
    nothing to read."""
    sent, _ = ssh
    assert cli.main(["collect", "--out", str(tmp_path), "--force-offline", "c", "a", "b"]) == 2
    assert "--force-offline" in capsys.readouterr().err
    assert sent == {}


@pytest.mark.parametrize(("read", "says"), [
    (None, "no Cynthion"),
    ("apollo did not answer", "apollo did not answer"),
    ("stuck", "rpi-hwid fpga --recover-cynthion"),
])
def test_a_traceid_read_that_went_wrong_is_said_and_fails_the_host(ssh, tmp_path, capsys,
                                                                     read, says):
    _, replies = ssh
    replies["b"] = reply(read)
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--force-offline", "b", "b"]) == 1
    assert says in capsys.readouterr().out
    # what was read is still written: it is evidence
    assert list(tmp_path.glob("*.json"))


def test_a_good_traceid_read_is_not_a_failure(ssh, tmp_path, capsys):
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--force-offline", "b", "b"]) == 0
    out = capsys.readouterr().out
    assert "FAILED" not in out
    assert "recover-cynthion" not in out
