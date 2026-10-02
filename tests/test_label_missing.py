"""What each label needs (label_input.missing), `labels --check`, and the
labels refusing a label input that is short of a field."""

from __future__ import annotations

import copy
import json

import jsonschema
import pytest

from conftest import RAW
from rpi_hwid import label_input, labels
from rpi_hwid.collect import load_collected


def summary(host="pi-sw2-p48", **changes):
    s = copy.deepcopy(RAW[host]["verdict"]["summary"])
    s.update(changes)
    return s


def missing(s):
    return label_input.missing(label_input.build("h", s))


def fpga(**changes):
    board = dict(RAW["pi-sw2-p48"]["verdict"]["summary"]["fpga"][0], **changes)
    return missing(summary(fpga=[board]))["fpga[0]"]


@pytest.mark.parametrize("host", sorted(RAW))
def test_every_fixture_is_complete(host):
    """Every fixture renders today, so none is short of a field."""
    assert all(v == [] for v in label_input.missing(
        label_input.from_probe(host, RAW[host])).values())


def test_keys_name_every_label_by_its_list_and_position():
    assert set(label_input.missing(label_input.from_probe("rpi5-netv2", RAW["rpi5-netv2"]))) \
        == {"board", "fpga[0]", "fpga[1]", "usb_net[0]"}
    assert set(label_input.missing(label_input.from_probe("rpi4-tt", RAW["rpi4-tt"]))) \
        == {"board", "tinytapeout[0]", "tinytapeout[1]"}


def test_it_takes_text_too():
    text = label_input.dumps(label_input.from_probe("pi3", RAW["pi3"]))
    assert label_input.missing(text) == {"board": [], "fpga[0]": []}


def test_the_pis_facts_alone_describe_no_fpga_label():
    s = summary()
    del s["fpga"]
    assert missing(s) == {"board": []}


@pytest.mark.parametrize(("changes", "need"), [
    ({"serial": None}, ["serial"]),
    ({"serial": ""}, ["serial"]),
    ({"macs": None}, ["macs"]),
    ({"revision": None}, ["revision"]),
    ({"revision": "ffffff"}, ["revision"]),       # names no board
    ({"header": None}, ["header"]),               # unread, unlike []
    ({"header": []}, []),
    ({"fan": None, "rtc_battery": None}, ["fan", "rtc_battery"]),   # a Pi 5's
    ({"hat_uuid": None, "power_class": None, "memory": None}, []),  # optional
])
def test_a_pi_label_needs(changes, need):
    assert missing(summary(**changes))["board"] == need


def test_fan_and_rtc_battery_are_a_pi_5s_alone():
    s = summary("rpi4-tt", fan=None, rtc_battery=None)
    assert missing(s)["board"] == []


def test_a_pi_5_is_known_by_its_model_when_its_revision_is_missing():
    assert missing(summary(revision=None, fan=None))["board"] == ["revision", "fan"]


def test_nothing_that_says_what_the_board_is():
    assert missing({"serial": "x"}) == {"board": ["model"]}


def test_a_board_this_package_does_not_label_has_no_board_key():
    assert "board" not in missing({"model": "Some Other Board", "compatible": "acme,board"})


@pytest.mark.parametrize(("changes", "need"), [
    ({"compatible": None}, ["compatible"]),
    ({"memory": None}, ["memory"]),
    ({"header": None}, ["header"]),
])
def test_an_orange_pi_label_needs(changes, need):
    assert missing(summary("pi-sw2-p22", **changes))["board"] == need


def test_a_risc_v_and_a_pc_label():
    assert missing(summary("hifive-unmatched-1", serial=None))["board"] == ["serial"]
    # a PC's firmware may have no serial at all, which its label says
    assert missing(summary("minnow-turbot-1", serial=None))["board"] == []


@pytest.mark.parametrize(("changes", "need"), [
    ({"dna": None}, ["dna"]),
    ({"idcode": None}, ["idcode"]),
    ({"flash_jedec": None}, ["flash_jedec"]),
    ({"flash_uid_state": None}, ["flash_uid_state"]),
    ({"flash_uid_state": "blank"}, ["flash_uid_state"]),
    ({"flash_uid": None}, ["flash_uid"]),
    ({"flash_uid_state": "none", "flash_uid": None}, ["flash_uid_note"]),
    ({"flash_uid_state": "none", "flash_uid": None, "flash_uid_note": "no factory ESN: x"},
     []),
    ({"flash_extended_id": None, "flash_sfdp": None, "soc_model": None}, []),  # optional
])
def test_an_acorn_label_needs(changes, need):
    assert fpga(**changes) == need


def test_an_arty_label_needs_its_ftdi_serial():
    assert fpga(kind="arty") == ["serial"]
    assert fpga(kind="arty", serial="210319A8B4C1") == []


def test_a_cynthion_label_needs_its_trace_id_revision_and_serial():
    board = RAW["rpi5-netv2"]["verdict"]["summary"]["fpga"][1]
    assert missing(summary(fpga=[board]))["fpga[0]"] == []
    assert missing(summary(fpga=[dict(board, trace_id=None)]))["fpga[0]"] == ["trace_id"]


def test_a_tiny_tapeout_label_needs():
    tt = RAW["rpi4-tt"]["verdict"]["summary"]["tinytapeout"]
    asic = next(t for t in tt if t.get("chip") == "asic")
    assert missing(summary(tinytapeout=[dict(asic, shuttle=None, mcu=None)]))[
        "tinytapeout[0]"] == ["mcu", "shuttle"]
    fpga_board = dict(asic, chip="fpga", shuttle=None)
    assert missing(summary(tinytapeout=[fpga_board]))["tinytapeout[0]"] == []


def test_a_record_without_the_fields_it_is_built_from_is_not_a_label_input():
    """A board with no kind, an adapter with no MAC: not a label short of a
    field but a record that is not one, so the document is refused."""
    u = RAW["pi-sw2-p37"]["verdict"]["summary"]["usb_net"][0]
    with pytest.raises(label_input.InputError,
                       match=r"summary.usb_net\[0\].mac: required in every UsbNetAdapter"):
        missing(summary(usb_net=[dict(u, mac=None)]))
    with pytest.raises(label_input.InputError,
                       match=r"summary.fpga\[0\].kind: required in every FpgaBoard"):
        missing(summary(fpga=[{"dna": "0x0054b48664b04854"}]))
    assert missing(summary(usb_net=[u]))["usb_net[0]"] == []


# --- the labels refuse what missing() reports ---------------------------------

def _docs(**changes):
    doc = label_input.build("pi-sw2-p48", summary(**changes))
    return {"pi-sw2-p48": label_input.to_probe_document(doc)}


def test_a_label_input_short_of_a_field_is_refused_with_the_list():
    with pytest.raises(labels.MissingFieldsError, match="the board label needs header"):
        list(labels.all_labels(_docs(header=None), labels.KINDS))


def test_an_fpga_label_short_of_a_field_is_refused_by_its_key():
    board = dict(RAW["pi-sw2-p48"]["verdict"]["summary"]["fpga"][0], flash_uid=None)
    with pytest.raises(labels.MissingFieldsError, match=r"the fpga\[0\] label needs flash_uid"):
        list(labels.all_labels(_docs(fpga=[board]), labels.KINDS))


def test_only_the_labels_asked_for_are_checked():
    rows = list(labels.all_labels(_docs(header=None), {"fpga"}))
    assert [r[1] for r in rows] == ["acorn"]


def test_a_complete_label_input_makes_the_same_labels_as_its_probe_document():
    probe_docs = {"pi-sw2-p48": label_input.to_probe_document(
        label_input.from_probe("pi-sw2-p48", RAW["pi-sw2-p48"]))}
    assert [r[2] for r in labels.all_labels(_docs(), labels.KINDS)] == \
        [r[2] for r in labels.all_labels(probe_docs, labels.KINDS)]


# --- labels --check --------------------------------------------------------------

def test_check_lists_every_label_and_what_it_needs(tmp_path, capsys):
    (tmp_path / "pi-sw2-p48.json").write_text(
        label_input.dumps(label_input.build("pi-sw2-p48", summary(header=None))))
    (tmp_path / "pi3.json").write_text(json.dumps(RAW["pi3"]))   # a probe document
    assert labels.main(["--data", str(tmp_path), "--check"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert "pi-sw2-p48  board          needs header" in out
    assert "pi-sw2-p48  fpga[0]        complete" in out
    assert "pi3  fpga[0]        complete" in out
    assert out[-1] == "1 label short of a field"


def test_check_passes_a_complete_directory(data_dir, capsys):
    assert labels.main(["--data", str(data_dir), "--check"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "0 labels short of a field"
    assert load_collected(data_dir)


def test_the_schema_requires_what_check_requires():
    schema = json.loads(label_input.schema_path().read_text())
    doc = json.loads(label_input.dumps(label_input.build("h", summary())))
    jsonschema.validate(doc, schema)
    doc["summary"]["fpga"][0]["kind"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)
