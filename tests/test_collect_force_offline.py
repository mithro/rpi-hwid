"""rpi-hwid collect --force-offline HOST: a Cynthion's ECP5 TraceID, read
on the hosts named and no others."""

from __future__ import annotations

import json
import subprocess

from rpi_hwid import cli, collect
from rpi_hwid.collect import probe_source


def test_the_script_asks_for_the_traceid_only_when_told():
    assert "collect_fpga(False, False)" in probe_source(fpga=True)
    assert "collect_fpga(False, False, True)" in probe_source(fpga=True, force_offline=True)
    # with --jtag and --flash too, all three are passed
    assert "collect_fpga(True, True, True)" in probe_source(
        fpga=True, jtag=True, flash=True, force_offline=True)


def test_collect_force_offline_goes_to_the_hosts_named_and_no_others(monkeypatch, tmp_path):
    """It stops a Cynthion's capture for a few seconds and may drop power to
    its TARGET port, so a fleet-wide --fpga must never do it."""
    sent: list[str] = []

    def fake_run(cmd, input, capture_output, text, timeout):
        sent.append(input)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"model": "m", "verdict": {"summary": {}}}), stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    # the fake reply is no full document: the scripts sent are what count
    cli.main(["collect", "--out", str(tmp_path), "--force-offline", "b", "a", "b"])
    assert len(sent) == 2
    offline = [s for s in sent if "collect_fpga(False, False, True)" in s]
    plain = [s for s in sent if "collect_fpga" not in s]
    assert len(offline) == 1
    assert len(plain) == 1


def test_force_offline_with_fpga_everywhere_still_only_names_its_host(monkeypatch, tmp_path):
    sent: list[str] = []

    def fake_run(cmd, input, capture_output, text, timeout):
        sent.append(input)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"model": "m", "verdict": {"summary": {}}}), stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    cli.main(["collect", "--out", str(tmp_path), "--fpga", "--force-offline", "b", "a", "b"])
    calls = sorted(line for s in sent for line in s.splitlines()
                   if line.startswith("merge_fpga(_doc, collect_fpga("))
    assert calls == ["merge_fpga(_doc, collect_fpga(False, False))",
                     "merge_fpga(_doc, collect_fpga(False, False, True))"]
