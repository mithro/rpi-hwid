"""The label input document (rpi_hwid.label_input): versioned, checked,
written one way, and read by the labels exactly as a probe document is."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path

import jsonschema
import pytest
from reportlab.pdfgen import canvas

from conftest import RAW
from rpi_hwid import cli, label_input, labels
from rpi_hwid.collect import load_collected
from rpi_hwid.model import ProbeDocument


@pytest.fixture
def no_processes(monkeypatch):
    """The site calls this module from its web workers: nothing in it may
    start a process."""
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail(f"started {a!r}"))


def pi_doc(host="pi-sw2-p48"):
    return label_input.from_probe(host, copy.deepcopy(RAW[host]))


@pytest.mark.parametrize("host", sorted(RAW))
def test_every_fixture_reads_back_to_the_same_summary(host, no_processes):
    doc = label_input.from_probe(host, copy.deepcopy(RAW[host]))
    assert label_input.to_probe_document(doc).summary == \
        ProbeDocument.from_dict(host, copy.deepcopy(RAW[host])).summary
    # through its own text, too
    again = label_input.to_probe_document(label_input.dumps(doc))
    assert again.summary == label_input.to_probe_document(doc).summary
    assert again.host == host


@pytest.mark.parametrize("host", sorted(RAW))
def test_every_fixture_matches_the_shipped_json_schema(host):
    schema = json.loads(label_input.schema_path().read_text())
    jsonschema.validate(json.loads(label_input.dumps(pi_doc(host))), schema)


def test_the_shipped_schema_is_the_one_the_records_give():
    shipped = json.loads(label_input.schema_path().read_text())
    assert shipped == label_input.json_schema()
    jsonschema.Draft202012Validator.check_schema(shipped)


def test_dumps_is_sorted_one_space_ascii_with_a_trailing_newline():
    text = label_input.dumps(pi_doc())
    assert text.endswith("}\n")
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=1) + "\n"
    assert text.isascii()


def test_dumps_writes_every_field_filling_what_was_not_sent_with_its_default():
    doc = label_input.build("h", {"model": "Raspberry Pi 4 Model B Rev 1.5",
                                  "fpga": [{"kind": "acorn", "dna": "0x0054b48664b04854"}]})
    out = json.loads(label_input.dumps(doc))
    s = out["summary"]
    assert set(s) == set(label_input.PI_FIELDS)
    # absent: the default the probe writes
    assert s["macs"] == s["usb_net"] == s["tinytapeout"] == []
    assert s["compatible"] == ""
    assert s["fan"] is None
    assert set(s["fpga"][0]) == set(label_input.FPGA_FIELDS)
    assert s["fpga"][0]["dna_sources"] == []
    assert s["fpga"][0]["flash_uid"] is None
    # no default: not read
    assert s["serial"] is None
    # and the one field whose default is itself a reading: not read
    assert s["header"] is None


def test_an_explicit_null_stays_not_read():
    out = json.loads(label_input.dumps(label_input.build("h", {"macs": None, "header": None})))
    assert out["summary"]["macs"] is None
    assert out["summary"]["header"] is None
    out = json.loads(label_input.dumps(label_input.build("h", {"header": []})))
    assert out["summary"]["header"] == []           # read, and bare


def _sent(value):
    """`value` as a builder that sends only what it has would: no nulls, no
    empty lists, no empty strings, at any depth."""
    if isinstance(value, dict):
        return {k: _sent(v) for k, v in value.items() if v not in (None, [], "")}
    if isinstance(value, list):
        return [_sent(v) for v in value]
    return value


def test_a_document_of_only_the_sent_fields_is_byte_identical_to_the_pis():
    """The site builds from the fields a Pi sent, leaving out what it did not
    get -- nulls, and empty lists such as a board's dna_sources, which
    fpgas-verify never sends; the Pi writes every field. The two must be
    the same text."""
    pi = pi_doc()
    sent = _sent(pi["summary"])
    assert "dna_sources" not in sent["fpga"][0]
    assert "usb_net" not in sent
    site = label_input.build("pi-sw2-p48", sent, dict.fromkeys(sent, "fpgas-verify"))
    assert label_input.comparable(site) == label_input.comparable(pi)
    # and sources are provenance: they differ, and dumps says so
    assert label_input.dumps(site) != label_input.dumps(pi)


def test_comparable_is_dumps_without_sources_and_the_measured():
    doc = pi_doc()
    whole = json.loads(label_input.dumps(doc))
    del whole["sources"]
    for key in label_input.MEASURED:
        del whole["summary"][key]
    for mac in whole["summary"]["macs"]:
        del mac["signal"]
    assert label_input.comparable(doc) == json.dumps(whole, sort_keys=True, indent=1) + "\n"


def test_a_whole_number_in_a_float_field_is_one_text():
    a = label_input.build("h", {"model": "m", "ext5v_v": 5})
    b = label_input.build("h", {"model": "m", "ext5v_v": 5.0})
    assert label_input.dumps(a) == label_input.dumps(b)


def test_tuples_are_lists():
    a = label_input.build("h", {"header": ("Pmod HAT Adaptor",)})
    assert a["summary"]["header"] == ["Pmod HAT Adaptor"]


def test_load_takes_text_and_bytes_as_well_as_a_dict():
    text = label_input.dumps(pi_doc())
    assert label_input.load(text) == label_input.load(text.encode()) == label_input.load(
        json.loads(text))


def test_load_does_not_change_its_argument():
    raw = {"schema": label_input.SCHEMA, "version": 1, "host": "h",
           "summary": {"model": "m"}, "sources": {}}
    before = copy.deepcopy(raw)
    label_input.load(raw)
    assert raw == before


@pytest.mark.parametrize(("doc", "problem"), [
    ("{", "not JSON"),
    ([], "not a JSON object"),
    ({"schema": "rpi-hwid/probe"}, "schema is 'rpi-hwid/probe'"),
    ({"schema": "rpi-hwid/label-input", "version": 2}, "version 2: this rpi-hwid reads version 1"),
    ({"schema": "rpi-hwid/label-input", "version": True}, "version True: this rpi-hwid"),
    ({"schema": "rpi-hwid/label-input", "version": 1.0}, "version 1.0: this rpi-hwid"),
    ({"schema": "rpi-hwid/label-input", "version": "1"}, "version '1': this rpi-hwid"),
    ({"schema": "rpi-hwid/label-input", "version": 1, "summary": {}, "sources": {}},
     "host: a non-empty string is required"),
    ({"schema": "rpi-hwid/label-input", "version": 1, "host": "h", "sources": {}},
     "summary: an object is required"),
    ({"schema": "rpi-hwid/label-input", "version": 1, "host": "h", "summary": {}},
     "sources: an object is required"),
])
def test_what_is_not_a_label_input_is_refused(doc, problem):
    with pytest.raises(label_input.InputError, match=problem):
        label_input.load(doc)


@pytest.mark.parametrize(("summary", "sources", "problem"), [
    ({"power": "x"}, {}, "summary.power: not a field of Summary"),
    ({"fpga": [{"kind": "acorn", "variant": "cle-215+"}]}, {},
     r"summary.fpga\[0\].variant: not a field of FpgaBoard"),
    ({"fpga": ["acorn"]}, {}, r"summary.fpga\[0\]: an object is required"),
    ({"serial": 1234}, {}, "summary.serial: a string is required"),
    ({"fan": "yes"}, {}, "summary.fan: true or false is required"),
    ({"max_current_ma": True}, {}, "summary.max_current_ma: an integer is required"),
    ({"ext5v_v": "5.1"}, {}, "summary.ext5v_v: a finite number is required"),
    ({"ext5v_v": float("nan")}, {}, "summary.ext5v_v: a finite number is required"),
    ({"ext5v_v": float("inf")}, {}, "summary.ext5v_v: a finite number is required"),
    ({"macs": [{"kind": "eth"}]}, {}, r"summary.macs\[0\].mac: required in every Mac"),
    ({"fpga": [{"dna": "0x0054b48664b04854"}]}, {},
     r"summary.fpga\[0\].kind: required in every FpgaBoard"),
    ({"usb_net": [{"iface": "eth1", "mac": "00:e0:4c:68:01:03", "kind": "ethernet",
                   "vidpid": None}]}, {},
     r"summary.usb_net\[0\].vidpid: required in every UsbNetAdapter"),
    ({"header": "Pmod HAT"}, {}, "summary.header: a list is required"),
    ({"header": [1]}, {}, r"summary.header\[0\]: a string is required"),
    ({"dmi": []}, {}, "summary.dmi: an object is required"),
    ({"macs": [{"kind": "eth", "mac": 1}]}, {}, r"summary.macs\[0\].mac: a string is required"),
    ({}, {"x": float("nan")}, "sources.x: not writable as JSON"),
])
def test_a_wrong_field_is_named(summary, sources, problem):
    with pytest.raises(label_input.InputError, match=problem):
        label_input.build("h", summary, sources)


def test_every_problem_is_listed_not_only_the_first():
    with pytest.raises(label_input.InputError) as exc:
        label_input.build("h", {"power": 1, "serial": 2})
    assert len(exc.value.problems) == 2


def test_an_unknown_top_level_key_is_refused():
    doc = dict(pi_doc(), extra=1)
    with pytest.raises(label_input.InputError, match="unknown top-level keys"):
        label_input.load(doc)


def test_from_probe_needs_a_probe_document():
    with pytest.raises(label_input.InputError, match=r"no verdict\.summary"):
        label_input.from_probe("h", {"model": "m"})
    with pytest.raises(label_input.InputError, match="not an object"):
        label_input.from_probe("h", {"verdict": {"summary": []}})


def test_from_probe_says_rpi_hwid_read_every_field():
    doc = pi_doc()
    assert set(doc["sources"]) == set(RAW["pi-sw2-p48"]["verdict"]["summary"])
    assert set(doc["sources"].values()) == {"rpi-hwid"}


def test_is_label_input():
    assert label_input.is_label_input(pi_doc())
    assert not label_input.is_label_input(RAW["pi-sw2-p48"])
    assert not label_input.is_label_input("text")


def test_power_class_is_optional():
    doc = label_input.build("h", {"model": "Raspberry Pi 4 Model B Rev 1.5"})
    assert label_input.to_probe_document(doc).summary.power_class is None


def test_load_collected_reads_label_inputs_beside_probe_documents(tmp_path):
    """A directory may hold either; the labels come out the same."""
    probes, inputs = tmp_path / "probes", tmp_path / "inputs"
    probes.mkdir()
    inputs.mkdir()
    for host, raw in RAW.items():
        (probes / f"{host}.json").write_text(json.dumps(raw))
        (inputs / f"{host}.json").write_text(label_input.dumps(pi_doc(host)))

    def rows(docs):
        return [(h, k, t) for h, k, t, _d, _r in labels.all_labels(docs, labels.KINDS)]

    assert rows(load_collected(inputs)) == rows(load_collected(probes))


def test_load_collected_names_the_file_of_a_bad_label_input(tmp_path):
    (tmp_path / "h.json").write_text(json.dumps(
        {"schema": label_input.SCHEMA, "version": 9}))
    with pytest.raises(ValueError, match=r"h\.json: version 9"):
        load_collected(tmp_path)


def test_cli_writes_the_label_input_of_a_probe_document(tmp_path, capsys):
    path = tmp_path / "pi-sw2-p48.json"
    path.write_text("Welcome to the Pi\n" + json.dumps(RAW["pi-sw2-p48"]))
    assert cli.main(["label-input", "--from", str(path)]) == 0
    out = capsys.readouterr().out
    assert out == label_input.dumps(pi_doc())
    assert cli.main(["label-input", "--from", str(path), "--host", "p48"]) == 0
    assert json.loads(capsys.readouterr().out)["host"] == "p48"


def test_cli_refuses_what_is_not_a_probe_document(tmp_path, capsys):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"verdict": {"summary": {"power": 1}}}))
    assert cli.main(["label-input", "--from", str(path)]) == 1
    err = capsys.readouterr().err
    assert "x.json: summary has fields this model does not know: ['power']" in err


def test_the_documented_example_is_a_label_input():
    text = (Path(__file__).parent.parent / "docs" / "LABEL-INPUT.md").read_text()
    example = json.loads(re.search(r"```json\n(.*?)```", text, re.S).group(1))
    record = label_input.to_probe_document(example)
    assert record.summary.fpga[0].flash_extended_id == "0x4d0180"


def test_a_nan_is_never_written():
    """check() refuses every NaN a field could carry; the writer refuses one
    all the same, so no future field can slip one into the text."""
    with pytest.raises(label_input.InputError, match="not writable as JSON"):
        label_input._text({"x": float("nan")})


@pytest.mark.parametrize(("summary", "problem"), [
    ({"header": [None]}, r"summary.header\[0\]: null is not allowed in a list"),
    ({"fpga": [{"kind": "acorn", "dna_sources": [None]}]},
     r"summary.fpga\[0\].dna_sources\[0\]: null is not allowed in a list"),
    ({"macs": [None]}, r"summary.macs\[0\]: null is not allowed in a list"),
    ({"dmi": {"unread": 5}}, "summary.dmi.unread: a list is required"),
    ({"dmi": {"sys_vendor": 3}}, "summary.dmi.sys_vendor: a string is required"),
    ({"dmi": {"unread": [None]}}, r"summary.dmi.unread\[0\]: null is not allowed here"),
    ({"dmi": {"colour": "red"}}, "summary.dmi.colour: not a field here"),
    ({"riscv": {"harts": "4"}}, "summary.riscv.harts: an integer is required"),
    ({"riscv": {"eeprom": {"crc_ok": "yes"}}},
     "summary.riscv.eeprom.crc_ok: true or false is required"),
    ({"riscv": {"eeprom": 7}}, "summary.riscv.eeprom: an object is required"),
    ({"fpga": [{"kind": "acorn", "dna_conflict": {"jtag": 1}}]},
     r"summary.fpga\[0\].dna_conflict.jtag: a string is required"),
])
def test_what_would_crash_the_labels_is_refused(summary, problem):
    with pytest.raises(label_input.InputError, match=problem):
        label_input.build("h", summary)


@pytest.mark.parametrize("bad", [
    {"header": [None]}, {"dmi": {"unread": 5, "sys_vendor": 3}},
    {"riscv": {"harts": "4"}}, {"fpga": [{"kind": "acorn", "dna_sources": [None]}]},
])
def test_the_schema_refuses_them_too(bad):
    schema = json.loads(label_input.schema_path().read_text())
    doc = json.loads(label_input.dumps(pi_doc()))
    doc["summary"].update(bad)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, schema)


def test_the_real_dmi_and_riscv_records_pass():
    for host in ("minnow-turbot-1", "hifive-unmatched-1"):
        jsonschema.validate(json.loads(label_input.dumps(pi_doc(host))),
                            json.loads(label_input.schema_path().read_text()))


def test_a_null_header_in_a_probe_document_is_not_read_either():
    raw = copy.deepcopy(RAW["pi-sw2-p48"])
    raw["verdict"]["summary"]["header"] = None
    record = ProbeDocument.from_dict("pi-sw2-p48", raw)
    assert record.summary.header is None
    with pytest.raises(labels.HeaderNotReadError):
        labels.board_record(record)


def test_the_schema_refuses_what_check_refuses():
    schema = json.loads(label_input.schema_path().read_text())
    good = json.loads(label_input.dumps(pi_doc()))
    jsonschema.validate(good, schema)
    for bad in ({"fpga": [{"kind": None}]}, {"macs": [{"kind": "eth"}]}):
        doc = copy.deepcopy(good)
        doc["summary"].update(bad)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(dict(good, version=True), schema)


def test_load_collected_refuses_a_record_without_its_fields_by_name(tmp_path):
    raw = json.loads(label_input.dumps(pi_doc()))
    raw["summary"]["fpga"][0]["kind"] = None
    (tmp_path / "h.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match=r"h\.json: summary.fpga\[0\].kind: required"):
        load_collected(tmp_path)


def test_a_probe_document_still_needs_its_model_serial_revision_and_power_class():
    raw = copy.deepcopy(RAW["pi-sw2-p48"])
    del raw["verdict"]["summary"]["power_class"]
    with pytest.raises(ValueError, match="summary is missing"):
        ProbeDocument.from_dict("h", raw)


def test_a_label_inputs_summary_may_leave_them_out():
    record = label_input.to_probe_document(label_input.build("h", {"header": []}))
    assert (record.summary.model, record.summary.power_class) == ("", None)


# --- a header nobody read is never "HAT none" ------------------------------------

def _record(header):
    s = copy.deepcopy(RAW["pi-sw2-p48"]["verdict"]["summary"])
    s["header"] = header
    return label_input.to_probe_document(label_input.build("pi-sw2-p48", s))


def test_a_header_read_as_bare_prints_none(tmp_path, monkeypatch):
    drawn = []
    real = labels.Label.captioned

    def captioned(self, x_cap, x_val, y, cap, val, *a, **k):
        drawn.append((cap, val))
        return real(self, x_cap, x_val, y, cap, val, *a, **k)
    monkeypatch.setattr(labels.Label, "captioned", captioned)
    labels.register_fonts()
    lab = labels.Label(canvas.Canvas(str(tmp_path / "x.pdf")), 0, 0)
    labels.draw_board(lab, labels.board_record(_record([])))
    assert ("HAT", "none") in drawn


def test_a_header_nobody_read_is_refused_not_printed_as_none():
    assert _record(None).summary.header is None
    with pytest.raises(labels.HeaderNotReadError, match="nothing read the 40-pin header"):
        labels.board_record(_record(None))
    with pytest.raises(labels.HeaderNotReadError):
        list(labels.all_labels({"pi-sw2-p48": _record(None)}, labels.KINDS))


def test_sources_is_free_form_provenance():
    """Contract 20: the site's own keys and values are its business."""
    sources = {"collected_by": "fpgas.online-site 1.2", "registration": {"at": "2026-10-02"},
               "pi-identified": ["2026-10-02T01:02:03Z", 7], "fpga-board-identified": None}
    doc = label_input.build("h", {"model": "m"}, sources)
    assert doc["sources"] == sources
    assert json.loads(label_input.dumps(doc))["sources"] == sources
    jsonschema.validate(json.loads(label_input.dumps(doc)),
                        json.loads(label_input.schema_path().read_text()))


def test_comparable_leaves_out_what_each_read_measures_afresh():
    """Contract 25: the site's boot-time reading and the Pi's label-time one
    differ on these without either being wrong."""
    a = pi_doc()
    b = copy.deepcopy(a)
    b["summary"]["ext5v_v"] = 5.1
    b["summary"]["max_current_ma"] = 5000
    b["summary"]["macs"] = [dict(m, signal="driver") for m in b["summary"]["macs"]]
    assert label_input.comparable(a) == label_input.comparable(b)
    assert label_input.dumps(a) != label_input.dumps(b)          # kept as read
    text = json.loads(label_input.comparable(a))["summary"]
    assert "ext5v_v" not in text
    assert "max_current_ma" not in text
    assert all("signal" not in m for m in text["macs"])
    # and anything else still counts
    b["summary"]["serial"] = "0000000000000000"
    assert label_input.comparable(a) != label_input.comparable(b)



@pytest.mark.parametrize(("sources", "problem"), [
    ({1: "a", "b": "c"}, "sources: key 1 is not a string"),
    ({"a": {2: "x"}}, "sources.a: key 2 is not a string"),
    ({"a": [{"b": {3: 1}}]}, r"sources.a\[0\].b: key 3 is not a string"),
    ({"a": float("inf")}, "sources.a: not writable as JSON"),
    ({"a": {1, 2}}, "sources.a: not writable as JSON: a set"),
])
def test_sources_that_dumps_could_not_write_are_refused(sources, problem):
    with pytest.raises(label_input.InputError, match=problem):
        label_input.build("h", {"model": "m"}, sources)


def test_a_number_too_big_for_a_float_is_refused_not_a_traceback():
    with pytest.raises(label_input.InputError, match="ext5v_v: a finite number is required"):
        label_input.build("h", {"ext5v_v": 10 ** 400})
    with pytest.raises(label_input.InputError, match="ext5v_v: a finite number is required"):
        label_input.load('{"schema": "rpi-hwid/label-input", "version": 1, "host": "h", '
                         '"sources": {}, "summary": {"ext5v_v": 1' + "0" * 400 + '}}')


def test_the_cli_says_so_too(tmp_path, capsys):
    raw = copy.deepcopy(RAW["pi-sw2-p48"])
    raw["verdict"]["summary"]["ext5v_v"] = 10 ** 400
    path = tmp_path / "p48.json"
    path.write_text(json.dumps(raw))
    assert cli.main(["label-input", "--from", str(path)]) == 1
    assert "a finite number is required" in capsys.readouterr().err
