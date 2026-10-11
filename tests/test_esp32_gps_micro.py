"""ESP32 GPS-node micro labels: the plain ESP32 label and the receiver."""

from __future__ import annotations

import copy
import dataclasses
import glob
import io
import json
import pathlib
import shutil
import subprocess

import pytest

import conftest
from rpi_hwid import esp32_gps_micro, esp32_micro, labels, micro
from rpi_hwid.micro import Icon
from rpi_hwid.model import ProbeDocument

# verdict.esp32 as `rpi-hwid collect --esp32-read ... --esp32-gps ...` wrote it
# for rpiz-gps on 2026-10-08 (boot output trimmed): the three nodes, each
# wired to the receiver its firmware identified on its first boot.
REAL = json.loads((pathlib.Path(__file__).parent / "esp32_gps_devices.json").read_text())


def _real(mac):
    (d,) = [d for d in REAL["rpiz-gps"] if d["mac"] == mac]
    return copy.deepcopy(d)


M10 = _real("44:1b:f6:2e:a9:a4")
U7 = _real("44:1b:f6:2f:18:78")
LC29H = _real("e8:3d:c1:8c:5c:bc")


def _docs(*devices, host="rpiz-gps"):
    raw = copy.deepcopy(conftest.RAW["rpi5-netv2"])
    raw["verdict"]["esp32"] = [copy.deepcopy(d) for d in devices]
    return {host: ProbeDocument.from_dict(host, raw)}


def _one(dev):
    (m,) = esp32_gps_micro.micro_labels(_docs(dev))
    return m


def test_every_node_gets_a_label():
    got = [(m.host, m.title, m.ident)
           for m in esp32_gps_micro.micro_labels(_docs(*REAL["rpiz-gps"]))]
    assert got == [("rpiz-gps", "ESP32-C3FH4", "44:1b:f6:2e:a9:a4"),
                   ("rpiz-gps", "ESP32-C3FH4", "44:1b:f6:2f:18:78"),
                   ("rpiz-gps", "ESP32-C3FH4", "e8:3d:c1:8c:5c:bc")]


def test_the_receiver_line_is_its_model_and_firmware():
    assert esp32_gps_micro.receiver_text(M10["gps"]) == "u-blox M10 · SPG 5.10"
    assert esp32_gps_micro.receiver_text(U7["gps"]) == "u-blox 7 · 1.00 (59842)"


def test_a_quectel_firmware_drops_the_model_it_repeats():
    assert LC29H["gps"]["firmware"] == "LC29HAANR11A05S"
    assert esp32_gps_micro.receiver_text(LC29H["gps"]) == "LC29H(AA) · NR11A05S"
    # a firmware that does not start with the model is kept whole
    assert esp32_gps_micro.receiver_text(
        {"model": "LC29H(AA)", "firmware": "R11A05S"}) == "LC29H(AA) · R11A05S"
    assert esp32_gps_micro.receiver_text({"model": "u-blox M10"}) == "u-blox M10"


def test_the_mark_is_the_receiver_makers():
    assert esp32_gps_micro.maker_for("h", "m", M10["gps"]) == "u-blox"
    assert esp32_gps_micro.maker_for("h", "m", U7["gps"]) == "u-blox"
    assert esp32_gps_micro.maker_for("h", "m", LC29H["gps"]) == "Quectel"
    assert esp32_gps_micro.maker_for("h", "m", dict(M10["gps"], module="m8")) == "u-blox"
    for name in esp32_gps_micro.MAKER_MARK.values():
        assert labels.artwork(name), name


def test_a_receiver_type_with_no_maker_is_fatal_and_says_what_it_saw():
    with pytest.raises(esp32_gps_micro.UnknownReceiverError, match=r"h: .*'zed-f9p'"):
        esp32_gps_micro.maker_for("h", "m", dict(M10["gps"], module="zed-f9p"))


def test_the_dish_says_what_each_receiver_type_tracks():
    """Each from its maker's datasheet or product page (GNSS's sources): the
    u-blox 7 tracks GPS or GLONASS, never both; the M8, M10 and LC29H the
    four global constellations; only the LC29H(AA) is dual band."""
    got = {k: esp32_gps_micro.gnss_text("h", {"module": k}) for k in esp32_gps_micro.MAKER}
    assert got == {"ublox7": "G/R L1", "m8": "GREC L1", "m10": "GREC L1", "lc29h": "GREC L1L5"}
    assert [_one(n).icons[-1] for n in (M10, U7, LC29H)] == [
        Icon("gnss", "GREC L1"), Icon("gnss", "G/R L1"), Icon("gnss", "GREC L1L5")]
    assert set(esp32_gps_micro.GNSS) == set(esp32_gps_micro.MAKER)
    for g in esp32_gps_micro.GNSS.values():
        micro.gnss_parts(f"{g.letters} {g.band}")
        assert g.source


def test_a_receiver_type_with_no_constellations_is_fatal():
    with pytest.raises(esp32_gps_micro.UnknownReceiverError, match=r"h: .*'zed-f9p'.*GNSS"):
        esp32_gps_micro.gnss_text("h", {"module": "zed-f9p"})


def test_a_failed_gps_read_is_fatal_and_says_how_to_read_it_again():
    dev = dict(M10, gps=None, gps_error="cannot open /dev/ttyACM2: busy")
    with pytest.raises(esp32_micro.Esp32NotReadError, match=r"busy.*--esp32-gps rpiz-gps="):
        esp32_gps_micro.micro_labels(_docs(dev))


def test_a_remembered_receiver_that_did_not_say_its_model_is_fatal():
    dev = dict(M10, gps=dict(M10["gps"], model=None))
    with pytest.raises(esp32_micro.Esp32NotReadError, match="wired and powered"):
        esp32_gps_micro.micro_labels(_docs(dev))


def test_a_node_that_has_not_found_its_receiver_gets_only_the_plain_label():
    dev = dict(M10, gps=dict(M10["gps"], module=None, model=None))
    assert esp32_gps_micro.micro_labels(_docs(dev)) == []


def test_an_esp32_never_asked_about_a_receiver_is_not_a_gps_node():
    dev = copy.deepcopy(M10)
    del dev["gps"]
    assert esp32_gps_micro.micro_labels(_docs(dev)) == []


def test_the_gps_label_is_the_plain_label_plus_the_receiver():
    host, dev = next(esp32_micro.devices(_docs(M10)))
    plain = esp32_micro.esp32_label(host, dev)
    m = _one(M10)
    assert (m.ident, m.ident_caption, m.title, m.mark, m.subtitle, m.qr_content) == (
        plain.ident, plain.ident_caption, plain.title, plain.mark, plain.subtitle,
        plain.qr_content)
    assert m.icons == (*plain.icons, Icon("gnss", "GREC L1"))
    assert m.rows == plain.rows
    assert m.specs == plain.specs
    assert m.extra is not None
    for node in (M10, U7, LC29H):
        assert micro.title_fits(_one(node))


def test_with_both_kinds_a_gps_node_gets_one_label_not_two():
    docs = _docs(*REAL["rpiz-gps"])
    rows = list(labels.all_labels(docs, {"esp32", "esp32-gps"}))
    titles = " | ".join(r[2] for r in rows)
    for d in REAL["rpiz-gps"]:
        assert titles.count(d["mac"]) == 1
    labelled = [m for r in rows for m in r[4] if m is not None]
    assert all(m.icons[-1].name == "gnss" for m in labelled)


@pytest.mark.parametrize("node", [M10, U7, LC29H], ids=["max-m10s", "gt-u7", "lc29h"])
def test_the_receiver_line_fits_under_both_ids(node):
    """Every id is printed (as on the 433 MHz node label), and the receiver
    line takes the room under them down to the foot caption's line, its
    text no smaller than the labels print."""
    m = _one(node)
    boxes = []
    real = m.extra

    def extra(cell, box):
        boxes.append(box)
        real(cell, box)

    micro.render_micro([dataclasses.replace(m, extra=extra)], io.BytesIO())
    ((_x, y, _w, h),) = boxes
    assert y + h == pytest.approx(micro.caption_baseline())
    assert h >= micro.MIN_SIZE * 0.72


def test_render_and_decode_the_gps_labels(tmp_path):
    ls = esp32_gps_micro.micro_labels(_docs(*REAL["rpiz-gps"]))
    out = tmp_path / "gps.pdf"
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
    assert got == {"44:1b:f6:2e:a9:a4", "44:1b:f6:2f:18:78", "e8:3d:c1:8c:5c:bc"}
