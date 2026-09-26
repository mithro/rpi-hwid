"""The ESP32 radio-node probe: what a cc1101-node firmware says about its radio."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import tokenize

import pytest

from rpi_hwid import esp32_radio

# What /dev/radio-cc1101-blue on rpi5-433mhz printed on 2026-09-26 after an
# RTS reset, trimmed: the ROM banner, Tasmota's first lines and the driver's
# bring-up line, which names the pins the CC1101 answered on.
BLUE_BOOT = (
    "ESP-ROM:esp32c3-api1-20210207\r\n"
    "rst:0x15 (USB_UART_CHIP_RESET),boot:0xd (SPI_FAST_FLASH_BOOT)\r\n"
    "00:00:00.001 HDW: ESP32-C3 v0.4 \r\n"
    "00:00:00.074 Project cc1101-node - esp32-433mhz-cc1101-blue Version "
    "15.5.0(cc1101-node)-3.3.8(2026-09-09T01:38:31)\r\n"
    "00:00:00.165 CC1: CC1101 PARTNUM 0x00 VERSION 0x14, SCK=3 MISO=7 MOSI=4 CS=1 "
    "GDO0=10 GDO2=6\r\n"
    "00:00:00.169 CC1: mode weather preset fineoffset-fsk\r\n"
    "00:00:04.542 HTP: Web server active on esp32-433mhz-cc1101-blue with IP address "
    "10.1.90.183\r\n"
)
# ...and its answers on the console, the same day.
BLUE_ANSWERS = (
    '09:14:06.195 MQT: stat/esp32-433mhz-cc1101-blue/RESULT = {"CcStatus":{"Present":1,'
    '"PARTNUM":"0x00","VERSION":"0x14","MARCSTATE":"0x0D","Mode":"weather",'
    '"Preset":"fineoffset-fsk","RSSI":-88,"Rx":1,"Decoded":1}}\r\n'
    '09:09:46.747 MQT: stat/esp32-433mhz-cc1101-blue/RESULT = {"Radio":{"Config":"cc1101",'
    '"Active":"cc1101"}}\r\n'
    '09:10:02.256 MQT: stat/esp32-433mhz-cc1101-blue/RESULT = {"SxStatus":{"Present":0,'
    '"VERSION":"0x00","Active":0,"Mode":"weather"}}\r\n'
)
# E8:3D:C1:8C:5C:88, the reference board with no radio fitted, running the
# firmware's safeboot image (esp32_devices.json's boot_after, 2026-09-26, and
# its console answers read without a reset the same day).
EMPTY_BOOT = (
    "00:00:00.008 Project cc1101-node - CC1101 node Version "
    "15.5.0(safeboot)-3.3.8(2026-09-06T09:38:26)\r\n"
    "00:00:00.108 CC1: no CC1101 on any board map (last PARTNUM 0x00 VERSION 0x00) - "
    "check wiring\r\n"
)
EMPTY_ANSWERS = (
    '02:03:57.566 RSL: RESULT = {"CcStatus":{"Present":0,"PARTNUM":"0x00","VERSION":"0x00",'
    '"MARCSTATE":"0x00","Mode":"remotes"}}\r\n'
    '02:03:59.120 RSL: RESULT = {"SxStatus":{"Present":0,"VERSION":"0x00","Active":0}}\r\n'
    '02:04:00.675 RSL: RESULT = {"Radio":{"Config":"auto","Active":"cc1101"}}\r\n'
)


def test_the_bring_up_line_names_the_chip_and_its_pins():
    got = esp32_radio.parse_bringup(BLUE_BOOT)
    assert got == [{
        "line": "CC1: CC1101 PARTNUM 0x00 VERSION 0x14, SCK=3 MISO=7 MOSI=4 CS=1 GDO0=10 "
                "GDO2=6",
        "chip": "CC1101", "present": True, "partnum": "0x00", "version": "0x14",
        "pins": {"SCK": 3, "MISO": 7, "MOSI": 4, "CS": 1, "GDO0": 10, "GDO2": 6},
    }]


# /dev/radio-sx1278-ra02's bring-up, 2026-09-26: the second line names the
# chip too, but it is the driver's configuration, not the chip answering.
SX_BOOT = (
    "00:00:00.079 Project cc1101-node - esp32-433mhz-sx1278 Version "
    "15.5.0(cc1101-node)-3.3.8(2026-09-09T01:38:31)\r\n"
    "00:00:00.138 CC1: SX1278 present, RegVersion 0x12, SCK=3 MISO=7 MOSI=4 NSS=1 RST=10 "
    "DIO0=6\r\n"
    "00:00:00.139 CC1: SX1278 FSK weather RX: 433.92 MHz 17.241 kbps sync 0x2DD4, fixed len "
    "30\r\n"
)
SX_ANSWERS = (
    '09:21:17.122 MQT: stat/esp32-433mhz-sx1278/RESULT = {"Radio":{"Config":"sx1278",'
    '"Active":"sx1278"}}\r\n'
    '09:21:17.222 MQT: stat/esp32-433mhz-sx1278/RESULT = {"CcStatus":{"Present":0,'
    '"PARTNUM":"0x00","VERSION":"0x00","MARCSTATE":"0x00","Mode":"weather"}}\r\n'
    '09:21:17.322 MQT: stat/esp32-433mhz-sx1278/RESULT = {"SxStatus":{"Present":1,'
    '"VERSION":"0x12","Active":1,"Mode":"weather","WeatherRx":1}}\r\n'
)


def test_an_sx1278_bring_up_line():
    (got,) = esp32_radio.parse_bringup(SX_BOOT)
    assert got["chip"] == "SX1278"
    assert got["present"] is True
    assert got["version"] == "0x12"
    assert got["pins"] == {"SCK": 3, "MISO": 7, "MOSI": 4, "NSS": 1, "RST": 10, "DIO0": 6}


def test_the_sx1278_found_keeps_its_pins():
    got = esp32_radio.summarise(SX_BOOT, SX_ANSWERS)
    assert (got["chip"], got["present"], got["version"], got["partnum"]) == (
        "SX1278", True, "0x12", None)
    assert got["pins"]["NSS"] == 1


def test_a_board_with_no_radio_says_so():
    (got,) = esp32_radio.parse_bringup(EMPTY_BOOT)
    assert got["chip"] == "CC1101"
    assert got["present"] is False
    assert got["pins"] == {}
    (got,) = esp32_radio.parse_bringup(
        "00:00:00.170 CC1: no SX1278 (RegVersion 0x00, expected 0x12) - check SPI/RST wiring\n")
    assert (got["chip"], got["present"], got["version"]) == ("SX1278", False, "0x00")


def test_the_answers_are_taken_from_whatever_prefix_the_console_puts_on_them():
    got = esp32_radio.parse_answers(BLUE_ANSWERS + EMPTY_ANSWERS[:0])
    assert got["CcStatus"]["VERSION"] == "0x14"
    assert got["Radio"] == {"Config": "cc1101", "Active": "cc1101"}
    got = esp32_radio.parse_answers(EMPTY_ANSWERS)
    assert got["Radio"]["Active"] == "cc1101"
    assert got["CcStatus"]["Present"] == 0


def test_the_firmware_is_kept_as_evidence():
    assert esp32_radio.firmware(BLUE_BOOT) == "15.5.0(cc1101-node)"
    assert esp32_radio.firmware(EMPTY_BOOT) == "15.5.0(safeboot)"
    assert esp32_radio.firmware("nothing here") is None


def test_the_radio_found():
    got = esp32_radio.summarise(BLUE_BOOT, BLUE_ANSWERS)
    assert got == {
        "chip": "CC1101", "present": True, "partnum": "0x00", "version": "0x14",
        "pins": {"SCK": 3, "MISO": 7, "MOSI": 4, "CS": 1, "GDO0": 10, "GDO2": 6},
        "firmware": "15.5.0(cc1101-node)",
    }


def test_no_radio_is_a_finding_not_an_error():
    got = esp32_radio.summarise(EMPTY_BOOT, EMPTY_ANSWERS)
    assert got["present"] is False
    assert got["chip"] is None
    assert got["firmware"] == "15.5.0(safeboot)"


def test_the_console_answer_wins_over_a_bring_up_line_it_contradicts():
    """The bring-up line is what the driver saw at boot, the answer what it
    holds now; were they ever to disagree, the answer is the current one and
    the bring-up line only lends its pins."""
    answers = BLUE_ANSWERS.replace('"VERSION":"0x14"', '"VERSION":"0x04"')
    got = esp32_radio.summarise(BLUE_BOOT, answers)
    assert got["version"] == "0x04"
    assert got["pins"]["SCK"] == 3


def test_a_console_that_never_answered_is_an_error():
    with pytest.raises(esp32_radio.NoAnswerError, match="cc1101-node"):
        esp32_radio.summarise("ESP-ROM:esp32c3-api1-20210207\r\n", "")


def test_merge_hangs_the_radio_on_its_esp32():
    doc = {"verdict": {"esp32": [{"tty": "/dev/ttyACM3", "mac": "e8:3d:c1:8c:3e:b8"},
                                 {"tty": "/dev/ttyACM2", "mac": "44:1b:f6:2e:b3:80"}]},
           "esp32": {"usb": [], "reads": [], "candidates": []}}
    reads = [{"port": "/dev/radio-cc1101-blue", "tty": "/dev/ttyACM3", "error": None,
              "radio": {"chip": "CC1101", "present": True}, "boot": "..."}]
    esp32_radio.merge_radios(doc, reads)
    assert doc["verdict"]["esp32"][0]["radio"] == {"chip": "CC1101", "present": True}
    assert "radio" not in doc["verdict"]["esp32"][1]
    assert doc["esp32"]["radio_reads"] == reads
    json.dumps(doc)


def test_a_failed_read_is_kept_on_its_esp32_with_the_reason():
    doc = {"verdict": {"esp32": [{"tty": "/dev/ttyACM3"}]}, "esp32": {}}
    esp32_radio.merge_radios(doc, [{"port": "p", "tty": "/dev/ttyACM3", "radio": None,
                                    "error": "no answer"}])
    assert doc["verdict"]["esp32"][0]["radio_error"] == "no answer"
    assert doc["verdict"]["esp32"][0]["radio"] is None


def test_the_module_is_a_plain_python35_script():
    with pathlib.Path(esp32_radio.__file__).open("rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    assert not [t for t in tokens if tokenize.tok_name[t.type] == "FSTRING_START"]
    r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", esp32_radio.__file__],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr


# --- the read itself, against a firmware on the other end of a pty --------------


@pytest.fixture
def firmware_pty(monkeypatch):
    """A pty whose far end plays the node: the boot text as soon as the
    reset is pulsed, then an answer to each command it is sent."""
    master, slave = os.openpty()
    port = os.ttyname(slave)
    pulses = []

    def fake_pulse(fd):
        pulses.append(fd)
        os.write(master, BLUE_BOOT.encode())

    monkeypatch.setattr(esp32_radio, "pulse_reset", fake_pulse)
    answers = {line.split('{"', 1)[1].split('"', 1)[0]: line
               for line in BLUE_ANSWERS.splitlines(keepends=True)}
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
    yield port, pulses
    stop.set()
    os.close(slave)
    os.close(master)


def test_the_read_resets_listens_and_asks(firmware_pty):
    port, pulses = firmware_pty
    t0 = time.monotonic()
    got = esp32_radio.read_radio(port, listen=0.5, answer_wait=1.0)
    assert len(pulses) == 1
    assert got["error"] is None
    assert got["radio"]["chip"] == "CC1101"
    assert got["radio"]["pins"]["GDO2"] == 6
    assert "CC1: CC1101 PARTNUM" in got["boot"]
    # every command was answered, so none waited out its whole time
    assert time.monotonic() - t0 < 0.5 + 3 * 1.0


def test_a_port_that_cannot_be_opened_is_an_error_not_a_crash():
    got = esp32_radio.read_radio("/dev/no-such-port", listen=0.1, answer_wait=0.1)
    assert got["radio"] is None
    assert "no-such-port" in got["error"]


def test_the_collector_embeds_the_module_and_reads_only_what_it_is_told():
    from rpi_hwid.collect import probe_source

    assert "collect_radios" not in probe_source(esp32=True)
    src = probe_source(esp32=True, esp32_radio=("/dev/radio-cc1101-blue",))
    assert "merge_radios(_doc, collect_radios(['/dev/radio-cc1101-blue']))" in src
    # after the esptool read, which must see the chip first
    assert src.index("merge_esp32(_doc") < src.index("merge_radios(_doc")
    compile(src, "probe", "exec")


def test_collect_hands_each_host_its_own_radio_ports(monkeypatch, tmp_path):
    from rpi_hwid import collect

    seen = {}

    def fake(host, users, jump, fpga, jtag, flash, tinytapeout=False, take_port=True,
             esp32=False, esp32_read=(), esp32_radio=()):
        seen[host] = (esp32, tuple(esp32_read), tuple(esp32_radio))
        return collect.Result(host, False, error="not really")

    monkeypatch.setattr(collect, "probe_host", fake)
    collect.collect(["a", "b"], tmp_path, workers=1, esp32_read=("a=/dev/ttyACM3",),
                    esp32_radio=("a=/dev/ttyACM3", "b=/dev/ttyACM2"))
    assert seen == {"a": (True, ("/dev/ttyACM3",), ("/dev/ttyACM3",)),
                    "b": (True, (), ("/dev/ttyACM2",))}


def test_a_radio_read_for_a_host_not_being_collected_is_an_error(tmp_path):
    from rpi_hwid import collect

    with pytest.raises(ValueError, match="--esp32-radio nosuchhost=/dev/ttyACM0"):
        collect.collect(["rpi5"], tmp_path, esp32_radio=("nosuchhost=/dev/ttyACM0",))
