"""Labels: records from documents, a rendered sheet, and every QR decoding."""

from __future__ import annotations

import copy
import glob
import shutil
import subprocess

import pytest

from rpi_hwid import labels
from rpi_hwid.cli import main as cli_main
from rpi_hwid.model import ProbeDocument


def test_board_record_derives_a_broadcom_radio_mac(docs):
    p = labels.board_record(docs["pi-sw1-p10"])
    assert p.kind == "rpi"
    assert p.title == "Raspberry Pi 3 Model B+"
    assert p.subtitle == "1 GB  ·  Rev 1.3  ·  rev code a020d3"
    assert p.memory == "1 GB"
    assert p.mark == "raspberry-pi.svg"
    assert p.macs == (("eth", "b8:27:eb:e3:e7:e4"), ("wlan", "b8:27:eb:b6:b2:b1"))
    assert p.wlan_note is None


def test_board_record_pi5_without_radio_says_so(docs):
    p = labels.board_record(docs["pi-sw2-p47"])
    assert p.macs == (("eth", "98:fe:54:13:f5:75"),)
    assert p.wlan_note == "radio disabled, not readable"
    assert p.header == ("Waveshare PoE M.2 HAT+ (B)",)


def test_board_record_zero_has_no_wired_port(docs):
    p = labels.board_record(docs["rpiz-serial"])
    assert p.eth_note == "no wired port"
    assert p.macs[0] == ("eth", "00:e0:4c:36:0b:0a"), "the bonnet's port is still printed"


def test_board_record_orange_pi_pc(docs):
    p = labels.board_record(docs["pi-sw2-p22"])
    assert p.kind == "opi"
    assert p.short == "Orange Pi PC"
    assert p.title == "Orange Pi PC"
    assert p.subtitle == "1 GB  ·  Allwinner H3  ·  dt orangepi-pc"
    assert p.mark == "orange-pi.png"
    assert p.serial == "02c000812eb7a34e"
    assert p.macs == (("eth", "02:81:2e:b7:a3:4e"),)
    assert p.wlan_note == "no radio"
    assert p.eth_note is None
    # The HAT band is the Pi's, not a special case: this board wears a
    # Digilent Pmod HAT Adaptor and its label says so exactly as the Pi 4
    # wearing the same adaptor does.
    assert p.header == ("Pmod HAT Adaptor",)
    assert p.hat_uuid == "363bffaa-8824-a94d-7242-3c0955f9126c"


@pytest.mark.parametrize(("idcode", "part"), [
    ("0x362d093", "XC7A35T"),      # openFPGALoader's spelling
    ("0x0362d093", "XC7A35T"),     # openocd's: the same number, zero-padded
    ("0x3631093", "XC7A100T"),
    ("0x13631093", "XC7A100T"),    # the top nibble is the silicon revision
    ("0x23631093", "XC7A100T"),    # ...so a revision nobody has met yet is fine too
    ("0x03636093", "XC7A200T"),
    ("0x0bad0093", None),          # not an Artix-7 this table knows
    ("", None), (None, None), ("junk", None),
])
def test_the_die_is_found_whichever_tool_read_the_idcode(idcode, part):
    """The five NeTV2s on gsm7252ps-s2 are read by openocd, which prints
    0x0362d093. The table was keyed on openFPGALoader's 0x362d093 string, so
    every one of them was labelled "die not read" with its idcode in hand."""
    assert labels.idcode_part(idcode) == part


def test_listing_titles_carry_the_identifier_on_the_label(docs):
    """A Pi row shows its serial and a USB row its MAC, so an FPGA row shows
    the DNA (or Digilent serial) the sticker is keyed on -- a listing of
    names alone read as though the DNA had never been read."""
    rows = {k: t for _h, k, t, _d, _r in labels.all_labels(docs, {"fpga"})}
    assert rows["netv2"] == "netv2-grove 0x00742c4e63b9085c"
    assert rows["arty"].startswith("arty-hawk ")
    # pi-sw2-p48's Acorn, keyed on the DNA its chain gave up. Its PCIe id is
    # its gateware's, so the model comes out "FPGA" and the die names the part.
    assert rows["acorn"].endswith(" 0x0054b48664b04854")
    assert rows["acorn"].startswith("acorn-")


def test_a_pcileech_board_gets_a_model_but_no_invented_maker_or_name():
    doc = ProbeDocument.from_dict("pi-sw1-p38", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.1", "serial": "e8387e35dbce7843",
        "revision": "b04171", "power_class": "undetermined",
        "fpga": [{"kind": "pcileech"}]}}})
    (rec,) = labels.fpga_records({"pi-sw1-p38": doc})
    assert rec.model == "PCILeech FPGA"
    assert rec.maker == ""                  # the board under the gateware is unknown
    assert rec.name is None
    assert rec.dna is None
    assert rec.gateware is None             # nothing was read from it


def test_a_pcileech_board_shows_its_gateware_as_numbers_not_a_board_name(tmp_path):
    doc = ProbeDocument.from_dict("pi-sw1-p38", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.1", "serial": "e8387e35dbce7843",
        "revision": "b04171", "power_class": "undetermined",
        "fpga": [{"kind": "pcileech", "gateware": "4.14", "gateware_id": 9}]}}})
    docs = {"pi-sw1-p38": doc}
    (rec,) = labels.fpga_records(docs)
    assert rec.gateware == "gateware v4.14  ·  FPGA id 9"
    # ...but a board with no identifier still may not be labelled. pcileech
    # gateware carries none -- its FT601 answers the part's default serial,
    # shared by every unit -- so the sticker would say nothing about which
    # board it is stuck to.
    with pytest.raises(labels.IdentifierNotReadError, match="--jtag"):
        list(labels.all_labels(docs, {"fpga"}))


def test_a_cynthion_is_keyed_on_the_die_not_the_flash_chip(docs, tmp_path):
    """An ECP5's TraceID is the number burned into the die, which is what a
    Xilinx Device DNA is, so it belongs in the same place and carries the
    same QR. The configuration flash's uid identifies a chip that could be
    unsoldered and replaced, so it sits with the other flash facts."""
    rec = {r.kind: r for r in labels.fpga_records(docs)}["cynthion"]
    assert rec.ident == "0x1b808604604e0e"
    assert rec.ident_caption == "ECP5 TraceID"
    assert rec.flash_uid == "267125df30c460de"
    assert rec.name == labels.naming.cynthion_name("0x1b808604604e0e")
    assert rec.maker == "Great Scott Gadgets"
    assert rec.model == "Cynthion r1.4"
    assert rec.part == "LFE5U-12F"          # from the revision, not from JTAG
    labels.render(docs, tmp_path / "cynthion.pdf", only={"fpga"})    # and it draws


def test_a_trace_id_is_printed_when_it_has_been_read(tmp_path, monkeypatch):
    """The ECP5's die identifier, read from rpi5-netv2 on 2026-09-21. It is
    displayed and never keyed on: reaching it costs the board's capture, so a
    name derived from it could not be recovered without going offline again."""
    doc = ProbeDocument.from_dict("h", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply",
        "fpga": [{"kind": "cynthion", "serial": "267125df30c460de", "hw_rev": "1.4",
                  "mode": "analyzer", "trace_id": "0x1b808604604e0e"}]}}})
    docs = {"h": doc}
    (rec,) = labels.fpga_records(docs)
    assert rec.trace_id == "0x1b808604604e0e"
    assert rec.ident == "0x1b808604604e0e"      # the die's own number is the key
    seen = _drawn_strings(monkeypatch, docs, {"cynthion"}, tmp_path)
    assert "0x1b808604604e0e" in seen
    assert "ECP5 TraceID" in seen
    assert not [s for s in seen if s.endswith("…")]


def test_an_acorn_is_named_and_modelled_like_every_other_board(docs, tmp_path,
                                                               monkeypatch):
    """The Acorn used to be the one FPGA that came out as a bare "FPGA" with
    no name and no model, because it alone was made to prove itself through
    PCIe. The harness names it, the die picks the variant, and the DNA names
    it -- exactly as for a NeTV2."""
    rec = {r.kind: r for r in labels.fpga_records(docs)}["acorn"]
    assert rec.model == "Acorn CLE-215+"          # XC7A200T picks the variant
    assert rec.part == "XC7A200T"
    assert rec.maker == "SQRL"
    assert rec.name == labels.naming.acorn_name("0x0054b48664b04854")
    assert rec.ident == "0x0054b48664b04854"
    seen = _drawn_strings(monkeypatch, docs, {"acorn"}, tmp_path)
    assert "Acorn CLE-215+  ·  XC7A200T" in seen
    assert not [s for s in seen if "not read" in s or s.endswith("…")]


def test_the_smaller_acorn_is_the_cle_101(docs):
    """ps1's blades carry the XC7A100T card; the die is what tells the two
    Acorn variants apart once a PCIe id can no longer be relied on."""
    doc = ProbeDocument.from_dict("pi20", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply",
        "fpga": [{"kind": "acorn", "dna": "0x0028e5c45e304854",
                  "idcode": "0x3631093"}]}}})
    (rec,) = labels.fpga_records({"pi20": doc})
    assert rec.model == "Acorn CLE-101"
    assert rec.part == "XC7A100T"


@pytest.mark.parametrize(("jedec", "vendor", "part", "size"), [
    # the Arty's, measured; one JEDEC id, two parts with the same density
    ("0x012018", "Spansion", "S25FL128S/S25FL127S", "16 MiB"),
    # pi-sw2-p48's Acorn, RDID 01 02 19 read by the Acorn deployment
    ("0x010219", "Spansion", "S25FL256S", "32 MiB"),
    ("0xef4018", "Winbond", "W25Q128", "16 MiB"),
    ("0xc22019", "Macronix", None, "32 MiB"),     # vendor and density, no part
    ("0x000000", None, None, None),
    (None, None, None, None), ("junk", None, None, None),
])
def test_a_jedec_id_gives_the_vendor_the_part_and_the_density(jedec, vendor, part, size):
    """The third byte is a power of two, so the density falls out of any
    id; the vendor is the first byte; only the part needs a table."""
    got = labels.flash_from_jedec(jedec)
    assert (got["vendor"], got["part"], got["size"]) == (vendor, part, size)


@pytest.mark.parametrize(("jedec", "how"), [
    # measured on the fleet 2026-09-21, each read twice and identical
    ("0x20ba18", "read-uid"),    # Micron N25Q128, 112 bits in the extended 0x9F
    ("0x010219", "otp"),         # Spansion S25FL256S, 128-bit factory number in OTP
    ("0x012018", "otp"),         # S25FL128S/S25FL127S, the same mechanism
    ("0xef4018", "read-uid"),    # Winbond W25Q, 64 bits via 0x4B
    # Macronix: a factory ESN exists only on a factory-locked part, and both
    # NeTV2s read security register 0x00, so theirs have none
    ("0xc22017", "otp-if-factory-locked"),
    ("0xabcdef", "unknown"),     # a part nobody here has met
])
def test_how_a_flash_unique_id_is_read_is_a_property_of_the_part(jedec, how):
    """Silence about a flash uid means two different things: not read yet, or
    no such thing to read. A Macronix part has no factory unique-ID command
    at all, so a blank there is the answer and not a gap -- and 0x4B is not
    one instruction either (Read Unique ID on a Winbond, OTP read with a
    three-byte address on a Spansion), so the method has to be per part."""
    assert labels.flash_from_jedec(jedec)["uid_read_with"] == how


def test_every_fpga_label_renders_its_flash_the_same_way(docs, tmp_path, monkeypatch):
    """One flash block, in one place, on every FPGA label that has flash
    facts -- and absent, not placeholdered, on the ones that do not."""
    seen = _drawn_strings(monkeypatch, docs, {"arty"}, tmp_path)
    assert "Spansion S25FL128S/S25FL127S  ·  16 MiB" in seen
    assert "flash" in seen
    # a Cynthion's configuration flash: its uid is free over USB, its JEDEC
    # id is not read at all, so the block carries the row it has and no other
    cyn = _drawn_strings(monkeypatch, docs, {"cynthion"}, tmp_path)
    assert "267125df30c460de" in cyn
    assert "uid" in cyn
    # nothing read, nothing claimed
    netv2 = _drawn_strings(monkeypatch, docs, {"netv2"}, tmp_path)
    assert not [s for s in netv2 if "flash" in s.lower()]
    assert not [s for s in netv2 + cyn + seen if "not read" in s]


def test_the_other_boards_still_say_device_dna(docs):
    """The four existing kinds must be untouched: same foot, same caption."""
    recs = {r.kind: r for r in labels.fpga_records(docs)}
    assert recs["netv2"].ident == "0x00742c4e63b9085c"
    assert recs["netv2"].ident_caption == "Device DNA"
    assert recs["acorn"].ident == "0x0054b48664b04854"
    assert recs["acorn"].ident_caption == "Device DNA"
    assert recs["acorn"].part == "XC7A200T"


def test_nothing_on_any_fpga_label_says_it_was_not_read(docs, tmp_path, monkeypatch):
    """A row that says "not read" is the placeholder this package exists to
    avoid. A fact that was not read is left off the label rather than
    announced on it -- the Arty's flash row said "flash not read" on a board
    whose flash simply had not been asked for."""
    # pi3 on ps1, read 2026-09-21 with --jtag and not --flash, so its flash
    # part is simply not among the things that were read.
    bare_arty = ProbeDocument.from_dict("pi3", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply",
        "fpga": [{"kind": "arty", "serial": "210319A43AD3",
                  "dna": "0x0064f5483229085c", "idcode": "0x362d093"}]}}})
    for kind, where in (("cynthion", docs), ("netv2", docs), ("acorn", docs),
                        ("arty", docs), ("arty", {"pi3": bare_arty})):
        seen = _drawn_strings(monkeypatch, where, {kind}, tmp_path)
        assert not [s for s in seen if "not read" in s], kind
        assert not [s for s in seen if s.endswith("…")], kind


def test_a_board_named_only_by_its_die_does_not_say_it_twice(tmp_path, monkeypatch):
    """A chain on a harness nobody has described still names no board, so the
    headline falls back to the model -- which is "FPGA" and printed twice,
    once as the headline and again in the row below."""
    doc = ProbeDocument.from_dict("h", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply",
        "fpga": [{"kind": "jtag", "dna": "0x0054b48664b04854",
                  "idcode": "0x13636093"}]}}})
    seen = _drawn_strings(monkeypatch, {"h": doc}, {"jtag"}, tmp_path)
    assert "XC7A200T" in seen            # the die is the headline
    assert "FPGA  ·  XC7A200T" not in seen
    assert seen.count("FPGA") <= 1


def test_a_board_whose_identifier_was_not_read_is_fatal(tmp_path):
    """The whole point of the tool is that nobody transcribes hex by hand, so
    a label with the identifier missing must never be generated. It fails
    loudly at generation time, which is the last moment the missing read is
    still cheap to do."""
    doc = ProbeDocument.from_dict("pi-sw2-p47", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply", "fpga": [{"kind": "acorn"}]}}})
    with pytest.raises(labels.IdentifierNotReadError) as excinfo:
        list(labels.all_labels({"pi-sw2-p47": doc}, {"fpga"}))
    message = str(excinfo.value)
    assert "pi-sw2-p47" in message          # which machine to go to
    assert "acorn" in message               # which board on it
    assert "Device DNA" in message          # which identifier is missing
    assert "--jtag" in message              # and how to read it


def test_the_error_names_the_flag_that_reads_each_identifier(tmp_path):
    """A Cynthion's uid and its TraceID are read by different commands, so
    being told the wrong one wastes a trip to the rack."""
    doc = ProbeDocument.from_dict("rpi5-netv2", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply",
        "fpga": [{"kind": "cynthion", "hw_rev": "1.4", "mode": "apollo"}]}}})
    with pytest.raises(labels.IdentifierNotReadError, match="--force-offline"):
        list(labels.all_labels({"rpi5-netv2": doc}, {"fpga"}))


def test_a_cynthion_in_apollo_mode_is_not_keyed_on_the_wrong_chip(docs):
    """In Apollo mode the serial belongs to the debug controller. A label
    keyed on it would name one board two different things."""
    doc = ProbeDocument.from_dict("h", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.0", "serial": "s", "revision": "c04170",
        "power_class": "usbc-supply",
        "fpga": [{"kind": "cynthion", "hw_rev": "1.4", "mode": "apollo"}]}}})
    (rec,) = labels.fpga_records({"h": doc})
    assert rec.ident is None
    assert rec.name is None                  # unnameable until its uid is read
    assert rec.model == "Cynthion r1.4"


def test_only_can_name_a_single_fpga_kind(docs):
    """--only fpga on the rig yields both its boards; one sticker at a time
    needs the kind itself."""
    both = [k for _h, k, _t, _d, _r in labels.all_labels(docs, {"fpga"})
            if k in ("netv2", "cynthion")]
    assert sorted(both) == ["cynthion", "netv2"]
    rows = list(labels.all_labels(docs, {"cynthion"}))
    assert [k for _h, k, _t, _d, _r in rows] == ["cynthion"]


def _drawn_strings(monkeypatch, docs, only, tmp_path):
    """Every string that actually reaches the canvas, after fitting has had
    its way with it -- so an elided value is visible to a test."""
    seen: list[str] = []
    real = labels.Label.text

    def spy(self, x, y, s, *args, **kwargs):
        seen.append(s)
        return real(self, x, y, s, *args, **kwargs)

    monkeypatch.setattr(labels.Label, "text", spy)
    labels.render(docs, tmp_path / "spy.pdf", only=only)
    return seen


def test_a_label_records_nothing_a_reflash_would_change(docs, tmp_path, monkeypatch):
    """A label carries only what cannot change. Which gateware answered is
    the most changeable thing about an FPGA board -- it survives until the
    next `cynthion flash` -- so it settles what the probe may trust and then
    stays off the sticker."""
    seen = _drawn_strings(monkeypatch, docs, {"cynthion"}, tmp_path)
    assert "gateware" not in seen
    assert "USB Analyzer" not in seen


def test_a_cynthion_carries_the_makers_mark_not_its_name_in_type(docs, tmp_path,
                                                                 monkeypatch):
    """Like a NeTV2's Alphamax and an Arty's Digilent: the mark identifies
    the maker, and the name in type is only the fallback when a mark is
    missing."""
    seen = _drawn_strings(monkeypatch, docs, {"cynthion"}, tmp_path)
    assert "Great Scott Gadgets" not in seen
    assert labels.artwork("great-scott-gadgets.png") is not None


def test_nothing_on_a_cynthion_label_is_elided(docs, tmp_path, monkeypatch):
    """`fit` cuts with an ellipsis rather than running off the label, which
    is right for a board name and wrong for a caption: "ECP5 config flash
    UI…" reads as a typo, and the instruction to write the uid in is gone."""
    seen = _drawn_strings(monkeypatch, docs, {"cynthion"}, tmp_path)
    assert "ECP5 TraceID" in seen
    assert not [s for s in seen if s.endswith("…")]





def test_fpga_records_named_and_typed(docs):
    recs = {r.kind: r for r in labels.fpga_records(docs)}
    assert recs["netv2"].name == "netv2-grove"
    assert recs["netv2"].part == "XC7A100T"
    assert recs["arty"].name == "arty-hawk"
    assert recs["arty"].model == "Arty A7-35T"
    assert recs["arty"].flash == "Spansion S25FL128S/S25FL127S  ·  16 MiB"
    assert recs["acorn"].maker == "SQRL"


def test_tinytapeout_records(docs):
    breakout, tt06, gf = labels.tinytapeout_records(docs)
    assert tt06.headline == "TT06"
    assert tt06.subtitle == "ASIC  ·  sky130"
    assert tt06.url == "https://tinytapeout.com/chips/tt06/"
    assert tt06.demoboard_text == "TT06+  ·  Rev 2.0.1"
    assert tt06.commit == "0f5a1b2c"
    assert tt06.usb_serial == "E6614C311B7A7A37"
    assert tt06.mcu == "RP2040"
    # The spreadsheet's own hex, not the one generic pink in COLOURS: a
    # swatch is meant to be a sample of the board it names.
    assert (tt06.chip_colour, tt06.demoboard_colour) == ("#c98599", "#c98599")
    assert (tt06.chip_colour_name, tt06.demoboard_colour_name) == ("pink", "pink")
    assert gf.headline == "TTGF0p2"
    assert gf.subtitle == "ASIC  ·  gf180mcu"
    assert gf.demoboard_text == "TTDBv3  ·  Rev 3.2"
    assert gf.mcu == "RP2350"
    # Both of this one's boxes are filled: the shuttle is in the sheet with
    # its own hex, and it is listed under the DB ETR v3.2's "Used by", so
    # neither box needs the revision fallback to be found.
    assert (gf.chip_colour, gf.chip_silk) == ("#c6cad1", "#24252a")
    assert (gf.chip_colour_name, gf.chip_silk_name) == ("white", "black")
    assert (gf.demoboard_colour, gf.demoboard_silk) == ("#c98599", "#bae7c6")
    assert (gf.demoboard_colour_name, gf.demoboard_silk_name) == ("purple", "teal")

    # The FPGA breakout has no shuttle at all, so neither of its boards can
    # be found by one: the demo board is found by its revision, and the
    # carrier by being the sheet's one FPGA rather than a packaged die.
    assert breakout.headline == "FPGA"
    assert breakout.subtitle == "FPGA breakout, no ASIC"
    assert breakout.demoboard_text == "TTDBv3  ·  Rev 3.2"
    assert breakout.usb_serial == "4df39a7a6856f86f"
    assert (breakout.chip_colour, breakout.chip_silk) == ("#c36eb1", "#c7dbce")
    # "light green" would elide to "light gr..." in the box beside the
    # swatch, and the swatch already shows the shade.
    assert (breakout.chip_colour_name, breakout.chip_silk_name) == ("purple", "green")
    assert (breakout.demoboard_colour, breakout.demoboard_silk) == ("#c98599", "#bae7c6")


def test_plain_colour_drops_only_the_shade():
    from rpi_hwid import tt_boards

    assert tt_boards.plain_colour("light green") == "green"
    assert tt_boards.plain_colour("dark blue") == "blue"
    assert tt_boards.plain_colour("pink") == "pink"
    # the qualifier on its own is the whole name, so it stays
    assert tt_boards.plain_colour("light") == "light"
    assert tt_boards.plain_colour(None) is None
    # only the shade goes: a two-word colour that is not one keeps both
    assert tt_boards.plain_colour("burnt orange") == "burnt orange"


def test_plain_colour_does_not_change_the_hex(monkeypatch):
    """The palette is keyed on the sheet's spelling, so it must be asked
    before the qualifier is dropped."""
    from rpi_hwid import tt_boards

    monkeypatch.setattr(tt_boards, "shuttle_boards", lambda *a, **k: {
        "chip": {"colour": "Light Green", "colour_hex": None, "silk": None, "silk_hex": None},
        "demoboard": None})
    got = tt_boards.colours("whatever", {"light green": "#abcdef", "green": "#000000"})
    assert got["chip"] == "#abcdef", "looked up under the sheet's own name"
    assert got["chip_name"] == "green"


def test_demoboard_text_normalises_both_sdk_forms():
    assert labels.demoboard_text("TT06+", "v2.0.1") == "TT06+  ·  Rev 2.0.1"
    assert labels.demoboard_text("TTDBv3 [3.3]", None) == "TTDBv3  ·  Rev 3.3"
    assert labels.demoboard_text("TTDBv3 [3.3]", "v9") == "TTDBv3  ·  Rev 3.3"
    assert labels.demoboard_text("TT04/TT05", None) == "TT04/TT05"
    assert labels.demoboard_text(None, "v1.2.1") == "Rev 1.2.1"
    assert labels.demoboard_text(None, None) == "not read"


def test_tinytapeout_records_without_a_rom_or_with_the_fpga_breakout():
    def doc(board):
        return ProbeDocument.from_dict("h", {"verdict": {"summary": {
            "model": "m", "serial": "s", "revision": "c03114", "power_class": "p",
            "tinytapeout": [board]}}})
    (fpga,) = labels.tinytapeout_records({"h": doc({"chip": "fpga", "usb_serial": "E1"})})
    assert fpga.headline == "FPGA"
    assert fpga.subtitle == "FPGA breakout, no ASIC"
    assert fpga.url == "https://tinytapeout.com/chips/"
    assert fpga.demoboard_text == "not read"
    (blank,) = labels.tinytapeout_records({"h": doc({"demoboard": "TT04/TT05"})})
    assert blank.headline == "TT"
    assert blank.subtitle == "shuttle not read"
    assert blank.demoboard_text == "TT04/TT05"
    assert blank.mcu is None
    assert blank.commit is None
    assert blank.usb_serial is None
    # a shuttle the table has no page for falls back to the chips index
    (t35,) = labels.tinytapeout_records({"h": doc({"shuttle": "tt03p5", "chip": "asic"})})
    assert t35.url == "https://tinytapeout.com/chips/"
    assert t35.demoboard_text == "Rev 1.2.1"
    assert t35.chip_colour == "#5e355f", "TT03p5's own purple, from the sheet"


def test_usb_records(docs):
    linksys, wifi, asix, old_wifi, cm_eth, cm_wifi = labels.usb_records(docs)
    assert asix.title == "ASIX Elec. Corp. AX88179"
    assert ("USB", "3.00 SS 5 Gbit/s") in asix.lines
    assert asix.mac == "00:0e:c6:82:b5:e1"
    assert asix.kind == "ethernet"
    assert linksys.title == "Linksys Linksys USB3GIGV1"
    assert linksys.mac == "60:38:e0:e3:56:4f"
    # A wireless adapter's kind is what swaps the RJ45 glyph for the WiFi
    # one, so it is worth pinning.
    assert wifi.kind == "wifi"
    assert wifi.title == "Realtek 802.11ac NIC"
    assert ("USB", "2.00 HS 480 Mbit/s") in wifi.lines
    assert ("driver", "rtw88_8821cu") in wifi.lines
    # The older boards reach the network entirely through adapters, and each
    # one gets its own label: a radio for the Model B, which has none of its
    # own, and both a wired port and a radio for the Compute Module 1.
    assert (old_wifi.kind, old_wifi.title) == ("wifi", "Realtek 802.11n NIC")
    assert (cm_eth.kind, cm_eth.mac) == ("ethernet", "00:e0:4c:68:36:95")
    assert (cm_wifi.kind, cm_wifi.mac) == ("wifi", "6c:1f:f7:51:2d:d6")
    # The Model B's own wired port is NOT among them: it is on an internal
    # USB bus, but its MAC is the one the board derives from its own serial,
    # so it belongs on the Pi's label, not on a dongle's.
    assert "b8:27:eb:0a:ee:d6" not in [u.mac for u in labels.usb_records(docs)]


def test_all_labels_order_and_count(docs):
    rows = [(h, k) for h, k, _t, _d, _r in labels.all_labels(docs, labels.KINDS)]
    # Host by host in sorted order, and within a host the board first, then
    # what is attached to it: FPGA, Tiny Tapeout, then the USB adapters.
    assert rows == [
        ("pi-sw1-p10", "rpi"),
        ("pi-sw2-p16", "rpi"), ("pi-sw2-p16", "arty"),
        ("pi-sw2-p22", "opi"),
        ("pi-sw2-p33", "rpi"), ("pi-sw2-p33", "tt"),
        ("pi-sw2-p37", "rpi"), ("pi-sw2-p37", "usb"),
        ("pi-sw2-p47", "rpi"),
        ("pi-sw2-p48", "rpi"), ("pi-sw2-p48", "acorn"),
        ("rpi4-tt", "rpi"), ("rpi4-tt", "tt"), ("rpi4-tt", "tt"),
        ("rpi5-433mhz", "rpi"), ("rpi5-433mhz", "usb"),
        # this rig carries two FPGA boards, and both come out with it
        ("rpi5-netv2", "rpi"), ("rpi5-netv2", "netv2"), ("rpi5-netv2", "cynthion"),
        ("rpi5-netv2", "usb"),
        ("rpib-serial", "rpi"), ("rpib-serial", "usb"),
        ("rpicm1-serial", "rpi"), ("rpicm1-serial", "usb"), ("rpicm1-serial", "usb"),
        ("rpiz-serial", "rpi"),
    ]
    titles = [t for _h, _k, t, _d, _r in labels.all_labels(docs, {"tt"})]
    assert titles == ["FPGA 4df39a7a6856f86f", "TT06 E6614C311B7A7A37",
                      "TTGF0p2 E66360B8A3C1D5F2"]
    only_opi = [k for _h, k, _t, _d, _r in labels.all_labels(docs, {"opi"})]
    assert only_opi == ["opi"]


def test_order_puts_named_hosts_first_and_keeps_groups_whole(docs):
    # A caller that knows which switch port each host is on can ask for that
    # sequence; rpi-hwid has no idea what a switch is, so it only obeys.
    wanted = ["rpiz-serial", "rpi5-netv2", "pi-sw2-p16"]
    rows = [(h, k) for h, k, _t, _d, _r in
            labels.all_labels(docs, labels.KINDS, order=wanted)]
    hosts = [h for h, _k in rows]
    assert hosts[:1] == ["rpiz-serial"]
    # its Pi, NeTV2, Cynthion and dongle
    assert hosts[1:5] == ["rpi5-netv2"] * 4
    assert hosts[5:7] == ["pi-sw2-p16"] * 2
    # anything unnamed still follows in host-name order
    rest = hosts[7:]
    assert rest == sorted(rest)
    # and the same labels come out, just rearranged
    assert sorted(rows) == sorted(
        (h, k) for h, k, _t, _d, _r in labels.all_labels(docs, labels.KINDS))


def test_every_hosts_labels_are_contiguous(docs):
    # The point of the grouping: one machine's labels are never split by
    # another's, whatever the mix of kinds asked for.
    for only in (labels.KINDS, {"rpi", "opi", "usb"}, {"rpi", "fpga"}, {"usb", "tt"}):
        hosts = [h for h, _k, _t, _d, _r in labels.all_labels(docs, set(only))]
        seen, runs = set(), []
        for h in hosts:
            if not runs or runs[-1] != h:
                assert h not in seen, f"{h} appears in two runs with only={only}"
                seen.add(h)
                runs.append(h)
        assert runs == sorted(runs)


def test_board_record_carries_the_pi5_extras(docs):
    fitted = labels.board_record(docs["pi-sw2-p47"])
    assert (fitted.fan, fitted.rtc_battery) == (True, True)
    # a Pi 5 that answered "no" to both, which is not the same as not asking
    bare = labels.board_record(docs["rpi5-netv2"])
    assert (bare.fan, bare.rtc_battery) == (False, False)
    # a 3B+ has neither signal to give, so neither is False
    older = labels.board_record(docs["pi-sw1-p10"])
    assert (older.fan, older.rtc_battery) == (None, None)


@pytest.mark.parametrize(("host", "expected"), [
    ("pi-sw2-p47", ["fan", "clock"]),   # both fitted
    ("rpi5-433mhz", ["fan"]),           # a fan, no backup cell
    ("rpi5-netv2", []),                 # a Pi 5 that answered no to both
    ("pi-sw1-p10", []),                 # not a Pi 5: neither signal exists
])
def test_pi5_icons_are_drawn_only_for_what_was_found(docs, tmp_path, monkeypatch,
                                                     host, expected):
    """False and None both draw nothing, and must.

    A fan glyph on a board that has no fan header would be a claim about
    hardware invented from a missing field, which is the one thing a label
    must never do.
    """
    drawn = []
    monkeypatch.setattr(labels, "mark_fan", lambda *a: drawn.append("fan"))
    monkeypatch.setattr(labels, "mark_clock", lambda *a: drawn.append("clock"))
    labels.render({host: docs[host]}, tmp_path / "icons.pdf", only={"rpi"})
    assert drawn == expected


def test_an_unreadable_board_does_not_cost_its_dongles_their_labels(docs, capsys):
    # The revision code names the Pi; it says nothing about what is plugged
    # into it, so a board that cannot be named must not take the adapters
    # sharing its host down with it.
    from dataclasses import replace

    doc = copy.deepcopy(docs["rpicm1-serial"])
    doc.summary = replace(doc.summary, revision="000a")     # a code with no model listed
    broken = dict(docs, **{"rpicm1-serial": doc})
    rows = [(h, k) for h, k, _t, _d, _r in labels.all_labels(broken, labels.KINDS)]
    assert ("rpicm1-serial", "rpi") not in rows
    assert rows.count(("rpicm1-serial", "usb")) == 2
    assert "rpicm1-serial: 000a: no model is listed" in capsys.readouterr().err


def test_render_and_decode_every_qr(data_dir, tmp_path):
    out = tmp_path / "labels.pdf"
    rc = cli_main(["labels", "--data", str(data_dir), "--out", str(out), "--outline"])
    assert rc == 0
    assert out.exists()
    assert out.stat().st_size > 10_000

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        pytest.skip("pdftoppm not installed")
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    subprocess.run([pdftoppm, "-r", "300", "-png", str(out), str(tmp_path / "page")], check=True)
    got = set()
    for png in sorted(glob.glob(str(tmp_path / "page-*.png"))):
        got |= {b.text for b in zxingcpp.read_barcodes(Image.open(png))}
    want = {
        "0x00742c4e63b9085c", "0x00628502251ea85c",       # netv2 DNA, arty DNA
        "0x012018",                                      # the arty's flash, small QR
        # The Cynthion keys on its ECP5 TraceID, the number in the die, which
        # is where a Xilinx part carries its Device DNA. Its configuration
        # flash's uid is the flash chip's own id and gets the small flash QR.
        "0x1b808604604e0e", "267125df30c460de",
        "2c:cf:67:16:bd:98", "2c:cf:67:16:bd:99",         # rpi5-netv2
        "b8:27:eb:e3:e7:e4", "b8:27:eb:b6:b2:b1",         # 3B+, radio derived
        "e4:5f:01:96:f8:a5", "e4:5f:01:96:f8:a7",         # arty host
        "00:e0:4c:36:0b:0a", "b8:27:eb:02:a3:24",         # zero with bonnet
        "98:fe:54:13:f5:75",                              # acorn host
        "02:81:2e:b7:a3:4e",                              # the Orange Pi PC
        "dc:a6:32:8f:2b:11", "dc:a6:32:8f:2b:12",         # the Tiny Tapeout host
        "e4:5f:01:97:0e:77", "e4:5f:01:97:0e:79",         # the FPGA breakout's host
        "e4:5f:01:97:1f:7e",                              # the Linksys adapter's host
        "88:a2:9e:45:c5:5d", "88:a2:9e:45:c5:5e",         # the WiFi adapter's host
        "b8:27:eb:0a:ee:d6",                              # the Model B's own wired port
        "00:0e:c6:82:b5:e1",                              # the dongles
        "60:38:e0:e3:56:4f", "6c:1f:f7:51:2e:a3",
        "80:3f:5d:13:8e:67",                              # the Model B's radio
        "00:e0:4c:68:36:95", "6c:1f:f7:51:2d:d6",         # both of the CM1's
        "https://tinytapeout.com/chips/tt06/",            # the chip pages
        "https://tinytapeout.com/chips/ttgf0p2/",
        "https://tinytapeout.com/chips/",                 # no chip: the index
        "E6614C311B7A7A37", "E66360B8A3C1D5F2",           # the demo boards' RP2 ids
        "4df39a7a6856f86f",
        # the board serials, as a small QR at the top of each board label's spine
        "d88100008543dc30", "000000004fe3e7e4", "10000000ce8e3593",
        "000000005157f671", "c36b093f773d46b8", "100000003a7e1c9b",
        "02c000812eb7a34e", "1000000085948b10", "10000000613a4524",
        "7070c78090a6d6d8", "00000000110aeed6", "0000000067bdbf54",
        # pi-sw2-p48, the live Acorn CLE-215+ read 2026-09-21
        "0cd35697db04a4ab", "88:a2:9e:45:85:77", "0x0054b48664b04854",
    }
    # b8:27:eb:5f:bb:83 is deliberately absent: it is what the Broadcom rule
    # derives from the Model B's serial, and that board has no radio to
    # carry it. A MAC is only printed where something has one.
    assert "b8:27:eb:5f:bb:83" not in got
    assert got == want


def test_list_and_names_cli(data_dir, capsys):
    assert cli_main(["labels", "--data", str(data_dir), "--list"]) == 0
    out = capsys.readouterr().out
    assert "netv2-grove" in out
    assert "arty-hawk" in out
    assert "opi    Orange Pi PC 1 GB 02c000812eb7a34e" in out
    assert "rpi    Pi 5 4 GB d88100008543dc30" in out
    assert "tt     TT06 E6614C311B7A7A37" in out
    assert cli_main(["labels", "--data", str(data_dir), "--list", "--only", "tt"]) == 0
    assert capsys.readouterr().out.count("\n") == 3
    assert cli_main(["name", "--netv2", "0x00742c4e63b9085c", "--arty", "210319B301DE",
                     "--cynthion", "267125df30c460de"]) == 0
    out = capsys.readouterr().out
    assert "netv2-grove" in out
    assert "arty-hawk" in out
    assert "cynthion-alidade  267125df30c460de" in out
    assert cli_main(["revision", "c04170"]) == 0
    assert "Raspberry Pi 5, 4 GB, Rev 1.0" in capsys.readouterr().out


def test_awkward_records_still_fit(docs, tmp_path, monkeypatch):
    """A Pi with no raspberry artwork (the mark's box stays blank and the
    bands stay where they are) and a three-board HAT line (elided at the
    size floor) render without overflowing; `fit` cuts rather than runs
    off."""
    from dataclasses import replace

    from reportlab.pdfgen import canvas

    monkeypatch.setattr(
        labels, "artwork",
        lambda name: None if name == "raspberry-pi.svg" else labels.PACKAGE_ARTWORK + "/" + name,
    )
    long_hat = ("Waveshare PoE M.2 HAT+ (B)", "Pmod HAT Adaptor", "Google VoiceBonnet")
    doc = docs["pi-sw2-p47"]
    doc.summary = replace(doc.summary, header=long_hat)
    n, sheets = labels.render({"h": doc}, tmp_path / "x.pdf", only=("rpi",))
    assert (n, sheets) == (1, 1)

    c = canvas.Canvas(str(tmp_path / "y.pdf"))
    lab = labels.Label(c, 0, 0)
    size = lab.fit(0, 0, "x" * 200, labels.SANS, 8, 30 * labels.mm)
    assert size == 5.5  # stopped at the floor, then elided
    assert lab.width("x" * 200, labels.SANS, 5.5) > 30 * labels.mm


def test_a_board_with_no_label_design_is_skipped(docs, capsys):
    """A document from a board this package has no label for (the probe's
    "other") is skipped with a note, not fatal to the whole run."""
    from dataclasses import replace

    doc = copy.deepcopy(docs["pi-sw2-p22"])
    doc.summary = replace(doc.summary, model="MinnowBoard Turbot", compatible="")
    with_other = dict(docs, minnow=doc)
    kinds = [k for _h, k, _t, _d, _r in labels.all_labels(with_other, {"rpi", "opi"})]
    assert kinds == [k for _h, k, _t, _d, _r in labels.all_labels(docs, {"rpi", "opi"})]
    assert "minnow: not a board this package labels" in capsys.readouterr().err


def test_a_board_whose_revision_cannot_be_read_costs_one_label_not_the_sheet(docs, capsys):
    """Every board is asked for in one pass, so a Pi whose revision code
    names no model has to be skipped the way an unlabellable board is --
    with the reason, and without taking the other labels with it."""
    from dataclasses import replace

    doc = copy.deepcopy(docs["rpib-serial"])
    doc.summary = replace(doc.summary, revision="000a")     # a code with no model listed
    with_bad = dict(docs, unreadable=doc)
    kinds = [k for _h, k, _t, _d, _r in labels.all_labels(with_bad, {"rpi", "opi"})]
    assert kinds == [k for _h, k, _t, _d, _r in labels.all_labels(docs, {"rpi", "opi"})]
    err = capsys.readouterr().err
    assert "unreadable: 000a: no model is listed" in err
    assert "skipped" in err


def test_tt_label_tells_an_empty_rom_commit_from_an_unread_rom(docs, tmp_path):
    """A TT03p5's chip ROM carries the shuttle name and nothing else
    (seen on pi-sw2-p3), so the label must not claim the ROM went unread.
    Both wordings are drawn; this checks the record reaches them."""
    from dataclasses import replace

    doc = copy.deepcopy(docs["rpi4-tt"])
    boards = doc.summary.tinytapeout
    doc.summary = replace(doc.summary, tinytapeout=(
        replace(boards[0], shuttle="tt03p5", commit=None),
        replace(boards[1], shuttle=None, commit=None),
    ))
    recs = labels.tinytapeout_records({"h": doc})
    assert (recs[0].shuttle, recs[0].commit) == ("tt03p5", None)
    assert (recs[1].shuttle, recs[1].commit) == (None, None)
    n, _sheets = labels.render({"h": doc}, tmp_path / "tt.pdf", only=("tt",))
    assert n == 2


def test_tt_label_carries_the_shuttle_marks(docs, tmp_path, monkeypatch):
    """The shuttle's operator and foundry marks reach the record, and a
    mark whose artwork is missing is skipped rather than leaving a hole."""
    from dataclasses import replace

    doc = copy.deepcopy(docs["rpi4-tt"])
    boards = doc.summary.tinytapeout
    doc.summary = replace(doc.summary, tinytapeout=(
        replace(boards[0], shuttle="tt06"),         # efabless on skywater
        replace(boards[1], shuttle="ttgf26a"),      # wafer.space on globalfoundries
    ))
    recs = labels.tinytapeout_records({"h": doc})
    assert recs[0].marks == ("efabless", "skywater")
    assert recs[1].marks == ("wafer-space", "globalfoundries")
    for name in recs[0].marks + recs[1].marks:
        assert labels.mark_file(name), name

    _drawn, full = labels.marks_widths(recs[0].marks, 3 * labels.mm)
    _drawn, one = labels.marks_widths(("efabless", "nonesuch"), 3 * labels.mm)
    assert 0 < one < full
    assert labels.marks_widths(("nonesuch",), 3 * labels.mm) == ([], 0)
    n, _sheets = labels.render({"h": doc}, tmp_path / "tt.pdf", only=("tt",))
    assert n == 2


def shuttle_mark_names():
    """Every foundry and shuttle-operator mark the shuttle table names."""
    from rpi_hwid import tinytapeout as tt_data

    names = sorted({n for marks in tt_data.SHUTTLE_MARKS.values() for n in marks})
    assert names, "the shuttle table names no marks"
    return names


def test_every_shuttle_mark_takes_the_same_square_cell():
    """A row of marks is square cells plus gaps, whatever shape the marks
    themselves are: each is fitted into a `height` x `height` box, so no
    mark can take more of the row than its neighbours."""
    names = shuttle_mark_names()
    h, gap = 3 * labels.mm, 1.2 * labels.mm
    for name in names:
        drawn, total = labels.marks_widths((name,), h, gap)
        assert drawn, name
        assert total == pytest.approx(h), name
    drawn, total = labels.marks_widths(tuple(names), h, gap)
    assert len(drawn) == len(names)
    assert total == pytest.approx(h * len(names) + gap * (len(names) - 1))


def test_no_shuttle_mark_is_a_wordmark():
    """Fitting keeps a mark inside its cell but cannot make a wide one
    legible: a 4.5:1 wordmark drew a quarter of its cell's height beside
    the symbols it sits with, which is why the Efabless artwork is the red
    "e" alone rather than the "efabless.com" lockup."""
    for name in shuttle_mark_names():
        path = labels.mark_file(name)
        assert path, name
        wide = 1 / labels.mark_aspect(path)          # width / height
        assert 0.6 <= wide <= 1.4, (name, round(wide, 3))
