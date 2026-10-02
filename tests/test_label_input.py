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


def test_dumps_writes_every_field_and_null_for_what_was_not_sent():
    doc = label_input.build("h", {"model": "Raspberry Pi 4 Model B Rev 1.5",
                                  "fpga": [{"kind": "acorn", "dna": "0x0054b48664b04854"}]})
    out = json.loads(label_input.dumps(doc))
    assert set(out["summary"]) == set(label_input.PI_FIELDS)
    assert out["summary"]["header"] is None          # not read, which is not "none"
    assert out["summary"]["serial"] is None
    assert set(out["summary"]["fpga"][0]) == set(label_input.FPGA_FIELDS)
    assert out["summary"]["fpga"][0]["flash_uid"] is None


def test_a_document_of_only_the_sent_fields_is_byte_identical_to_the_pis():
    """The site builds from the fields a Pi sent; the Pi writes every field,
    null where it read nothing. The two must be the same text."""
    pi = pi_doc()
    sent = {k: v for k, v in pi["summary"].items() if v is not None}
    sent["fpga"] = [{k: v for k, v in b.items() if v is not None} for b in sent["fpga"]]
    sent["macs"] = [{k: v for k, v in m.items() if v is not None} for m in sent["macs"]]
    site = label_input.build("pi-sw2-p48", sent, dict.fromkeys(sent, "fpgas-verify"))
    assert label_input.comparable(site) == label_input.comparable(pi)
    # and sources are provenance: they differ, and dumps says so
    assert label_input.dumps(site) != label_input.dumps(pi)


def test_comparable_is_dumps_without_sources():
    doc = pi_doc()
    whole = json.loads(label_input.dumps(doc))
    del whole["sources"]
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
    ({"ext5v_v": "5.1"}, {}, "summary.ext5v_v: a number is required"),
    ({"header": "Pmod HAT"}, {}, "summary.header: a list is required"),
    ({"header": [1]}, {}, r"summary.header\[0\]: a string is required"),
    ({"dmi": []}, {}, "summary.dmi: an object is required"),
    ({"macs": [{"kind": "eth", "mac": 1}]}, {}, r"summary.macs\[0\].mac: a string is required"),
    ({}, {"power": "rpi-hwid"}, "sources.power: not a summary field"),
    ({}, {"serial": "guess"}, "sources.serial: 'guess' is not one of"),
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
