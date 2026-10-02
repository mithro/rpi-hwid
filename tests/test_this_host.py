"""`rpi-hwid labels --this-host`: this host's labels from the Pi-only probe
and fpgas-verify's identity, and the label input behind them -- the
document the fpgas.online site must match (contract 15 and 17)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rpi_hwid import fpga, label_input, labels, probe, this_host, tinytapeout
from test_probe_collect import _pi5_tree, _w

GOLDEN = Path(__file__).parent / "data" / "identity-v1-acorn-p48.json"


@pytest.fixture
def host(tmp_path, monkeypatch):
    """pi-sw2-p48 as a fake tree: a Pi 5 with the Acorn on PCIe, its ID bus
    up and empty, fpgas-verify installed and answering with the golden
    document. Nothing may reach the FPGA but that."""
    _pi5_tree(tmp_path)
    _w(tmp_path, "/dev/i2c-0", "")
    _w(tmp_path, "/proc/device-tree/hat/vendor", "Waveshare\0")
    _w(tmp_path, "/proc/device-tree/hat/product", "PoE M.2 HAT+ (B)\0")
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(fpga, "ROOT", str(tmp_path))

    def sh(args, timeout=15):
        if args[:2] == ["sudo", "vcgencmd"]:
            return "EXT5V_V volt(24)=5.33990000V\nBATT_V volt(25)=3.26000000V\n"
        if args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x0"
        return ""
    monkeypatch.setattr(probe, "sh", sh)
    monkeypatch.setattr(probe, "i2c_open", lambda bus, addr: None)
    monkeypatch.setattr(fpga, "sh", sh)
    monkeypatch.setattr(fpga, "identity_probe", lambda: {
        "read": fpga.identity_parse(GOLDEN.read_text())[0], "error": None})
    for name in ("jtag_probe", "soc_probe", "pcileech_probe", "cynthion_offline_probe"):
        monkeypatch.setattr(fpga, name, lambda *a, _n=name, **k: pytest.fail(_n + " ran"))
    monkeypatch.setattr(tinytapeout, "collect_tinytapeout",
                        lambda *a, **k: pytest.fail("Tiny Tapeout board probed"))
    return tmp_path


def test_the_document_is_the_pis_facts_and_fpgas_verifys_boards(host):
    doc = this_host.label_input_document("pi-sw2-p48")
    s = doc["summary"]
    assert s["serial"] == "c36b093f773d46b8"
    assert s["header"] == ["Waveshare PoE M.2 HAT+ (B)"]
    (board,) = s["fpga"]
    assert board["kind"] == "acorn"
    assert board["dna"] == "0x0054b48664b04854"
    assert board["flash_extended_id"] == "0x4d0180"
    assert doc["sources"]["fpga"] == "fpgas-verify"
    assert doc["sources"]["serial"] == "rpi-hwid"
    # and the tree's USB Ethernet dongle
    assert label_input.missing(doc) == {"board": [], "fpga[0]": [], "usb_net[0]": []}


# The site's half (fpgas.online-site#41): it rebuilds the document from the
# pi-identified event's details (contract 13 and 17: an unread field left
# out, a list or object as compact JSON, numbers as decimal strings) and
# each board's fpga-board-identified details (the golden document's fields,
# fpgas-verify's own extras dropped through FPGA_FIELDS).

def _event(summary):
    out = {}
    for key, value in summary.items():
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            out[key] = json.dumps(value, separators=(",", ":"), sort_keys=True)
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def _site_summary(details):
    import typing

    from rpi_hwid.model import Summary

    hints = typing.get_type_hints(Summary)
    out = {}
    for key, text in details.items():
        hint = hints[key]
        if type(None) in typing.get_args(hint):
            (hint,) = [h for h in typing.get_args(hint) if h is not type(None)]
        kind = typing.get_origin(hint) or hint
        if kind in (tuple, dict):
            out[key] = json.loads(text)
        elif kind is bool:
            out[key] = text == "true"
        elif kind in (int, float):
            out[key] = kind(text)
        else:
            out[key] = text
    return out


def test_the_site_rebuilds_the_same_document_byte_for_byte(host):
    pi_doc = this_host.label_input_document("pi-sw2-p48")
    pi_event = _event({k: v for k, v in pi_doc["summary"].items() if k != "fpga"})
    site = _site_summary(pi_event)
    site["fpga"] = [{k: v for k, v in b.items() if k in label_input.FPGA_FIELDS}
                    for b in json.loads(GOLDEN.read_text())["boards"]]
    built = label_input.build("pi-sw2-p48", site, {"serial": "registration"})
    assert label_input.comparable(built) == label_input.comparable(pi_doc)


def test_labels_this_host_lists_this_hosts_labels(host, capsys):
    assert labels.main(["--this-host", "--host", "pi-sw2-p48", "--list"]) == 0
    out = capsys.readouterr().out
    assert "pi-sw2-p48  rpi" in out
    assert "acorn" in out
    assert "0x0054b48664b04854" in out


def test_labels_this_host_writes_the_sheet_and_its_input(host, tmp_path):
    pdf, doc = tmp_path / "l.pdf", tmp_path / "in.json"
    assert labels.main(["--this-host", "--host", "pi-sw2-p48", "--out", str(pdf),
                        "--input", str(doc)]) == 0
    assert pdf.read_bytes().startswith(b"%PDF")
    assert doc.read_text() == label_input.dumps(this_host.label_input_document("pi-sw2-p48"))


def test_a_header_the_pi_only_probe_could_not_settle_is_refused(host):
    """No HAT that the ID bus or the firmware names: with the user bus not
    read, that is not a bare header, so the Pi label is refused."""
    import shutil

    shutil.rmtree(host / "proc/device-tree/hat")
    with pytest.raises(labels.MissingFieldsError, match="board label needs header"):
        labels.main(["--this-host", "--host", "pi-sw2-p48", "--list"])
    # the FPGA's label still comes out
    assert labels.main(["--this-host", "--host", "pi-sw2-p48", "--list", "--only", "fpga"]) == 0


def test_host_and_input_go_with_this_host(tmp_path):
    with pytest.raises(SystemExit):
        labels.main(["--data", str(tmp_path), "--host", "x"])
    with pytest.raises(SystemExit):
        labels.main(["--data", str(tmp_path), "--this-host"])
