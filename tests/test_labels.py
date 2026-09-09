"""Labels: records from documents, a rendered sheet, and every QR decoding."""

from __future__ import annotations

import glob
import shutil
import subprocess

import pytest

from rpi_hwid import labels
from rpi_hwid.cli import main as cli_main


def test_pi_record_derives_a_broadcom_radio_mac(docs):
    p = labels.pi_record(docs["pi-sw1-p10"])
    assert p.model == "3 Model B+"
    assert p.memory == "1 GB"
    assert p.macs == (("eth", "b8:27:eb:e3:e7:e4"), ("wlan", "b8:27:eb:b6:b2:b1"))
    assert p.wlan_note is None


def test_pi_record_pi5_without_radio_says_so(docs):
    p = labels.pi_record(docs["pi-sw2-p47"])
    assert p.macs == (("eth", "98:fe:54:13:f5:75"),)
    assert p.wlan_note == "radio disabled, not readable"
    assert p.header == ("Waveshare PoE M.2 HAT+ (B)",)


def test_fpga_records_named_and_typed(docs):
    recs = {r.kind: r for r in labels.fpga_records(docs)}
    assert recs["netv2"].name == "netv2-grove"
    assert recs["netv2"].part == "XC7A100T"
    assert recs["arty"].name == "arty-hawk"
    assert recs["arty"].model == "Arty A7-35T"
    assert recs["arty"].flash == "S25FL128S/127S"
    assert recs["acorn"].name is None
    assert recs["acorn"].maker == "SQRL"


def test_usb_records(docs):
    (u,) = labels.usb_records(docs)
    assert u.title == "ASIX Elec. Corp. AX88179"
    assert ("USB", "3.00 SS 5 Gbit/s") in u.lines
    assert u.mac == "00:0e:c6:82:b5:e1"


def test_all_labels_order_and_count(docs):
    kinds = [k for k, _t, _d, _r in labels.all_labels(docs, {"fpga", "rpi", "usb"})]
    # FPGA boards first in sorted-host order, then one Pi per document, then adapters
    assert kinds == ["arty", "acorn", "netv2", "rpi", "rpi", "rpi", "rpi", "rpi", "usb"]


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
        "00:0e:c6:82:b5:e1",                              # the dongle
    }
    assert got == want


def test_list_and_names_cli(data_dir, capsys):
    assert cli_main(["labels", "--data", str(data_dir), "--list"]) == 0
    out = capsys.readouterr().out
    assert "netv2-grove" in out
    assert "arty-hawk" in out
    assert cli_main(["name", "--netv2", "0x00742c4e63b9085c", "--arty", "210319B301DE"]) == 0
    out = capsys.readouterr().out
    assert "netv2-grove" in out
    assert "arty-hawk" in out
    assert cli_main(["revision", "c04170"]) == 0
    assert "Raspberry Pi 5, 4 GB, Rev 1.0" in capsys.readouterr().out
