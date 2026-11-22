"""A Label knows its own size (rpi_hwid.labels.Label), so a strip can be one."""
from __future__ import annotations

import io

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from rpi_hwid import labels, micro


def _canvas():
    return canvas.Canvas(io.BytesIO())


def test_a_label_is_the_sheet_label_size_unless_told():
    lab = labels.Label(_canvas(), 10, 20)
    assert (lab.w, lab.h) == (labels.LABEL_W, labels.LABEL_H)
    assert lab.pt(0, 0) == (10, 20 + labels.LABEL_H)


def test_a_label_of_another_size_measures_from_its_own_top():
    lab = labels.Label(_canvas(), 10, 20, 80 * mm, 25 * mm)
    assert (lab.w, lab.h) == (80 * mm, 25 * mm)
    assert lab.pt(3, 4) == (13, 20 + 25 * mm - 4)


def test_a_micro_cell_is_a_label_a_quarter_the_size():
    cell = micro.Cell(_canvas(), 0, 0)
    assert (cell.w, cell.h) == (micro.MICRO_W, micro.MICRO_H)
    assert "pt" not in vars(micro.Cell)
    assert "outline" not in vars(micro.Cell)


def test_outline_and_cut_line_draw_the_labels_own_size(monkeypatch):
    rects = []
    c = _canvas()
    monkeypatch.setattr(c, "rect", lambda *a, **k: rects.append(a[:4]))
    lab = labels.Label(c, 1, 2, 50, 30)
    lab.outline()
    lab.cut_line()
    assert rects == [(1, 2, 50, 30), (1, 2, 50, 30)]
