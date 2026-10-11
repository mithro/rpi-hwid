"""The ESP32 GPS-node probe: what an esp32-to-gps firmware says about its receiver."""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys
import threading
import tokenize

import pytest

from rpi_hwid import esp32_gps

# What /dev/gps-max-m10s on rpiz-gps answered on 2026-10-08, the day the
# nodes were first flashed, without a reset (its uptime ran on between
# reads), trimmed: GpsStatus to its Receiver block.
M10_ANSWERS = (
    '00:02:54.977 RSL: RESULT = {"GpsConfig":{"Module":"auto","Baud":0,"Remembered":'
    '{"Module":"m10","Baud":38400},"Ntrip":1,"Caster":"10.1.10.1:2101","Mount":"ADDE_RTCM3",'
    '"User":"","Password":"","Period":10,"SatEntities":0,"Hass":1}}\r\n'
    '00:02:55.081 RSL: RESULT = {"GpsStatus":{"Time":null,"Fix":"No fix","FixType":0,'
    '"Receiver":{"Model":"u-blox M10","Firmware":"SPG 5.10","Hardware":"000A0000",'
    '"Protocol":"34.10","Baud":38400,"Antenna":null,"Jamming":null,"Noise":null,"QErr":null},'
    '"PPS":{"Present":false,"Count":0}}}\r\n'
    '00:02:55.173 RSL: STATUS2 = {"StatusFWR":{"Version":"15.6.0.2(gps)","BuildDateTime":'
    '"2026-10-08T14:28:51","Core":"3.3.12","SDK":"5.5.5","CpuFrequency":160,'
    '"Hardware":"ESP32-C3 v0.4","CR":"311/699"}}\r\n'
)
# /dev/gps-lc29h, the same day: a Quectel receiver has no protocol version.
LC29H_ANSWERS = (
    '02:23:32.273 RSL: RESULT = {"GpsConfig":{"Module":"auto","Baud":0,"Remembered":'
    '{"Module":"lc29h","Baud":115200},"Ntrip":1,"Caster":"10.1.10.1:2101","Mount":"ADDE_RTCM3",'
    '"User":"","Password":"","Period":10,"SatEntities":0,"Hass":1}}\r\n'
    '02:23:32.380 RSL: RESULT = {"GpsStatus":{"Time":"2080-01-06T01:21:50Z","Fix":"No fix",'
    '"Receiver":{"Model":"LC29H(AA)","Firmware":"LC29HAANR11A05S","Hardware":"AG3335M",'
    '"Protocol":null,"Baud":115200,"Antenna":null,"Jamming":null,"Noise":null,"QErr":null}}}\r\n'
)
# A node that has not found its receiver yet (GpsModule auto, nothing wired),
# as GpsConfig words it: the receiver it remembers is "none".
NONE_ANSWERS = (
    '00:00:09.100 RSL: RESULT = {"GpsConfig":{"Module":"auto","Baud":0,"Remembered":'
    '{"Module":"none","Baud":0},"Ntrip":1,"Caster":"10.1.10.1:2101","Mount":"",'
    '"User":"","Password":"","Period":10,"SatEntities":0,"Hass":1}}\r\n'
)


def test_the_answers_are_taken_from_whatever_prefix_the_console_puts_on_them():
    got = esp32_gps.parse_gps_answers(M10_ANSWERS)
    assert got["GpsConfig"]["Remembered"] == {"Module": "m10", "Baud": 38400}
    assert got["StatusFWR"]["Version"] == "15.6.0.2(gps)"
    mqtt = M10_ANSWERS.replace("RSL: RESULT", "MQT: stat/gps-max-m10s/RESULT")
    assert esp32_gps.parse_gps_answers(mqtt)["GpsStatus"]["Receiver"]["Model"] == "u-blox M10"


def test_the_receiver_found():
    assert esp32_gps.summarise_gps(M10_ANSWERS) == {
        "module": "m10", "model": "u-blox M10", "firmware": "SPG 5.10",
        "hardware": "000A0000", "protocol": "34.10", "baud": 38400,
        "tasmota": "15.6.0.2(gps)",
    }


def test_a_quectel_receiver_has_no_protocol_version():
    got = esp32_gps.summarise_gps(LC29H_ANSWERS)
    assert (got["module"], got["model"], got["hardware"]) == ("lc29h", "LC29H(AA)", "AG3335M")
    assert got["protocol"] is None
    assert got["tasmota"] is None   # Status 2 not answered: kept as not said


def test_no_receiver_yet_is_a_finding_not_an_error():
    got = esp32_gps.summarise_gps(NONE_ANSWERS)
    assert got["module"] is None
    assert got["model"] is None


def test_a_console_that_never_answered_is_an_error():
    with pytest.raises(esp32_gps.NoGpsAnswerError, match="esp32-to-gps"):
        esp32_gps.summarise_gps("00:00:00.001 HDW: ESP32-C3 v0.4\r\n")


def test_merge_hangs_the_receiver_on_its_esp32():
    doc = {"verdict": {"esp32": [{"tty": "/dev/ttyACM2", "mac": "44:1b:f6:2e:a9:a4"},
                                 {"tty": "/dev/ttyACM0", "mac": "44:1b:f6:2f:18:78"}]},
           "esp32": {"usb": [], "reads": [], "candidates": []}}
    reads = [{"port": "/dev/gps-max-m10s", "tty": "/dev/ttyACM2", "error": None,
              "gps": {"module": "m10"}, "answers": "..."}]
    esp32_gps.merge_gps(doc, reads)
    assert doc["verdict"]["esp32"][0]["gps"] == {"module": "m10"}
    assert "gps" not in doc["verdict"]["esp32"][1]
    assert doc["esp32"]["gps_reads"] == reads
    json.dumps(doc)


def test_a_failed_read_is_kept_on_its_esp32_with_the_reason():
    doc = {"verdict": {"esp32": [{"tty": "/dev/ttyACM2"}]}, "esp32": {}}
    esp32_gps.merge_gps(doc, [{"port": "p", "tty": "/dev/ttyACM2", "gps": None,
                               "error": "no answer"}])
    assert doc["verdict"]["esp32"][0]["gps_error"] == "no answer"
    assert doc["verdict"]["esp32"][0]["gps"] is None


def test_the_module_is_a_plain_python35_script():
    with pathlib.Path(esp32_gps.__file__).open("rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    assert not [t for t in tokens if tokenize.tok_name[t.type] == "FSTRING_START"]
    r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", esp32_gps.__file__],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


def test_its_helpers_do_not_shadow_the_radio_reads_in_the_one_probe_script():
    """The probe embeds esp32_radio and esp32_gps in one script, so a
    top-level name they share would let the later one replace the other's.
    (Every embedded module has a main(), which the probe never calls.)"""
    from rpi_hwid import esp32_radio

    def names(mod):
        tree = ast.parse(pathlib.Path(mod.__file__).read_text())
        return {n.name for n in tree.body if hasattr(n, "name")}

    assert names(esp32_radio) & names(esp32_gps) == {"main"}


# --- the read itself, against a firmware on the other end of a pty --------------


@pytest.fixture
def firmware_pty():
    """A pty whose far end plays the node: an answer to each command."""
    master, slave = os.openpty()
    port = os.ttyname(slave)
    lines = M10_ANSWERS.splitlines(keepends=True)
    answers = {"GpsConfig": lines[0], "GpsStatus": lines[1], "Status 2": lines[2]}
    stop = threading.Event()

    def node():
        buf = b""
        while not stop.is_set():
            try:
                buf += os.read(master, 1024)
            except OSError:
                return
            while b"\n" in buf:
                cmd, buf = buf.split(b"\n", 1)
                cmd = cmd.strip().decode()
                if cmd in answers:
                    os.write(master, answers[cmd].encode())

    t = threading.Thread(target=node, daemon=True)
    t.start()
    yield port
    stop.set()
    os.close(slave)
    os.close(master)


def test_the_read_asks_without_a_reset(firmware_pty, monkeypatch):
    from rpi_hwid import esp32_radio

    def no_reset(fd):
        raise AssertionError("the GPS read must not reset the node")

    monkeypatch.setattr(esp32_radio, "pulse_reset", no_reset)
    got = esp32_gps.read_gps(firmware_pty, answer_wait=1.0)
    assert got["error"] is None
    assert got["gps"]["module"] == "m10"
    assert got["gps"]["tasmota"] == "15.6.0.2(gps)"
    assert "GpsStatus" in got["answers"]


def test_a_port_that_cannot_be_opened_is_an_error_not_a_crash():
    got = esp32_gps.read_gps("/dev/no-such-port", answer_wait=0.1)
    assert got["gps"] is None
    assert "no-such-port" in got["error"]


def test_the_collector_embeds_the_module_and_reads_only_what_it_is_told():
    from rpi_hwid.collect import probe_source

    assert "collect_gps" not in probe_source(esp32=True)
    src = probe_source(esp32=True, esp32_gps=("/dev/gps-max-m10s",))
    assert "merge_gps(_doc, collect_gps(['/dev/gps-max-m10s']))" in src
    assert src.index("merge_esp32(_doc") < src.index("merge_gps(_doc")
    both = probe_source(esp32=True, esp32_radio=("/dev/r",), esp32_gps=("/dev/g",))
    compile(both, "probe", "exec")


def test_collect_hands_each_host_its_own_gps_ports(monkeypatch, tmp_path):
    from rpi_hwid import collect

    seen = {}

    def fake(host, users, jump, fpga, jtag, flash, tinytapeout=False, take_port=True,
             esp32=False, esp32_read=(), esp32_radio=(), force_offline=False, esp32_gps=()):
        seen[host] = (esp32, tuple(esp32_gps))
        return collect.Result(host, False, error="not really")

    monkeypatch.setattr(collect, "probe_host", fake)
    collect.collect(["a", "b"], tmp_path, workers=1, esp32_gps=("a=/dev/gps-gt-u7",))
    assert seen == {"a": (True, ("/dev/gps-gt-u7",)), "b": (False, ())}


def test_the_esp32_command_reads_the_receivers_it_is_told(monkeypatch, capsys):
    from rpi_hwid import esp32
    from rpi_hwid.cli import main as cli_main

    monkeypatch.setattr(esp32, "collect_esp32", lambda ports=(): {
        "usb": [], "reads": [], "candidates": [],
        "devices": [{"tty": "/dev/ttyACM2", "mac": "44:1b:f6:2e:a9:a4", "transport": "x",
                     "chip_description": None, "read_error": None}]})
    asked = []

    def fake(ports):
        asked.extend(ports)
        return [{"port": ports[0], "tty": "/dev/ttyACM2", "error": None, "answers": "",
                 "gps": esp32_gps.summarise_gps(M10_ANSWERS)}]

    monkeypatch.setattr(esp32_gps, "collect_gps", fake)
    assert cli_main(["esp32", "--gps", "/dev/gps-max-m10s"]) == 0
    assert asked == ["/dev/gps-max-m10s"]
    assert "u-blox M10 firmware SPG 5.10 hardware 000A0000" in capsys.readouterr().out
    assert cli_main(["esp32", "--json", "--gps", "/dev/gps-max-m10s"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["devices"][0]["gps"]["model"] == "u-blox M10"


def test_a_gps_read_for_a_host_not_being_collected_is_an_error(tmp_path):
    from rpi_hwid import collect

    with pytest.raises(ValueError, match="--esp32-gps nosuchhost=/dev/ttyACM0"):
        collect.collect(["rpiz-gps"], tmp_path, esp32_gps=("nosuchhost=/dev/ttyACM0",))
