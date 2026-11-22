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


# --- per-element floors: captions at 6 pt, values at their own min_size ------


def test_a_caption_under_6_pt_is_a_problem():
    def d(lab, _r):
        lab.captioned(1 * mm, 20 * mm, 10 * mm, "cap", "value", lb.MONO, 8, 20 * mm,
                      cap_size=5)

    r = sc.on_strip(d, None)
    assert [(t.s, t.floor) for t in r.texts] == [("cap", 6), ("value", 5.5)]
    assert any("'cap' at 5.0 pt" in p and "floor 6" in p for p in sc.problems(r, *sc.STRIP))


def test_captioned_after_and_no_code_captions_have_the_caption_floor():
    def d(lab, _r):
        lab.captioned_after(1 * mm, 40 * mm, 10 * mm, "cap", "val", lb.MONO, 8, min_size=5)
        lab.no_code(50 * mm, 1 * mm, 8 * mm)

    assert [(t.s, t.floor) for t in sc.on_strip(d, None).texts] == [
        ("val", 5), ("cap", 6), ("no id", 6)]


def test_a_plain_string_at_exactly_caption_size_is_a_caption():
    def d(lab, _r):
        lab.text(1 * mm, 1 * mm, "plain", lb.SANS, 5)
        lab.text(1 * mm, 6 * mm, "capt", lb.SANS, lb.CAPTION)

    assert [t.floor for t in sc.on_strip(d, None).texts] == [4.5, 6]


def test_a_fitted_value_under_its_min_size_is_a_problem():
    def d(lab, _r):
        lab.fit(1 * mm, 1 * mm, "a value that must shrink to fit", lb.SANS, 8, 15 * mm,
                min_size=5.5)
        lab.fit(1 * mm, 9 * mm, "a value that must shrink to fit", lb.SANS, 8, 15 * mm,
                min_size=4.5)

    r = sc.on_strip(d, None)
    assert [t.floor for t in r.texts] == [5.5, 4.5]
    assert sc.problems(r, *sc.STRIP) == []          # both at or above their floors
    r.texts[0].size = 5.0                           # ... and one pushed under
    assert any("under its floor 5.5 pt" in p for p in sc.problems(r, *sc.STRIP))


def test_grey_captions_at_the_swatch_floor_are_not_captions_by_colour():
    def d(lab, _r):
        lab.fit(1 * mm, 1 * mm, "swatch name", lb.SANS, lb.CAPTION, 30 * mm,
                min_size=4.5, color=lb.GREY)

    r = sc.on_strip(d, None)
    assert r.texts[0].floor == 4.5
    assert sc.problems(r, *sc.STRIP) == []


def test_a_strip_value_under_the_full_labels_floor_is_named():
    def full_d(lab, _r):
        lab.fit(1 * mm, 1 * mm, "VALUE", lb.SANS, 8, 60 * mm, min_size=5.5)
        lab.captioned(1 * mm, 20 * mm, 10 * mm, "Cap", "other", lb.SANS, 8, 30 * mm)

    def small(lab, _r):
        lab.text(1 * mm, 1 * mm, "VALUE", lb.SANS, 5)
        lab.text(1 * mm, 9 * mm, "Cap", lb.SANS, 6)
        lab.text(1 * mm, 15 * mm, "other", lb.SANS, 5.5)

    def right(lab, _r):
        lab.text(1 * mm, 1 * mm, "VALUE", lb.SANS, 5.5)
        lab.text(1 * mm, 9 * mm, "Cap", lb.SANS, 6)
        lab.text(1 * mm, 15 * mm, "other", lb.SANS, 5.5)

    def tiny_cap(lab, _r):
        lab.text(1 * mm, 1 * mm, "VALUE", lb.SANS, 5.5)
        lab.text(1 * mm, 9 * mm, "Cap", lb.SANS, 5)
        lab.text(1 * mm, 15 * mm, "other", lb.SANS, 5.5)

    f = sc.full(full_d, None)
    assert sc.missing_facts(f, f) == []
    assert sc.missing_facts(f, sc.on_strip(right, None)) == []
    assert sc.missing_facts(f, sc.on_strip(small, None)) == [
        "text 'VALUE' at 5.0 pt on the strip, under its full-label floor 5.5 pt"]
    assert sc.missing_facts(f, sc.on_strip(tiny_cap, None)) == [
        "text 'Cap' at 5.0 pt on the strip, under its full-label floor 6 pt"]


def test_the_largest_containing_strip_string_is_the_one_judged():
    def full_d(lab, _r):
        lab.fit(1 * mm, 1 * mm, "abc", lb.SANS, 8, 60 * mm, min_size=5.5)

    def strip_d(lab, _r):
        lab.text(1 * mm, 1 * mm, "abc", lb.SANS, 5)
        lab.text(1 * mm, 9 * mm, "abcdef", lb.SANS, 6)

    assert sc.missing_facts(sc.full(full_d, None), sc.on_strip(strip_d, None)) == []


# --- arcs: only the swept arc is ink ----------------------------------------


def test_an_arc_is_boxed_by_its_swept_part_not_its_whole_ellipse():
    """mark_wifi's 45-135 degree arcs near the strip's bottom edge: the upper
    cap of a circle whose centre and lower half would be off the strip."""
    def d(lab, _r):
        lab.c.arc(10, 1, 30, 21, 45, 90)        # circle centre (20, 11), r 10

    (box,) = sc.on_strip(d, None).marks
    assert box[1] == pytest.approx(11 + 10 * 0.5 ** 0.5)
    assert box[3] == pytest.approx(21)
    assert box[0] == pytest.approx(20 - 10 * 0.5 ** 0.5)
    assert sc.problems(sc.on_strip(d, None), *sc.STRIP) == []

    def real_wifi(lab, _r):
        lb.mark_wifi(lab, 1 * mm, lab.h - 6 * mm, 5 * mm)

    assert sc.problems(sc.on_strip(real_wifi, None), *sc.STRIP) == []


def test_an_arc_whose_swept_part_leaves_the_strip_is_still_caught():
    def d(lab, _r):
        lab.c.arc(10, -10, 30, 10, 225, 90)     # the bottom of the circle, below y = 0

    assert any("mark outside" in p for p in sc.problems(sc.on_strip(d, None), *sc.STRIP))


def test_a_wedge_includes_its_centre():
    def d(lab, _r):
        lab.c.wedge(10, -4, 30, 16, 45, 90)     # centre (20, 6), arc above it

    (box,) = sc.on_strip(d, None).marks
    assert box[1] == pytest.approx(6)


# --- fitted_size then plain text: the min_size follows the size --------------


def test_a_plain_text_at_a_fitted_size_is_held_to_that_min_size():
    """The probe's case: a value sized by fitted_size(min_size=5.5) and drawn
    with plain text is held to 5.5; an unrelated 5.0 pt text is not."""
    def d(lab, _r):
        s = "a long value that shrinks"
        size = lab.fitted_size(s, lb.SANS, 8, 15 * mm, min_size=5.5)
        lab.text(1 * mm, 1 * mm, s, lb.SANS, size)
        lab.text(1 * mm, 9 * mm, "x", lb.SANS, 5.0)

    r = sc.on_strip(d, None)
    assert [(t.size, t.floor) for t in r.texts] == [(5.5, 5.5), (5.0, 4.5)]


def test_fitted_size_squeezed_under_its_min_size_is_a_problem():
    def d(lab, _r):
        size = lab.fitted_size("x", lb.SANS, 5.0, 60 * mm, min_size=5.5)   # already under
        lab.text(1 * mm, 1 * mm, "x", lb.SANS, size)

    r = sc.on_strip(d, None)
    assert r.texts[0].size == 5.0
    assert r.texts[0].floor == 5.5
    assert any("under its floor 5.5 pt" in p for p in sc.problems(r, *sc.STRIP))


def test_one_fitted_size_applies_to_several_strings_whatever_the_order():
    def d(lab, _r):
        a = lab.fitted_size("00:00:00:00:00:00", lb.MONO, 8, 15 * mm, min_size=6)
        b = lab.fitted_size("short", lb.SANS, 8, 60 * mm, min_size=4.5)
        for i, mac in enumerate(("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66")):
            lab.text(1 * mm, (1 + 8 * i) * mm, mac, lb.MONO, a)
        lab.text(1 * mm, 17 * mm, "short", lb.SANS, b)

    r = sc.on_strip(d, None)
    assert r.texts[0].size < 8                       # it really was squeezed
    assert [t.floor for t in r.texts] == [6, 6, 4.5]
    assert sc.problems(r, *sc.STRIP) == []


def test_a_fitted_size_at_or_above_its_min_size_never_flags_its_own_text():
    def d(lab, _r):
        size = lab.fitted_size("a string", lb.SANS, 8, 60 * mm, min_size=5.5)
        lab.text(1 * mm, 1 * mm, "a string", lb.SANS, size)

    r = sc.on_strip(d, None)
    assert r.texts[0].floor == 5.5
    assert sc.problems(r, *sc.STRIP) == []


def test_the_checker_measures_in_the_fonts_a_render_registers(monkeypatch):
    """render_roll registers the DejaVu mono faces before drawing; the
    checker must too, or a strip is measured in Courier (about 0.3% narrower)
    whenever no earlier test happened to register them."""
    import os
    monkeypatch.setattr(lb, "MONO", "Courier-Bold")
    monkeypatch.setattr(lb, "MONO_REGULAR", "Courier")
    d = sc.on_strip(lambda lab, _r: lab.text(1 * mm, 1 * mm, "0123", lb.MONO, 8), None)
    assert d.texts[0].font == lb.MONO
    if os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"):
        assert lb.MONO == "LabelMono"
