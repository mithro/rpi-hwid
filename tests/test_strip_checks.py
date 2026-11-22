"""The rules every strip layout is held to (tests/strip_checks.py), tested on
toy layouts so a real layout's failure means the layout, not the checker."""
from __future__ import annotations

import io

import pytest
from PIL import Image
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

import strip_checks as sc
from rpi_hwid import labels as lb


def good(lab, _r):
    lab.text(1 * mm, 1 * mm, "hello", lb.SANS, 8)
    lab.qr(10 * mm, 1 * mm, 6.5 * mm, "0123456789abcdef", error="l")


def tiny(lab, _r):
    lab.text(1 * mm, 1 * mm, "too small", lb.SANS, 4)


def outside(lab, _r):
    lab.text(lab.w - 1 * mm, 1 * mm, "runs off the end", lb.SANS, 8)


def dense(lab, _r):
    lab.qr(1 * mm, 1 * mm, 3 * mm, "0123456789abcdef0123456789abcdef")


def test_a_good_layout_has_no_problems():
    d = sc.on_strip(good, None)
    assert [t.s for t in d.texts] == ["hello"]
    assert [q.payload for q in d.qrs] == ["0123456789abcdef"]
    assert sc.problems(d, *sc.STRIP) == []


def test_text_under_the_floor_is_a_problem():
    assert any("4.0 pt" in p for p in sc.problems(sc.on_strip(tiny, None), *sc.STRIP))


def test_ink_past_the_edge_is_a_problem():
    assert any("outside" in p for p in sc.problems(sc.on_strip(outside, None), *sc.STRIP))


def test_a_qr_with_modules_under_the_floor_is_a_problem():
    assert any("module" in p for p in sc.problems(sc.on_strip(dense, None), *sc.STRIP))


def test_a_fact_missing_from_the_strip_is_named():
    full = sc.full(good, None)
    assert sc.missing_facts(full, sc.on_strip(tiny, None)) == [
        "text 'hello'", "QR '0123456789abcdef'"]
    assert sc.missing_facts(full, full) == []


def test_split_and_elided_text_still_counts():
    """The full board label sets its serial in two halves, and a fitted
    string may end in an ellipsis: a half is found inside the strip's whole,
    and an elided string is not demanded."""
    def halves(lab, _r):
        lab.text(1, 1, "c36b093f", lb.MONO, 8)
        lab.text(1, 10, "773d46b8", lb.MONO, 8)
        lab.text(1, 20, "Waveshare PoE M.2 HAT+…", lb.SANS, 8)

    def whole(lab, _r):
        lab.text(1, 1, "c36b093f773d46b8", lb.MONO, 8)

    assert sc.missing_facts(sc.full(halves, None), sc.on_strip(whole, None)) == []


# --- the geometry the recorder reports is the geometry that is drawn --------


def test_text_box_is_cap_height_below_y_and_right_aligned_ends_at_x():
    def d(lab, _r):
        lab.text(10, 5, "abc", lb.SANS, 10)
        lab.text(50, 5, "abc", lb.SANS, 10, align="right")
        lab.text(30, 5, "abc", lb.SANS, 10, align="centre")

    r = sc.on_strip(d, None)
    left, right, centre = r.texts
    h = sc.STRIP[1]
    w = lb.Label.width(None, "abc", lb.SANS, 10)
    assert left.box == (10, h - 5 - 7.2, 10 + w, h - 5)
    assert right.box[2] == 50
    assert abs((centre.box[0] + centre.box[2]) / 2 - 30) < 1e-9


def test_rotated_text_box_runs_up_from_y():
    def d(lab, _r):
        lab.rotated(4, 20, "abcd", lb.SANS, 10)

    t = sc.on_strip(d, None).texts[0]
    w = lb.Label.width(None, "abcd", lb.SANS, 10)
    h = sc.STRIP[1]
    assert t.box == (4, h - 20, 4 + 7.2, h - 20 + w)


def test_rotated_text_past_the_top_is_a_problem():
    def d(lab, _r):
        lab.rotated(2, 5, "a long run going up the edge", lb.SANS, 8)

    assert any("outside" in p for p in sc.problems(sc.on_strip(d, None), *sc.STRIP))


def test_qr_module_is_size_over_the_matrix_with_no_quiet_zone():
    import segno

    d = sc.on_strip(good, None)
    n = len(list(segno.make("0123456789abcdef", error="l", micro=False).matrix))
    assert d.qrs[0].module == pytest.approx(6.5 * mm / n)
    assert d.qrs[0].box[2] - d.qrs[0].box[0] == pytest.approx(6.5 * mm)


def test_qr_module_is_the_module_actually_drawn():
    """Independent of the recorder: the rects Label.qr puts on the canvas are
    exactly one recorded module wide."""
    from unittest import mock

    from reportlab.pdfgen import canvas

    widths = []
    real = canvas.Canvas.rect

    def spy(self, x, y, w, h, *a, **k):
        widths.append(w)
        return real(self, x, y, w, h, *a, **k)

    with mock.patch.object(canvas.Canvas, "rect", spy):
        d = sc.on_strip(good, None)
    assert widths
    assert all(w == pytest.approx(d.qrs[0].module) for w in widths)


def test_the_qr_error_level_is_the_one_drawn():
    """A level-H code has more modules than level L for the same payload, so
    the module floor must be judged at the level the layout asked for."""
    def level(error):
        def d(lab, _r):
            lab.qr(1 * mm, 1 * mm, 6.5 * mm, "0123456789abcdef0123456789", error=error)
        return sc.on_strip(d, None).qrs[0].module

    assert level("h") < level("l")


def test_fit_and_captioned_text_is_recorded_at_the_size_drawn():
    def d(lab, _r):
        lab.fit(1 * mm, 1 * mm, "a string far too long for its box " * 3, lb.SANS, 8,
                20 * mm, min_size=4)
        lab.captioned(1 * mm, 20 * mm, 10 * mm, "cap", "value", lb.MONO, 8, 20 * mm,
                      cap_size=4)

    r = sc.on_strip(d, None)
    assert [t.s for t in r.texts][-2:] == ["cap", "value"]
    assert r.texts[0].s.endswith("…")
    assert r.texts[0].size == 4
    assert any("4.0 pt" in p for p in sc.problems(r, *sc.STRIP))


# --- anything drawn past the Label helpers is seen --------------------------


def test_text_drawn_straight_on_the_canvas_is_a_problem():
    def d(lab, _r):
        lab.c.setFont(lb.SANS, 3)
        lab.c.drawString(10, 10, "sneaky")
        lab.c.drawRightString(10, 10, "sneaky")
        lab.c.drawCentredString(10, 10, "sneaky")

    found = sc.problems(sc.on_strip(d, None), *sc.STRIP)
    assert len(found) == 3
    assert all("outside Label.text" in p for p in found)


def test_a_qr_made_with_segno_directly_is_a_problem():
    def d(lab, _r):
        import segno
        segno.make("0123456789")

    assert any("segno" in p for p in sc.problems(sc.on_strip(d, None), *sc.STRIP))


def test_label_qr_does_not_trip_the_segno_check():
    assert sc.on_strip(good, None).stray == []


def test_ink_drawn_with_canvas_primitives_is_bounds_checked():
    def rect(lab, _r):
        lab.c.rect(10, 10, 5, sc.STRIP[1], stroke=1, fill=0)

    def circle(lab, _r):
        lab.c.circle(2, 10, 5)

    def line(lab, _r):
        lab.c.line(0, 0, sc.STRIP[0] + 3, 0)

    def path(lab, _r):
        p = lab.c.beginPath()
        p.moveTo(1, 1)
        p.lineTo(sc.STRIP[0] + 3, 1)
        p.lineTo(5, 5)
        p.close()
        lab.c.drawPath(p)

    for draw in (rect, circle, line, path):
        assert any("mark outside" in p for p in sc.problems(sc.on_strip(draw, None), *sc.STRIP)), \
            draw.__name__


def test_primitives_inside_the_strip_pass_and_follow_the_transform():
    def inside(lab, _r):
        lab.c.rect(1, 1, 5, 5)
        lab.c.circle(10, 10, 3)
        lab.c.line(1, 1, 20, 1)
        p = lab.c.beginPath()
        p.moveTo(1, 1)
        p.lineTo(5, 5)
        p.close()
        lab.c.drawPath(p)

    def moved(lab, _r):
        lab.c.saveState()
        lab.c.translate(sc.STRIP[0] - 2, 0)
        lab.c.rect(1, 1, 5, 5)
        lab.c.restoreState()

    assert sc.problems(sc.on_strip(inside, None), *sc.STRIP) == []
    assert any("mark outside" in p for p in sc.problems(sc.on_strip(moved, None), *sc.STRIP))


def test_a_raster_mark_off_the_strip_is_a_problem():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "PNG")
    buf.seek(0)
    img = ImageReader(buf)

    def d(lab, _r):
        lab.c.drawImage(img, lab.w - 2, 1, 10, 10)

    assert any("mark outside" in p for p in sc.problems(sc.on_strip(d, None), *sc.STRIP))


def test_ink_the_checker_cannot_measure_is_reported_not_ignored():
    def d(lab, _r):
        lab.c.grid([1, 2], [1, 2])

    assert any("cannot measure" in p for p in sc.problems(sc.on_strip(d, None), *sc.STRIP))


def test_the_patches_are_removed_afterwards():
    from reportlab.pdfgen import canvas
    before = (lb.Label.text, canvas.Canvas.drawString, canvas.Canvas.rect)
    sc.on_strip(good, None)
    def boom(_lab, _r):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        sc.on_strip(boom, None)
    assert (lb.Label.text, canvas.Canvas.drawString, canvas.Canvas.rect) == before
