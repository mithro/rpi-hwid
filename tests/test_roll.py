"""A roll of stickers (labels.ROLLS, rpi_hwid.roll): one driver-sized page per
sticker, the strip turned onto it inside the printable area."""
from __future__ import annotations

import io
import math
import re

import pytest
from reportlab.lib.units import mm

from rpi_hwid import labels, micro, roll, strip
from rpi_hwid.sheet import ipp
from test_sheet_ipp import FakePrinter, ok
from test_tasmota_micro import DEVICES, _docs

R = labels.ROLLS["dymo-99010"]


@pytest.fixture
def micro_docs():
    """Documents that carry micro labels (the Tasmota fixtures: the main
    fixtures' boards have none); an odd number, so one sticker is half full."""
    return _docs(*sorted(DEVICES)[:3])


@pytest.fixture
def micro_data(tmp_path, micro_docs):
    for host, doc in micro_docs.items():
        (tmp_path / f"{host}.json").write_text(doc.to_json())
    return tmp_path


def _pages(pdf):
    return [(float(w), float(h))
            for w, h in re.findall(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", pdf)]


def test_rolls_and_sheets_do_not_share_a_name():
    assert not set(labels.ROLLS) & set(labels.STOCKS)


def test_the_99010_roll_is_the_dymo_drivers_page():
    """lw450.ppd (printer-driver-dymo 1.4.0): *PageSize w79h252.2 is
    [79 252], *ImageableArea "4.32 4.32 76.08 235.44"; CUPS names it to IPP
    by its size, 2786 x 8890 hundredths of a millimetre."""
    assert R.page == (79.0, 252.0)
    assert R.printable == (4.32, 4.32, 76.08, 235.44)
    assert R.ppd_size == "w79h252.2"
    assert R.media_size == (2786, 8890)


def test_the_strip_box_is_the_printable_area_less_a_millimetre():
    x, y, w, h = R.strip_box
    x0, y0, x1, y1 = R.printable
    assert (x - x0, y - y0, x1 - x - w, y1 - y - h) == pytest.approx((mm,) * 4)
    # the strip is the box turned a quarter: its length runs up the page
    assert pytest.approx((h, w)) == (strip.STRIP_W, strip.STRIP_H)
    assert strip.STRIP_W / mm == pytest.approx(79.5, abs=0.05)
    assert strip.STRIP_H / mm == pytest.approx(23.3, abs=0.05)


@pytest.mark.parametrize("u", [0, strip.STRIP_W])
@pytest.mark.parametrize("v", [0, strip.STRIP_H])
def test_every_corner_of_the_strip_lands_in_the_printable_area(u, v):
    x, y = roll.page_point(R, u, v)
    x0, y0, x1, y1 = R.printable
    assert x0 + mm - 1e-6 <= x <= x1 - mm + 1e-6
    assert y0 + mm - 1e-6 <= y <= y1 - mm + 1e-6


def test_two_micro_labels_fit_a_strip():
    assert 2 * micro.MICRO_W <= strip.STRIP_W
    assert micro.MICRO_H <= strip.STRIP_H


def test_pack_groups_in_twos_when_asked_and_fours_by_default():
    a, b, c = object(), object(), object()
    assert micro.pack([a, b, c], per_sticker=2) == [(a, b), (c, None)]
    assert micro.pack([a, b, c]) == [(a, b, c, None)]


def test_a_roll_is_one_page_per_sticker(micro_docs):
    only = set(micro.kinds())
    n_micro = len(micro.selected(micro_docs, only))
    assert n_micro == 3
    buf = io.BytesIO()
    n, stickers = labels.render(micro_docs, buf, only, invariant=True, stock="dymo-99010")
    assert n == n_micro
    assert stickers == math.ceil(n_micro / 2)
    pages = _pages(buf.getvalue())
    assert len(pages) == stickers
    assert all(p == pytest.approx(R.page) for p in pages)


def test_a_roll_is_the_same_bytes_every_time(micro_docs):
    def once():
        buf = io.BytesIO()
        labels.render(micro_docs, buf, set(micro.kinds()), invariant=True, stock="dymo-99010")
        return buf.getvalue()
    assert once() == once()


def test_a_label_kind_with_no_strip_yet_is_refused_by_name(docs, monkeypatch):
    monkeypatch.setattr(strip, "STRIP_OF", {})
    with pytest.raises(roll.NoStripError, match="no strip layout"):
        roll.roll_rows(docs, {"rpi"})


def test_a_roll_has_no_positions_to_leave_blank(docs):
    with pytest.raises(ValueError, match="roll"):
        labels.render(docs, io.BytesIO(), {"rpi"}, start=1, stock="dymo-99010")


def test_the_command_takes_a_roll(micro_data, tmp_path, capsys):
    out = tmp_path / "roll.pdf"
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(micro_data), *only, "--stock", "dymo-99010",
                        "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert re.search(r"3 labels on 2 stickers -> ", said)
    assert _pages(out.read_bytes())[0] == pytest.approx(R.page)


def test_list_on_a_roll_counts_stickers(micro_data, capsys):
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(micro_data), *only, "--stock", "dymo-99010",
                        "--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert all(re.match(r"sticker \d+  ", ln) for ln in lines)
    assert lines[0].startswith("sticker 1  ")


@pytest.mark.parametrize("extra", [["--start", "1"], ["--list", "--json"],
                                   ["--place", "plan.json"]])
def test_sheet_only_options_are_refused_on_a_roll(data_dir, extra):
    with pytest.raises(SystemExit):
        labels.main(["--data", str(data_dir), "--stock", "dymo-99010", *extra])


def test_a_kind_with_no_strip_stops_the_command_with_its_reason(data_dir, tmp_path, capsys,
                                                                monkeypatch):
    monkeypatch.setattr(strip, "STRIP_OF", {})
    assert labels.main(["--data", str(data_dir), "--only", "rpi", "--stock", "dymo-99010",
                        "--out", str(tmp_path / "r.pdf")]) == 1
    assert "no strip layout" in capsys.readouterr().err


class _Ctm:
    """A canvas that only keeps the transform _place builds (translate and
    rotate), as the matrix a point is mapped through."""

    def __init__(self):
        self.m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # a b c d e f, PDF order

    def translate(self, dx, dy):
        a, b, c, d, e, f = self.m
        self.m = (a, b, c, d, a * dx + c * dy + e, b * dx + d * dy + f)

    def rotate(self, deg):
        a, b, c, d, e, f = self.m
        s, k = math.sin(math.radians(deg)), math.cos(math.radians(deg))
        self.m = (a * k + c * s, b * k + d * s, -a * s + c * k, -b * s + d * k, e, f)

    def map(self, u, v):
        a, b, c, d, e, f = self.m
        return a * u + c * v + e, b * u + d * v + f


@pytest.mark.parametrize("u", [0, strip.STRIP_W])
@pytest.mark.parametrize("v", [0, strip.STRIP_H])
def test_the_transform_render_roll_applies_puts_the_strip_in_the_printable_area(u, v):
    """roll._place is what render_roll draws through: its matrix must agree
    with page_point and put every corner a millimetre inside the printable area."""
    c = _Ctm()
    roll._place(c, R)
    x, y = c.map(u, v)
    assert (x, y) == pytest.approx(roll.page_point(R, u, v))
    x0, y0, x1, y1 = R.printable
    assert x0 + mm - 1e-6 <= x <= x1 - mm + 1e-6
    assert y0 + mm - 1e-6 <= y <= y1 - mm + 1e-6


def test_the_library_api_gets_the_roll_through_render_sheet(data_dir):
    import json

    from rpi_hwid import label_input
    from rpi_hwid.collect import load_collected
    host = "pi-sw2-p47"                 # a Pi: one board label, so one sticker
    doc = load_collected(data_dir)[host]
    inputs = [json.loads(label_input.dumps(label_input.from_probe(host, doc.evidence)))]
    assert len(roll.roll_rows(labels.documents(inputs), {"rpi"})) == 1
    pdf = labels.render_sheet(inputs, only={"rpi"}, stock="dymo-99010")
    assert _pages(pdf) == [pytest.approx(R.page)]


# --- printing: --print QUEUE ---------------------------------------------------


def _queue(states, *, printer_state=3, sizes=((2786, 8890),), accepting=True,
           message="", reasons=("none",)):
    """A fake CUPS queue: printer attributes, a job id, then `states` in turn."""
    seen = iter(states)

    def answer(msg):
        if msg.code == ipp.GET_PRINTER_ATTRIBUTES:
            return ok(ipp.PRINTER_GROUP, {
                "printer-state": ipp.enum(printer_state),
                "printer-state-message": ipp.text(message),
                "printer-is-accepting-jobs": ipp.boolean(accepting),
                "media-size-supported": [ipp.collection({
                    "x-dimension": ipp.integer(x), "y-dimension": ipp.integer(y)})
                    for x, y in sizes]})
        if msg.code == ipp.PRINT_JOB:
            return ok(ipp.JOB_GROUP, {"job-id": ipp.integer(77), "job-state": ipp.enum(3)})
        return ok(ipp.JOB_GROUP, {"job-state": ipp.enum(next(seen)),
                                  "job-state-reasons": [ipp.keyword(r) for r in reasons]})
    return FakePrinter(answer)


def test_a_queue_name_is_the_local_cups_queue():
    assert roll.queue_uri("LabelWriter-450") == "ipp://localhost:631/printers/LabelWriter-450"
    assert roll.queue_uri("ipp://h/ipp/print") == "ipp://h/ipp/print"


def test_the_roll_is_printed_at_its_size_unscaled_for_codes():
    p = _queue([5, 9])
    try:
        assert roll.print_pdf(b"%PDF-roll", p.uri, R, "labels", poll=0.01) == "completed"
        job = next(m for m in p.requests if m.code == ipp.PRINT_JOB)
        attrs = job.attributes("job")
        assert attrs["media-col"] == [{"media-size": [{"x-dimension": [2786],
                                                       "y-dimension": [8890]}]}]
        assert attrs["print-scaling"] == ["none"]
        assert attrs["printer-resolution"] == [(300, 600, 3)]
    finally:
        p.close()


@pytest.mark.parametrize(("kw", "words"), [
    ({"printer_state": 5, "message": "Unplugged or turned off"},
     "stopped.*Unplugged or turned off"),
    ({"accepting": False}, "not accepting"),
    ({"sizes": ((5419, 2227),)}, r"27\.86 x 88\.9 mm.*supports 54\.19 x 22\.27 mm"),
])
def test_a_queue_that_cannot_print_the_roll_is_refused_before_sending(kw, words):
    p = _queue([], **kw)
    try:
        with pytest.raises(roll.PrintError, match=words):
            roll.print_pdf(b"%PDF-roll", p.uri, R, "labels", poll=0.01)
        assert not any(m.code == ipp.PRINT_JOB for m in p.requests)
    finally:
        p.close()


@pytest.mark.parametrize(("state", "name"), [(8, "aborted"), (7, "canceled")])
def test_a_job_that_does_not_complete_is_an_error_naming_why(state, name):
    p = _queue([state], reasons=("job-canceled-at-device",))
    try:
        with pytest.raises(roll.PrintError, match=f"{name}.*job-canceled-at-device"):
            roll.print_pdf(b"%PDF-roll", p.uri, R, "labels", poll=0.01)
    finally:
        p.close()


def test_a_job_still_going_at_the_deadline_names_it_for_lpstat():
    p = _queue([5] * 1000)
    try:
        with pytest.raises(roll.PrintError, match=r"job 77.*lpstat"):
            roll.print_pdf(b"%PDF-roll", p.uri, R, "labels", poll=0.01, wait=0.05)
    finally:
        p.close()


def test_an_unreachable_queue_is_an_error_naming_the_uri_tried():
    uri = "ipp://127.0.0.1:9/ipp/print"
    with pytest.raises(roll.PrintError, match=r"ipp://127\.0\.0\.1:9/ipp/print"):
        roll.print_pdf(b"%PDF-roll", uri, R, "labels", poll=0.01)


def test_print_is_for_a_roll(data_dir):
    with pytest.raises(SystemExit):
        labels.main(["--data", str(data_dir), "--print", "LabelWriter-450"])


def test_the_command_prints_the_roll_it_wrote(micro_data, tmp_path, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(roll, "print_pdf", lambda pdf, q, r, name, **k: (
        sent.append((pdf[:5], q, r.name)), "completed")[1])
    out = tmp_path / "roll.pdf"
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(micro_data), *only, "--stock", "dymo-99010",
                        "--out", str(out), "--print", "LabelWriter-450"]) == 0
    assert sent == [(b"%PDF-", "LabelWriter-450", "dymo-99010")]
    assert "completed" in capsys.readouterr().out


def test_a_failed_print_is_one_line_on_stderr_and_exit_1(micro_data, tmp_path, monkeypatch,
                                                         capsys):
    def refuse(*a, **k):
        raise roll.PrintError("LabelWriter-450: the queue is not accepting jobs")
    monkeypatch.setattr(roll, "print_pdf", refuse)
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(micro_data), *only, "--stock", "dymo-99010",
                        "--out", str(tmp_path / "r.pdf"), "--print", "LabelWriter-450"]) == 1
    assert capsys.readouterr().err.strip() == "LabelWriter-450: the queue is not accepting jobs"


@pytest.mark.parametrize("extra", [[], ["--print", "LabelWriter-450"]])
def test_a_roll_with_no_labels_is_refused_before_writing_or_printing(
        micro_data, tmp_path, monkeypatch, capsys, extra):
    monkeypatch.setattr(roll, "print_pdf",
                        lambda *a, **k: pytest.fail("a blank sticker would be printed"))
    out = tmp_path / "roll.pdf"
    # --only of a kind this data has no label of: nothing is selected
    assert labels.main(["--data", str(micro_data), "--only", "usb", "--stock", "dymo-99010",
                        "--out", str(out), *extra]) == 1
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "no labels" in err
    assert not out.exists()
