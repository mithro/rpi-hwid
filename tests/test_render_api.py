"""Rendering as a library call (labels.list_labels, render_sheet,
render_label): label inputs in, PDF bytes out, for the fpgas.online site's
web workers -- no hardware, no process, no file, no state left behind."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import RAW
from rpi_hwid import label_input, labels, placement


@pytest.fixture
def inputs():
    return [label_input.from_probe(h, copy.deepcopy(r)) for h, r in sorted(RAW.items())]


@pytest.fixture
def no_processes(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail(f"started {a!r}"))


def pages(pdf):
    return len(re.findall(rb"/Type /Page\b", pdf))


def media_box(pdf):
    m = re.search(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", pdf)
    return float(m.group(1)), float(m.group(2))


def test_list_labels_is_the_placement_listing(inputs):
    docs = labels.documents(inputs)
    assert labels.list_labels(inputs) == json.loads(
        placement.list_json(docs, labels._only(None)))
    ids = [row["id"] for row in labels.list_labels(inputs)]
    assert len(ids) == len(set(ids))


def test_list_labels_takes_only(inputs):
    rows = labels.list_labels(inputs, only=["acorn"])
    assert [(r["host"], r["kind"]) for r in rows] == [("pi-sw2-p48", "acorn")]


def test_render_sheet_is_the_sheet_labels_writes(inputs, tmp_path, no_processes):
    pdf = labels.render_sheet(inputs)
    assert pdf.startswith(b"%PDF")
    n = len(labels.list_labels(inputs))
    assert pages(pdf) == (n + labels.COLS * labels.ROWS - 1) // (labels.COLS * labels.ROWS)
    assert media_box(pdf) == pytest.approx((595.2756, 841.8898), abs=0.01)


def test_render_sheet_is_the_same_bytes_every_time(inputs):
    assert labels.render_sheet(inputs) == labels.render_sheet(inputs)


def test_render_sheet_leaves_the_first_positions_blank(inputs):
    one = labels.render_sheet(inputs[:1])
    assert pages(labels.render_sheet(inputs[:1], start=labels.COLS * labels.ROWS)) == 2
    assert pages(one) == 1


def test_render_label_is_one_label_sized_page(inputs, no_processes):
    row = labels.list_labels(inputs, only=["acorn"])[0]
    pdf = labels.render_label(inputs, row["id"])
    assert pages(pdf) == 1
    assert media_box(pdf) == pytest.approx((labels.LABEL_W, labels.LABEL_H), abs=0.01)
    assert labels.render_label(inputs, row["id"]) == pdf


def test_render_label_takes_the_json_text_too(inputs):
    texts = [label_input.dumps(d) for d in inputs]
    row = labels.list_labels(texts, only=["rpi"])[0]
    assert labels.render_label(texts, row["id"]) == labels.render_label(inputs, row["id"])


def test_an_unknown_label_is_a_key_error(inputs):
    with pytest.raises(KeyError, match="no such label"):
        labels.render_label(inputs, "nowhere/rpi/x")


def test_two_documents_for_one_host_are_refused(inputs):
    with pytest.raises(label_input.InputError, match="two documents for one host"):
        labels.documents([inputs[0], inputs[0]])


def test_a_label_short_of_a_field_is_refused_with_the_list(inputs):
    short = copy.deepcopy(inputs)
    p48 = next(d for d in short if d["host"] == "pi-sw2-p48")
    p48["summary"]["fpga"][0]["dna"] = None
    with pytest.raises(labels.MissingFieldsError, match=r"fpga\[0\] label needs dna"):
        labels.render_sheet(short)
    with pytest.raises(labels.MissingFieldsError):
        labels.list_labels(short)


def test_the_artwork_directory_is_the_calls_alone(inputs, tmp_path):
    mark = Path(labels.PACKAGE_ARTWORK) / "raspberry-pi.svg"
    (tmp_path / "raspberry-pi.svg").write_bytes(mark.read_bytes())
    seen = []
    real = labels.artwork

    def spy(name):
        seen.append(real(name))
        return real(name)
    try:
        labels.artwork = spy
        row = labels.list_labels(inputs, only=["rpi"])[0]
        assert labels.render_label(inputs, row["id"], artwork=tmp_path).startswith(b"%PDF")
    finally:
        labels.artwork = real
    assert str(tmp_path / "raspberry-pi.svg") in seen
    assert labels.ARTWORK_DIR.get() is None, "nothing set outlives the call"
    assert labels.artwork("raspberry-pi.svg").startswith(labels.PACKAGE_ARTWORK)


def test_render_label_requires_only_the_label_it_renders(inputs):
    """The Acorn's label from a host whose Pi header nobody read: the Pi
    label cannot be made, and is not being made."""
    short = copy.deepcopy(inputs)
    p48 = next(d for d in short if d["host"] == "pi-sw2-p48")
    p48["summary"]["header"] = None
    (acorn,) = labels.list_labels(short, only=["acorn"])
    assert labels.render_label(short, acorn["id"]).startswith(b"%PDF")
    (pi,) = [r for r in labels.list_labels(inputs, only=["rpi"])
             if r["host"] == "pi-sw2-p48"]
    with pytest.raises(labels.MissingFieldsError, match="board label needs header"):
        labels.render_label(short, pi["id"])


def test_another_hosts_short_label_does_not_stop_this_one(inputs):
    short = copy.deepcopy(inputs)
    other = next(d for d in short if d["host"] == "pi3")
    other["summary"]["fpga"][0]["dna"] = None
    (acorn,) = labels.list_labels(inputs, only=["acorn"])
    assert labels.render_label(short, acorn["id"]) == labels.render_label(inputs, acorn["id"])


def test_render_label_finds_a_micro_label_and_a_numbered_twin(inputs):
    rows = labels.list_labels(inputs)
    for row in rows:
        assert labels.render_label(inputs, row["id"]).startswith(b"%PDF")


def test_main_with_artwork_uses_it_for_that_run_alone(data_dir, tmp_path, monkeypatch):
    mark = Path(labels.PACKAGE_ARTWORK) / "raspberry-pi.svg"
    art = tmp_path / "art"
    art.mkdir()
    (art / "raspberry-pi.svg").write_bytes(mark.read_bytes())
    seen = []
    real = labels.artwork
    monkeypatch.setattr(labels, "artwork", lambda name: seen.append(real(name)) or real(name))
    out = tmp_path / "l.pdf"
    assert labels.main(["--data", str(data_dir), "--out", str(out), "--artwork", str(art),
                        "--only", "rpi"]) == 0
    assert out.read_bytes().startswith(b"%PDF")
    assert str(art / "raspberry-pi.svg") in seen
    assert labels.ARTWORK_DIR.get() is None
