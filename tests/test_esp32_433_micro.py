"""ESP32 + 433 MHz radio micro labels: the plain ESP32 label and the radio."""

from __future__ import annotations

import copy
import glob
import json
import pathlib
import shutil
import subprocess

import pytest

import conftest
from rpi_hwid import esp32_433_micro, esp32_micro, labels, micro
from rpi_hwid.micro import Icon
from rpi_hwid.model import ProbeDocument

# verdict.esp32 as `rpi-hwid collect --esp32-read ... --esp32-radio ...` wrote
# it for rpi5-433mhz on 2026-09-26 (boot output trimmed): the SX1278 node,
# the blue CC1101 node, and the reference board with no radio fitted.
REAL = json.loads((pathlib.Path(__file__).parent / "esp32_433_devices.json").read_text())


def _real(mac):
    (d,) = [d for d in REAL["rpi5-433mhz"] if d["mac"] == mac]
    return copy.deepcopy(d)


SX = _real("44:1b:f6:2e:b3:80")
BLUE = _real("e8:3d:c1:8c:3e:b8")
EMPTY = _real("e8:3d:c1:8c:5c:88")


def _docs(*devices, host="rpi5-433mhz"):
    raw = copy.deepcopy(conftest.RAW["rpi5-netv2"])
    raw["verdict"]["esp32"] = [copy.deepcopy(d) for d in devices]
    return {host: ProbeDocument.from_dict(host, raw)}


def _one(dev):
    (m,) = esp32_433_micro.micro_labels(_docs(dev))
    return m


def test_the_nodes_with_a_radio_get_a_label_and_the_empty_board_does_not():
    got = [(m.host, m.title, m.ident) for m in esp32_433_micro.micro_labels(_docs(*REAL[
        "rpi5-433mhz"]))]
    assert got == [("rpi5-433mhz", "ESP32-C3", "44:1b:f6:2e:b3:80"),
                   ("rpi5-433mhz", "ESP32-C3", "e8:3d:c1:8c:3e:b8")]


def test_the_board_is_told_by_the_pins_its_chip_answered_on():
    b = esp32_433_micro.board_for("h", BLUE["mac"], BLUE["radio"])
    assert (b.name, b.chip, b.band) == ("E07-M1101D", "CC1101", "433")
    b = esp32_433_micro.board_for("h", SX["mac"], SX["radio"])
    assert (b.name, b.chip, b.maker) == ("Ra-02", "SX1278", "Ai-Thinker")
    # the green D-Sun board, as its own boot log printed its map
    green = dict(BLUE["radio"], pins={"SCK": 1, "MISO": 3, "MOSI": 10, "CS": 6, "GDO0": 7,
                                      "GDO2": 4})
    assert esp32_433_micro.board_for("h", "m", green).name == "D-Sun"


def test_a_pin_map_no_board_has_is_fatal_and_says_what_it_saw():
    odd = dict(BLUE["radio"], pins={"SCK": 4, "MISO": 5, "MOSI": 6, "CS": 7})
    with pytest.raises(esp32_433_micro.UnknownRadioBoardError,
                       match=r"h: .*CC1101.*SCK=4.*esp32-to-433mhz"):
        esp32_433_micro.board_for("h", "m", odd)


def test_a_radio_read_without_pins_is_fatal_and_says_how_to_read_it():
    dev = dict(BLUE, radio=dict(BLUE["radio"], pins={}))
    with pytest.raises(esp32_micro.Esp32NotReadError,
                       match=r"rpi5-433mhz: .*--esp32-radio rpi5-433mhz=/dev/radio-cc1101-blue"):
        esp32_433_micro.micro_labels(_docs(dev))


def test_a_failed_radio_read_is_fatal_and_says_why():
    dev = dict(BLUE, radio=None, radio_error="cannot open /dev/ttyACM3: busy")
    with pytest.raises(esp32_micro.Esp32NotReadError, match="busy"):
        esp32_433_micro.micro_labels(_docs(dev))


def test_an_esp32_never_asked_about_a_radio_is_not_a_radio_node():
    dev = copy.deepcopy(BLUE)
    del dev["radio"]
    assert esp32_433_micro.micro_labels(_docs(dev)) == []


def test_the_radio_label_is_the_plain_label_plus_the_radio():
    host, dev = next(esp32_micro.devices(_docs(BLUE)))
    plain = esp32_micro.esp32_label(host, dev)
    m = _one(BLUE)
    assert (m.ident, m.ident_caption, m.title, m.mark, m.subtitle, m.qr_content) == (
        plain.ident, plain.ident_caption, plain.title, plain.mark, plain.subtitle,
        plain.qr_content)
    assert m.icons == (*plain.icons, Icon("antenna", "433"))
    # the chip's own unique id stays, both halves; the derived BT MAC gives
    # way to the radio
    assert [r.caption for r in plain.rows] == ["BT", "chip", ""]
    assert m.rows == plain.rows[1:]
    assert m.extra is not None


def test_the_radio_line_names_chip_and_board():
    assert esp32_433_micro.radio_text(
        esp32_433_micro.board_for("h", "m", BLUE["radio"])) == "CC1101 · E07-M1101D"
    assert esp32_433_micro.radio_text(
        esp32_433_micro.board_for("h", "m", SX["radio"])) == "SX1278 · Ra-02"


def test_the_marks_are_the_makers_of_the_chip_and_the_board():
    blue = esp32_433_micro.board_for("h", "m", BLUE["radio"])
    sx = esp32_433_micro.board_for("h", "m", SX["radio"])
    assert esp32_433_micro.marks(blue) == ("ti.svg",)
    assert esp32_433_micro.marks(sx) == ("semtech.svg", "ai-thinker.png")
    for name in ("ti.svg", "semtech.svg", "ai-thinker.png"):
        assert labels.artwork(name), name


def test_with_both_kinds_a_radio_node_gets_one_label_not_two():
    docs = _docs(*REAL["rpi5-433mhz"])
    rows = list(labels.all_labels(docs, {"esp32", "esp32-433"}))
    titles = " | ".join(r[2] for r in rows)
    assert titles.count("44:1b:f6:2e:b3:80") == 1
    assert titles.count("e8:3d:c1:8c:3e:b8") == 1
    assert titles.count("e8:3d:c1:8c:5c:88") == 1
    # and the one kept for a radio node is the radio label
    groups = [m for r in rows for m in r[4] if m is not None]
    by_mac = {m.ident: m for m in groups}
    assert Icon("antenna", "433") in by_mac["e8:3d:c1:8c:3e:b8"].icons
    assert Icon("antenna", "433") not in by_mac["e8:3d:c1:8c:5c:88"].icons
    # asked for alone, the plain kind still labels every ESP32
    plain = list(labels.all_labels(docs, {"esp32"}))
    assert sum(m is not None for r in plain for m in r[4]) == 3


def test_render_and_decode_the_radio_labels(tmp_path):
    ls = esp32_433_micro.micro_labels(_docs(*REAL["rpi5-433mhz"]))
    out = tmp_path / "radio.pdf"
    micro.render_micro(ls, out, outline=True)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        pytest.skip("pdftoppm not installed")
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    subprocess.run([pdftoppm, "-r", "600", "-png", str(out), str(tmp_path / "page")],
                   check=True)
    got = set()
    for png in sorted(glob.glob(str(tmp_path / "page-*.png"))):
        got |= {b.text for b in zxingcpp.read_barcodes(Image.open(png))}
    assert got == {"44:1b:f6:2e:b3:80", "e8:3d:c1:8c:3e:b8"}
