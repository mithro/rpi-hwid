"""collect's ssh timeout: room for every disruptive read a host is asked for."""

from __future__ import annotations

import subprocess

import pytest

from rpi_hwid import cli, collect, esp32, esp32_radio

READ = esp32.ESPTOOL_CHECK_TIMEOUT + esp32.READ_TIMEOUT
RADIO = esp32_radio.LISTEN + len(esp32_radio.COMMANDS) * esp32_radio.ANSWER_WAIT


@pytest.fixture
def timeouts(monkeypatch):
    """The timeout ssh was run with for each host; every host then fails to
    log in, which is enough to see it."""
    got: dict[str, int] = {}

    def fake_run(cmd, input, capture_output, text, timeout):
        got[cmd[-2].split("@", 1)[1]] = timeout
        return subprocess.CompletedProcess(cmd, 255, stdout="", stderr="ssh: no route")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    return got


def test_the_timeout_allows_for_each_read_on_top_of_the_base():
    assert collect.probe_timeout() == collect.BASE_TIMEOUT == 180
    assert collect.probe_timeout(esp32_read=("a", "b", "c")) == 180 + 3 * READ
    assert collect.probe_timeout(100, ("a",), ("a", "b")) == 100 + READ + int(2 * RADIO)


def test_each_read_gives_up_inside_the_time_the_probe_allows_it():
    """The read's own timeouts fire first, so a slow chip costs only that
    read, which the document reports, never the host's whole record."""
    assert READ > esp32.READ_TIMEOUT
    assert collect.probe_timeout(esp32_read=("p",)) - collect.BASE_TIMEOUT >= READ


def test_three_esptool_reads_on_a_pi_zero_now_fit():
    """rpiz-gps, 2026-10-08: three --esp32-read ports and a few console
    questions took 191 s, over the old flat 180 s."""
    assert collect.probe_timeout(esp32_read=("a", "b", "c")) > 191


def test_collect_gives_each_host_the_time_its_own_reads_need(timeouts, tmp_path):
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--esp32-read", "a=/dev/ttyACM0", "--esp32-read", "a=/dev/ttyACM1",
                     "--esp32-radio", "b=/dev/ttyACM0", "a", "b", "c"]) != 0
    assert timeouts == {"a": 180 + 2 * READ, "b": 180 + int(RADIO), "c": 180}


def test_timeout_sets_the_base(timeouts, tmp_path):
    cli.main(["collect", "--out", str(tmp_path), "--users", "pi", "--timeout", "600",
              "--esp32-read", "a=/dev/ttyACM0", "a", "b"])
    assert timeouts == {"a": 600 + READ, "b": 600}


def test_a_probe_that_times_out_says_after_how_long(monkeypatch):
    def fake_run(cmd, input, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    r = collect.probe_host("h", users=("pi",), esp32=True, esp32_read=("/dev/ttyACM0",))
    assert r.error == f"timed out after {180 + READ} s"
