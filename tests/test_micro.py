"""Micro labels: the small layout, four to one sticker, and every QR decoding."""

from __future__ import annotations

import glob
import shutil
import subprocess

import pytest
from reportlab.pdfbase import pdfmetrics

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


class _Canvas:
    """Records what a glyph draws: enough of reportlab's canvas for them."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def _glyph(monkeypatch, name, text):
    cell = micro.Cell(_Canvas(), 0, 0)
    rotated, flat = [], []
    monkeypatch.setattr(micro.Cell, "rotated",
                        lambda self, x, y, s, font, size, color=None: rotated.append(
                            (x, y, s, font, size)))
    monkeypatch.setattr(micro.Cell, "text",
                        lambda self, x, y, s, font=None, size=8, **k: flat.append(s))
    labels.register_fonts()
    w = micro.ICONS[name](cell, 0, 0, micro.HEAD_H, text)
    return cell, w, rotated, flat


def test_the_antenna_s_text_is_its_mast_set_upright(monkeypatch):
    """Tim, 2026-09-27: the band, "433", is the pole itself, rotated 90
    degrees and reading upwards, not a caption beside the mast's foot."""
    cell, w, rotated, flat = _glyph(monkeypatch, "antenna", "433")
    assert flat == []
    ((x, y, s, font, size),) = rotated
    assert s == "433"
    assert font == labels.SANS_BOLD
    assert size >= micro.MIN_SIZE
    run = cell.width("433", font, size)
    # standing on the foot and reaching most of the way up the header
    assert micro.HEAD_H * 0.75 <= run <= y
    assert y <= micro.HEAD_H
    # the column is centred in the glyph, with the waves either side of it
    assert x + size * 0.72 / 2 == pytest.approx(w / 2)
    assert w == pytest.approx(micro._icon_width(cell, Icon("antenna", "433")))
    arcs = [a for name, a in cell.c.calls if name == "arc"]
    assert len(arcs) == 4


def test_the_plain_antenna_keeps_its_line_mast(monkeypatch):
    cell, w, rotated, flat = _glyph(monkeypatch, "antenna", "")
    assert rotated == []
    assert flat == []
    assert any(name == "line" for name, _ in cell.c.calls)
    assert w == pytest.approx(micro._icon_width(cell, Icon("antenna")))


def test_a_band_too_long_to_stand_as_the_mast_is_refused(monkeypatch):
    """Below 4 pt it is an error, not a smaller mast -- and it is raised when
    the label is made, naming the host, not half way through a sheet."""
    with pytest.raises(ValueError, match=r"bench-1: .*'433\.92 MHz'.*4 pt"):
        _label(icons=(Icon("antenna", "433.92 MHz"),))
    with pytest.raises(ValueError, match=r"433\.92 MHz"):
        _glyph(monkeypatch, "antenna", "433.92 MHz")


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


# --- the spec strip and its glyphs ----------------------------------------------

SPEC_GLYPHS = ("riscv", "xtensa", "cores", "memory", "tasmota", "bluetooth", "zigbee")


def test_the_spec_glyphs_are_registered():
    assert set(SPEC_GLYPHS) <= set(micro.ICONS)


@pytest.mark.parametrize(("name", "text"), [
    ("riscv", ""), ("xtensa", ""), ("cores", "1"), ("cores", "2+1"), ("cores", "1+0"),
    ("memory", "400K"), ("memory", "512K+8M"), ("tasmota", ""), ("bluetooth", ""),
    ("zigbee", ""), ("wifi", "2.4 b/g/n"), ("wifi", "2.4 b/g/n/ax"),
    ("wifi", "2.4/5 a/b/g/n/ac/ax"), ("wifi", ""), ("chip", "C3"), ("antenna", "433"),
    ("usb", ""), ("wifi", "2.4 n +bt"), ("wifi", "2.4/5 ax +bt +zb"), ("usb", "OJS"),
    ("usb", "JS"), ("usb", "O"), ("revision", "v0.4"),
])
def test_a_glyph_takes_the_width_the_layout_reserves_for_it(name, text):
    """The header and the strip are laid out from ``_icon_width`` before
    anything is drawn, so every glyph must draw exactly that wide."""
    from reportlab.pdfgen import canvas

    labels.register_fonts()
    # the lettered antenna and the lettered Wi-Fi arcs are header glyphs
    # only: their lettering needs the height
    header_only = name == "antenna" or (name in ("wifi", "usb") and text)
    for h in (micro.HEAD_H,) if header_only else (micro.HEAD_H, micro.SPEC_H):
        cell = micro.Cell(canvas.Canvas(_null_pdf()), 0, 0)
        w = micro.ICONS[name](cell, 0, 0, h, text)
        assert w > 0
        assert w == pytest.approx(micro._icon_width(cell, Icon(name, text), h))


def _glyph_text(monkeypatch, name, text, size=None):
    """(string, x, y, font, size, colour) for everything a glyph letters."""
    cell = micro.Cell(_Canvas(), 0, 0)
    flat = []
    monkeypatch.setattr(micro.Cell, "text",
                        lambda self, x, y, s, font=None, size=8, align="left", color=None,
                        **k: flat.append((s, x, y, font, size, align, color)))
    labels.register_fonts()
    w = micro.ICONS[name](cell, 0, 0, size or micro.HEAD_H, text)
    return w, flat


@pytest.mark.parametrize(("text", "lines"), [
    ("2.4 b/g/n", ["2.4", "b/g/n"]),
    ("2.4 b/g/n/ax", ["2.4", "b/g/n", "ax"]),
    ("2.4/5 a/b/g/n/ac/ax", ["2.4/5", "a/b/g/n", "ac/ax"]),
    # the newest standard alone, as the ESP32 labels print it: joined to the
    # band under the arcs, "2.4n", "2.4/5ax" (Tim, 2026-09-27)
    ("2.4 n", ["2.4n"]),
    ("2.4 ax", ["2.4ax"]),
    ("2.4/5 ax", ["2.4/5ax"]),
])
def test_the_wifi_glyph_letters_its_bands_and_standards(monkeypatch, text, lines):
    """Tim, 2026-09-27: the Wi-Fi logo says which bands and which 802.11
    standards. The band under the arcs; the single-letter standards beside
    the arcs, the two-letter ones (ac, ax) on a second line under them."""
    w, flat = _glyph_text(monkeypatch, "wifi", text)
    assert [f[0] for f in flat] == lines
    for s, x, y, font, size, align, _ in flat:
        assert size >= micro.MIN_SIZE
        assert font == labels.SANS_BOLD
        width = pdfmetrics.stringWidth(s, font, size)
        left = x - width if align == "right" else x
        assert left >= -0.01, s
        assert left + width <= w + 0.01, s
        assert y >= 0, s
        assert y + size * 0.72 <= micro.HEAD_H + 0.01, s
    band = flat[0]
    assert band[2] == max(f[2] for f in flat)        # the band is on the bottom line
    # the standards are right of the arcs, and of the band under them
    arcs_right = micro.wifi_arcs_width(micro.HEAD_H)
    for s, x, *_ in flat[1:]:
        assert x - pdfmetrics.stringWidth(s, labels.SANS_BOLD, micro.MIN_SIZE) >= min(
            arcs_right, pdfmetrics.stringWidth(band[0], labels.SANS_BOLD, micro.MIN_SIZE))


@pytest.mark.parametrize("text", ["6", "2.4", "b/g/n", "x b/g/n", "2.4 b/q/n", "2.4 b//n"])
def test_a_wifi_text_the_glyph_cannot_letter_is_refused(text):
    with pytest.raises(ValueError, match="bench-1"):
        _label(icons=(Icon("wifi", text),))


def test_the_plain_wifi_glyph_is_the_arcs_alone(monkeypatch):
    w, flat = _glyph_text(monkeypatch, "wifi", "")
    assert flat == []
    assert w == pytest.approx(micro.HEAD_H)


def test_the_memory_glyph_is_as_wide_as_its_size_needs(monkeypatch):
    _, w, _, flat = _glyph(monkeypatch, "memory", "400K")
    assert flat == ["400K"]
    _, wide, _, _ = _glyph(monkeypatch, "memory", "512K+8M")
    assert wide > w


@pytest.mark.parametrize(("text", "counts"), [("1", (1, 0)), ("2", (2, 0)), ("2+1", (2, 1)),
                                              ("1+1", (1, 1)), ("1+0", (1, 0))])
def test_the_cores_glyph_reads_application_and_low_power_cores(text, counts):
    assert micro.core_counts(text) == counts


@pytest.mark.parametrize("text", ["", "0", "x", "10", "2+", "2+10", "+1"])
def test_a_core_count_the_glyph_cannot_draw_is_refused(text):
    with pytest.raises(ValueError, match="bench-1"):
        _label(specs=(Icon("cores", text),))


@pytest.mark.parametrize(("text", "numbers"), [("2+1", ["2", "1"]), ("1+0", ["1", "0"]),
                                               ("1", ["1", "0"])])
def test_the_cores_glyph_is_two_numbers_in_a_package(monkeypatch, text, numbers):
    """Tim, 2026-09-27: no little squares, two numbers. The application
    cores large and black, the low-power cores beside them smaller and
    grey; a part without a low-power core says 0."""
    w, flat = _glyph_text(monkeypatch, "cores", text, micro.SPEC_H)
    assert [f[0] for f in flat] == numbers
    (_, _, _, _, main_size, _, main_colour), (_, x, _, _, lp_size, _, lp_colour) = flat
    assert main_size > lp_size >= micro.MIN_SIZE
    assert main_colour != labels.GREY
    assert lp_colour == labels.GREY
    assert flat[0][1] < x < w


def _spy_glyphs(monkeypatch):
    seen = []
    for name in list(micro.ICONS):
        real = micro.ICONS[name]

        def spy(cell, x, y, size, text, _real=real, _name=name):
            seen.append((_name, x, y, size))
            return _real(cell, x, y, size, text)

        monkeypatch.setitem(micro.ICONS, name, spy)
    return seen


def _strip():
    return (Icon("riscv"), Icon("cores", "1+1"), Icon("memory", "512K"), Icon("tasmota"))


def test_the_spec_strip_heads_the_band_beside_the_qr(monkeypatch):
    seen = _spy_glyphs(monkeypatch)
    micro.render_micro([_label(specs=_strip())], _null_pdf())
    strip = [s for s in seen if s[3] == pytest.approx(micro.SPEC_H)]
    assert [s[0] for s in strip] == ["riscv", "cores", "memory", "tasmota"]
    assert {round(s[2], 6) for s in strip} == {round(micro.band_top(), 6)}
    assert strip[0][1] == pytest.approx(micro.rows_x())
    xs = [s[1] for s in strip]
    assert xs == sorted(xs)


def test_five_rows_under_the_strip_stop_at_the_foot_caption(monkeypatch):
    """Right of the QR the rows may run down beside the foot's caption: the
    last one sits on that caption's baseline, clear of the identifier.
    Under a strip there are SPEC_ROWS of them, set at most SPEC_ROW."""
    drawn = _spy_text(monkeypatch)
    rows = (MicroRow("rev", "v0.4 · 40 MHz"), MicroRow("flash", "XMC 0x464016 · 4 MiB"),
            MicroRow("uid", "0123456789", mono=True),
            MicroRow("chip", "0123456789abcdef", mono=True),
            MicroRow("", "fedcba9876543210", mono=True))
    assert len(rows) == micro.SPEC_ROWS
    micro.render_micro([_label(specs=_strip(), rows=rows)], _null_pdf())
    last = next(d for d in drawn if d[0] == "fedcba9876543210")
    ident = next(d for d in drawn if d[0] == "02:00:00:12:34:56")
    caption = next(d for d in drawn if d[0] == "MAC")
    assert last[1] >= micro.rows_x() - 0.01
    assert caption[2] < micro.rows_x()
    # its cap height ends on or above the caption's baseline
    size = micro.SPEC_ROW
    assert last[3] + size * 0.72 <= caption[3] + micro.CAPTION * 0.72 + 0.01
    assert last[3] + size * 0.72 < ident[3]


def test_a_strip_too_wide_for_the_band_is_refused():
    wide = tuple(Icon("memory", "999K+999M") for _ in range(4))
    with pytest.raises(ValueError, match=r"bench-1.*strip"):
        _label(specs=wide)


def test_the_foot_caption_must_leave_room_for_the_rows_beside_it():
    rows = tuple(MicroRow(f"r{i}", "v") for i in range(micro.MAX_ROWS))
    with pytest.raises(ValueError, match=r"bench-1.*caption"):
        micro.render_micro([_label(ident_caption="A Very Long Foot Caption Indeed",
                                   rows=rows)], _null_pdf())


def test_the_two_isa_marks_take_the_same_box():
    """Tim, 2026-09-27: the Xtensa mark as big as the RISC-V one, which is
    RISC-V International's mark without its wordmark."""
    for h in (micro.SPEC_H, micro.HEAD_H):
        assert micro._icon_width(None, Icon("xtensa"), h) == pytest.approx(
            micro._icon_width(None, Icon("riscv"), h))
        assert micro._icon_width(None, Icon("riscv"), h) == pytest.approx(h, rel=0.05)


def test_the_xtensa_mark_is_its_x_and_t(monkeypatch):
    _, flat = _glyph_text(monkeypatch, "xtensa", "", micro.SPEC_H)
    assert [f[0] for f in flat] == ["X", "t"]


def test_the_riscv_glyph_draws_the_simplified_mark(monkeypatch):
    drawn = []
    monkeypatch.setattr(micro.Cell, "svg", lambda self, path, x, y, h: drawn.append(path) or h)
    micro.ICONS["riscv"](micro.Cell(_Canvas(), 0, 0), 0, 0, micro.SPEC_H, "")
    assert [p.rsplit("/", 1)[-1] for p in map(str, drawn)] == ["risc-v-simple.svg"]


def test_the_full_riscv_logo_is_kept_byte_for_byte():
    """risc-v.svg is RISC-V International's file, shared with the board
    labels' branch: it stays byte for byte what their site serves."""
    import hashlib

    data = (micro.labels.artwork("risc-v.svg") or "").encode()
    assert data
    with open(micro.labels.artwork("risc-v.svg"), "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    assert digest == "38bd6ca96a81a16a5d5dda173327cafd5ad45fa22df0df1e54f1b80fd128085b"


def test_title_fits_says_when_the_header_would_elide_the_title():
    assert micro.title_fits(_label(title="ESP32-C6FH4", icons=(Icon("wifi"),)))
    crowded = (Icon("wifi"), Icon("bluetooth"), Icon("zigbee"), Icon("usb"), Icon("usb"))
    assert not micro.title_fits(_label(title="ESP32-C6FH4", icons=crowded))


def test_under_a_spec_strip_the_extra_section_runs_down_to_the_caption_line():
    """What a label that builds on the ESP32 one gets when it drops a row."""
    boxes = []
    rows = (MicroRow("flash", "XMC 0x464016 · 4 MiB"),
            MicroRow("uid", "0123456789abcdef", mono=True),
            MicroRow("", "fedcba9876543210", mono=True))
    micro.render_micro([_label(specs=_strip(), rows=rows,
                               extra=lambda cell, box: boxes.append(box))], _null_pdf())
    ((x, y, w, h),) = boxes
    assert w == pytest.approx(micro.rows_w())
    assert x == pytest.approx(micro.rows_x())
    assert y + h == pytest.approx(micro.caption_baseline())
    assert h >= micro.MIN_SIZE * 0.72


def _row_sizes(monkeypatch, lab):
    """{value: (point size, top)} for every row value drawn."""
    seen = {}
    real = micro.Cell.fit

    def fit(self, x, y, s, font, size, max_w, min_size=5.5, color=None, **k):
        seen[s] = (size, y)
        return real(self, x, y, s, font, size, max_w, min_size=min_size)

    monkeypatch.setattr(micro.Cell, "fit", fit)
    micro.render_micro([lab], _null_pdf())
    return seen


def test_a_row_with_a_size_prints_at_that_size_whatever_its_length(monkeypatch):
    """A caller that wants one kind of row the same size on every label
    says so, rather than have each shrink to its own length."""
    rows = (MicroRow("flash", "GD25Q32x · 4 MiB", size=4.4),
            MicroRow("uid", "0123", mono=True, size=4.3))
    seen = _row_sizes(monkeypatch, _label(specs=_strip(), rows=rows))
    assert seen["GD25Q32x · 4 MiB"][0] == 4.4
    assert seen["0123"][0] == 4.3


def test_a_row_that_does_not_fit_at_its_size_is_refused():
    rows = (MicroRow("flash", "N25Q128/MT25QL128 · GigaDevice · 16 MiB", size=5.5),)
    with pytest.raises(ValueError, match=r"bench-1.*flash.*5\.5 pt"):
        micro.render_micro([_label(specs=_strip(), rows=rows)], _null_pdf())


def test_a_blank_row_keeps_its_place_empty(monkeypatch):
    """A fact that does not apply leaves its place empty: the rows under
    it stay where they are on every other label."""
    full = (MicroRow("chip", "v0.4"), MicroRow("flash", "GD25Q32x · 4 MiB"))
    gap = (micro.BLANK_ROW, MicroRow("flash", "GD25Q32x · 4 MiB"))
    a = _row_sizes(monkeypatch, _label(specs=_strip(), rows=full))
    b = _row_sizes(monkeypatch, _label(specs=_strip(), rows=gap))
    assert a["GD25Q32x · 4 MiB"] == b["GD25Q32x · 4 MiB"]
    assert "" not in b


def test_under_a_strip_a_label_holds_one_row_more_set_tighter():
    rows = tuple(MicroRow(f"r{i}", "v") for i in range(micro.SPEC_ROWS + 1))
    with pytest.raises(ValueError, match="bench-1"):
        _label(specs=_strip(), rows=rows)
    assert len(_label(specs=_strip(), rows=rows[:-1]).rows) == micro.SPEC_ROWS
    assert micro.SPEC_ROWS == micro.MAX_ROWS + 1
    assert micro.spec_pitch() < micro.ROW_PITCH
    assert micro.SPEC_ROW < micro.ROW


def test_fewer_rows_under_a_strip_spread_over_its_height():
    """Tim, 2026-09-29: use the height there is. Four rows end where five
    do, on the caption's line; two or three stop at ROW_PITCH apart."""
    five, four = micro.spec_pitch(5), micro.spec_pitch(4)
    assert four > five
    assert four * 3 == pytest.approx(five * 4)
    assert four <= micro.ROW_PITCH
    assert micro.spec_pitch(2) == micro.spec_pitch(3) == micro.ROW_PITCH


def test_the_wifi_band_is_centred_under_the_arcs(monkeypatch):
    """Tim, 2026-09-29: "Center the frequency text under the wifi logo",
    however wide the band text is."""
    from reportlab.pdfgen import canvas

    labels.register_fonts()
    for text in ("2.4 n +bt", "2.4/5 ax +bt +zb", "2.4 ax"):
        cell = micro.Cell(canvas.Canvas(_null_pdf()), 0, 0)
        drawn, arcs = [], []
        monkeypatch.setattr(micro.Cell, "text", lambda self, x, y, s, font, size, **k:
                            drawn.append((x, s, font, size)))
        real_arc = cell.c.arc
        cell.c.arc = lambda x1, y1, x2, y2, *a: (arcs.append((x1 + x2) / 2),
                                                 real_arc(x1, y1, x2, y2, *a))
        micro.glyph_wifi(cell, 0, 0, micro.HEAD_H, text)
        (x, band, font, size), = drawn
        centre = x + labels.Label.width(cell, band, font, size) / 2
        assert all(a == pytest.approx(centre) for a in arcs), text


def test_an_unsized_row_under_a_strip_is_set_at_most_spec_row(monkeypatch):
    seen = _row_sizes(monkeypatch, _label(specs=_strip(), rows=(MicroRow("chip", "v0.4"),)))
    assert seen["v0.4"][0] == micro.SPEC_ROW


def test_a_wide_row_starts_right_of_its_own_caption(monkeypatch):
    """A value longer than the column holds starts sooner: right of the
    widest caption among the wide rows, which share that column."""
    drawn = _spy_text(monkeypatch)
    rows = (MicroRow("flash", "GD25Q32x · 4 MiB", size=4.4),
            MicroRow("uid", "240c1119088539540150", mono=True, size=4.0, wide=True),
            MicroRow("", "0123", mono=True, size=4.0, wide=True),
            MicroRow("eFuse", "89e4bec55c62671e", mono=True, size=4.0))
    micro.render_micro([_label(specs=_strip(), rows=rows)], _null_pdf())
    at = {d[0]: d[1] for d in drawn}
    gap = labels.Label.CAPTION_GAP * 0.6
    uid_w = pdfmetrics.stringWidth("uid", labels.SANS, micro.CAPTION)
    efuse_w = pdfmetrics.stringWidth("eFuse", labels.SANS, micro.CAPTION)
    assert at["240c1119088539540150"] == pytest.approx(micro.rows_x() + uid_w + gap)
    assert at["0123"] == at["240c1119088539540150"]
    assert at["uid"] == pytest.approx(micro.rows_x())
    assert at["89e4bec55c62671e"] == pytest.approx(micro.rows_x() + efuse_w + gap)
    assert at["GD25Q32x · 4 MiB"] == at["89e4bec55c62671e"]


@pytest.mark.parametrize("text", ["2.4 b/g/n +bt", "2.4 b/g/n +zb", "2.4 n +BT", "+bt"])
def test_bluetooth_rides_only_on_a_one_standard_wifi_glyph(text):
    with pytest.raises(ValueError, match="Wi-Fi glyph"):
        micro.wifi_lines(text)


def test_the_wifi_glyph_s_bluetooth_is_parsed_off():
    assert micro.wifi_bluetooth("2.4/5 ax +bt") == ("2.4/5 ax", True)
    assert micro.wifi_bluetooth("2.4 n") == ("2.4 n", False)
    assert micro.wifi_lines("2.4 n +bt") == ("2.4n", "", "")
    assert micro.wifi_marks("2.4 ax +bt +zb") == ("2.4 ax", frozenset({"+bt", "+zb"}))


@pytest.mark.parametrize("text", ["2.4 n", "2.4/5 ax", "2.4 ax"])
def test_bluetooth_and_zigbee_cost_the_wifi_glyph_no_width(text):
    """They stand in the empty corners beside the arcs' dot (Tim, 2026-09-29)."""
    w = micro.wifi_width(micro.HEAD_H, text)
    for marks in (" +bt", " +zb", " +bt +zb"):
        assert micro.wifi_width(micro.HEAD_H, text + marks) == w


@pytest.mark.parametrize("text", ["X", "OO", "SJX", "ojs"])
def test_a_usb_glyph_letters_only_otg_jtag_and_serial(text):
    with pytest.raises(ValueError, match="USB glyph"):
        micro.usb_letters(text)


def test_a_usb_glyph_letters_in_one_order():
    assert micro.usb_letters("SJO") == "OJS"
