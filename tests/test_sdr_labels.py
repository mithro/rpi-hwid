"""SDR labels (rpi_hwid.sdr_labels) from the fleet's own radios."""

from __future__ import annotations

import copy
import glob
import shutil
import subprocess
from dataclasses import replace

import pytest

from rpi_hwid import labels, sdr_labels
from rpi_hwid.model import ProbeDocument
from sdr_fixtures import SDR_RAW, sdr_docs


def records(docs=None):
    return {r.host: r for r in sdr_labels.sdr_records(docs or sdr_docs())}


def test_the_pluto_is_keyed_on_its_own_serial():
    p = records()["rpi-sdr-pluto"]
    assert p.title == "ADALM-Pluto"
    assert p.maker == "Analog Devices"
    assert p.ident == "10447354119600022000120009f61e2b82"
    assert p.shared is None


def test_the_pluto_prints_what_it_said_of_itself():
    p = records()["rpi-sdr-pluto"]
    assert p.rx == ((325000000, 3800000000),)
    assert p.tx == ((325000000, 3800000000),)
    assert (p.rx_channels, p.tx_channels) == (1, 1)
    assert p.facts[0] == "30.72 MS/s  ·  12-bit"
    assert p.facts[1] == "40 MHz ref"
    assert "Rev.B" in p.subtitle
    assert "v0.39" not in p.subtitle      # firmware changes; the label does not
    prov = dict(p.provenance)
    assert prov["RX range"].startswith("read: ip:192.168.2.1")
    assert prov["channels, ADC, SoC"].startswith("https://wiki.analog.com/")


def test_the_kraken_has_no_identity_and_says_so():
    k = records()["rpi-sdr-kraken"]
    assert k.title == "KrakenSDR"
    assert k.ident is None
    assert k.shared == "1000\u20131004"
    assert "not unique" in k.shared_caption
    assert (k.rx_channels, k.tx_channels) == (5, 0)
    assert k.coherent
    assert k.noise_source
    assert k.rx == ((24_000_000, 1_766_000_000),)
    assert "krakensdr_docs" in dict(k.provenance)[
        "tuner, range, bandwidth, ADC, clock, noise source"]


def test_a_radio_known_only_by_its_bus_id_is_refused():
    raw = copy.deepcopy(SDR_RAW["rpi-sdr-kraken"])
    raw["verdict"]["summary"]["sdr"] = [{"kind": "usdr", "pcie_id": "10ee:7049",
                                         "usdr_family": "m2_lm7_1"}]
    docs = {"rpi-sdr-xsdr": ProbeDocument.from_dict("rpi-sdr-xsdr", raw)}
    with pytest.raises(sdr_labels.RadioNotIdentifiedError, match="--sdr-open"):
        sdr_labels.sdr_records(docs)


def test_an_xsdr_whose_unique_id_was_not_read_is_refused():
    raw = copy.deepcopy(SDR_RAW["rpi-sdr-kraken"])
    raw["verdict"]["summary"]["sdr"] = [{"kind": "usdr", "pcie_id": "10ee:7049",
                                         "usdr_family": "m2_lm7_1", "usdr_hwid": "8030012d"}]
    docs = {"rpi-sdr-xsdr": ProbeDocument.from_dict("rpi-sdr-xsdr", raw)}
    (r,) = sdr_labels.sdr_records(docs)
    assert r.title == "XSDR"
    with pytest.raises(labels.IdentifierNotReadError, match="rpi-sdr-xsdr"):
        list(labels.all_labels(docs, {"sdr"}))


def test_a_label_row_per_radio():
    rows = [(h, k, t) for h, k, t, _d, _r in labels.all_labels(sdr_docs(), {"sdr"})]
    assert rows == [("rpi-sdr-kraken", "sdr", "KrakenSDR 1000\u20131004"),
                    ("rpi-sdr-pluto", "sdr", "ADALM-Pluto 10447354119600022000120009f61e2b82"),
                    ("rpi-sdr-rtlsdr-v3", "sdr", "RTL-SDR V3 00000001"),
                    ("rpi-sdr-xsdr", "sdr", "XSDR 19040203090e9769")]


def test_frequencies_read_as_people_write_them():
    assert sdr_labels.mhz(325_000_000) == "325 MHz"
    assert sdr_labels.mhz(3_800_000_000) == "3.8 GHz"
    assert sdr_labels.mhz(1_766_000_000) == "1.766 GHz"
    assert sdr_labels.mhz(500_000) == "500 kHz"


def test_render_and_decode(tmp_path):
    out = tmp_path / "sdr.pdf"
    n, sheets = labels.render(sdr_docs(), out, only=["sdr"], outline=True)
    assert (n, sheets) == (4, 1)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        pytest.skip("pdftoppm not installed")
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    subprocess.run([pdftoppm, "-r", "300", "-png", str(out), str(tmp_path / "page")], check=True)
    got = set()
    for png in sorted(glob.glob(str(tmp_path / "page-*.png"))):
        got |= {b.text for b in zxingcpp.read_barcodes(Image.open(png))}
    # the Kraken and the V3 have no identity to encode, so they get no code
    assert got == {"10447354119600022000120009f61e2b82", "19040203090e9769"}


def test_a_missing_mark_sets_the_makers_name(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "PACKAGE_ARTWORK", str(tmp_path))
    k = replace(records()["rpi-sdr-kraken"])
    out = tmp_path / "one.pdf"
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out))
    sdr_labels.draw_sdr(labels.Label(c, 0, 0), k)
    c.save()
    assert out.stat().st_size > 1000


def test_the_xsdr_is_keyed_on_its_flash_esn():
    x = records()["rpi-sdr-xsdr"]
    assert x.title == "XSDR"
    assert x.maker == "Wavelet Lab"
    assert x.ident == "19040203090e9769"         # the programmed half; the rest is erased
    assert x.ident_caption == "flash uid"
    assert "XC7A50T" in x.subtitle
    assert (x.rx_channels, x.tx_channels) == (2, 2)
    prov = dict(x.provenance)
    assert prov["model"].startswith("read: HWID 8030012d")
    assert "DS-AT25SL321-112" in prov["identity"]


def test_the_v3_prints_its_shared_serial_as_not_unique():
    v = records()["rpi-sdr-rtlsdr-v3"]
    assert v.title == "RTL-SDR V3"
    assert v.maker == "RTL-SDR Blog"
    assert v.ident is None
    assert v.shared == "00000001"
    assert "not unique" in v.shared_caption
    assert v.rx_aux == ((500_000, 24_000_000),)
    assert dict(v.provenance)["model"].startswith("read: tuner Rafael Micro R820T")


def test_the_xsdr_flash_reads_as_an_fpga_boards_does():
    """The same part-and-size line an FPGA label prints, from the same
    JEDEC decoding, and the uid in its own row."""
    x = records()["rpi-sdr-xsdr"]
    assert x.flash == labels.flash_text(labels.flash_from_jedec("0x1f4216"))
    assert x.flash == "Atmel AT25SL321  \u00b7  4 MiB"
    assert x.flash_uid == "19040203090e9769"


def test_the_pluto_flash_uid_is_its_serial():
    p = records()["rpi-sdr-pluto"]
    assert p.flash_uid == p.ident
    assert p.flash is None          # the part was never read, so no part row
