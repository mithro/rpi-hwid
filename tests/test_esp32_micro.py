"""ESP32 micro labels: records from real documents, and the refusals."""

from __future__ import annotations

import copy
import dataclasses
import json
import pathlib

import pytest

import conftest
from rpi_hwid import esp32_micro, labels, micro
from rpi_hwid.micro import Icon, MicroRow
from rpi_hwid.model import ProbeDocument

# verdict.esp32 as `rpi-hwid collect --esp32-read` wrote it on 2026-09-26,
# with the boot output trimmed: three ESP32-C3 SuperMinis on rpi5-433mhz's
# USB-Serial-JTAG (esptool 4.7.0), and on rpi4-esp an ESP32-CAM behind a
# CH340 and a first-generation devkit behind a CP2102 (esptool 5.2.0).
REAL = json.loads((pathlib.Path(__file__).parent / "esp32_devices.json").read_text())


def _real(host, mac):
    (d,) = [d for d in REAL[host] if d["mac"] == mac]
    return copy.deepcopy(d)


C3 = _real("rpi5-433mhz", "e8:3d:c1:8c:3e:b8")          # radio-cc1101-blue
CAM = _real("rpi4-esp", "a4:f0:0f:76:46:64")             # ESP32-CAM
DEVKIT = _real("rpi4-esp", "24:0a:c4:11:44:e8")
# The same C3 as the USB tree alone shows it, before any read.
UNREAD = dict(C3, mac_source="usb-serial-jtag serial", chip=None, chip_description=None,
              revision=None, package=None, features=[], crystal_mhz=None,
              flash_jedec=None, flash_uid=None, efuse={}, read=None, read_errors={})


def _docs(*devices, host="rpi5-433mhz"):
    raw = copy.deepcopy(conftest.RAW["rpi5-netv2"])
    raw["verdict"]["esp32"] = [copy.deepcopy(d) for d in devices]
    return {host: ProbeDocument.from_dict(host, raw)}


def test_every_real_device_gets_a_label():
    docs = {}
    for host, devs in REAL.items():
        docs.update(_docs(*devs, host=host))
    got = [(m.host, m.title, m.ident) for m in esp32_micro.micro_labels(docs)]
    assert got == [
        ("rpi4-esp", "ESP32-D0WD-V3", "a4:f0:0f:76:46:64"),
        ("rpi4-esp", "ESP32-D0WDQ6", "24:0a:c4:11:44:e8"),
        ("rpi5-433mhz", "ESP32-C3", "e8:3d:c1:8c:5c:88"),
        ("rpi5-433mhz", "ESP32-C3", "44:1b:f6:2e:b3:80"),
        ("rpi5-433mhz", "ESP32-C3", "e8:3d:c1:8c:3e:b8"),
    ]


def test_a_c3_carries_its_chip_unique_id_over_two_rows():
    """The C3's in-package flash answers Read Unique ID with zeroes on all
    three boards, so the chip's own 128-bit OPTIONAL_UNIQUE_ID is what
    identifies it past the MAC."""
    (lab,) = esp32_micro.micro_labels(_docs(C3))
    assert lab.title == "ESP32-C3"
    assert lab.mark == "espressif.svg"
    assert lab.icons == (Icon("wifi"), Icon("chip", "C3"))
    assert lab.subtitle == "v0.4 · QFN32 · 4 MiB XMC"
    assert lab.ident_caption == "Wi-Fi MAC"
    assert lab.ident == "e8:3d:c1:8c:3e:b8"
    assert lab.rows == (MicroRow("BT", "e8:3d:c1:8c:3e:ba", mono=True),
                        MicroRow("chip", "53b9b91842e41b19", mono=True),
                        MicroRow("", "ee00321402a8b49b", mono=True))


def test_an_original_esp32_carries_its_flash_and_the_flash_uid():
    (cam,) = esp32_micro.micro_labels(_docs(CAM, host="rpi4-esp"))
    assert cam.title == "ESP32-D0WD-V3"
    assert cam.icons == (Icon("wifi"), Icon("chip", "32"))
    assert cam.subtitle == "v3.1 · 40 MHz xtal"
    assert cam.rows == (MicroRow("BT", "a4:f0:0f:76:46:66", mono=True),
                        MicroRow("flash", "Boya 0x684016 · 4 MiB"),
                        MicroRow("uid", "343738393844fa77", mono=True))
    (dev,) = esp32_micro.micro_labels(_docs(DEVKIT, host="rpi4-esp"))
    assert dev.rows[1:] == (MicroRow("flash", "GD25Q32x · 4 MiB"),
                            MicroRow("uid", "3130343531118566", mono=True))


def test_an_esp32_found_only_on_usb_is_fatal_and_says_how_to_read_it():
    with pytest.raises(esp32_micro.Esp32NotReadError) as e:
        esp32_micro.micro_labels(_docs(UNREAD))
    msg = str(e.value)
    assert "rpi5-433mhz" in msg
    assert "e8:3d:c1:8c:3e:b8" in msg
    assert "`rpi-hwid esp32 --read /dev/radio-cc1101-blue` on that host" in msg
    assert ("`rpi-hwid collect --esp32-read rpi5-433mhz=/dev/radio-cc1101-blue "
            "rpi5-433mhz`") in msg
    assert "resets the chip" in msg
    assert isinstance(e.value, labels.IdentifierNotReadError)


def test_a_failed_read_says_why():
    with pytest.raises(esp32_micro.Esp32NotReadError, match="Failed to connect"):
        esp32_micro.micro_labels(_docs(dict(UNREAD, read_error="FatalError: Failed to connect")))


def test_a_c3_whose_efuse_was_not_read_is_fatal():
    """Every C3 has an OPTIONAL_UNIQUE_ID; a missing one is a failed read."""
    d = dict(C3, efuse={}, read_errors={"efuse": "FatalError: timed out"})
    with pytest.raises(esp32_micro.Esp32NotReadError, match="timed out"):
        esp32_micro.micro_labels(_docs(d))


@pytest.mark.parametrize("uid", ["ffffffffffffffff", "0000000000000000", "", None])
def test_a_blank_flash_uid_is_left_off_not_printed(uid):
    (lab,) = esp32_micro.micro_labels(_docs(dict(DEVKIT, flash_uid=uid), host="rpi4-esp"))
    assert [r.caption for r in lab.rows] == ["BT", "flash"]


def test_the_bt_mac_adds_to_the_last_octet_only():
    assert esp32_micro.derived_mac("e8:3d:c1:8c:3e:fe", 2) == "e8:3d:c1:8c:3e:00"
    assert esp32_micro.derived_mac("44:1b:f6:2e:b3:80", 2) == "44:1b:f6:2e:b3:82"


@pytest.mark.parametrize(("chip", "family"), [
    ("ESP32-C3", "ESP32-C3"), ("ESP32-S3", "ESP32-S3"), ("ESP32-D0WD-V3", "ESP32"),
    ("ESP32-D0WDQ6", "ESP32"), ("ESP32-H2", "ESP32-H2"), ("ESP32-PICO-D4", "ESP32"),
])
def test_the_family_of_a_chip(chip, family):
    assert esp32_micro.family(chip) == family


def test_no_bt_row_for_a_chip_the_table_does_not_cover():
    d = dict(C3, chip="ESP32-S2", chip_description="ESP32-S2 (revision v0.0)",
             features=["WiFi", "Embedded Flash 4MB"])
    (lab,) = esp32_micro.micro_labels(_docs(d))
    assert [r.caption for r in lab.rows] == ["chip", ""]
    assert lab.icons == (Icon("wifi"), Icon("chip", "S2"))


def test_the_label_can_be_extended_by_a_caller():
    """What the ESP32 + 433 MHz labels will do: take the plain label apart."""
    host, dev = next(esp32_micro.devices(_docs(C3)))
    base = esp32_micro.esp32_label(host, dev)
    radio = dataclasses.replace(base, icons=(Icon("antenna", "433"), *base.icons),
                                extra=lambda cell, box: None)
    assert radio.ident == base.ident
    assert radio.icons[0] == Icon("antenna", "433")


def test_the_esp32_kind_is_found_and_printed(tmp_path):
    assert "esp32" in micro.kinds()
    docs = _docs(*REAL["rpi5-433mhz"])
    rows = list(labels.all_labels(docs, {"esp32"}))
    assert [r[1] for r in rows] == ["micro"]
    assert "ESP32-C3 44:1b:f6:2e:b3:80" in rows[0][2]
    n, stickers, _ = micro.render_micro(esp32_micro.micro_labels(docs), tmp_path / "e.pdf")
    assert (n, stickers) == (3, 1)
