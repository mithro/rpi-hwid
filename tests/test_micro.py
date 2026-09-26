"""Micro labels: the small layout, four to one sticker, and every QR decoding."""

from __future__ import annotations

import glob
import shutil
import subprocess

import pytest

from rpi_hwid import labels, micro
from rpi_hwid.micro import Icon, MicroLabel, MicroRow


def _label(**kw):
    base = {"host": "bench-1", "title": "Widget 3000", "ident_caption": "MAC",
            "ident": "02:00:00:12:34:56"}
    base.update(kw)
    return MicroLabel(**base)


def test_four_micro_labels_tile_one_sticker_exactly():
    assert pytest.approx(labels.LABEL_W) == micro.MICRO_COLS * micro.MICRO_W
    assert pytest.approx(labels.LABEL_H) == micro.MICRO_ROWS * micro.MICRO_H
    assert micro.PER_STICKER == 4


def test_a_label_without_its_identifier_is_refused():
    """No placeholder, no rule to write one in: an unread identifier is an
    error naming the host, raised before anything is drawn."""
    with pytest.raises(micro.IdentifierMissingError, match="bench-1"):
        _label(ident="")
    with pytest.raises(micro.IdentifierMissingError, match="bench-1"):
        _label(ident="   ")


def test_a_row_without_a_value_is_refused():
    with pytest.raises(ValueError, match=r"bench-1.*flash"):
        _label(rows=(MicroRow("flash", ""),))


def test_too_many_rows_is_an_error_not_a_silent_drop():
    rows = tuple(MicroRow(f"r{i}", f"value {i}") for i in range(micro.MAX_ROWS + 1))
    with pytest.raises(ValueError, match="bench-1"):
        _label(rows=rows)
    assert len(_label(rows=rows[:-1]).rows) == micro.MAX_ROWS


def test_an_unknown_icon_is_an_error():
    with pytest.raises(ValueError, match="no-such-glyph"):
        _label(icons=(Icon("no-such-glyph"),))


def test_a_caller_can_register_its_own_icon(monkeypatch):
    seen = []

    def plug(lab, x, y, size, text):
        seen.append((round(size, 3), text))
        return size

    monkeypatch.setitem(micro.ICONS, "plug", plug)
    lab = _label(icons=(Icon("plug", "AU"),))
    micro.render_micro([lab], _null_pdf())
    assert seen
    assert seen[0][1] == "AU"


def test_the_qr_defaults_to_the_identifier():
    assert _label().qr_content == "02:00:00:12:34:56"
    assert _label(qr="https://example.org/x").qr_content == "https://example.org/x"


def test_pack_keeps_order_and_fills_by_four():
    ls = [_label(host=f"h{i}") for i in range(6)]
    groups = micro.pack(ls)
    assert [len(g) for g in groups] == [4, 4]
    assert [g.host if g else None for g in groups[0]] == ["h0", "h1", "h2", "h3"]
    assert [g.host if g else None for g in groups[1]] == ["h4", "h5", None, None]


def test_pack_can_skip_used_quarters_on_the_first_sticker():
    groups = micro.pack([_label(host="a"), _label(host="b")], start=3)
    assert [g.host if g else None for g in groups[0]] == [None, None, None, "a"]
    assert [g.host if g else None for g in groups[1]] == ["b", None, None, None]


def test_micro_origin_is_reading_order_within_the_sticker():
    x0, y0 = 100.0, 200.0
    assert micro.micro_origin(x0, y0, 0) == (x0, y0 + micro.MICRO_H)
    assert micro.micro_origin(x0, y0, 1) == (x0 + micro.MICRO_W, y0 + micro.MICRO_H)
    assert micro.micro_origin(x0, y0, 2) == (x0, y0)
    assert micro.micro_origin(x0, y0, 3) == (x0 + micro.MICRO_W, y0)


def _null_pdf():
    import io
    return io.BytesIO()


def _spy_text(monkeypatch):
    """Every string drawn, with its left and right edge in cell coordinates."""
    drawn = []
    real = micro.Cell.text

    def text(self, x, y, s, font=labels.SANS, size=8, align="left", color=None, **kw):
        w = self.width(s, font, size)
        left = x - w if align == "right" else x - w / 2 if align == "centre" else x
        drawn.append((s, left, left + w, y, self.w, self.h))
        return real(self, x, y, s, font, size, align, **({"color": color} if color else {}))

    monkeypatch.setattr(micro.Cell, "text", text)
    return drawn


def test_nothing_runs_off_the_cell_even_when_every_field_is_long(monkeypatch):
    drawn = _spy_text(monkeypatch)
    lab = _label(title="An Unreasonably Long Device Title For A Tiny Label",
                 subtitle="subtitle that goes on and on and on and on",
                 ident="0123456789abcdef0123456789abcdef",
                 rows=tuple(MicroRow("caption", "x" * 60) for _ in range(micro.MAX_ROWS)),
                 icons=(Icon("wifi"), Icon("usb"), Icon("chip", "C3")))
    micro.render_micro([lab], _null_pdf())
    assert drawn
    for s, left, right, y, w, h in drawn:
        assert left >= micro.MICRO_PAD - 0.01, (s, left)
        assert right <= w - micro.MICRO_PAD + 0.01, (s, right)
        assert micro.MICRO_PAD - 0.01 <= y <= h - micro.MICRO_PAD, (s, y)


def test_an_identifier_row_that_cannot_fit_whole_is_refused_not_elided():
    lab = _label(rows=(MicroRow("uid", "0123456789abcdef" * 3, mono=True),))
    with pytest.raises(ValueError, match="never elided"):
        micro.render_micro([lab], _null_pdf())


def test_a_mono_row_of_sixteen_hex_digits_fits(monkeypatch):
    drawn = _spy_text(monkeypatch)
    lab = _label(rows=(MicroRow("uid", "0123456789abcdef", mono=True),))
    micro.render_micro([lab], _null_pdf())
    assert "0123456789abcdef" in [d[0] for d in drawn]


def test_the_identifier_is_printed_whole_never_elided(monkeypatch):
    drawn = _spy_text(monkeypatch)
    micro.render_micro([_label(ident="e8:3d:c1:8c:5c:88")], _null_pdf())
    assert "e8:3d:c1:8c:5c:88" in [d[0] for d in drawn]


def test_the_extra_section_gets_the_room_under_the_rows():
    boxes = []

    def extra(cell, box):
        boxes.append(box)

    micro.render_micro([_label(rows=(MicroRow("a", "b"),), extra=extra)], _null_pdf())
    (box,) = boxes
    x, y, w, h = box
    assert w > 0
    assert h > 0
    assert x + w <= micro.MICRO_W - micro.MICRO_PAD + 0.01
    assert y + h <= micro.foot_top() + 0.01


def test_render_micro_counts(tmp_path):
    out = tmp_path / "micro.pdf"
    n, stickers, sheets = micro.render_micro([_label(host=f"h{i}") for i in range(9)], out)
    assert (n, stickers, sheets) == (9, 3, 1)
    assert out.stat().st_size > 5_000


def test_render_and_decode_every_micro_qr(tmp_path):
    ls = [
        _label(host="a", ident="02:00:00:00:00:01", icons=(Icon("wifi"),),
               rows=(MicroRow("rev", "v1.0"), MicroRow("uid", "1122334455667788", mono=True))),
        _label(host="b", ident="02:00:00:00:00:02", subtitle="a subtitle"),
        _label(host="c", ident="02:00:00:00:00:03", qr="https://example.org/device/3"),
        _label(host="d", ident="02:00:00:00:00:04", icons=(Icon("usb"), Icon("chip", "C3"))),
        _label(host="e", ident="02:00:00:00:00:05"),
    ]
    out = tmp_path / "micro.pdf"
    micro.render_micro(ls, out, outline=True)
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        pytest.skip("pdftoppm not installed")
    zxingcpp = pytest.importorskip("zxingcpp")
    from PIL import Image

    subprocess.run([pdftoppm, "-r", "600", "-png", str(out), str(tmp_path / "page")],
                   check=True)
    got = set()
    for png in sorted(glob.glob(str(tmp_path / "page-*.png"))):
        got |= {b.text for b in zxingcpp.read_barcodes(Image.open(png))}
    assert got == {"02:00:00:00:00:01", "02:00:00:00:00:02", "https://example.org/device/3",
                   "02:00:00:00:00:04", "02:00:00:00:00:05"}


def test_list_rows_name_sticker_and_quarter():
    rows = micro.listing([_label(host=f"h{i}") for i in range(5)], start=2)
    assert rows[0] == (1, 1, 1, 3, "h0", "Widget 3000 02:00:00:12:34:56")
    assert rows[2] == (1, 1, 2, 1, "h2", "Widget 3000 02:00:00:12:34:56")


class _FakeProvider:
    """A device module as ``micro.providers`` finds them: a KIND and a
    ``micro_labels(docs)``."""

    KIND = "widget"

    @staticmethod
    def micro_labels(docs):
        return [MicroLabel(host=h, title="Widget", ident_caption="MAC",
                           ident=f"02:00:00:00:{i:02x}:01")
                for i, h in enumerate(sorted(docs)[:5])]


def test_no_provider_ships_in_the_base_layout():
    """The layout is device-neutral: device modules add themselves, and are
    found by their name."""
    assert all(name.endswith("_micro") for name in micro.provider_modules())


def test_micro_stickers_come_after_every_whole_label(docs, monkeypatch):
    monkeypatch.setattr(micro, "providers", lambda: {"widget": _FakeProvider})
    rows = list(labels.all_labels(docs, set(labels.KINDS) | {"widget"}))
    kinds = [r[1] for r in rows]
    assert kinds[-2:] == ["micro", "micro"]
    assert "micro" not in kinds[:-2]
    groups = [r[4] for r in rows[-2:]]
    assert [sum(m is not None for m in g) for g in groups] == [4, 1]
    assert rows[-1][2] == "Widget 02:00:00:00:04:01"


def test_only_selects_micro_kinds(docs, monkeypatch):
    monkeypatch.setattr(micro, "providers", lambda: {"widget": _FakeProvider})
    assert [r[1] for r in labels.all_labels(docs, {"widget"})] == ["micro", "micro"]
    rpi = [r[1] for r in labels.all_labels(docs, {"rpi"})]
    assert rpi
    assert "micro" not in rpi


def test_the_labels_command_prints_micro_stickers(data_dir, tmp_path, monkeypatch, capsys):
    from rpi_hwid.cli import main as cli_main

    monkeypatch.setattr(micro, "providers", lambda: {"widget": _FakeProvider})
    assert cli_main(["labels", "--data", str(data_dir), "--list", "--only", "widget"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert "micro" in out[0]
    assert "Widget 02:00:00:00:00:01" in out[0]
    pdf = tmp_path / "l.pdf"
    assert cli_main(["labels", "--data", str(data_dir), "--out", str(pdf),
                     "--only", "widget"]) == 0
    assert "2 labels on 1 sheet" in capsys.readouterr().out
