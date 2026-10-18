"""rpi-hwid collect --no-user-bus HOST: the header's user bus (GPIO2/3) left
alone on the hosts named and no others, as the probe's own --no-user-bus
does. A Tiny Tapeout TT03.5 rig holds GPIO2/3 with its wiring service
(tt-pmod-wiring), and the probe stops rather than read past a line in use."""

from __future__ import annotations

import subprocess

import pytest

from rpi_hwid import cli, collect
from rpi_hwid.collect import probe_source
from test_collect_force_offline import reply


@pytest.fixture
def ssh(monkeypatch):
    """The remote command each host was given, keyed by its user@host, and
    the script it was sent."""
    commands: dict[str, str] = {}
    scripts: dict[str, str] = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        commands[cmd[-2]] = cmd[-1]
        scripts[cmd[-2]] = input
        return subprocess.CompletedProcess(cmd, 0, stdout=reply(), stderr="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    return commands, scripts


def test_no_user_bus_goes_to_the_host_named_and_no_other(ssh, tmp_path):
    commands, _ = ssh
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--no-user-bus", "b", "a", "b"]) == 0
    assert commands["pi@b"] == "python3 - --json --no-user-bus"
    assert commands["pi@a"] == "python3 - --json"


def test_with_modules_appended_the_script_still_honours_it(ssh, tmp_path):
    """With a module appended the script runs collect() itself, not the
    probe's main(), so it must read --no-user-bus the same way."""
    commands, scripts = ssh
    cli.main(["collect", "--out", str(tmp_path), "--users", "pi", "--tinytapeout",
              "--jtag", "b", "--no-user-bus", "b", "b"])
    assert commands["pi@b"] == "python3 - --json --no-user-bus"
    assert "collect(user_bus=False if '--no-user-bus' in sys.argv else None)" in scripts["pi@b"]
    assert "_doc = collect()" not in probe_source(tinytapeout=True)


def test_a_host_not_being_collected_is_an_error_before_anything_is_read(ssh, tmp_path,
                                                                         capsys):
    commands, _ = ssh
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--no-user-bus", "c", "a", "b"]) == 2
    assert commands == {}
    assert "--no-user-bus: c is not one of the hosts being collected" in capsys.readouterr().err
