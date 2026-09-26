"""Tasmota micro labels, from documents the collector wrote."""

from __future__ import annotations

import copy
import glob
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rpi_hwid import labels, micro, tasmota, tasmota_micro
from rpi_hwid.micro import Icon, MicroRow
from rpi_hwid.model import ProbeDocument

DEVICES = json.loads((Path(__file__).parent / "tasmota_devices.json").read_text())


def _doc(host, errors=None, **raw_changes):
    d = copy.deepcopy(DEVICES[host])
    mac = d["status"]["StatusNET"]["Mac"]
    dev = tasmota.SheetDevice(host=host, name="tasmota-" + mac.replace(":", "")[6:],
                              ip=d["ip"], mac=tasmota.normalise_mac(mac), site="Welland")
    raw = {"status": d["status"], "module": d["module"], "template": d["template"],
           "info": tasmota.parse_info_page("x" + d["in"])}
    raw.update(raw_changes)
    for k in errors or ():
        raw.pop(k, None)
    return tasmota.document(dev, raw, {k: f"no answer to {k}" for k in errors or ()})


def _docs(*hosts):
    return {h: _doc(h) for h in hosts or DEVICES}


def _one(host):
    (m,) = tasmota_micro.micro_labels({host: _doc(host)})
    return m


def test_the_module_is_found_by_the_micro_layout():
    assert micro.providers()["tasmota"] is tasmota_micro
    assert "tasmota" in micro.kinds()


def test_the_athom_plug_label():
    m = _one("au-plug-29")
    assert m.host == "au-plug-29"
    assert m.mark == "athom.png"
    assert m.title == "Plug V3"
    assert m.icons == (Icon("tasmota"), Icon("plug"), Icon("chip", "C3"))
    assert m.ident_caption == "Wi-Fi MAC"
    assert m.ident == "7c:2c:67:d7:c0:e8"
    assert m.qr_content == "7c:2c:67:d7:c0:e8"
    assert m.subtitle == "ESP32-C3 rev.4  ·  4 MB flash"
    assert m.rows == (MicroRow("chip id", "14139624", mono=True),
                      MicroRow("flash id", "20 40 16", mono=True))


def test_the_sonoff_s31_label():
    m = _one("us-plug-1")
    assert m.mark == "sonoff.png"
    assert m.title == "S31"
    assert m.icons == (Icon("tasmota"), Icon("plug"), Icon("chip", "8266"))
    assert m.subtitle == "ESP8266EX  ·  4 MB flash"
    assert m.rows[1] == MicroRow("flash id", "ef 40 16", mono=True)


def test_a_device_without_a_relay_gets_no_plug():
    m = _one("ir-ac-remote")
    assert m.mark == "athom.png"
    assert m.title == "IR Remote"
    assert Icon("plug") not in m.icons
    assert m.subtitle == "ESP8266EX  ·  2 MB flash"


def test_a_generic_module_is_titled_by_its_chip_with_no_maker():
    m = _one("esp32-433mhz-cc1101-blue")
    assert m.mark is None
    assert m.title == "ESP32-C3"
    assert m.subtitle == "ESP32-C3 v0.4  ·  4 MB flash"


def test_a_known_model_without_its_maker_in_the_name():
    doc = _doc("ir-ac-remote", template=dict(DEVICES["ir-ac-remote"]["template"],
                                             NAME="ZHA ZBBridge"),
               module={"Module": {"0": "ZHA ZBBridge"}})
    (m,) = tasmota_micro.micro_labels({"bridge-zigbee-1": doc})
    assert (m.mark, m.title) == ("sonoff.png", "Zigbee Bridge")


def test_pi_documents_give_no_tasmota_labels(docs):
    assert tasmota_micro.micro_labels(docs) == []


def test_labels_come_in_host_order():
    ms = tasmota_micro.micro_labels(_docs())
    assert [m.host for m in ms] == sorted(DEVICES)


@pytest.mark.parametrize(("missing", "what"), [
    ("info", "ESP chip id"),
    ("module", "model"),
])
def test_an_unread_identifier_is_fatal_and_names_the_host_and_command(missing, what):
    doc = _doc("au-plug-29", errors=[missing])
    with pytest.raises(labels.IdentifierNotReadError) as err:
        tasmota_micro.micro_labels({"au-plug-29": doc})
    msg = str(err.value)
    assert "au-plug-29" in msg
    assert what in msg
    assert "rpi-hwid tasmota" in msg
    assert "au-plug-29" in msg.split("rpi-hwid tasmota", 1)[1]


def test_an_unread_flash_id_is_fatal():
    raw_status = copy.deepcopy(DEVICES["au-plug-29"]["status"])
    del raw_status["StatusMEM"]["FlashChipId"]
    doc = _doc("au-plug-29", status=raw_status)
    with pytest.raises(labels.IdentifierNotReadError, match=r"au-plug-29.*flash"):
        tasmota_micro.micro_labels({"au-plug-29": doc})


def _spy_text(monkeypatch):
    drawn = []
    real = micro.Cell.text

    def text(self, x, y, s, *a, **kw):
        drawn.append(s)
        return real(self, x, y, s, *a, **kw)

    monkeypatch.setattr(micro.Cell, "text", text)
    return drawn


def test_nothing_that_can_change_is_printed(monkeypatch, tmp_path):
    """No IP, no firmware version, no host name, no Wi-Fi network: only what
    the device will say about itself for as long as it exists."""
    drawn = _spy_text(monkeypatch)
    ms = tasmota_micro.micro_labels(_docs())
    micro.render_micro(ms, tmp_path / "t.pdf")
    text = " ".join(drawn)
    for host, d in DEVICES.items():
        assert d["ip"] not in text
        assert host not in text
        assert d["status"]["StatusFWR"]["Version"].split("(")[0] not in text
        assert d["status"]["StatusNET"]["Hostname"] not in text
        assert d["status"]["StatusSTS"]["Wifi"]["SSId"] not in text
        assert d["status"]["StatusNET"]["Mac"].lower() in text


def test_every_label_draws_inside_its_quarter(monkeypatch, tmp_path):
    drawn = []
    real = micro.Cell.text

    def text(self, x, y, s, font=labels.SANS, size=8, align="left", **kw):
        w = self.width(s, font, size)
        left = x - w if align == "right" else x - w / 2 if align == "centre" else x
        drawn.append((s, left, left + w, self.w))
        return real(self, x, y, s, font, size, align, **kw)

    monkeypatch.setattr(micro.Cell, "text", text)
    micro.render_micro(tasmota_micro.micro_labels(_docs()), tmp_path / "t.pdf")
    for s, left, right, w in drawn:
        assert left >= micro.MICRO_PAD - 0.01, s
        assert right <= w - micro.MICRO_PAD + 0.01, s


def test_render_and_decode_every_qr(tmp_path):
    ms = tasmota_micro.micro_labels(_docs())
    out = tmp_path / "t.pdf"
    micro.render_micro(ms, out, outline=True)
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
    assert got == {tasmota.normalise_mac(d["status"]["StatusNET"]["Mac"])
                   for d in DEVICES.values()}


def test_the_labels_command_prints_them(tmp_path, capsys):
    from rpi_hwid.cli import main as cli_main

    data = tmp_path / "data"
    data.mkdir()
    for host in DEVICES:
        (data / f"{host}.json").write_text(_doc(host).to_json())
    # a document goes through the file and back unchanged
    back = ProbeDocument.from_json("au-plug-29", (data / "au-plug-29.json").read_text())
    assert back.evidence == _doc("au-plug-29").evidence
    assert cli_main(["labels", "--data", str(data), "--list", "--only", "tasmota"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    assert "micro" in out[0]
    assert "Plug V3 7c:2c:67:d7:c0:e8" in out[0]
    pdf = tmp_path / "l.pdf"
    assert cli_main(["labels", "--data", str(data), "--out", str(pdf),
                     "--only", "tasmota"]) == 0
    assert "1 labels on 1 sheet" in capsys.readouterr().out
