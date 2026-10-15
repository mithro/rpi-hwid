"""collect's ssh timeout: room for every disruptive read a host is asked for."""

from __future__ import annotations

import math
import subprocess

import pytest

from rpi_hwid import cli, collect, esp32, esp32_gps, esp32_radio

READ = esp32.ESPTOOL_CHECK_TIMEOUT + esp32.READ_TIMEOUT
RADIO = (esp32_radio.SETTLE + esp32_radio.RESET_PULSE + esp32_radio.LISTEN
         + len(esp32_radio.COMMANDS) * esp32_radio.ANSWER_WAIT)
GPS = esp32_gps.GPS_SETTLE + len(esp32_gps.GPS_COMMANDS) * esp32_gps.GPS_ANSWER_WAIT


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
    assert collect.probe_timeout(100, ("a",), ("a", "b")) == 100 + READ + math.ceil(2 * RADIO)


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
    assert timeouts == {"a": 180 + 2 * READ, "b": 180 + math.ceil(RADIO), "c": 180}


def test_a_gps_node_is_allowed_its_questions_too(timeouts, tmp_path):
    """--esp32-gps asks without a reset, but still waits for each answer."""
    assert collect.probe_timeout(esp32_gps=("a", "b")) == 180 + math.ceil(2 * GPS)
    assert cli.main(["collect", "--out", str(tmp_path), "--users", "pi",
                     "--esp32-read", "a=/dev/ttyACM0", "--esp32-gps", "a=/dev/ttyACM0",
                     "--esp32-gps", "b=/dev/ttyACM0", "a", "b", "c"]) != 0
    assert timeouts == {"a": 180 + READ + math.ceil(GPS), "b": 180 + math.ceil(GPS), "c": 180}


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


# The tests above rebuild the allowance from the probes' constants, so they
# cannot see a wait the constants leave out. These run each read against a
# port that never answers and add up what it actually waited.

def allowed(**reads):
    return collect.probe_timeout(**reads) - collect.BASE_TIMEOUT


@pytest.fixture
def waited(monkeypatch):
    """Every second a console read spends waiting, on a silent port."""
    spent: list[float] = []

    def silent(fd, secs, until=None):
        spent.append(secs)
        return ""

    for mod, opener, drain in ((esp32_radio, "open_port", "drain"),
                               (esp32_gps, "gps_open_port", "gps_drain")):
        monkeypatch.setattr(mod, opener, lambda port: 99)
        monkeypatch.setattr(mod, drain, silent)
    monkeypatch.setattr(esp32_radio, "_modem", lambda fd, bit, on: None)
    monkeypatch.setattr(esp32_radio.time, "sleep", spent.append)
    monkeypatch.setattr(esp32_radio.os, "write", lambda fd, data: len(data))
    monkeypatch.setattr(esp32_radio.os, "close", lambda fd: None)
    return spent


def test_a_silent_radio_node_is_given_up_on_inside_its_allowance(waited):
    assert esp32_radio.read_radio("/dev/ttyACM0")["radio"] is None
    assert 0 < sum(waited) <= allowed(esp32_radio=("p",))


def test_a_silent_gps_node_is_given_up_on_inside_its_allowance(waited):
    assert esp32_gps.read_gps("/dev/ttyACM0")["gps"] is None
    assert 0 < sum(waited) <= allowed(esp32_gps=("p",))


def test_the_search_for_esptool_and_the_read_fit_one_ports_allowance(monkeypatch):
    """Every interpreter hangs, then (in a second run) the read does: the
    search shares one deadline however many virtualenvs there are, and is
    not made again for the next port."""
    clock = [0.0]
    monkeypatch.setattr(esp32.time, "time", lambda: clock[0])
    monkeypatch.setattr(esp32, "esptool_pythons", lambda: ["py-a", "py-b", "py-c"])
    monkeypatch.setattr(esp32, "ESPTOOL_FOUND", [])

    def hang(cmd, stdout, stderr, timeout):
        clock[0] += timeout
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(esp32.subprocess, "run", hang)
    assert esp32.run_read("/dev/ttyACM0")[0] is None
    assert clock[0] <= esp32.ESPTOOL_CHECK_TIMEOUT

    clock[0] = 0.0
    monkeypatch.setattr(esp32, "ESPTOOL_FOUND", [])
    ran = []

    def slow_import_then_hung_read(cmd, stdout, stderr, timeout):
        ran.append(cmd[0])
        if cmd[1] == "-c" and cmd[0] != "py-c":
            clock[0] += 25
            return subprocess.CompletedProcess(cmd, 1, stdout=b"no esptool")
        if cmd[1] == "-c":
            clock[0] += 5
            return subprocess.CompletedProcess(cmd, 0, stdout=b"")
        clock[0] += timeout
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(esp32.subprocess, "run", slow_import_then_hung_read)
    assert esp32.run_read("/dev/ttyACM0")[1].startswith("timed out")
    assert clock[0] <= allowed(esp32_read=("p",))
    first = clock[0]
    assert esp32.run_read("/dev/ttyACM1")[1].startswith("timed out")
    assert clock[0] - first == esp32.READ_TIMEOUT     # the interpreter was remembered
    assert ran == ["py-a", "py-b", "py-c", "py-c", "py-c"]


@pytest.mark.parametrize("bad", ["0", "-5", "soon"])
def test_a_timeout_that_is_no_time_at_all_is_refused(bad, tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["collect", "--out", str(tmp_path), "--timeout", bad, "a"])
    assert "--timeout" in capsys.readouterr().err
