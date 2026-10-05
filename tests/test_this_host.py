"""`rpi-hwid labels --this-host`: this host's labels from the Pi-only probe
and fpgas-verify's identity, and the label input behind them -- the
document the fpgas.online site must match (contract 15 and 17)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rpi_hwid import cli, fpga, label_input, labels, probe, this_host, tinytapeout
from test_probe_collect import _pi5_tree, _w

GOLDEN = Path(__file__).parent / "data" / "identity-v1-acorn-p48.json"
# what the PMIC's ADC reads: a measurement, so not the same twice
PMIC = {"ext5v": "5.33990000"}


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
            return f"EXT5V_V volt(24)={PMIC['ext5v']}V\nBATT_V volt(25)=3.26000000V\n"
        if args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x0"
        return ""
    monkeypatch.setattr(probe, "sh", sh)
    monkeypatch.setattr(probe, "i2c_open", lambda bus, addr: None)
    monkeypatch.setattr(fpga, "sh", sh)
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: {
        "read": fpga.identity_parse(GOLDEN.read_text())[0], "error": None,
        "document": json.loads(GOLDEN.read_text())})
    for name in ("jtag_probe", "soc_probe", "pcileech_probe", "cynthion_offline_probe"):
        monkeypatch.setattr(fpga, name, lambda *a, _n=name, **k: pytest.fail(_n + " ran"))
    monkeypatch.setattr(tinytapeout, "collect_tinytapeout",
                        lambda *a, **k: pytest.fail("Tiny Tapeout board probed"))
    monkeypatch.setattr(tinytapeout, "open_tty",
                        lambda *a, **k: pytest.fail("a Tiny Tapeout board's port opened"))
    monkeypatch.setattr(tinytapeout, "ROOT", str(tmp_path))
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


# The site's half (fpgas.online-site#41, PR #42's fleet/hwid.py), rebuilt
# here from the fields it takes and where it takes them -- not from the
# Pi's summary, or the comparison would prove nothing:
#   the registration      model, serial, revision, memory, MACs (no signal)
#   pi-identified         SITE_PI_FIELDS, flat k=v (contract 13 and 17: an
#                         unread field left out, "-" for read-and-none, a
#                         list or object as compact JSON)
#   fpga-board-identified FpgaBoard's fields of each board, fpgas-verify's
#                         extras dropped
# and nothing else; a header nothing sent is null.

REGISTRATION = ("model", "serial", "revision", "memory", "macs")
# site #42's PI_FIELDS (fleet/src/fleet/hwid.py at d1272a6, branch
# hwid-label-input): keep in step with it.
SITE_PI_FIELDS = {
    "compatible": str, "power_class": str, "hat_uuid": str,
    "fan": bool, "rtc_battery": bool, "max_current_ma": int, "ext5v_v": float,
    "header": list, "macs": list, "usb_net": list,
}
# ...and as it was before contract 19 gave it usb_net
SITE_PI_FIELDS_BEFORE_19 = {k: v for k, v in SITE_PI_FIELDS.items() if k != "usb_net"}


def _event(summary, fields):
    """What fpgas-verify sends in pi-identified, from its boot-time reading."""
    out = {}
    for key in fields:
        value = summary.get(key)
        if value is None:
            if key != "header":          # the one field --pi-only leaves unread
                out[key] = "-"
        elif isinstance(value, (list, dict)):
            out[key] = json.dumps(value, separators=(",", ":"), sort_keys=True)
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def _typed(text, kind):
    if text == "-":
        return None
    if kind is list:
        return json.loads(text)
    if kind is bool:
        return text == "true"
    return kind(text)


def _boot_reading(host):
    """The Pi-only reading fpgas-verify takes at boot: independent of the
    label-time one -- another PMIC sample, and the MACs' evidence as that
    boot's probe named it."""
    PMIC["ext5v"] = "5.21870000"
    try:
        boot = cli.pi_only_label_input("pi-sw2-p48")
    finally:
        PMIC["ext5v"] = "5.33990000"
    boot["summary"]["macs"] = [dict(m, signal="boot-time evidence")
                               for m in boot["summary"]["macs"]]
    return boot["summary"]


# site #42's FPGA_FIELDS: the FpgaBoard fields fpgas-verify fills
SITE_FPGA_FIELDS = ("kind", "serial", "dna", "idcode", "flash", "flash_jedec",
                    "flash_extended_id", "flash_sfdp", "flash_uid", "flash_uid_bits",
                    "flash_uid_state", "flash_uid_note", "flash_error", "flash_source",
                    "soc_model")


def _site_board(b):
    """An fpga-board-identified event's board as the site records it: its
    FPGA_FIELDS, none left out, and a DNA recorded as fpgas-verify's reading
    (contract 32)."""
    record = {k: b[k] for k in SITE_FPGA_FIELDS if k in b and b[k] is not None}
    if record.get("dna"):
        record["dna_sources"] = ["fpgas-verify"]
    return record


def _site(boot, fields):
    summary = {k: boot[k] for k in REGISTRATION if boot.get(k)}
    summary["macs"] = [{"kind": m["kind"], "mac": m["mac"], "signal": None}
                       for m in boot["macs"]]
    details = _event(boot, fields)
    summary.update({k: _typed(v, fields[k]) for k, v in details.items()})
    summary.setdefault("header", None)
    summary["fpga"] = [_site_board(b) for b in json.loads(GOLDEN.read_text())["boards"]
                       if b["kind"] != "tt"]
    sources = {"collected_by": "fpgas.online-site", "registration": "fp",
               "pi-identified": "boot 2026-10-02T00:00:00Z", "fpga-board-identified": "boot"}
    return label_input.build("pi-sw2-p48", summary, sources)


def test_the_site_rebuilds_the_same_document_byte_for_byte(host):
    boot = _boot_reading(host)
    pi_doc = this_host.label_input_document("pi-sw2-p48")
    site = _site(boot, SITE_PI_FIELDS)
    # two readings: they differ on what is measured...
    assert site["summary"]["ext5v_v"] != pi_doc["summary"]["ext5v_v"]
    assert site["summary"]["macs"][0]["signal"] != pi_doc["summary"]["macs"][0]["signal"]
    # ...and agree on every fact (contract 25)
    assert label_input.comparable(site) == label_input.comparable(pi_doc)


def test_without_usb_net_in_the_event_the_documents_differ(host):
    """What contract 19 fixed: the Pi's own r8152 dongle is in its usb_net,
    and the site had no way to know it."""
    boot = _boot_reading(host)
    pi_doc = this_host.label_input_document("pi-sw2-p48")
    assert pi_doc["summary"]["usb_net"]
    assert label_input.comparable(_site(boot, SITE_PI_FIELDS_BEFORE_19)) != \
        label_input.comparable(pi_doc)


def _identity_only(monkeypatch, tmp, *boards):
    """A host where sysfs shows no FPGA: nothing on PCIe, no Digilent cable;
    fpgas-verify knows `boards`."""
    import shutil
    for slot in ("0001:01:00.0",):
        shutil.rmtree(tmp / "sys/bus/pci/devices" / slot, ignore_errors=True)
    doc = dict(json.loads(GOLDEN.read_text()), boards=list(boards))
    asked = []
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: asked.append(1) or {
        "read": fpga.identity_parse(json.dumps(doc))[0], "error": None, "document": doc})
    return asked


def test_a_netv2_only_host_asks_fpgas_verify(host, monkeypatch):
    """A NeTV2 on its harness alone: nothing in sysfs, and fpgas-verify asked
    all the same (contract 26)."""
    netv2 = {"board": "netv2", "kind": "netv2", "variant": "a7-100",
             "idcode": "0x13631093", "dna": "0x00742c4e63b9085c"}
    asked = _identity_only(monkeypatch, host, netv2)
    doc = this_host.label_input_document("rpi5-netv2")
    assert asked
    assert [b["kind"] for b in doc["summary"]["fpga"]] == ["netv2"]
    assert doc["summary"]["fpga"][0]["dna"] == "0x00742c4e63b9085c"


def test_fpgas_verifys_boards_alone_and_as_it_gave_them(host, monkeypatch):
    """A board only sysfs sees here (a Cynthion on USB) is one the site
    cannot see; and no merge here adds a field fpgas-verify did not send --
    only who read its DNA (contract 32)."""
    monkeypatch.setattr(fpga, "cynthion_devices", lambda: [
        {"path": "1-1.1", "id": "1d50:615b", "serial": "267125df30c460de",
         "bcd_device": "0104", "product": "Cynthion"}])
    doc = this_host.label_input_document("pi-sw2-p48")
    (board,) = doc["summary"]["fpga"]
    golden = json.loads(GOLDEN.read_text())["boards"][0]
    assert board == label_input.load(label_input.build("h", {"fpga": [
        dict({k: v for k, v in golden.items() if k in label_input.FPGA_FIELDS},
             dna_sources=["fpgas-verify"])]}))["summary"]["fpga"][0]


def test_fields_fpgas_verify_does_not_fill_are_not_taken(host, monkeypatch):
    """FpgaBoard fields outside fpga.IDENTITY_FIELDS are not taken from the
    document: identity_parse drops them, and so does the site."""
    acorn = json.loads(GOLDEN.read_text())["boards"][0]
    doc = json.loads(GOLDEN.read_text())
    doc["boards"] = [dict(acorn, dna_sources=["jtag"], dna_agree=True, gateware="4.14",
                          mode="analyzer")]
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: {
        "read": fpga.identity_parse(json.dumps(doc))[0], "error": None, "document": doc})
    (board,) = this_host.label_input_document("pi-sw2-p48")["summary"]["fpga"]
    assert board["dna_sources"] == ["fpgas-verify"]
    assert [board[k] for k in ("dna_agree", "gateware", "mode")] == [None, None, None]


def test_without_fpgas_verify_the_fpga_module_says_what_is_there(host, monkeypatch):
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: None)
    doc = this_host.label_input_document("pi-sw2-p48")
    assert [b["kind"] for b in doc["summary"]["fpga"]] == ["acorn"]
    assert doc["sources"]["fpga"] == "rpi-hwid"


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


# --- Tiny Tapeout, from fpgas-verify (contract 21) ------------------------------

TT_BOARD = {"board": "tt", "kind": "tt", "variant": "ttdbv3", "serial": "E6614C311B7A7A37",
            "usb": "1-1.2", "usb_serial": "E6614C311B7A7A37", "mcu": "RP2350",
            "chip": "asic", "shuttle": "tt06", "demoboard": "TT06+",
            "demoboard_version": "v2.0.1", "sdk": "2.0.1", "from_report": ["shuttle"]}


def _with_boards(monkeypatch, *extra):
    doc = json.loads(GOLDEN.read_text())
    doc["boards"] += list(extra)
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: {
        "read": fpga.identity_parse(json.dumps(doc))[0], "error": None, "document": doc})


def test_a_tiny_tapeout_board_comes_from_fpgas_verify(host, monkeypatch):
    """The tree's demo board at 1-1.2 has this serial; nothing opens its port."""
    _with_boards(monkeypatch, TT_BOARD)
    doc = this_host.label_input_document("pi-sw2-p48")
    (tt,) = doc["summary"]["tinytapeout"]
    assert tt["usb_serial"] == "E6614C311B7A7A37"
    assert tt["shuttle"] == "tt06"
    assert {k for k, v in tt.items() if v is not None} <= set(label_input.TT_FIELDS)
    assert doc["sources"]["tinytapeout"] == "fpgas-verify"
    assert [b["kind"] for b in doc["summary"]["fpga"]] == ["acorn"]
    assert label_input.missing(doc)["tinytapeout[0]"] == []


def test_a_tiny_tapeout_board_not_on_usb_is_left_out_and_named(host, monkeypatch):
    _with_boards(monkeypatch, dict(TT_BOARD, usb_serial="0123456789ABCDEF"))
    doc = this_host.label_input_document("pi-sw2-p48")
    assert doc["summary"]["tinytapeout"] == []
    assert doc["sources"]["tinytapeout_not_on_usb"] == ["0123456789ABCDEF"]


def test_a_tiny_tapeout_board_without_a_usb_serial_is_not_taken(host, monkeypatch):
    """What fpgas-verify sends before contract 21 (serial, but no
    TinyTapeoutBoard fields) is not yet a Tiny Tapeout identity."""
    _with_boards(monkeypatch, {"board": "tt", "kind": "tt", "serial": "E6614C311B7A7A37"})
    doc = this_host.label_input_document("pi-sw2-p48")
    assert doc["summary"]["tinytapeout"] == []
    assert "tinytapeout" not in doc["sources"]



def test_a_tiny_tapeout_only_host_asks_fpgas_verify(host, monkeypatch):
    """No FPGA on PCIe or USB, just a demo board: fpgas-verify is asked all
    the same (contract 26), and the board comes from it."""
    asked = _identity_only(monkeypatch, host, TT_BOARD)
    doc = this_host.label_input_document("rpi4-tt")
    assert asked
    assert doc["summary"]["fpga"] == []
    (tt,) = doc["summary"]["tinytapeout"]
    assert tt["usb_serial"] == "E6614C311B7A7A37"


# --- the Pi facts fpgas-verify read at boot (contract 29) -------------------------

def _with_boot_pi(monkeypatch, pi):
    doc = dict(json.loads(GOLDEN.read_text()), pi=pi)
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: {
        "read": fpga.identity_parse(json.dumps(doc))[0], "error": None, "document": doc})


def test_the_boot_scans_header_fills_what_this_read_left_unread(host, monkeypatch):
    """At boot the user bus was scanned and the header read bare, with a PoE
    HAT (B) powering it; on demand it was not, so the header is null and the
    power class undetermined. The boot reading settles both, and the Pi's
    document then matches the site's, which has the same boot event."""
    import shutil

    shutil.rmtree(host / "proc/device-tree/hat")        # nothing the ID bus names
    boot = _boot_reading(host)
    boot.update(header=["Waveshare PoE HAT (B)"], power_class="gpio-poe-hat")
    _with_boot_pi(monkeypatch, dict(
        {k: v for k, v in boot.items() if v is not None and k not in ("fpga", "tinytapeout")},
        read_at="2026-10-03T00:00:00+00:00"))
    doc = this_host.label_input_document("pi-sw2-p48")
    assert doc["summary"]["header"] == ["Waveshare PoE HAT (B)"]
    assert doc["summary"]["power_class"] == "gpio-poe-hat"
    assert doc["sources"]["pi_from_boot"] == {
        "fields": ["power_class", "header"], "read_at": "2026-10-03T00:00:00+00:00"}
    assert label_input.missing(doc)["board"] == []
    assert label_input.comparable(_site(boot, SITE_PI_FIELDS)) == label_input.comparable(doc)


def test_a_value_read_here_is_never_replaced_by_the_boots(host, monkeypatch):
    _with_boot_pi(monkeypatch, {"header": ["Something Else"], "serial": "0000000000000000",
                                "power_class": "usbc-supply", "read_at": "x"})
    doc = this_host.label_input_document("pi-sw2-p48")
    assert doc["summary"]["header"] == ["Waveshare PoE M.2 HAT+ (B)"]
    assert doc["summary"]["serial"] == "c36b093f773d46b8"
    assert "pi_from_boot" not in doc["sources"]


def test_no_boot_pi_is_todays_behaviour(host):
    doc = this_host.label_input_document("pi-sw2-p48")
    assert "pi_from_boot" not in doc["sources"]


# --- fpgas-verify installed, and failing (contract 30) ----------------------------

FAILED = "fpgas-verify printed nothing (stderr: sudo: a password is required)"


@pytest.fixture
def failing(host, monkeypatch):
    monkeypatch.setattr(fpga, "identity_probe", lambda boards=1: {
        "read": [], "error": FAILED, "document": None})
    return host


def test_a_failing_fpgas_verify_leaves_the_boards_unknown_not_empty(failing):
    doc = this_host.label_input_document("pi-sw2-p48")
    assert doc["summary"]["fpga"] is None
    assert doc["summary"]["tinytapeout"] is None
    assert doc["sources"]["fpgas_verify_error"] == FAILED
    assert doc["sources"]["fpga_sysfs"] == ["acorn"]    # passive facts, recorded only
    need = label_input.missing(doc)
    assert need["fpga"] == ["fpga"]
    assert need["tinytapeout"] == ["tinytapeout"]


def test_labels_this_host_refuses_the_fpga_labels_and_says_why(failing, capsys):
    assert labels.main(["--this-host", "--host", "pi-sw2-p48", "--list", "--only", "fpga"]) == 1
    err = capsys.readouterr().err
    assert "the FPGA and Tiny Tapeout boards are not known" in err
    assert FAILED in err
    # the Pi's own label still comes out
    assert labels.main(["--this-host", "--host", "pi-sw2-p48", "--list", "--only", "rpi"]) == 0


def test_a_document_with_its_boards_unknown_is_refused_from_data_too(failing, tmp_path):
    (tmp_path / "pi-sw2-p48.json").write_text(
        label_input.dumps(this_host.label_input_document("pi-sw2-p48")))
    with pytest.raises(labels.MissingFieldsError, match="the fpga label needs fpga"):
        labels.main(["--data", str(tmp_path), "--list", "--only", "fpga"])
    assert labels.main(["--data", str(tmp_path), "--check", "--only", "fpga"]) == 1


# --- the same boards as the merge path builds them (contract 32) ----------------

NETV2_NO_DNA = {"board": "netv2", "kind": "netv2", "variant": "a7-100", "idcode": "0x13631093"}


def _merged(doc):
    """fpgas-verify's boards as rpi-hwid's merge path puts them in a summary
    (identity_parse, merge_identity, fpga_summary) -- how the site's
    comparison test (fpgas.online-site tests/test_fleet_hwid_compare.py)
    builds its Pi side. A board on PCIe is one sysfs shows there."""
    read, _ = fpga.identity_parse(json.dumps(doc))
    boards = [{"kind": r["kind"], "slot": r["bdf"]} for r in read if r.get("bdf")]
    assert fpga.merge_identity(boards, read) == []
    return fpga.fpga_summary(boards)


@pytest.mark.parametrize("extra", [(), (NETV2_NO_DNA,)], ids=["acorn-with-dna", "no-dna"])
def test_this_host_and_the_merge_path_give_the_same_boards(host, monkeypatch, extra):
    """A board whose DNA fpgas-verify read has dna_sources ["fpgas-verify"]
    both ways, as merge_dna records it and the site sets it (contract 32);
    one without a DNA has none either way."""
    doc = json.loads(GOLDEN.read_text())
    if extra:
        _identity_only(monkeypatch, host, *extra)
        doc["boards"] = list(extra)
    pi_doc = this_host.label_input_document("pi-sw2-p48")
    merged = label_input.build("pi-sw2-p48", dict(pi_doc["summary"], fpga=_merged(doc)),
                               pi_doc["sources"])
    assert label_input.comparable(pi_doc) == label_input.comparable(merged)
    want = [] if extra else ["fpgas-verify"]
    assert [b["dna_sources"] for b in label_input.load(pi_doc)["summary"]["fpga"]] == [want]


def test_label_input_this_host_prints_what_labels_this_host_draws_from(host, capsys):
    assert cli.main(["label-input", "--this-host", "--host", "pi-sw2-p48"]) == 0
    out = capsys.readouterr().out
    assert out == label_input.dumps(this_host.label_input_document("pi-sw2-p48"))
    assert json.loads(out)["summary"]["fpga"][0]["dna"] == "0x0054b48664b04854"


def test_label_input_this_host_needs_no_labels_dependencies(host, capsys, monkeypatch):
    """The fpgas.online Pi root installs rpi-hwid without segno or reportlab
    (2026-10-05: `labels --this-host --input` died on `import segno` there),
    so this path must import neither, nor rpi_hwid.labels, which does."""
    import rpi_hwid

    # This file imported rpi_hwid.labels above, so it is cached twice over:
    # in sys.modules, and as an attribute of the package, which `from
    # rpi_hwid import labels` looks at first. Both go, and a None entry in
    # sys.modules makes any import of the name raise ImportError.
    monkeypatch.delattr(rpi_hwid, "labels")
    for module in ("segno", "reportlab", "svglib", "PIL", "rpi_hwid.labels"):
        monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(ImportError):
        from rpi_hwid import labels as _  # noqa: F401  the guard itself works
    with pytest.raises(ImportError):
        import segno  # noqa: F401
    assert cli.main(["label-input", "--this-host", "--host", "pi-sw2-p48"]) == 0
    assert '"schema": "rpi-hwid/label-input"' in capsys.readouterr().out
    assert sys.modules["rpi_hwid.labels"] is None
    assert not hasattr(rpi_hwid, "labels")


def test_label_input_this_host_says_when_the_boards_are_not_known(failing, capsys):
    assert cli.main(["label-input", "--this-host", "--host", "pi-sw2-p48"]) == 1
    got = capsys.readouterr()
    doc = json.loads(got.out)  # still printed, with the boards not known
    assert doc["summary"]["fpga"] is None
    assert "the FPGA and Tiny Tapeout boards are not known: " + FAILED in got.err


def test_label_input_this_host_excludes_pi_only_and_takes_no_user_bus(host, capsys):
    with pytest.raises(SystemExit):
        cli.main(["label-input", "--this-host", "--pi-only"])
    assert cli.main(["label-input", "--this-host", "--user-bus"]) == 2
