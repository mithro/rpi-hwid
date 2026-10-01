"""Printing chosen labels into chosen slots of a part-used sheet
(rpi_hwid.placement): what ``rpi-hwid labels --list --json`` names and
``rpi-hwid labels --place PLAN`` draws, and nothing else."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from rpi_hwid import labels, micro, placement
from rpi_hwid.cli import main as cli_main
from test_micro import _FakeProvider

PT_PER_PX = 72.0 / 50          # the rasters below are 50 dpi


@pytest.fixture
def widgets(monkeypatch):
    """Five micro labels (one per host of the first five) beside the
    fixtures' whole labels."""
    monkeypatch.setattr(micro, "providers", lambda: {"widget": _FakeProvider})


def ink_box(pdf, tmp_path):
    """The bounding box of everything printed on the PDF's one page, in
    points from the page's bottom-left, or None for a blank page."""
    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm not installed")
    from PIL import Image, ImageOps

    subprocess.run(["pdftoppm", "-r", "50", "-gray", "-png", "-singlefile", str(pdf),
                    str(tmp_path / "page")], check=True)
    page = Image.open(tmp_path / "page.png")
    # anything not near-white is ink
    bbox = ImageOps.invert(page.convert("L")).point(lambda v: 255 if v > 40 else 0).getbbox()
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    h = page.height
    return (left * PT_PER_PX, (h - bottom) * PT_PER_PX,
            right * PT_PER_PX, (h - top) * PT_PER_PX)


def inside(box, outer, slack=1.5):
    x0, y0, x1, y1 = box
    a0, b0, a1, b1 = outer
    return x0 >= a0 - slack and y0 >= b0 - slack and x1 <= a1 + slack and y1 <= b1 + slack


def sticker_box(slot):
    x, y = labels.label_origin(slot - 1)
    return (x, y, x + labels.LABEL_W, y + labels.LABEL_H)


# --- naming the labels ---------------------------------------------------------


def test_every_label_is_listed_on_its_own_with_its_size(docs, widgets):
    entries = placement.entries(docs, set(labels.KINDS) | {"widget"})
    whole = [e for e in entries if e.size == "sticker"]
    quarters = [e for e in entries if e.size == "quarter"]
    # the whole labels are all_labels' rows, one each; the micro labels one
    # each too, not packed four to a sticker
    assert len(whole) == len(list(labels.all_labels(docs, labels.KINDS)))
    assert len(quarters) == 5
    assert {e.kind for e in quarters} == {"widget"}
    assert quarters[0].title == "Widget 02:00:00:00:00:01"


def test_label_ids_are_unique_and_name_host_kind_and_title(docs, widgets):
    entries = placement.entries(docs, set(labels.KINDS) | {"widget"})
    ids = [e.id for e in entries]
    assert len(ids) == len(set(ids))
    rpi = next(e for e in entries if e.kind == "rpi")
    assert rpi.id == f"{rpi.host}/rpi/{rpi.title}"


def test_two_labels_alike_get_told_apart():
    a = placement.unique_ids(["h/usb/X", "h/usb/X", "h/usb/Y", "h/usb/X"])
    assert a == ["h/usb/X", "h/usb/X#2", "h/usb/Y", "h/usb/X#3"]


def test_the_list_command_speaks_json(data_dir, widgets, capsys):
    assert cli_main(["labels", "--data", str(data_dir), "--list", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert {"id", "host", "kind", "title", "size"} <= set(got[0])
    assert {g["size"] for g in got} == {"sticker", "quarter"}


# --- slots ---------------------------------------------------------------------


@pytest.mark.parametrize(("text", "slot"), [
    ("1", (1, None)), ("21", (21, None)), ("7a", (7, 0)), ("7d", (7, 3)), ("12B", (12, 1)),
])
def test_slots_parse(text, slot):
    assert placement.parse_slot(text) == slot


@pytest.mark.parametrize("text", ["0", "22", "7e", "a", "", "3ab", "-1"])
def test_a_slot_off_the_sheet_is_refused(text):
    with pytest.raises(placement.PlacementError, match="slot"):
        placement.parse_slot(text)


# --- placing -------------------------------------------------------------------


def plan_for(docs, picks, **extra):
    """A plan placing the entries `picks` names (kind -> slot)."""
    entries = placement.entries(docs, set(labels.KINDS) | {"widget"})
    chosen = []
    for kind, slot in picks:
        e = next(e for e in entries if e.kind == kind
                 and e.id not in {c["id"] for c in chosen})
        chosen.append({"id": e.id, "slot": slot})
    return {"labels": chosen, **extra}


def test_a_placed_label_is_drawn_in_its_slot_and_nowhere_else(docs, widgets, tmp_path):
    plan = plan_for(docs, [("rpi", "21")])
    pdf = tmp_path / "p.pdf"
    placement.render(docs, plan, pdf)
    box = ink_box(pdf, tmp_path)
    assert box is not None
    assert inside(box, sticker_box(21))


def test_a_micro_label_is_drawn_in_its_quarter_and_nowhere_else(docs, widgets, tmp_path):
    plan = plan_for(docs, [("widget", "5d")])
    pdf = tmp_path / "p.pdf"
    placement.render(docs, plan, pdf)
    x, y = micro.micro_origin(*labels.label_origin(4), 3)
    box = ink_box(pdf, tmp_path)
    assert box is not None
    assert inside(box, (x, y, x + micro.MICRO_W, y + micro.MICRO_H))


def test_cut_guides_are_drawn_only_where_the_plan_asks(docs, widgets, tmp_path):
    plain, guided = tmp_path / "a.pdf", tmp_path / "b.pdf"
    placement.render(docs, plan_for(docs, [("widget", "5a")]), plain)
    placement.render(docs, plan_for(docs, [("widget", "5a")], guides=["5"]), guided)
    x, y = micro.micro_origin(*labels.label_origin(4), 0)
    assert inside(ink_box(plain, tmp_path), (x, y, x + micro.MICRO_W, y + micro.MICRO_H))
    # the guides cross the whole sticker, past the one quarter printed
    assert inside(ink_box(guided, tmp_path), sticker_box(5))
    assert not inside(ink_box(guided, tmp_path), (x, y, x + micro.MICRO_W, y + micro.MICRO_H))


def test_several_labels_and_kinds_share_a_plan(docs, widgets, tmp_path):
    plan = plan_for(docs, [("rpi", "1"), ("usb", "3"), ("widget", "2c"), ("widget", "2b")])
    assert placement.render(docs, plan, tmp_path / "p.pdf") == 4


@pytest.mark.parametrize(("picks", "match"), [
    ([("rpi", "4b")], "whole sticker"),
    ([("widget", "4")], "quarter"),
    ([("rpi", "4"), ("usb", "4")], "twice"),
    ([("widget", "4a"), ("widget", "4a")], "twice"),
    ([("rpi", "4"), ("widget", "4a")], "twice"),
])
def test_a_plan_that_overprints_or_misfits_is_refused(docs, widgets, tmp_path, picks, match):
    with pytest.raises(placement.PlacementError, match=match):
        placement.render(docs, plan_for(docs, picks), tmp_path / "p.pdf")


def test_a_label_the_data_does_not_have_is_refused_by_name(docs, widgets, tmp_path):
    plan = {"labels": [{"id": "nowhere/rpi/Pi 9", "slot": "1"}]}
    with pytest.raises(placement.PlacementError, match="nowhere/rpi/Pi 9"):
        placement.render(docs, plan, tmp_path / "p.pdf")


def test_a_label_placed_twice_is_refused(docs, widgets, tmp_path):
    plan = plan_for(docs, [("rpi", "1")])
    plan["labels"].append({"id": plan["labels"][0]["id"], "slot": "2"})
    with pytest.raises(placement.PlacementError, match="more than once"):
        placement.render(docs, plan, tmp_path / "p.pdf")


# --- the sheet's own marking -----------------------------------------------------


def test_the_sheet_id_is_printed_top_and_bottom_outside_the_labels(docs, tmp_path):
    plan = {"labels": [], "sheet": {"id": "K7QX", "note": "ten64 2026-10-01 09:30"}}
    pdf = tmp_path / "p.pdf"
    placement.render(docs, plan, pdf)
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext not installed")
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], check=True,
                          capture_output=True, text=True).stdout
    assert text.count("K7QX") == 2
    assert text.count("ten64 2026-10-01 09:30") == 2
    top = labels.PAGE_H - labels.MARGIN_Y
    bottom = labels.MARGIN_Y
    box = ink_box(pdf, tmp_path)
    assert box is not None
    # ink only in the margins: above the first row and below the last
    from PIL import Image

    page = Image.open(tmp_path / "page.png").convert("L")
    h = page.height
    # across the labels themselves, that is: the registration ticks beside
    # the rows stand in the side margins
    x0 = round(labels.MARGIN_X / PT_PER_PX) + 1
    x1 = round((labels.PAGE_W - labels.MARGIN_X) / PT_PER_PX) - 1
    for py in range(round(h - top / PT_PER_PX) + 2, round(h - bottom / PT_PER_PX) - 2):
        row = page.crop((x0, py, x1, py + 1))
        assert min(row.getdata()) > 215, f"ink across the label grid at row {py}"
    # and inside the printer's unprintable edge, 4.32 mm
    assert box[1] > placement.PRINTER_EDGE
    assert box[3] < labels.PAGE_H - placement.PRINTER_EDGE


def raster(pdf, tmp_path, dpi):
    from PIL import Image

    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm not installed")
    subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-singlefile", str(pdf),
                    str(tmp_path / "r")], check=True)
    page = Image.open(tmp_path / "r.png").convert("L")
    scale = dpi / 72.0

    def ink(x, y0, y1):
        """Is there ink at x (points) anywhere between heights y0 and y1?"""
        px = round(x * scale)
        top, bottom = sorted((round(page.height - y0 * scale), round(page.height - y1 * scale)))
        return any(page.getpixel((px + dx, py)) < 128
                   for py in range(top, bottom + 1) for dx in (-1, 0, 1))

    def ink_row(y, x0, x1):
        py = round(page.height - y * scale)
        left, right = round(x0 * scale), round(x1 * scale)
        return any(page.getpixel((px, py + dy)) < 128
                   for px in range(left, right + 1) for dy in (-1, 0, 1))

    return ink, ink_row


def test_registration_ticks_point_at_every_die_cut(docs, tmp_path):
    """On a sheet's first pass the margins carry ticks in line with every
    die-cut edge -- each column's sides above and below the grid, each
    row's top and bottom beside it -- so a print that is shifted or scaled
    shows it against the stickers."""
    pdf = tmp_path / "p.pdf"
    placement.render(docs, {"labels": [], "sheet": {"id": "K7QX"}}, pdf)
    ink, ink_row = raster(pdf, tmp_path, 150)
    grid_top = labels.PAGE_H - labels.MARGIN_Y
    grid_bottom = labels.MARGIN_Y
    for col in range(labels.COLS):
        x, _ = labels.label_origin(col)
        for edge in (x, x + labels.LABEL_W):
            assert ink(edge, grid_top + 1, grid_top + 3), f"no tick above x={edge:.1f}"
            assert ink(edge, grid_bottom - 3, grid_bottom - 1), f"no tick below x={edge:.1f}"
        # and none mid-column, where the sheet's id and note are
        mid = x + labels.LABEL_W / 2
        assert not ink(mid, grid_top + 1, grid_top + 2.0)
    side = (placement.PRINTER_EDGE + labels.MARGIN_X) / 2
    for row in range(labels.ROWS + 1):
        y = labels.PAGE_H - labels.MARGIN_Y - row * labels.LABEL_H
        assert ink_row(y, side - 0.5, side + 0.5), f"no tick beside y={y:.1f}"
        assert ink_row(y, labels.PAGE_W - side - 0.5, labels.PAGE_W - side + 0.5)
    # mid-row, nothing
    assert not ink_row(labels.PAGE_H - labels.MARGIN_Y - labels.LABEL_H / 2,
                       side - 0.5, side + 0.5)


def test_no_ticks_without_the_sheet_marking(docs, tmp_path):
    """A later pass prints only its labels: the ticks are already there."""
    pdf = tmp_path / "p.pdf"
    placement.render(docs, {"labels": []}, pdf)
    assert ink_box(pdf, tmp_path) is None


def test_the_place_command_reads_a_plan_file(data_dir, docs, widgets, tmp_path, capsys):
    plan = plan_for(docs, [("rpi", "2"), ("widget", "9c")], sheet={"id": "AB12", "note": "x"})
    # not in the data directory, which is read as probe documents
    (tmp_path / "plans").mkdir()
    path = tmp_path / "plans" / "plan.json"
    path.write_text(json.dumps(plan))
    pdf = tmp_path / "plans" / "out.pdf"
    assert cli_main(["labels", "--data", str(data_dir), "--place", str(path),
                     "--out", str(pdf)]) == 0
    assert "2 labels placed on sheet AB12" in capsys.readouterr().out
    assert pdf.exists()


def test_the_place_command_names_a_bad_plan_and_fails(data_dir, tmp_path, capsys):
    (tmp_path / "plans").mkdir()
    path = tmp_path / "plans" / "plan.json"
    path.write_text(json.dumps({"labels": [{"id": "nowhere/rpi/x", "slot": "1"}]}))
    assert cli_main(["labels", "--data", str(data_dir), "--place", str(path),
                     "--out", str(tmp_path / "plans" / "o.pdf")]) == 2
    assert "nowhere/rpi/x" in capsys.readouterr().err
