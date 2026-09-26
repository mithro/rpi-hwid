"""The ESP32 probe module: the USB tree from sysfs, and the esptool read."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tokenize

import pytest

from rpi_hwid import esp32

# rpi5-433mhz's USB tree as sysfs showed it on 2026-09-26 (read with cat,
# nothing opened): three ESP32-C3 SuperMinis on their own USB-Serial-JTAG,
# a LilyGO board behind a CH9102, an Ebyte radio behind a CH340 and a
# Realtek Wi-Fi adapter with no tty at all.
RPI5_433MHZ = {
    "3-1.2": {"idVendor": "303a", "idProduct": "1001", "bcdDevice": "0101", "speed": "12",
              "manufacturer": "Espressif", "product": "USB JTAG/serial debug unit",
              "serial": "E8:3D:C1:8C:5C:88", "tty": ("acm", "ttyACM0")},
    "3-1.3": {"idVendor": "303a", "idProduct": "1001", "bcdDevice": "0101", "speed": "12",
              "manufacturer": "Espressif", "product": "USB JTAG/serial debug unit",
              "serial": "44:1B:F6:2E:B3:80", "tty": ("acm", "ttyACM2")},
    "3-1.4": {"idVendor": "303a", "idProduct": "1001", "bcdDevice": "0101", "speed": "12",
              "manufacturer": "Espressif", "product": "USB JTAG/serial debug unit",
              "serial": "E8:3D:C1:8C:3E:B8", "tty": ("acm", "ttyACM3")},
    "1-1.3": {"idVendor": "1a86", "idProduct": "55d4", "bcdDevice": "0444", "speed": "12",
              "product": "USB Single Serial", "serial": "591B031339",
              "tty": ("acm", "ttyACM1")},
    "1-1.4": {"idVendor": "1a86", "idProduct": "7523", "bcdDevice": "8233", "speed": "12",
              "product": "USB Serial", "tty": ("usb-serial", "ttyUSB0")},
    "1-2": {"idVendor": "0bda", "idProduct": "c811", "bcdDevice": "0200", "speed": "480",
            "manufacturer": "Realtek", "product": "802.11ac NIC", "serial": "123456"},
}
LINKS = {
    "serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_E8:3D:C1:8C:5C:88-if00": "ttyACM0",
    "serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_44:1B:F6:2E:B3:80-if00": "ttyACM2",
    "serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_E8:3D:C1:8C:3E:B8-if00": "ttyACM3",
    "radio-esp32-E8:3D:C1:8C:5C:88": "ttyACM0",
    "radio-cc1101-blue": "ttyACM3",
    "radio-sx1278-ra02": "ttyACM2",
    "radio-lilygo": "ttyACM1",
    "radio-e22": "ttyUSB0",
}


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    base = tmp_path / "sys/bus/usb/devices"
    base.mkdir(parents=True)
    for name, attrs in RPI5_433MHZ.items():
        d = base / name
        d.mkdir()
        for k, v in attrs.items():
            if k != "tty":
                (d / k).write_text(v + "\n")
        if "tty" in attrs:
            how, tty = attrs["tty"]
            iface = d / f"{name}:1.0"
            (iface / "tty" / tty if how == "acm" else iface / tty).mkdir(parents=True)
    dev = tmp_path / "dev"
    (dev / "serial/by-id").mkdir(parents=True)
    for link, tty in LINKS.items():
        (dev / tty).touch()
        target = ("../../" if link.startswith("serial/") else "") + tty
        (dev / link).symlink_to(target)
    monkeypatch.setattr(esp32, "ROOT", str(tmp_path))
    return tmp_path


def test_the_usb_tree_finds_the_espressif_ports_and_the_bridges(sysfs):
    e = esp32.collect_esp32()
    assert [(d["tty"], d["mac"]) for d in e["devices"]] == [
        ("/dev/ttyACM0", "e8:3d:c1:8c:5c:88"),
        ("/dev/ttyACM2", "44:1b:f6:2e:b3:80"),
        ("/dev/ttyACM3", "e8:3d:c1:8c:3e:b8"),
    ]
    assert all(d["transport"] == "usb-serial-jtag" for d in e["devices"])
    assert all(d["mac_source"] == "usb-serial-jtag serial" for d in e["devices"])
    # a bridge is only a candidate: the chip behind it is invisible
    assert [(d["bridge"], d["usb_serial"], d["tty"]) for d in e["candidates"]] == [
        ("CH9102", "591B031339", "/dev/ttyACM1"), ("CH340", None, "/dev/ttyUSB0")]
    assert all(d["mac"] is None for d in e["candidates"])
    assert e["reads"] == []


def test_the_udev_names_travel_with_the_port(sysfs):
    (blue,) = [d for d in esp32.collect_esp32()["devices"] if d["tty"] == "/dev/ttyACM3"]
    assert "/dev/radio-cc1101-blue" in blue["tty_links"]


def test_nothing_is_opened_without_a_read_being_asked_for(sysfs, monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("a serial port was opened")

    monkeypatch.setattr(esp32, "run_read", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    esp32.collect_esp32()


# The shape the read returns. Synthetic values (the uid especially): the
# real reads are the ESP32 fixtures in conftest.
READ = {
    "port": "/dev/radio-cc1101-blue", "esptool": "4.7.0",
    "chip_description": "ESP32-C3 (QFN32) (revision v0.4)",
    "features": ["WiFi", "BLE", "Embedded Flash 4MB (XMC)"], "crystal_mhz": 40,
    "mac": "e8:3d:c1:8c:3e:b8", "flash_jedec": "0x204016",
    "flash_uid": "0123456789abcdef",
    "efuse": {"MAC": "e8:3d:c1:8c:3e:b8 (OK)", "WAFER_VERSION_MINOR_LO": 4},
    "boot_after": "ESP-ROM:esp32c3-api1-20210207\nrst:0x15 (USB_UART_CHIP_RESET)\n",
}


def test_only_the_named_ports_are_read(sysfs, monkeypatch):
    asked = []

    def fake(port, timeout=90):
        asked.append(port)
        return dict(READ, port=port), None, "RESULT ..."

    monkeypatch.setattr(esp32, "run_read", fake)
    e = esp32.collect_esp32(["/dev/radio-cc1101-blue"])
    assert asked == ["/dev/radio-cc1101-blue"]
    (blue,) = [d for d in e["devices"] if d["tty"] == "/dev/ttyACM3"]
    assert blue["chip"] == "ESP32-C3"
    assert blue["package"] == "QFN32"
    assert blue["revision"] == "v0.4"
    assert blue["crystal_mhz"] == 40
    assert blue["flash_jedec"] == "0x204016"
    assert blue["mac_source"] == "usb-serial-jtag serial; efuse"
    assert "mac_conflict" not in blue
    others = [d for d in e["devices"] if d["tty"] != "/dev/ttyACM3"]
    assert all(d["chip"] is None for d in others)


def test_a_mac_that_disagrees_is_recorded_not_resolved(sysfs, monkeypatch):
    monkeypatch.setattr(esp32, "run_read", lambda p, timeout=90: (
        dict(READ, mac="e8:3d:c1:8c:3e:b9"), None, ""))
    (blue,) = [d for d in esp32.collect_esp32(["/dev/ttyACM3"])["devices"]
               if d["tty"] == "/dev/ttyACM3"]
    assert blue["mac_conflict"] == {"usb": "e8:3d:c1:8c:3e:b8", "efuse": "e8:3d:c1:8c:3e:b9"}


def test_a_bridge_whose_chip_answers_becomes_a_device(sysfs, monkeypatch):
    monkeypatch.setattr(esp32, "run_read", lambda p, timeout=90: (
        dict(READ, chip_description="ESP32-D0WD-V3 (revision v3.1)",
             mac="a4:f0:0f:74:2e:68"), None, ""))
    e = esp32.collect_esp32(["/dev/ttyUSB0"])
    (d,) = [d for d in e["devices"] if d["tty"] == "/dev/ttyUSB0"]
    assert d["mac"] == "a4:f0:0f:74:2e:68"
    assert d["mac_source"] == "efuse"
    assert d["bridge"] == "CH340"
    assert d["chip"] == "ESP32-D0WD-V3"
    assert [c["tty"] for c in e["candidates"]] == ["/dev/ttyACM1"]


def test_a_failed_read_is_kept_with_its_reason(sysfs, monkeypatch):
    monkeypatch.setattr(esp32, "run_read", lambda p, timeout=90: (
        None, "esptool read failed: Failed to connect to Espressif device", "..."))
    e = esp32.collect_esp32(["/dev/ttyACM0"])
    (d,) = [d for d in e["devices"] if d["tty"] == "/dev/ttyACM0"]
    assert d["read_error"].startswith("esptool read failed")
    assert e["reads"][0]["ok"] is False


@pytest.mark.parametrize(("text", "want"), [
    ("ESP32-C3 (QFN32) (revision v0.4)", ("ESP32-C3", "QFN32", "v0.4")),
    ("ESP32-D0WD-V3 (revision v3.1)", ("ESP32-D0WD-V3", None, "v3.1")),
    ("ESP32-S3 (QFN56) (revision v0.2)", ("ESP32-S3", "QFN56", "v0.2")),
    ("", (None, None, None)),
])
def test_chip_facts(text, want):
    assert esp32.chip_facts(text) == want


def test_parse_read_takes_the_result_line():
    assert esp32.parse_read("banner\nRESULT {\"a\": 1}\n") == {"a": 1}
    assert esp32.parse_read("A fatal error occurred: Failed to connect") is None


def test_merge_puts_devices_in_the_verdict():
    doc = {"verdict": {"summary": {}}}
    esp32.merge_esp32(doc, {"usb": [], "reads": [], "devices": [{"mac": "m"}],
                            "candidates": []})
    assert doc["verdict"]["esp32"] == [{"mac": "m"}]
    assert doc["esp32"]["reads"] == []
    json.dumps(doc)


def test_the_module_is_a_plain_python35_script():
    with pathlib.Path(esp32.__file__).open("rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    assert not [t for t in tokens if tokenize.tok_name[t.type] == "FSTRING_START"]
    r = subprocess.run([sys.executable, "-W", "error", "-m", "py_compile", esp32.__file__],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    # the read script is run by the host's own python3: it must compile too
    compile(esp32.READ_SCRIPT, "READ_SCRIPT", "exec")


def test_the_collector_embeds_the_module_and_reads_only_what_it_is_told():
    from rpi_hwid.collect import probe_source

    assert "collect_esp32" not in probe_source()
    src = probe_source(esp32=True)
    assert src.startswith("RPI_HWID_EMBEDDED = True")
    assert "merge_esp32(_doc, collect_esp32([]))" in src
    src = probe_source(esp32=True, esp32_read=("/dev/radio-cc1101-blue",))
    assert "merge_esp32(_doc, collect_esp32(['/dev/radio-cc1101-blue']))" in src
    compile(src, "probe", "exec")


def test_collect_hands_each_host_its_own_ports(monkeypatch, tmp_path):
    from rpi_hwid import collect

    seen = {}

    def fake(host, users, jump, fpga, jtag, flash, tinytapeout=False, take_port=True,
             esp32=False, esp32_read=()):
        seen[host] = (esp32, tuple(esp32_read))
        return collect.Result(host, False, error="not really")

    monkeypatch.setattr(collect, "probe_host", fake)
    collect.collect(["a", "b", "c"], tmp_path, esp32_read=("a=/dev/ttyACM0", "a=/dev/ttyACM3",
                                                            "b=/dev/ttyUSB1"), workers=1)
    assert seen == {"a": (True, ("/dev/ttyACM0", "/dev/ttyACM3")),
                    "b": (True, ("/dev/ttyUSB1",)), "c": (False, ())}


def test_the_esp32_command(sysfs, capsys):
    from rpi_hwid.cli import main as cli_main

    assert cli_main(["esp32"]) == 0
    out = capsys.readouterr().out
    assert "e8:3d:c1:8c:5c:88" in out
    assert "would reset it" in out
    assert cli_main(["esp32", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["devices"]) == 3
