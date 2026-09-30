"""The Espressif part table: every chip esptool names, and every value sourced."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from rpi_hwid import espressif
from rpi_hwid.espressif import part_for

ROOT = pathlib.Path(__file__).parent.parent
REAL = json.loads((ROOT / "tests" / "esp32_devices.json").read_text())


# What esptool's get_chip_description() says, as rpi_hwid.esp32.chip_facts
# splits it (chip, the description, features), from esptool/targets/*.py at
# d69fc94, and the part each is.
@pytest.mark.parametrize(("chip", "description", "features", "efuse", "part"), [
    ("ESP8266EX", "ESP8266EX", (), {}, "ESP8266EX"),
    ("ESP8285N08", "ESP8285N08", ("Wi-Fi", "Embedded Flash"), {}, "ESP8285N08"),
    ("ESP8285H16", "ESP8285H16", (), {}, "ESP8285H16"),
    ("ESP8285N16", "ESP8285N16", (), {}, "ESP8285N16"),
    ("ESP32-D0WD-V3", "ESP32-D0WD-V3 (revision v3.1)", (), {}, "ESP32-D0WD-V3"),
    ("ESP32-D0WDQ6", "ESP32-D0WDQ6 (revision v1.0)", (), {}, "ESP32-D0WDQ6"),
    ("ESP32-D0WDQ6-V3", "ESP32-D0WDQ6-V3 (revision v3.0)", (), {}, "ESP32-D0WDQ6-V3"),
    ("ESP32-D0WD", "ESP32-D0WD (revision v1.0)", (), {}, "ESP32-D0WD"),
    ("ESP32-D0WDR2-V3", "ESP32-D0WDR2-V3 (revision v3.1)", (), {}, "ESP32-D0WDR2-V3"),
    ("ESP32-D2WD", "ESP32-D2WD (revision v1.0)", (), {}, "ESP32-D2WD"),
    ("ESP32-S0WD", "ESP32-S0WD (revision v1.0)", (), {}, "ESP32-S0WD"),
    ("ESP32-U4WDH", "ESP32-U4WDH (revision v3.0)", (), {}, "ESP32-U4WDH"),
    ("ESP32-PICO-D4", "ESP32-PICO-D4 (revision v1.0)", (), {}, "ESP32-PICO-D4"),
    ("ESP32-PICO-V3", "ESP32-PICO-V3 (revision v3.0)", (), {}, "ESP32-PICO-V3"),
    ("ESP32-PICO-V3-02", "ESP32-PICO-V3-02 (revision v3.0)", (), {}, "ESP32-PICO-V3-02"),
    ("ESP32-S2", "ESP32-S2 (revision v0.0)", (), {}, "ESP32-S2"),
    ("ESP32-S2FH2", "ESP32-S2FH2 (revision v0.0)", (), {}, "ESP32-S2FH2"),
    ("ESP32-S2FH4", "ESP32-S2FH4 (revision v1.0)", (), {}, "ESP32-S2FH4"),
    ("ESP32-S2FNR2", "ESP32-S2FNR2 (revision v1.0)", (), {}, "ESP32-S2FN4R2"),
    ("ESP32-S2R2", "ESP32-S2R2 (revision v1.0)", (), {}, "ESP32-S2R2"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)", (), {}, "ESP32-S3"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)", ("Embedded Flash 8MB (GD)",), {},
     "ESP32-S3FN8"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)",
     ("Embedded Flash 4MB (XMC)", "Embedded PSRAM 2MB (AP_3v3)"), {}, "ESP32-S3FH4R2"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)", ("Embedded PSRAM 8MB (AP_3v3)",), {},
     "ESP32-S3R8"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)", ("Embedded PSRAM 8MB (AP_1v8)",), {},
     "ESP32-S3R8V"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)", ("Embedded PSRAM 16MB (AP_1v8)",), {},
     "ESP32-S3R16V"),
    ("ESP32-S3", "ESP32-S3 (QFN56) (revision v0.2)", ("Embedded PSRAM 2MB (AP_3v3)",), {},
     "ESP32-S3R2"),
    ("ESP32-C2", "ESP32-C2 (revision v1.0)", (), {}, "ESP32-C2"),
    ("ESP8684H", "ESP8684H (revision v1.2)", ("Embedded Flash 2MB (XMC)",), {}, "ESP8684H2"),
    ("ESP8684H", "ESP8684H (revision v1.2)", ("Embedded Flash 4MB (XMC)",), {}, "ESP8684H4"),
    ("ESP32-C3", "ESP32-C3 (QFN32) (revision v0.4)", (), {}, "ESP32-C3"),
    ("ESP32-C3", "ESP32-C3 (QFN32) (revision v0.4)", ("Embedded Flash 4MB (XMC)",),
     {"FLASH_TEMP": 1}, "ESP32-C3FH4"),
    ("ESP32-C3", "ESP32-C3 (QFN32) (revision v0.4)", ("Embedded Flash 4MB (GD)",),
     {"FLASH_TEMP": "85C"}, "ESP32-C3FN4"),
    ("ESP32-C3", "ESP32-C3 AZ (QFN32) (revision v0.4)", ("Embedded Flash 4MB (XMC)",),
     {"FLASH_TEMP": 1}, "ESP32-C3FH4AZ"),
    ("ESP8685", "ESP8685 (QFN28) (revision v0.4)", ("Embedded Flash 4MB (XMC)",), {},
     "ESP8685H4"),
    ("ESP32-C5", "ESP32-C5 (revision v1.0)", (), {}, "ESP32-C5"),
    ("ESP32-C6", "ESP32-C6 (QFN40) (revision v0.2)", (), {}, "ESP32-C6"),
    ("ESP32-C6FH4", "ESP32-C6FH4 (QFN32) (revision v0.2)", (), {}, "ESP32-C6FH4"),
    ("ESP32-C6FH8", "ESP32-C6FH8 (QFN32) (revision v0.2)", (), {}, "ESP32-C6FH8"),
    ("ESP32-H2", "ESP32-H2 (revision v0.1)", (), {}, "ESP32-H2"),
    ("ESP32-P4", "ESP32-P4 (revision v1.3)", (), {}, "ESP32-P4"),
])
def test_every_chip_esptool_names_is_a_part(chip, description, features, efuse, part):
    rev = re.search(r"revision (v[\d.]+)", description)
    got = part_for(chip, description, features, efuse, rev.group(1) if rev else None)
    assert got.part == part


def test_a_revision_1_1_c3_is_the_x_part():
    got = part_for("ESP32-C3", "ESP32-C3 (QFN32) (revision v1.1)",
                   ("Embedded Flash 4MB (XMC)",), {"FLASH_TEMP": 1}, "v1.1")
    assert got.part == "ESP32-C3FH4X"


@pytest.mark.parametrize(("host", "mac", "part"), [
    ("rpi4-esp", "a4:f0:0f:76:46:64", "ESP32-D0WD-V3"),
    ("rpi4-esp", "24:0a:c4:11:44:e8", "ESP32-D0WDQ6"),
    ("rpi5-433mhz", "e8:3d:c1:8c:5c:88", "ESP32-C3FH4"),
    ("rpi5-433mhz", "44:1b:f6:2e:b3:80", "ESP32-C3FH4"),
    ("rpi5-433mhz", "e8:3d:c1:8c:3e:b8", "ESP32-C3FH4"),
])
def test_the_real_reads_name_their_parts(host, mac, part):
    (d,) = [d for d in REAL[host] if d["mac"] == mac]
    got = part_for(d["chip"], d["chip_description"], tuple(d["features"]), d["efuse"],
                   d["revision"])
    assert got.part == part


@pytest.mark.parametrize("chip", ["Unknown ESP32", "ESP32-C61", "ESP32-S0WDQ6", "ESP32-C5HR2"])
def test_a_chip_the_table_does_not_know_is_an_error_naming_it(chip):
    with pytest.raises(espressif.UnknownPartError, match=re.escape(chip)):
        part_for(chip, chip)


def test_no_chip_is_an_error():
    with pytest.raises(espressif.UnknownPartError):
        part_for(None)


def test_a_c3_whose_flash_temperature_was_not_read_is_an_error():
    with pytest.raises(espressif.UnknownPartError, match="FLASH_TEMP"):
        part_for("ESP32-C3", "ESP32-C3 (QFN32) (revision v0.4)",
                 ("Embedded Flash 4MB (XMC)",), {})


@pytest.mark.parametrize(("family", "isa", "cores", "lp", "sram"), [
    ("ESP8266", "Xtensa", 1, None, 160), ("ESP32", "Xtensa", 2, "ULP-FSM", 520),
    ("ESP32-S2", "Xtensa", 1, "ULP-RISC-V + ULP-FSM", 320),
    ("ESP32-S3", "Xtensa", 2, "ULP-RISC-V + ULP-FSM", 512),
    ("ESP32-C2", "RISC-V", 1, None, 272), ("ESP32-C3", "RISC-V", 1, None, 400),
    ("ESP32-C5", "RISC-V", 1, "LP RISC-V", 384), ("ESP32-C6", "RISC-V", 1, "LP RISC-V", 512),
    ("ESP32-H2", "RISC-V", 1, None, 320), ("ESP32-P4", "RISC-V", 2, "LP RISC-V", 768),
])
def test_the_families(family, isa, cores, lp, sram):
    (f,) = [f for f in espressif.FAMILIES if f.name == family]
    assert (f.isa, f.cores, f.lp_core, f.sram_kb) == (isa, cores, lp, sram)


def test_tasmota_builds_for_every_family_but_the_h2():
    """The H2 has no Wi-Fi and no Tasmota binary; every other family has one
    (the P4's Wi-Fi is a companion chip's)."""
    for f in espressif.FAMILIES:
        assert (f.tasmota in espressif.TASMOTA_BINARY) == (f.name != "ESP32-H2"), f.name
        assert (f.tasmota_build is None) == (f.tasmota == "none")


def test_single_core_and_slow_esp32s():
    assert espressif.BY_NAME["ESP32-S0WD"].n_cores == 1
    assert espressif.BY_NAME["ESP32-S0WD"].mhz == 160
    assert espressif.BY_NAME["ESP32-D0WD-V3"].n_cores == 2


FIELDS = ("core", "cores", "max_mhz", "sram_kb", "rom_kb", "rtc_sram_kb", "wifi",
          "bluetooth", "ieee802154", "usb", "chip_uid", "tasmota")


@pytest.mark.parametrize("family", espressif.FAMILIES, ids=lambda f: f.name)
def test_every_family_value_has_a_source(family):
    cited = {k.strip() for key in family.sources for k in key.split(",")}
    assert set(FIELDS) <= cited, set(FIELDS) - cited
    for text in family.sources.values():
        assert text.split()[0] in espressif.REFS, text


@pytest.mark.parametrize("part", espressif.PARTS, ids=lambda p: p.part)
def test_every_part_has_a_source(part):
    assert part.sources
    cited = {k.strip() for key in part.sources for k in key.split(",")}
    assert "part" in cited or "part, flash_mb" in part.sources
    for text in part.sources.values():
        assert text.split()[0] in espressif.REFS, text


def test_the_docs_table_is_the_module_s():
    """docs/ESPRESSIF.md is printed from the table; it may not drift."""
    doc = (ROOT / "docs" / "ESPRESSIF.md").read_text()
    assert espressif.markdown() in doc
    assert espressif.sources_markdown() in doc


@pytest.mark.parametrize(("family", "standards", "bands"), [
    ("ESP8266", ("b", "g", "n"), ("2.4",)), ("ESP32", ("b", "g", "n"), ("2.4",)),
    ("ESP32-S2", ("b", "g", "n"), ("2.4",)), ("ESP32-S3", ("b", "g", "n"), ("2.4",)),
    ("ESP32-C2", ("b", "g", "n"), ("2.4",)), ("ESP32-C3", ("b", "g", "n"), ("2.4",)),
    ("ESP32-C5", ("a", "b", "g", "n", "ac", "ax"), ("2.4", "5")),
    ("ESP32-C6", ("b", "g", "n", "ax"), ("2.4",)),
    ("ESP32-H2", (), ()), ("ESP32-P4", (), ()),
])
def test_the_wifi_standards_and_bands(family, standards, bands):
    """What the label's Wi-Fi glyph prints: the 802.11 amendments and the
    bands, each datasheet's own (Features > Wi-Fi)."""
    (f,) = [f for f in espressif.FAMILIES if f.name == family]
    assert f.wifi_standards == standards
    assert f.wifi_band_ghz == bands


@pytest.mark.parametrize(("family", "lp"), [
    ("ESP8266", 0), ("ESP32", 1), ("ESP32-S2", 1), ("ESP32-S3", 1), ("ESP32-C2", 0),
    ("ESP32-C3", 0), ("ESP32-C5", 1), ("ESP32-C6", 1), ("ESP32-H2", 0), ("ESP32-P4", 1),
])
def test_the_low_power_core_count(family, lp):
    """The S2's and S3's two ULP coprocessors cannot run at once (their
    datasheets' note), so each is one low-power core, not two."""
    (f,) = [f for f in espressif.FAMILIES if f.name == family]
    assert f.lp_cores == lp


@pytest.mark.parametrize(("part", "pair"), [
    ("ESP8266EX", (1, 0)), ("ESP32-D0WD-V3", (2, 1)), ("ESP32-S0WD", (1, 1)),
    ("ESP32-S2FH4", (1, 1)), ("ESP32-S3R8", (2, 1)), ("ESP8684H2", (1, 0)),
    ("ESP32-C3FH4", (1, 0)), ("ESP32-C5", (1, 1)), ("ESP32-C6FH4", (1, 1)),
    ("ESP32-H2", (1, 0)), ("ESP32-P4", (2, 1)),
])
def test_a_part_s_core_pair(part, pair):
    """(application cores, low-power cores): the two numbers on the label."""
    assert espressif.BY_NAME[part].core_pair == pair


@pytest.mark.parametrize(("family", "newest"), [
    ("ESP8266", "n"), ("ESP32", "n"), ("ESP32-S2", "n"), ("ESP32-S3", "n"),
    ("ESP32-C2", "n"), ("ESP32-C3", "n"), ("ESP32-C5", "ax"), ("ESP32-C6", "ax"),
    ("ESP32-H2", None), ("ESP32-P4", None),
])
def test_the_newest_wifi_standard_is_the_one_printed(family, newest):
    """Tim, 2026-09-27: the label's Wi-Fi glyph names only the newest
    802.11 standard the radio has -- n for Wi-Fi 4, ax for Wi-Fi 6."""
    fam = {f.name: f for f in espressif.FAMILIES}[family]
    assert fam.wifi_newest == newest


@pytest.mark.parametrize(("chip", "family"), [
    ("ESP8266EX", espressif.ESP8266), ("ESP8285", espressif.ESP8266),
    ("ESP32", espressif.ESP32), ("ESP32-D0WD-V3", espressif.ESP32),
    ("ESP32-PICO-V3-02", espressif.ESP32), ("ESP32-S0WD", espressif.ESP32),
    ("ESP32-C3", espressif.ESP32_C3),
    ("ESP32-C3FH4", espressif.ESP32_C3), ("ESP32-S3", espressif.ESP32_S3),
    ("ESP32-C6", espressif.ESP32_C6), ("esp32-s2", espressif.ESP32_S2)])
def test_a_chip_as_tasmota_names_it_is_a_family(chip, family):
    """Tasmota reports the chip (StatusFWR.Hardware) as ESP.getChipModel()
    or a package name, never a part the table lists."""
    assert espressif.family_for(chip) is family


@pytest.mark.parametrize("chip", ["ESP32-C61", "ESP31B", "RP2040", ""])
def test_a_chip_of_no_family_is_an_error_naming_it(chip):
    with pytest.raises(espressif.UnknownPartError, match=re.escape(repr(chip))):
        espressif.family_for(chip)
