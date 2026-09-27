"""ESP32 micro labels: records from real documents, and the refusals."""

from __future__ import annotations

import copy
import dataclasses
import json
import pathlib

import pytest

import conftest
from rpi_hwid import esp32_micro, espressif, labels, micro
from rpi_hwid.micro import BLANK_ROW, Icon, MicroRow
from rpi_hwid.model import ProbeDocument

# verdict.esp32 as `rpi-hwid collect --esp32-read` wrote it on 2026-09-27,
# with the boot output trimmed: three ESP32-C3 SuperMinis on rpi5-433mhz's
# USB-Serial-JTAG (esptool 4.7.0), and on rpi4-esp an ESP32-CAM behind a
# CH340 and a first-generation devkit behind a CP2102 (esptool 5.2.0).
REAL = json.loads((pathlib.Path(__file__).parent / "esp32_devices.json").read_text())

FLASH, UID = esp32_micro.FLASH_PT, esp32_micro.UID_PT


def flash(value):
    return MicroRow("flash", value, size=FLASH)


def uid(caption, value):
    return MicroRow(caption, value, mono=True, size=UID)


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
        ("rpi5-433mhz", "ESP32-C3FH4", "e8:3d:c1:8c:5c:88"),
        ("rpi5-433mhz", "ESP32-C3FH4", "44:1b:f6:2e:b3:80"),
        ("rpi5-433mhz", "ESP32-C3FH4", "e8:3d:c1:8c:3e:b8"),
    ]


def test_a_c3_label():
    """The part from the chip and its eFuse; the spec strip from the table;
    the rows from the read. Its in-package XMC gave a 128-bit unique id,
    which takes the uid rows (SERIALS); the chip's eFuse id stays in the
    document."""
    (lab,) = esp32_micro.micro_labels(_docs(C3))
    assert lab.title == "ESP32-C3FH4"
    assert lab.mark == "espressif.svg"
    assert lab.icons == (Icon("wifi", "2.4 b/g/n"), Icon("bluetooth"))
    assert lab.specs == (Icon("riscv"), Icon("cores", "1+0"), Icon("memory", "400K"),
                         Icon("tasmota"))
    assert lab.subtitle == ""
    assert lab.ident_caption == "Wi-Fi MAC"
    assert lab.ident == "e8:3d:c1:8c:3e:b8"
    assert lab.rows == (MicroRow("chip", "v0.4"),
                        flash("XM25QH32D · 4 MiB"),
                        uid("uid", "1f2b10190882f754"),
                        uid("", "0150ffffffffffff"))


def test_without_a_flash_uid_the_chip_s_efuse_id_stands_in():
    (lab,) = esp32_micro.micro_labels(_docs(dict(C3, flash_uid=None)))
    assert lab.rows[2:] == (uid("eFuse", "53b9b91842e41b19"),
                            uid("", "ee00321402a8b49b"))


def test_serials_decides_which_id_a_c3_prints(monkeypatch):
    """The one switch between the two serials a C3 has."""
    monkeypatch.setattr(esp32_micro, "SERIALS", ("efuse", "flash"))
    (lab,) = esp32_micro.micro_labels(_docs(C3))
    assert lab.rows[2:] == (uid("eFuse", "53b9b91842e41b19"),
                            uid("", "ee00321402a8b49b"))
    # an original ESP32 has no eFuse id, so it prints its flash's either way
    (dev,) = esp32_micro.micro_labels(_docs(DEVKIT, host="rpi4-esp"))
    assert dev.rows[2:] == (uid("uid", "3130343531118566"),)


@pytest.mark.parametrize(("bits", "part"), [(128, "BY25Q32ES"), (64, "BY25Q32BS"),
                                            (None, "BY25Q32xS")])
def test_a_boya_is_named_by_the_length_of_its_uid(bits, part):
    (cam,) = esp32_micro.micro_labels(_docs(dict(CAM, flash_uid_bits=bits), host="rpi4-esp"))
    assert cam.rows[1] == flash(f"{part} · 4 MiB")


def test_a_flash_no_part_is_known_for_is_its_id_and_size():
    """Its vendor too where that fits at the flash row's one size; the
    id's first byte names the vendor anyway (JEP106)."""
    (dev,) = esp32_micro.micro_labels(_docs(dict(DEVKIT, flash_jedec="0x464017"),
                                            host="rpi4-esp"))
    assert dev.rows[1] == flash("0x464017 · 8 MiB")
    (dev,) = esp32_micro.micro_labels(_docs(dict(DEVKIT, flash_jedec="0xc84017"),
                                            host="rpi4-esp"))
    assert dev.rows[1] == flash("0xc84017 · 8 MiB")


def test_an_original_esp32_label():
    (cam,) = esp32_micro.micro_labels(_docs(CAM, host="rpi4-esp"))
    assert cam.title == "ESP32-D0WD-V3"
    assert cam.icons == (Icon("wifi", "2.4 b/g/n"), Icon("bluetooth"))
    assert cam.specs == (Icon("xtensa"), Icon("cores", "2+1"), Icon("memory", "520K"),
                         Icon("tasmota"))
    assert cam.rows == (MicroRow("chip", "v3.1"),
                        flash("BY25Q32ES · 4 MiB"),
                        uid("uid", "343738393844fa77"),
                        uid("", "fffcffff968f1f11"))
    (dev,) = esp32_micro.micro_labels(_docs(DEVKIT, host="rpi4-esp"))
    assert dev.rows[1:] == (flash("GD25Q32x · 4 MiB"), uid("uid", "3130343531118566"))


def test_every_label_has_the_same_rows_in_the_same_places():
    """chip, flash, then the uid rows: a fact that does not apply leaves its
    place empty at the end, never moves another up into it."""
    docs = {}
    for host, devs in REAL.items():
        docs.update(_docs(*devs, host=host))
    labs = esp32_micro.micro_labels(docs)
    labs += [esp32_micro.sample_label(p, i) for i, p in enumerate(espressif.PARTS)]
    for lab in labs:
        caps = [r.caption for r in lab.rows]
        # an ESP8266 reports no revision: its chip row's place is left empty
        assert caps[:2] == ["" if lab.rows[0] == BLANK_ROW else "chip", "flash"], (
            lab.title, caps)
        assert caps[2:] in ([], ["uid"], ["uid", ""], ["eFuse", ""]), (lab.title, caps)
        assert [i.name for i in lab.specs[:3]] in (
            ["riscv", "cores", "memory"], ["xtensa", "cores", "memory"]), lab.title


def test_an_esp8266_label():
    d = dict(DEVKIT, chip="ESP8266EX", chip_description="ESP8266EX", revision=None,
             features=["Wi-Fi", "160MHz"], crystal_mhz=26, efuse={},
             read_errors={"efuse": "ModuleNotFoundError: espefuse.efuse.esp8266"})
    (lab,) = esp32_micro.micro_labels(_docs(d, host="rpi4-esp"))
    assert lab.title == "ESP8266EX"
    assert lab.icons == (Icon("wifi", "2.4 b/g/n"),)
    assert lab.specs == (Icon("xtensa"), Icon("cores", "1+0"), Icon("memory", "160K"),
                         Icon("tasmota"))
    # no revision to print, and the crystal is not printed: the chip row's
    # place is left empty, and the flash row stays where it is on every label
    assert lab.rows[0] == BLANK_ROW
    assert lab.rows[1] == flash("GD25Q32x · 4 MiB")


def test_an_h2_has_no_wifi_and_no_tasmota():
    lab = esp32_micro.sample_label(espressif.BY_NAME["ESP32-H2"])
    assert lab.ident_caption == "MAC"
    assert lab.icons == (Icon("bluetooth"), Icon("mesh"))
    assert Icon("tasmota") not in lab.specs


def test_in_package_psram_is_on_the_memory_glyph():
    lab = esp32_micro.sample_label(espressif.BY_NAME["ESP32-S3R8"])
    assert Icon("memory", "512K+8M") in lab.specs
    assert lab.icons == (Icon("wifi", "2.4 b/g/n"), Icon("bluetooth"))


def test_a_chip_the_table_does_not_know_is_an_error_naming_the_host():
    d = dict(DEVKIT, chip="ESP32-C61", chip_description="ESP32-C61 (revision v1.0)")
    with pytest.raises(espressif.UnknownPartError,
                       match=r"rpi4-esp: the ESP32 24:0a:c4:11:44:e8 .*ESP32-C61"):
        esp32_micro.micro_labels(_docs(d, host="rpi4-esp"))


@pytest.mark.parametrize("part", espressif.PARTS, ids=lambda p: p.part)
def test_every_part_s_sample_label_renders(part):
    """Every row of the table makes a label that fits: nothing overflows,
    no identifier is elided (render_micro raises if one would be)."""
    import io

    lab = esp32_micro.sample_label(part, 7)
    assert lab.ident.startswith("02:")          # locally administered: a sample
    assert lab.title == part.part
    assert micro.title_fits(lab), "the part number would be elided"
    micro.render_micro([lab], io.BytesIO())


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
    assert [r.caption for r in lab.rows] == ["chip", "flash"]


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
    assert "ESP32-C3FH4 44:1b:f6:2e:b3:80" in rows[0][2]
    n, stickers, _ = micro.render_micro(esp32_micro.micro_labels(docs), tmp_path / "e.pdf")
    assert (n, stickers) == (3, 1)


def _all_labels():
    docs = {}
    for host, devs in REAL.items():
        docs.update(_docs(*devs, host=host))
    labs = esp32_micro.micro_labels(docs)
    return labs + [esp32_micro.sample_label(p, i + 1) for i, p in enumerate(espressif.PARTS)]


def test_no_label_prints_the_crystal():
    """Tim, 2026-09-27: the crystal is not needed on the label. It stays in
    the document (crystal_mhz)."""
    for lab in _all_labels():
        assert not any("MHz" in r.value or "xtal" in r.value for r in lab.rows), lab.title
    assert _real("rpi4-esp", "a4:f0:0f:76:46:64")["crystal_mhz"] == 40


def test_every_flash_row_and_every_uid_row_prints_at_one_size(monkeypatch):
    """Tim, 2026-09-27: the flash row's type does not change with the
    length of the flash's name. Nor does a uid row's with its caption."""
    import io

    drawn = []
    real = micro.Cell.fit

    def fit(self, x, y, s, font, size, max_w, min_size=5.5, **k):
        drawn.append((s, font, size))
        return real(self, x, y, s, font, size, max_w, min_size=min_size)

    monkeypatch.setattr(micro.Cell, "fit", fit)
    labs = _all_labels()
    micro.render_micro(labs, io.BytesIO())
    values = {r.value: r.caption for lab in labs for r in lab.rows}
    flash_sizes = {size for s, font, size in drawn if values.get(s) == "flash"}
    uid_sizes = {size for s, font, size in drawn
                 if s in values and values[s] in ("uid", "eFuse", "") and s}
    assert flash_sizes == {FLASH}
    assert uid_sizes == {UID}
    assert min(FLASH, UID) >= micro.MIN_SIZE


def test_the_wifi_glyph_carries_each_family_s_bands_and_standards():
    got = {esp32_micro.sample_label(p).title: esp32_micro.radio_icons(p.family)[:1]
           for p in espressif.PARTS if not p.listed_only}
    assert got["ESP32-C3FH4"] == (Icon("wifi", "2.4 b/g/n"),)
    assert got["ESP32-C6"] == (Icon("wifi", "2.4 b/g/n/ax"),)
    assert got["ESP32-C5"] == (Icon("wifi", "2.4/5 a/b/g/n/ac/ax"),)
    assert got["ESP32-H2"] == (Icon("bluetooth"),)
    assert got["ESP32-P4"] == ()


@pytest.mark.parametrize("part", espressif.PARTS, ids=lambda p: p.part)
def test_the_cores_glyph_is_the_part_s_core_pair(part):
    lab = esp32_micro.sample_label(part)
    hp, lp = part.core_pair
    assert Icon("cores", f"{hp}+{lp}") in lab.specs
    assert micro.strip_width(lab.specs) <= micro.rows_w() + 0.01
