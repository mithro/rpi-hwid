"""ESP32 micro labels: records from real documents, and the refusals."""

from __future__ import annotations

import copy
import dataclasses
import json
import pathlib

import pytest

import conftest
from rpi_hwid import esp32_micro, espressif, labels, micro
from rpi_hwid.micro import Icon, MicroRow
from rpi_hwid.model import ProbeDocument

# verdict.esp32 as `rpi-hwid collect --esp32-read` wrote it on 2026-09-27,
# with the boot output trimmed: three ESP32-C3 SuperMinis on rpi5-433mhz's
# USB-Serial-JTAG (esptool 4.7.0), and on rpi4-esp an ESP32-CAM behind a
# CH340 and a first-generation devkit behind a CP2102 (esptool 5.2.0).
REAL = json.loads((pathlib.Path(__file__).parent / "esp32_devices.json").read_text())

FLASH, UID = esp32_micro.FLASH_PT, esp32_micro.UID_PT


def flash(value):
    return MicroRow("flash", value, size=FLASH)


def uid(caption, value, wide=False):
    return MicroRow(caption, value, mono=True, size=UID, wide=wide)


def fuid(caption, value):
    """A flash uid row: set wide, its digits right of its own caption."""
    return uid(caption, value, wide=True)


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
    the rows from the read. Both its ids (Tim, 2026-09-27): its in-package
    XMC's, 80 bits once its ff padding is trimmed, on one row, and the
    chip's 128-bit eFuse id over the two under it."""
    (lab,) = esp32_micro.micro_labels(_docs(C3))
    assert lab.title == "ESP32-C3FH4"
    assert lab.mark == "espressif.svg"
    # Bluetooth small on the Wi-Fi glyph, and the USB Serial/JTAG
    # controller's JTAG and serial on the trident (Tim, 2026-09-29)
    assert lab.icons == (Icon("wifi", "2.4 n +bt"), Icon("usb", "JS"))
    # the revision closes the strip, bare (Tim, 2026-09-29)
    assert lab.specs == (Icon("riscv"), Icon("cores", "1+0"), Icon("memory", "400K"),
                         Icon("tasmota"), Icon("revision", "v0.4"))
    assert lab.subtitle == ""
    assert lab.ident_caption == "Wi-Fi MAC"
    assert lab.ident == "e8:3d:c1:8c:3e:b8"
    assert lab.rows == (flash("XM25QH32D · 4 MiB"),
                        fuid("uid", "1f2b10190882f7540150"),
                        uid("eFuse", "53b9b91842e41b19"),
                        uid("", "ee00321402a8b49b"))


@pytest.mark.parametrize(("mac", "flash_uid", "efuse"), [
    ("e8:3d:c1:8c:5c:88", "240c1119088539540150", ("89e4bec55c62671e", "ca93593f986fa7c9")),
    ("44:1b:f6:2e:b3:80", "2c30041916712aca013a", ("4cd07386bef134a5", "3cd0e87a86d36603")),
    ("e8:3d:c1:8c:3e:b8", "1f2b10190882f7540150", ("53b9b91842e41b19", "ee00321402a8b49b")),
])
def test_every_real_c3_prints_both_its_ids(mac, flash_uid, efuse):
    (lab,) = esp32_micro.micro_labels(_docs(_real("rpi5-433mhz", mac)))
    assert lab.rows[1:] == (fuid("uid", flash_uid), uid("eFuse", efuse[0]),
                            uid("", efuse[1]))


@pytest.mark.parametrize(("read", "shown"), [
    # the C3s' XM25QH32D: 80 programmed bits, then six bytes of ff
    ("240c1119088539540150ffffffffffff", "240c1119088539540150"),
    ("2c30041916712aca013affffffffffff", "2c30041916712aca013a"),
    # a Boya's 128 bits end in no ff, and the ff inside them are the id's
    ("343738393844fa77fffcffff968f1f11", "343738393844fa77fffcffff968f1f11"),
    # a 64-bit id is never trimmed, whatever its last byte
    ("3130343531118566", "3130343531118566"),
    ("31303435311185ff", "31303435311185ff"),
    # nor is a longer one trimmed below 64 bits
    ("3130343531118566ffffffffffffffff", "3130343531118566"),
    ("313034353111ffffffffffffffffffff", "313034353111ffff"),
    # only whole bytes: a lone f at the end is a digit of the id
    ("240c11190885395401500fffffffffff", "240c11190885395401500f"),
])
def test_a_flash_uid_is_trimmed_of_its_ff_padding(read, shown):
    assert esp32_micro.trim_flash_uid(read) == shown


@pytest.mark.parametrize("flash_uid", ["ffffffffffffffffffffffffffffffff",
                                       "00000000000000000000000000000000"])
def test_an_all_ones_or_all_zeroes_uid_is_no_uid_not_a_trimmed_one(flash_uid):
    (lab,) = esp32_micro.micro_labels(_docs(dict(C3, flash_uid=flash_uid)))
    assert [r.caption for r in lab.rows] == ["flash", "eFuse", ""]


def test_without_a_flash_uid_the_c3_prints_its_efuse_id_alone():
    (lab,) = esp32_micro.micro_labels(_docs(dict(C3, flash_uid=None)))
    assert lab.rows[1:] == (uid("eFuse", "53b9b91842e41b19"),
                            uid("", "ee00321402a8b49b"))


def test_a_two_row_flash_uid_beside_an_efuse_id_fills_every_row():
    """A 128-bit flash uid and an eFuse id: two rows each under the flash
    row, the five the label holds; every id is printed."""
    boya = "343738393844fa77fffcffff968f1f11"
    (lab,) = esp32_micro.micro_labels(_docs(dict(C3, flash_uid=boya)))
    assert lab.rows == (flash("XM25QH32D · 4 MiB"),
                        fuid("uid", "343738393844fa77"), fuid("", "fffcffff968f1f11"),
                        uid("eFuse", "53b9b91842e41b19"), uid("", "ee00321402a8b49b"))


@pytest.mark.parametrize(("bits", "part"), [(128, "BY25Q32ES"), (64, "BY25Q32BS"),
                                            (None, "BY25Q32xS")])
def test_a_boya_is_named_by_the_length_of_its_uid(bits, part):
    (cam,) = esp32_micro.micro_labels(_docs(dict(CAM, flash_uid_bits=bits), host="rpi4-esp"))
    assert cam.rows[0] == flash(f"{part} · 4 MiB")


def test_a_flash_no_part_is_known_for_stops_label_generation():
    """Tim, 2026-09-27: a bare JEDEC id is never printed. The tool stops,
    names the host, the ESP32 and the id, and asks for the part to be added."""
    with pytest.raises(esp32_micro.UnknownFlashPartError) as excinfo:
        esp32_micro.micro_labels(_docs(dict(DEVKIT, flash_jedec="0x464017"), host="rpi4-esp"))
    msg = str(excinfo.value)
    assert msg.startswith("rpi4-esp:")
    assert "24:0a:c4:11:44:e8" in msg
    assert "0x464017" in msg
    assert "JEDEC_PART" in msg


@pytest.mark.parametrize(("jedec", "part"), [
    ("0xc84014", "GD25Q80x · 1 MiB"), ("0xc84015", "GD25Q16x · 2 MiB"),
    ("0xc84016", "GD25Q32x · 4 MiB"), ("0xc84017", "GD25Q64x · 8 MiB"),
    ("0xc84018", "GD25Q128x · 16 MiB"),
])
def test_the_gigadevice_family_is_named(jedec, part):
    """flashrom's include/flashchips.h: GD25Q80/16/32/64/128, each id
    shared by its B/C/E variants, so the letter is written as x."""
    (dev,) = esp32_micro.micro_labels(_docs(dict(DEVKIT, flash_jedec=jedec), host="rpi4-esp"))
    assert dev.rows[0] == flash(part)


def test_an_original_esp32_label():
    (cam,) = esp32_micro.micro_labels(_docs(CAM, host="rpi4-esp"))
    assert cam.title == "ESP32-D0WD-V3"
    # no USB of its own: its port is the board's USB-UART bridge
    assert cam.icons == (Icon("wifi", "2.4 n +bt"),)
    assert cam.specs == (Icon("xtensa"), Icon("cores", "2+1"), Icon("memory", "520K"),
                         Icon("tasmota"), Icon("revision", "v3.1"))
    # an original ESP32 has no eFuse id: its flash's is its one serial, the
    # Boya's 128 bits (no ff to trim) over two rows, the GigaDevice's 64 on one
    assert cam.rows == (flash("BY25Q32ES · 4 MiB"),
                        fuid("uid", "343738393844fa77"),
                        fuid("", "fffcffff968f1f11"))
    (dev,) = esp32_micro.micro_labels(_docs(DEVKIT, host="rpi4-esp"))
    assert dev.rows == (flash("GD25Q32x · 4 MiB"), fuid("uid", "3130343531118566"))


def test_every_label_has_the_same_rows_in_the_same_places():
    """flash, then the flash's uid and the chip's eFuse id: a fact that
    does not apply leaves its place empty at the end, never moves another
    up into it. The revision is on the strip, last, where one was read."""
    labs = _all_labels() + [esp32_micro.sample_label(p, i)
                            for i, p in enumerate(espressif.PARTS)]
    serials = ([], ["uid"], ["uid", ""], ["eFuse", ""], ["uid", "eFuse", ""],
               ["uid", "", "eFuse", ""])
    for lab in labs:
        caps = [r.caption for r in lab.rows]
        assert caps[:1] == ["flash"], (lab.title, caps)
        assert caps[1:] in serials, (lab.title, caps)
        assert [i.name for i in lab.specs[:3]] in (
            ["riscv", "cores", "memory"], ["xtensa", "cores", "memory"]), lab.title
        assert "revision" not in [i.name for i in lab.specs[:-1]], lab.title


def test_an_esp8266_label():
    d = dict(DEVKIT, chip="ESP8266EX", chip_description="ESP8266EX", revision=None,
             features=["Wi-Fi", "160MHz"], crystal_mhz=26, efuse={},
             read_errors={"efuse": "ModuleNotFoundError: espefuse.efuse.esp8266"})
    (lab,) = esp32_micro.micro_labels(_docs(d, host="rpi4-esp"))
    assert lab.title == "ESP8266EX"
    # no Bluetooth, and no USB of its own
    assert lab.icons == (Icon("wifi", "2.4 n"),)
    # no revision to print, and the crystal is not printed
    assert lab.specs == (Icon("xtensa"), Icon("cores", "1+0"), Icon("memory", "160K"),
                         Icon("tasmota"))
    assert lab.rows[0] == flash("GD25Q32x · 4 MiB")


def test_an_h2_has_no_wifi_and_no_tasmota():
    lab = esp32_micro.sample_label(espressif.BY_NAME["ESP32-H2"])
    assert lab.ident_caption == "MAC"
    # no Wi-Fi to carry it: Bluetooth stands alone, full size
    assert lab.icons == (Icon("bluetooth"), Icon("usb", "JS"), Icon("mesh"))
    assert Icon("tasmota") not in lab.specs


def test_in_package_psram_is_on_the_memory_glyph():
    lab = esp32_micro.sample_label(espressif.BY_NAME["ESP32-S3R8"])
    assert Icon("memory", "512K+8M") in lab.specs
    assert lab.icons == (Icon("wifi", "2.4 n +bt"), Icon("usb", "OJS"))


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


@pytest.mark.parametrize("uid", ["ffffffffffffffff", "0000000000000000",
                                 "ffffffffffffffffffffffffffffffff", "", None])
def test_a_blank_flash_uid_is_left_off_not_printed(uid):
    (lab,) = esp32_micro.micro_labels(_docs(dict(DEVKIT, flash_uid=uid), host="rpi4-esp"))
    assert [r.caption for r in lab.rows] == ["flash"]


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
    kinds: dict[str, set[str]] = {}
    for lab in labs:
        kind = ""
        for r in lab.rows:
            kind = r.caption or kind            # a second row is its first's kind
            kinds.setdefault(r.value, set()).add(kind)
    sizes: dict[str, set[float]] = {}
    for s, _font, size in drawn:
        for kind in kinds.get(s, ()):
            sizes.setdefault(kind, set()).add(size)
    assert sizes == {"flash": {FLASH}, "uid": {UID}, "eFuse": {UID}}
    assert min(FLASH, UID) >= micro.MIN_SIZE


def _ink(s, size):
    """How far `s` at `size` reaches above and below its baseline: to the
    ascender line (Helvetica's 718/1000, which the digits and capitals of
    both faces stay under, with a hair to spare), and below it only where a
    letter has a descender (Helvetica's 207/1000)."""
    descends = any(ch in "gjpqy,;()[]" for ch in s)
    return size * 0.74, size * 0.21 if descends else 0.0


@pytest.mark.parametrize("which", ["real", "samples"])
def test_no_row_overflows_or_touches_another(monkeypatch, which):
    """Every label, real and sample: every string under the spec strip lies
    inside the label's margins, and no two touch -- each one's ink (_ink)
    clear of every other's. The rows are set tighter to hold both a C3's
    ids."""
    import io

    labs = (_all_labels()[:sum(len(d) for d in REAL.values())] if which == "real"
            else [esp32_micro.sample_label(p, i) for i, p in enumerate(espressif.PARTS)]
            + [esp32_micro.sample_label(p, i + 1) for i, p in enumerate(espressif.PARTS)])
    below_strip = micro.band_top() + micro.SPEC_H
    for lab in labs:
        drawn = []
        real = micro.Cell.text

        def text(self, x, y, s, font=labels.SANS, size=8, align="left", color=None,
                 _drawn=drawn, _real=real, **kw):
            w = self.width(s, font, size)
            left = x - w if align == "right" else x - w / 2 if align == "centre" else x
            up, down = _ink(s, size)
            base = y + size * 0.72
            _drawn.append((s, left, left + w, base - up, base + down, base))
            return _real(self, x, y, s, font, size, align,
                         **({"color": color} if color else {}))

        monkeypatch.setattr(micro.Cell, "text", text)
        micro.render_micro([lab], io.BytesIO())
        monkeypatch.setattr(micro.Cell, "text", real)
        body = [d for d in drawn if d[3] > below_strip]
        assert {r.value for r in lab.rows if r.value} <= {d[0] for d in body}, lab.title
        for s, left, right, _top, _bottom, base in body:
            assert left >= micro.MICRO_PAD - 0.01, (lab.title, s)
            assert right <= micro.MICRO_W - micro.MICRO_PAD + 0.01, (lab.title, s)
            assert base <= micro.MICRO_H - micro.MICRO_PAD + 0.01, (lab.title, s)
        for i, a in enumerate(body):
            for b in body[i + 1:]:
                apart = a[2] <= b[1] or b[2] <= a[1] or a[4] <= b[3] or b[4] <= a[3]
                assert apart, (lab.title, a[0], b[0])


def test_the_wifi_glyph_carries_each_family_s_bands_and_standards():
    got = {esp32_micro.sample_label(p).title: esp32_micro.radio_icons(p.family)[:1]
           for p in espressif.PARTS if not p.listed_only}
    assert got["ESP32-C3FH4"] == (Icon("wifi", "2.4 n +bt"),)
    assert got["ESP32-C6"] == (Icon("wifi", "2.4 ax +bt"),)
    assert got["ESP32-C5"] == (Icon("wifi", "2.4/5 ax +bt"),)
    assert got["ESP32-S2"] == (Icon("wifi", "2.4 n"),)
    assert got["ESP32-H2"] == (Icon("bluetooth"),)
    assert got["ESP32-P4"] == (Icon("usb", "OJS"),)


@pytest.mark.parametrize(("family", "letters"), [
    ("ESP8266", None), ("ESP32", None), ("ESP32-C2", None), ("ESP32-S2", "O"),
    ("ESP32-S3", "OJS"), ("ESP32-C3", "JS"), ("ESP32-C5", "JS"), ("ESP32-C6", "JS"),
    ("ESP32-H2", "JS"), ("ESP32-P4", "OJS")])
def test_the_usb_glyph_letters_what_the_chip_s_own_usb_does(family, letters):
    """O for OTG, J and S for the USB Serial/JTAG controller's JTAG and
    serial; no trident where the chip has no USB (Tim, 2026-09-29)."""
    (fam,) = [f for f in espressif.FAMILIES if f.name == family]
    usb = [i.text for i in esp32_micro.radio_icons(fam) if i.name == "usb"]
    assert usb == ([letters] if letters else [])


@pytest.mark.parametrize("part", espressif.PARTS, ids=lambda p: p.part)
def test_the_usb_glyph_is_no_larger_than_the_wifi_glyph(part):
    """Tim, 2026-09-29: "no bigger than the wifi icon". The upright
    trident is as high as the Wi-Fi glyph and no wider than one carrying
    Bluetooth. The one exception is the S2's: it has no Bluetooth, and its
    Wi-Fi glyph (2.8 mm) is narrower than three letters side by side at
    4 pt (3.2 mm)."""
    icons = {i.name: i for i in esp32_micro.radio_icons(part.family)}
    if "usb" not in icons or "wifi" not in icons:
        return
    usb = micro._icon_width(None, icons["usb"])
    widest = micro.wifi_width(micro.HEAD_H, "2.4 n +bt")
    assert usb <= widest + 0.01
    if micro.wifi_bluetooth(icons["wifi"].text)[1]:
        assert usb <= micro._icon_width(None, icons["wifi"]) + 0.01


@pytest.mark.parametrize("part", espressif.PARTS, ids=lambda p: p.part)
def test_the_cores_glyph_is_the_part_s_core_pair(part):
    lab = esp32_micro.sample_label(part)
    hp, lp = part.core_pair
    assert Icon("cores", f"{hp}+{lp}") in lab.specs
    assert micro.strip_width(lab.specs) <= micro.rows_w() + 0.01


def test_sample_devices_look_like_real_ones():
    """Every sample on the sheet has its own MAC, flash uid and eFuse id,
    none a counting pattern, and every MAC has a hex letter in it, as real
    ones do: a MAC of digits alone would come out as a Micro QR code (Tim,
    2026-09-27: "Why are all the flash ids identical?", "Why do some of the
    wifi MAC qrcodes look different?")."""
    import segno
    devs = [esp32_micro.sample_device(p, i + 1) for i, p in enumerate(espressif.PARTS)]
    macs = [d.mac for d in devs]
    uids = [d.flash_uid for d in devs if d.flash_uid]
    efuse = [d.efuse["OPTIONAL_UNIQUE_ID"] for d in devs if "OPTIONAL_UNIQUE_ID" in d.efuse]
    assert len(set(macs)) == len(macs)
    assert len(set(uids)) == len(uids)
    assert len(set(efuse)) == len(efuse)
    assert all(m.startswith("02:") for m in macs)
    assert all(segno.make(m, error="m").designator.startswith("2-") for m in macs)
    assert all(len({u[i:i + 4] for i in range(0, len(u), 4)}) > 1 for u in uids)
    assert len({d.flash_jedec for d in devs}) > 2
