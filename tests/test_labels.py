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
    assert p.header_note is None


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
    p = labels.board_record(docs["opi1pc-b"])
    assert p.kind == "opi"
    assert p.short == "Orange Pi PC"
    assert p.title == "Orange Pi PC"
    assert p.subtitle == "1 GB  ·  Allwinner H3  ·  dt orangepi-pc"
    assert p.mark == "orange-pi.png"
    assert p.serial == "02c00181e1ce7d46"
    assert p.macs == (("eth", "02:81:e1:ce:7d:46"),)
    assert p.wlan_note == "no radio"
    assert p.eth_note is None
    assert p.header == ()
    assert p.header_note == "40-pin"
    assert p.hat_uuid is None


def test_fpga_records_named_and_typed(docs):
    recs = {r.kind: r for r in labels.fpga_records(docs)}
    assert recs["netv2"].name == "netv2-grove"
    assert recs["netv2"].part == "XC7A100T"
    assert recs["arty"].name == "arty-hawk"
    assert recs["arty"].model == "Arty A7-35T"
    assert recs["arty"].flash == "S25FL128S/127S"
    assert recs["acorn"].name is None
    assert recs["acorn"].maker == "SQRL"


def test_tinytapeout_records(docs):
    tt06, ihp = labels.tinytapeout_records(docs)
    assert tt06.headline == "TT06"
    assert tt06.subtitle == "ASIC  ·  sky130"
    assert tt06.url == "https://tinytapeout.com/chips/tt06/"
    assert tt06.demoboard_text == "TT06+  ·  Rev 2.0.1"
    assert tt06.commit == "0f5a1b2c"
    assert tt06.usb_serial == "E6614C311B7A7A37"
    assert tt06.mcu == "RP2040"
    assert (tt06.chip_colour, tt06.demoboard_colour) == ("#f28cb3", "#f28cb3")
    assert (tt06.chip_colour_name, tt06.demoboard_colour_name) == ("pink", "pink")
    assert ihp.headline == "TTIHP25a"
    assert ihp.subtitle == "ASIC  ·  ihp-sg13g2"
    assert ihp.demoboard_text == "TTDBv3  ·  Rev 3.2"
    assert ihp.mcu == "RP2350"
    assert ihp.chip_colour is None
    assert ihp.chip_colour_name is None
    assert ihp.demoboard_colour is None


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
    assert t35.chip_colour == "#5c2d91"


def test_usb_records(docs):
    (u,) = labels.usb_records(docs)
    assert u.title == "ASIX Elec. Corp. AX88179"
    assert ("USB", "3.00 SS 5 Gbit/s") in u.lines
    assert u.mac == "00:0e:c6:82:b5:e1"


def test_all_labels_order_and_count(docs):
    kinds = [k for k, _t, _d, _r in labels.all_labels(docs, labels.KINDS)]
    # FPGA boards first in sorted-host order, then Tiny Tapeout boards, then
    # one board per document (the Orange Pi host sorts first), then adapters
    assert kinds == ["arty", "acorn", "netv2", "tt", "tt", "opi",
                     "rpi", "rpi", "rpi", "rpi", "rpi", "rpi", "usb"]
    titles = [t for k, t, _d, _r in labels.all_labels(docs, {"tt"})]
    assert titles == ["TT06 E6614C311B7A7A37", "TTIHP25a E66360B8A3C1D5F2"]
    only_opi = [k for k, _t, _d, _r in labels.all_labels(docs, {"opi"})]
    assert only_opi == ["opi"]


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
        "2c:cf:67:16:bd:98", "2c:cf:67:16:bd:99",         # rpi5-netv2
        "b8:27:eb:e3:e7:e4", "b8:27:eb:b6:b2:b1",         # 3B+, radio derived
        "e4:5f:01:96:f8:a5", "e4:5f:01:96:f8:a7",         # arty host
        "00:e0:4c:36:0b:0a", "b8:27:eb:02:a3:24",         # zero with bonnet
        "98:fe:54:13:f5:75",                              # acorn host
        "02:81:e1:ce:7d:46",                              # the Orange Pi PC
        "dc:a6:32:8f:2b:11", "dc:a6:32:8f:2b:12",         # the Tiny Tapeout host
        "00:0e:c6:82:b5:e1",                              # the dongle
        "https://tinytapeout.com/chips/tt06/",            # the chip pages
        "https://tinytapeout.com/chips/ttihp25a/",
        "E6614C311B7A7A37", "E66360B8A3C1D5F2",           # the demo boards' RP2 ids
        # the board serials, as a small QR at the top of each board label's spine
        "d88100008543dc30", "000000004fe3e7e4", "10000000ce8e3593",
        "000000005157f671", "c36b093f773d46b8", "100000003a7e1c9b",
        "02c00181e1ce7d46",
    }
    assert got == want


def test_list_and_names_cli(data_dir, capsys):
    assert cli_main(["labels", "--data", str(data_dir), "--list"]) == 0
    out = capsys.readouterr().out
    assert "netv2-grove" in out
    assert "arty-hawk" in out
    assert "opi    Orange Pi PC 1 GB 02c00181e1ce7d46" in out
    assert "rpi    Pi 5 4 GB d88100008543dc30" in out
    assert "tt     TT06 E6614C311B7A7A37" in out
    assert cli_main(["labels", "--data", str(data_dir), "--list", "--only", "tt"]) == 0
    assert capsys.readouterr().out.count("\n") == 2
    assert cli_main(["name", "--netv2", "0x00742c4e63b9085c", "--arty", "210319B301DE"]) == 0
    out = capsys.readouterr().out
    assert "netv2-grove" in out
    assert "arty-hawk" in out
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

    doc = copy.deepcopy(docs["opi1pc-b"])
    doc.summary = replace(doc.summary, model="MinnowBoard Turbot", compatible="")
    with_other = dict(docs, minnow=doc)
    kinds = [k for k, _t, _d, _r in labels.all_labels(with_other, {"rpi", "opi"})]
    assert kinds == [k for k, _t, _d, _r in labels.all_labels(docs, {"rpi", "opi"})]
    assert "minnow: not a board this package labels" in capsys.readouterr().err


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
