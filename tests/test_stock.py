"""The sheets a label can be laid out on (labels.STOCKS)."""
from __future__ import annotations

import io
import re

import pytest
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import inch, mm

from rpi_hwid import labels


def test_l7160_is_the_grid_it_always_was():
    s = labels.STOCKS["L7160"]
    assert s.page == A4
    assert (s.cols, s.rows) == (3, 7)
    for index in range(s.per_sheet):
        assert s.label_origin(index) == labels.label_origin(index)
        assert s.sticker_origin(index) == labels.label_origin(index)


def test_avery_5163_is_averys_own_template():
    """Avery's PDF template for 5163 (U-0090-01.pdf) draws ten 4 x 2 in
    outlines: left edges 0.1556 and 4.3438 in from the left, top edges
    0.5, 2.5, 4.5, 6.5, 8.5 in from the top, on an 8.5 x 11 in page."""
    s = labels.STOCKS["avery-5163"]
    assert s.page == letter
    assert s.per_sheet == 10
    lefts = sorted({round(s.sticker_origin(i)[0] / inch, 3) for i in range(10)})
    tops = sorted({round((s.page[1] - s.sticker_origin(i)[1] - s.sticker_h) / inch, 3)
                   for i in range(10)})
    assert lefts == [pytest.approx(0.1556, abs=0.001), pytest.approx(4.3438, abs=0.001)]
    assert tops == [0.5, 2.5, 4.5, 6.5, 8.5]
    assert (s.sticker_w / inch, s.sticker_h / inch) == (4, 2)
    # the right margin mirrors the left: the grid is centred on the sheet
    assert s.page[0] - (s.sticker_origin(1)[0] + s.sticker_w) == pytest.approx(s.margin_x)


@pytest.mark.parametrize("name", sorted(labels.STOCKS))
def test_every_label_sits_whole_inside_its_sticker_and_its_sheet(name):
    s = labels.STOCKS[name]
    edge = 4 * mm                 # what a laser printer cannot print on
    for index in range(s.per_sheet):
        sx, sy = s.sticker_origin(index)
        x, y = s.label_origin(index)
        assert sx <= x + 1e-6
        assert x + labels.LABEL_W <= sx + s.sticker_w + 1e-6
        assert sy <= y + 1e-6
        assert y + labels.LABEL_H <= sy + s.sticker_h + 1e-6
        # centred, at its own size: never stretched to the sticker
        assert x - sx == pytest.approx(sx + s.sticker_w - x - labels.LABEL_W)
        assert y - sy == pytest.approx(sy + s.sticker_h - y - labels.LABEL_H)
        if name != "L7160":
            assert edge <= x
            assert x + labels.LABEL_W <= s.page[0] - edge
            assert edge <= y
            assert y + labels.LABEL_H <= s.page[1] - edge
    # stickers do not overlap
    assert s.gap_x >= 0
    assert s.gap_y >= 0
    assert s.margin_x + s.cols * s.sticker_w + (s.cols - 1) * s.gap_x <= s.page[0] + 1e-6
    assert s.margin_y + s.rows * s.sticker_h + (s.rows - 1) * s.gap_y <= s.page[1] + 1e-6


def _pages(pdf):
    """Each page's size in points, from the PDF's own MediaBox entries."""
    return [(float(w), float(h))
            for w, h in re.findall(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", pdf)]


def _pdf(docs, **kw):
    buf = io.BytesIO()
    n, sheets = labels.render(docs, buf, only={"rpi"}, invariant=True, **kw)
    return n, sheets, buf.getvalue()


def test_the_default_sheet_is_unchanged_by_naming_it(docs):
    assert _pdf(docs)[2] == _pdf(docs, stock="L7160")[2]


@pytest.mark.parametrize(("name", "words"), [
    ("avery-5163", "Not yet tried on real stock"), ("letter-plain", "cut along the lines")])
def test_a_us_letter_sheet(docs, name, words, monkeypatch):
    s = labels.STOCKS[name]
    notes = []
    real = labels.canvas.Canvas.drawCentredString
    monkeypatch.setattr(labels.canvas.Canvas, "drawCentredString",
                        lambda self, x, y, text, *a, **k: (
                            notes.append((self.getPageNumber(), y, text)),
                            real(self, x, y, text, *a, **k))[1])
    start = s.per_sheet - 1               # one label on the first sheet, the rest after it
    n, sheets, pdf = _pdf(docs, stock=name, start=start)
    assert n > 1
    assert sheets == -(-(n + start) // s.per_sheet) >= 2
    pages = _pages(pdf)
    assert len(pages) == sheets
    assert all(page == pytest.approx(letter) for page in pages)
    said = [(page, y) for page, y, text in notes if words in text]
    assert [page for page, _ in said] == list(range(1, sheets + 1)), "one note on every sheet"
    top = s.page[1] - s.margin_y          # the first row of stickers starts here
    assert all(top < y < s.page[1] - 4 * mm for _, y in said), "in the top margin, printable"


def test_cut_lines_are_drawn_on_plain_paper_only(docs, monkeypatch):
    cut = []
    monkeypatch.setattr(labels.Label, "cut_line", lambda self: cut.append((self.x0, self.y0)))
    n, _, _ = _pdf(docs, stock="letter-plain")
    assert len(cut) == n
    cut.clear()
    _pdf(docs, stock="avery-5163", outline=True)
    _pdf(docs)
    assert cut == []


def test_the_command_takes_a_stock(data_dir, tmp_path, capsys):
    out = tmp_path / "us.pdf"
    assert labels.main(["--data", str(data_dir), "--only", "rpi", "--stock", "avery-5163",
                        "--out", str(out)]) == 0
    assert "labels on" in capsys.readouterr().out
    assert _pages(out.read_bytes())[0] == pytest.approx(letter)
    with pytest.raises(SystemExit):
        labels.main(["--data", str(data_dir), "--stock", "avery-5163", "--list", "--json"])
