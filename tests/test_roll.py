"""A roll of stickers (labels.ROLLS, rpi_hwid.roll): one driver-sized page per
sticker, the strip turned onto it inside the printable area."""
from __future__ import annotations

import io
import math
import re

import pytest
from reportlab.lib.units import mm

from rpi_hwid import labels, micro, roll, strip
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
