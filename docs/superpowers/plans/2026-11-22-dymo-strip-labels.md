# Strip labels for a DYMO roll — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rpi-hwid labels --stock dymo-99010 [--print QUEUE]` writes (and
optionally prints) one 28 x 89 mm strip label per DYMO 99010 sticker, for
every label kind, micro labels two to a sticker.

**Architecture:** `Label` learns its own size; a new `strip.py` holds a
strip layout per kind (registered in `STRIP_OF` against the full layout's
draw function, so `labels.whole_labels`' rows are reused unchanged); a new
`roll.py` turns those rows into one driver-sized PDF page per sticker and
prints it through the existing stdlib IPP client. `labels.py` only gains the
`Roll` record and CLI plumbing.

**Tech Stack:** Python 3.11+, reportlab, segno, svglib, pytest; `uv`.

**Spec:** `docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md`
(read it first: decisions 1-9 and "The roll, as measured" are the
requirements).

## Global Constraints

- Every command is run through `uv`: `uv run pytest`, `uv run ruff check`,
  `uv run mypy`. The full gate is `uv run ruff check && uv run mypy && uv run pytest`.
- Full labels and micro sheets must render **byte-identical** before and
  after this work (Task 1 checks it by hash; nothing later may change them).
- Strip box: the roll's printable area less 1 mm on every side; for
  `dymo-99010` 79.5 x 23.3 mm (Task 2 derives it, nothing hard-codes it).
- Floors (spec decision 7): no text below 4.5 pt, no QR module below 0.26 mm,
  and no element below the `min_size` its full-label counterpart passes.
- A strip carries every string and QR payload its full label carries
  (spec decision 2); Task 3's checker enforces it.
- Roll page = the DYMO driver's page: 79 x 252 pt; printable area
  (ImageableArea) 4.32 4.32 76.08 235.44 pt; IPP media size 2786 x 8890
  (1/100 mm); PPD size name `w79h252.2`.
- Commit messages: imperative, prefixed like the history (`labels:`,
  `strip:`, `roll:`, `micro:`, `sheet:`, `docs:`), ending with the session's
  attribution lines. Small commits: one per task at least.
- Never write to `/tmp`; scratch goes in the git-ignored `tmp/`.

## File structure

| file | change | responsibility |
|---|---|---|
| `src/rpi_hwid/labels.py` | modify | `Label(w, h)`; `Roll`, `ROLLS`; `render`/`_main` hand a roll to `roll` |
| `src/rpi_hwid/micro.py` | modify | `Cell` uses `Label`'s size; `pack(per_sticker=)`; `draw_pair` |
| `src/rpi_hwid/strip.py` | create | `STRIP_W/H`, `Strip`, the board/FPGA/USB/TT strip layouts, `STRIP_OF` |
| `src/rpi_hwid/sdr_labels.py` | modify | `draw_sdr_strip`, beside `draw_sdr` |
| `src/rpi_hwid/roll.py` | create | `roll_rows`, `render_roll`, `page_point`, `queue_uri`, `print_pdf` |
| `src/rpi_hwid/sheet/ipp.py` | modify | `resolution()` value; `print_document()`; `print_job` calls it |
| `tests/strip_checks.py` | create | the recording checker every strip test uses |
| `tests/test_label_size.py` | create | Task 1 |
| `tests/test_roll.py` | create | Tasks 2 and 9 |
| `tests/test_strip_checks.py` | create | Task 3 (the checker's own tests) |
| `tests/test_strip.py` | create | Tasks 4-8 |
| `tests/test_sheet_ipp.py` | modify | Task 9 |
| `docs/LABELS.md`, `docs/examples/render_strips.py`, `docs/DEVELOPING.md`, `README.md` | modify/create | Task 11 |

---

### Task 1: `Label` takes its size

**Files:**
- Modify: `src/rpi_hwid/labels.py:192-354` (class `Label`)
- Modify: `src/rpi_hwid/micro.py:197-217` (class `Cell`)
- Create: `tests/test_label_size.py`

**Interfaces:**
- Produces: `labels.Label(c, x0, y0, w=LABEL_W, h=LABEL_H)` with attributes
  `w`, `h`; `pt`, `outline`, `cut_line` use them. `micro.Cell(c, x0, y0,
  w=MICRO_W, h=MICRO_H)` is a plain subclass passing its size up.

- [ ] **Step 1: Record the hashes of today's output**

Create `tmp/golden.py`:

```python
"""sha256 of every sheet rpi-hwid draws today, for Task 1's before/after check."""
import copy
import hashlib
import io
import sys

sys.path.insert(0, "tests")
import conftest  # noqa: E402  (the RAW fixtures)

from rpi_hwid import labels, micro  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

docs = {n: ProbeDocument.from_dict(n, copy.deepcopy(r)) for n, r in conftest.RAW.items()}
only = set(labels.KINDS) | set(micro.kinds())
for stock in sorted(labels.STOCKS):
    for outline in (False, True):
        buf = io.BytesIO()
        labels.render(docs, buf, only, outline=outline, invariant=True, stock=stock)
        print(stock, outline, hashlib.sha256(buf.getvalue()).hexdigest())
```

Run: `uv run python tmp/golden.py | tee tmp/golden-before.txt`
Expected: six lines, `L7160 False <hash>` and so on.

- [ ] **Step 2: Write the failing test**

`tests/test_label_size.py`:

```python
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
    assert "pt" not in vars(micro.Cell) and "outline" not in vars(micro.Cell)


def test_outline_and_cut_line_draw_the_labels_own_size(monkeypatch):
    rects = []
    c = _canvas()
    monkeypatch.setattr(c, "rect", lambda *a, **k: rects.append(a[:4]))
    lab = labels.Label(c, 1, 2, 50, 30)
    lab.outline()
    lab.cut_line()
    assert rects == [(1, 2, 50, 30), (1, 2, 50, 30)]
```

- [ ] **Step 3: Run it to see it fail**

Run: `uv run pytest tests/test_label_size.py -v`
Expected: FAIL (`Label.__init__() takes 4 positional arguments`, no `w`).

- [ ] **Step 4: Implement**

In `labels.py`, class `Label`:

```python
class Label:
    """A drawing context whose origin is the label's top-left corner, with y
    growing downwards, because that is how one thinks about a label. It is
    LABEL_W x LABEL_H unless given another size (a micro label's quarter, a
    roll's strip)."""

    def __init__(self, c, x0, y0, w=LABEL_W, h=LABEL_H):
        self.c, self.x0, self.y0 = c, x0, y0
        self.w, self.h = w, h

    def pt(self, x, y):
        return self.x0 + x, self.y0 + self.h - y
```

and in `outline` and `cut_line` replace `LABEL_W, LABEL_H` with
`self.w, self.h`.

In `micro.py`, class `Cell` becomes:

```python
class Cell(labels.Label):
    """A ``labels.Label`` a quarter the size: the same helpers, with the
    origin at the quarter's top-left corner and y growing downwards."""

    def __init__(self, c: Any, x0: float, y0: float,
                 w: float = MICRO_W, h: float = MICRO_H) -> None:
        super().__init__(c, x0, y0, w, h)
```

(its `pt` and `outline` overrides are deleted: `Label`'s now do the same).

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `uv run pytest tests/test_label_size.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Check the output is byte-identical**

Run: `uv run python tmp/golden.py > tmp/golden-after.txt && diff tmp/golden-before.txt tmp/golden-after.txt && echo SAME`
Expected: `SAME`. Any difference is a regression: fix it before committing.

- [ ] **Step 7: Commit**

```bash
git add src/rpi_hwid/labels.py src/rpi_hwid/micro.py tests/test_label_size.py
git commit -m "labels: a Label knows its own size, and a micro Cell is just a smaller one"
```

---

### Task 2: The roll: `Roll`, `roll.py`, micro labels two to a sticker

**Files:**
- Modify: `src/rpi_hwid/labels.py` (after `DEFAULT_STOCK`, line ~157; `render`
  line ~2284; `main`/`_main` lines ~2436-2539)
- Modify: `src/rpi_hwid/micro.py` (`pack` line ~1227; new `draw_pair` after
  `draw_quad`)
- Create: `src/rpi_hwid/strip.py`, `src/rpi_hwid/roll.py`
- Create: `tests/test_roll.py`

**Interfaces:**
- Consumes: `labels.Label(c, x0, y0, w, h)` (Task 1).
- Produces:
  - `labels.Roll(name, page, printable, ppd_size, media_size, note)` with
    property `strip_box -> (x, y, w, h)` in page points; `labels.ROLLS:
    dict[str, Roll]`.
  - `strip.STRIP_W`, `strip.STRIP_H` (points); `strip.Strip(c, x0, y0)`;
    `strip.STRIP_OF: dict[Callable, Callable]` (full draw fn -> strip draw
    fn, each `(strip, record) -> None`), empty until Tasks 4-8.
  - `micro.pack(micros, start=0, per_sticker=PER_STICKER)`;
    `micro.draw_pair(strip, group)`.
  - `roll.NoStripError(ValueError)`; `roll.roll_rows(docs, only,
    pinned_names=None, order=None) -> list[(host, kind, title, draw, record)]`;
    `roll.page_point(roll, u, v) -> (x, y)`; `roll.render_roll(docs, out,
    roll, only, outline=False, pinned_names=None, order=None,
    invariant=False) -> (labels, stickers)`.
  - `labels.render(..., stock="dymo-99010")` returns `(labels, stickers)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_roll.py`:

```python
"""A roll of stickers (labels.ROLLS, rpi_hwid.roll): one driver-sized page per
sticker, the strip turned onto it inside the printable area."""
from __future__ import annotations

import io
import math
import re

import pytest
from reportlab.lib.units import mm

from rpi_hwid import labels, micro, roll, strip

R = labels.ROLLS["dymo-99010"]


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
    assert (strip.STRIP_W, strip.STRIP_H) == pytest.approx((h, w))
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


def test_a_roll_is_one_page_per_sticker(docs):
    only = set(micro.kinds())
    n_micro = len(micro.selected(docs, only))
    assert n_micro >= 1, "the fixtures carry micro labels"
    buf = io.BytesIO()
    n, stickers = labels.render(docs, buf, only, invariant=True, stock="dymo-99010")
    assert n == n_micro
    assert stickers == math.ceil(n_micro / 2)
    pages = _pages(buf.getvalue())
    assert len(pages) == stickers
    assert all(p == pytest.approx(R.page) for p in pages)


def test_a_roll_is_the_same_bytes_every_time(docs):
    def once():
        buf = io.BytesIO()
        labels.render(docs, buf, set(micro.kinds()), invariant=True, stock="dymo-99010")
        return buf.getvalue()
    assert once() == once()


def test_a_label_kind_with_no_strip_yet_is_refused_by_name(docs, monkeypatch):
    monkeypatch.setattr(strip, "STRIP_OF", {})
    with pytest.raises(roll.NoStripError, match="no strip layout"):
        roll.roll_rows(docs, {"rpi"})


def test_a_roll_has_no_positions_to_leave_blank(docs):
    with pytest.raises(ValueError, match="roll"):
        labels.render(docs, io.BytesIO(), {"rpi"}, start=1, stock="dymo-99010")


def test_the_command_takes_a_roll(data_dir, tmp_path, capsys):
    out = tmp_path / "roll.pdf"
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(data_dir), *only, "--stock", "dymo-99010",
                        "--out", str(out)]) == 0
    said = capsys.readouterr().out
    assert re.search(r"\d+ labels? on \d+ stickers? -> ", said)
    assert _pages(out.read_bytes())[0] == pytest.approx(R.page)


def test_list_on_a_roll_counts_stickers(data_dir, capsys):
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(data_dir), *only, "--stock", "dymo-99010",
                        "--list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(re.match(r"sticker \d+  ", ln) for ln in lines)
    assert lines[0].startswith("sticker 1  ")


@pytest.mark.parametrize("extra", [["--start", "1"], ["--list", "--json"],
                                   ["--place", "plan.json"]])
def test_sheet_only_options_are_refused_on_a_roll(data_dir, extra):
    with pytest.raises(SystemExit):
        labels.main(["--data", str(data_dir), "--stock", "dymo-99010", *extra])
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_roll.py -v`
Expected: FAIL at import (`labels` has no attribute `ROLLS`; no module `strip`).

- [ ] **Step 3: The `Roll` record** — in `labels.py`, after `DEFAULT_STOCK = "L7160"`:

```python
@dataclass(frozen=True)
class Roll:
    """A roll of stickers in a label printer, one sticker per page: the
    driver's page (`page`, points) and the part of it the driver prints
    (`printable`, its ImageableArea: x0, y0, x1, y1), the driver's name for
    the size (`ppd_size`, for lp and the docs) and IPP's (`media_size`,
    hundredths of a millimetre, which is how CUPS lists a PPD size). Labels
    on a roll are strips (rpi_hwid.strip), drawn in `strip_box` turned a
    quarter so they run along the sticker."""
    name: str
    page: tuple[float, float]
    printable: tuple[float, float, float, float]
    ppd_size: str
    media_size: tuple[int, int]
    note: str

    @property
    def strip_box(self):
        """(x, y, w, h) on the page, points: the printable area less 1 mm
        all round (the sticker's corners are rounded, and the print lands
        on it to about a millimetre)."""
        x0, y0, x1, y1 = self.printable
        return x0 + mm, y0 + mm, x1 - x0 - 2 * mm, y1 - y0 - 2 * mm


# dymo-99010: DYMO 99010 address labels, 28 x 89 mm, in a LabelWriter 450
# with DYMO's own CUPS driver (printer-driver-dymo 1.4.0, lw450.ppd, filter
# raster2dymolw): its *PageSize w79h252.2 "99010 Standard Address" and that
# size's *ImageableArea. Measured with a mm-ladder label on 2026-11-21: the
# page lands on the sticker to within ~1 mm. CUPS's generic sample.drv DYMO
# driver puts it ~4.5 mm along and ~1.5 mm across off the sticker, and no PPD
# setting can move it (docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md).
ROLLS = {r.name: r for r in (
    Roll("dymo-99010", (79.0, 252.0), (4.32, 4.32, 76.08, 235.44), "w79h252.2",
         (2786, 8890),
         note="DYMO 99010 (28 x 89 mm) in a LabelWriter 450 on DYMO's own driver "
              "(printer-driver-dymo, lw450.ppd)"),
)}
assert not set(ROLLS) & set(STOCKS), "a stock is a sheet or a roll, never both"
```

- [ ] **Step 4: `strip.py`, empty of layouts for now**

```python
"""Strip labels: each kind of label laid out for one sticker of a label
printer's roll -- for now DYMO 99010, 28 x 89 mm -- carrying the same facts
and codes as its full label, re-flowed along a strip a third as tall
(docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md).

A layout takes (strip, record), the record its full layout takes, and is
registered in STRIP_OF against that full layout's draw function, so the
rows labels.whole_labels makes for a sheet make a roll unchanged."""

from __future__ import annotations

from typing import Any, Callable

from rpi_hwid import labels as lb

# The strip is the roll's box turned a quarter: its length runs up the page.
_, _, STRIP_H, STRIP_W = lb.ROLLS["dymo-99010"].strip_box


class Strip(lb.Label):
    """A ``labels.Label`` the strip's size, origin top-left, y down."""

    def __init__(self, c: Any, x0: float, y0: float) -> None:
        super().__init__(c, x0, y0, STRIP_W, STRIP_H)


# full draw function -> strip draw function; each layout registers itself
STRIP_OF: dict[Callable[..., Any], Callable[..., Any]] = {}
```

- [ ] **Step 5: micro labels in twos** — in `micro.py` replace `pack`:

```python
def pack(micros: Sequence[MicroLabel], start: int = 0,
         per_sticker: int = PER_STICKER) -> list[Group]:
    """The labels in groups of `per_sticker` (four to a sheet's sticker, two
    to a roll's), in order, one group per sticker; `start` places of the
    first sticker are left empty (a partly used one)."""
    slots: list[MicroLabel | None] = [None] * start + list(micros)
    slots += [None] * (-len(slots) % per_sticker)
    return [tuple(slots[i:i + per_sticker]) for i in range(0, len(slots), per_sticker)]
```

and after `draw_quad` add:

```python
def draw_pair(lab: Any, group: Group) -> None:
    """One roll sticker: up to two micro labels at their true size, end to
    end and centred in the strip `lab`, with a guide to cut them apart."""
    x = (lab.w - 2 * MICRO_W) / 2
    y = (lab.h - MICRO_H) / 2
    for i, m in enumerate(group):
        if m is not None:
            draw_micro(Cell(lab.c, lab.x0 + x + i * MICRO_W, lab.y0 + y), m)
    if sum(m is not None for m in group) > 1:
        c = lab.c
        c.saveState()
        c.setStrokeColor(GUIDE)
        c.setLineWidth(0.3)
        c.setDash(1, 1.5)
        xm = lab.x0 + x + MICRO_W
        c.line(xm, lab.y0, xm, lab.y0 + lab.h)
        c.restoreState()
```

- [ ] **Step 6: `roll.py`**

```python
"""A roll of stickers: the labels as strips (rpi_hwid.strip), one per page
of the label printer's own page size, and printing them through its CUPS
queue (labels --stock ROLL [--print QUEUE])."""

from __future__ import annotations

from typing import IO, Any, Callable

from reportlab.lib.colors import HexColor, black
from reportlab.pdfgen import canvas

from rpi_hwid import labels, micro, strip

Row = tuple[str, str, str, Callable[[Any, Any], None], Any]


class NoStripError(ValueError):
    """A label kind has no strip layout, so it cannot go on a roll."""


def roll_rows(docs: Any, only: set[str], pinned_names: Any = None,
              order: Any = None) -> list[Row]:
    """(host, kind, title, strip draw, record) per sticker: the whole labels
    in labels.all_labels' order, then the micro labels two to a sticker."""
    rows: list[Row] = []
    for host, kind, title, draw, record in labels.whole_labels(docs, only, pinned_names, order):
        if draw not in strip.STRIP_OF:
            raise NoStripError(f"{host}: the {kind} label has no strip layout, "
                               f"so it cannot go on a roll")
        rows.append((host, kind, title, strip.STRIP_OF[draw], record))
    micros = [m for _kind, m in micro.selected(docs, only)]
    for group in micro.pack(micros, per_sticker=2):
        present = [m for m in group if m is not None]
        rows.append((present[0].host, "micro", " | ".join(m.listing_title for m in present),
                     micro.draw_pair, group))
    return rows


def page_point(roll: labels.Roll, u: float, v: float) -> tuple[float, float]:
    """Where the strip's point (u along it, v up it; reportlab's y-up
    coordinates) lands on the page: the strip turned a quarter
    anticlockwise into the roll's strip box."""
    bx, by, bw, _bh = roll.strip_box
    return bx + bw - v, by + u


def _place(c: Any, roll: labels.Roll) -> None:
    """Turn the canvas so the strip draws at (0, 0) in its own coordinates;
    page_point is the same transform, written out for the tests."""
    bx, by, bw, _bh = roll.strip_box
    c.translate(bx + bw, by)
    c.rotate(90)


def render_roll(docs: Any, out: str | IO[bytes], roll: labels.Roll, only: set[str],
                outline: bool = False, pinned_names: Any = None, order: Any = None,
                invariant: bool = False) -> tuple[int, int]:
    """Write the roll's PDF to `out`: one page per sticker. Returns (labels,
    stickers). `outline` draws the sticker's edge and the strip's, for a
    proof on plain paper."""
    labels.register_fonts()
    rows = roll_rows(docs, set(only), pinned_names, order)
    c = canvas.Canvas(out if hasattr(out, "write") else str(out), pagesize=roll.page,
                      invariant=1 if invariant else 0)
    c.setTitle("Hardware identity labels")
    c.setAuthor("rpi-hwid labels")
    for i, (_host, _kind, _title, draw, record) in enumerate(rows):
        if i:
            c.showPage()
        if outline:
            c.setStrokeColor(HexColor("#bbbbbb"))
            c.setLineWidth(0.3)
            c.rect(0, 0, *roll.page, stroke=1, fill=0)
            c.setStrokeColor(black)
        c.saveState()
        _place(c, roll)
        s = strip.Strip(c, 0, 0)
        if outline:
            s.outline()
        draw(s, record)
        c.restoreState()
    c.showPage()
    c.save()
    n = sum(sum(m is not None for m in r[4]) if r[1] == "micro" else 1 for r in rows)
    return n, len(rows)
```

- [ ] **Step 7: `labels.render` hands a roll over** — at the top of `render`,
before `register_fonts()`:

```python
    if stock in ROLLS:
        if start:
            raise ValueError("a roll has no positions to leave blank: --start is for sheets")
        from rpi_hwid import roll
        return roll.render_roll(docs, out, ROLLS[stock], only, outline, pinned_names,
                                order, invariant)
```

- [ ] **Step 8: the command** — in `main`, the `--stock` argument becomes:

```python
    ap.add_argument("--stock", choices=sorted(STOCKS) + sorted(ROLLS), default=DEFAULT_STOCK,
                    help="the sheet: L7160 (A4, the default), avery-5163 (US Letter, 4 x 2 in "
                         "stickers) or letter-plain (plain US Letter, with cut lines); or the "
                         "roll: dymo-99010 (DYMO 99010, 28 x 89 mm, one strip per sticker)")
```

In `_main`, replace `stock = STOCKS[args.stock]` and the `--list` and
render branches so that a roll is handled first:

```python
    on_roll = ROLLS.get(args.stock)
    if on_roll and args.start:
        ap.error("--start leaves a sheet's first positions blank: a roll has none")
    stock = None if on_roll else STOCKS[args.stock]
    if args.check:
        return check_main(docs, only)
    from rpi_hwid import placement
    if args.list and args.json:
        print(placement.list_json(docs, set(only), pinned))
        return 0
    if args.place:
        return placement.place_main(docs, args.place, args.out, set(only), pinned,
                                    args.outline)
    if on_roll:
        from rpi_hwid import roll
        try:
            if args.list:
                rows = roll.roll_rows(docs, set(only), pinned)
                width = max([len(r[0]) for r in rows] or [0])
                for i, (host, kind, title, _, _) in enumerate(rows):
                    print("sticker %d  %-*s  %-6s %s" % (i + 1, width, host, kind, title))
                return 0
            n, stickers = render(docs, args.out, only, 0, args.outline, pinned,
                                 stock=args.stock)
        except roll.NoStripError as exc:
            print(exc, file=sys.stderr)
            return 1
        print("%d label%s on %d sticker%s -> %s" % (n, "" if n == 1 else "s", stickers,
                                                    "" if stickers == 1 else "s", args.out))
        return 0
```

and the existing sheet `--list`/render code below it uses `stock` unchanged.
(The existing check `args.stock != DEFAULT_STOCK and (args.place or
(args.list and args.json))` already refuses `--place` and `--list --json`
on a roll.)

- [ ] **Step 9: Run the tests**

Run: `uv run pytest tests/test_roll.py -v && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 10: Look at it**

Create `tmp/roll_micro.py` (the fixtures' micro labels, as Task 1's
`tmp/golden.py` loads them):

```python
"""The fixtures' micro labels on a roll, as PNGs, for looking at."""
import copy
import subprocess
import sys

sys.path.insert(0, "tests")
import conftest  # noqa: E402

from rpi_hwid import labels, micro  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

docs = {n: ProbeDocument.from_dict(n, copy.deepcopy(r)) for n, r in conftest.RAW.items()}
labels.render(docs, "tmp/roll-micro.pdf", set(micro.kinds()), outline=True,
              invariant=True, stock="dymo-99010")
subprocess.run(["pdftoppm", "-r", "200", "-png", "tmp/roll-micro.pdf", "tmp/roll-micro"],
               check=True)
```

Run: `uv run python tmp/roll_micro.py`, then look at `tmp/roll-micro-*.png`.
Expected: each page portrait, two micro labels side by side reading along
the page's length, a dotted cut line between them, inside the outline.

- [ ] **Step 11: Commit**

```bash
git add src/rpi_hwid/labels.py src/rpi_hwid/micro.py src/rpi_hwid/strip.py src/rpi_hwid/roll.py tests/test_roll.py
git commit -m "roll: --stock dymo-99010, one driver-sized page per sticker, micro labels two to one"
```

---

### Task 3: The strip checker

Every layout task tests against the same rules; this task writes them once.

**Files:**
- Create: `tests/strip_checks.py`, `tests/test_strip_checks.py`

**Interfaces:**
- Consumes: `strip.Strip`, `labels.Label`, `labels.whole_labels`.
- Produces (in `tests/strip_checks.py`):
  - `Drawn` (dataclass): `texts: list[Text]`, `qrs: list[Qr]`,
    `marks: list[Box]`; `Text(s, font, size, box)`, `Qr(payload, module, box)`,
    `Box = tuple[float, float, float, float]` (x0, y0, x1, y1, reportlab y-up).
  - `record(draw, record, lab_factory) -> Drawn`: draws one label on a
    canvas the label's size and records what went on it.
  - `full(draw, record) -> Drawn`, `on_strip(draw, record) -> Drawn`.
  - `problems(drawn, w, h) -> list[str]`: floors and bounds (empty = good).
  - `missing_facts(full_drawn, strip_drawn) -> list[str]`.
  - `rows_for(docs, draw) -> list[(host, title, record)]`.

- [ ] **Step 1: Write the checker's own failing tests** — `tests/test_strip_checks.py`:

```python
"""The rules every strip layout is held to (tests/strip_checks.py), tested on
toy layouts so a real layout's failure means the layout, not the checker."""
from __future__ import annotations

from reportlab.lib.units import mm

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
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_strip_checks.py -v`
Expected: FAIL (`No module named 'strip_checks'`).

- [ ] **Step 3: Implement** — `tests/strip_checks.py`:

```python
"""What every strip layout is held to (spec decisions 2 and 7): drawn on a
canvas its own size, nothing is set under 4.5 pt, no QR module is under
0.26 mm, no text, code or mark runs outside the strip, and every string and
code the full label carries is on the strip too."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Callable

import segno
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from rpi_hwid import labels as lb
from rpi_hwid import strip

TEXT_FLOOR = 4.5          # points
MODULE_FLOOR = 0.26 * mm
STRIP = (strip.STRIP_W, strip.STRIP_H)
SLACK = 0.05              # points: rounding in the layouts' own arithmetic

Box = tuple[float, float, float, float]


@dataclass
class Text:
    s: str
    font: str
    size: float
    box: Box


@dataclass
class Qr:
    payload: str
    module: float
    box: Box


@dataclass
class Drawn:
    texts: list[Text] = field(default_factory=list)
    qrs: list[Qr] = field(default_factory=list)
    marks: list[Box] = field(default_factory=list)


def record(draw: Callable[[Any, Any], None], rec: Any,
           make: Callable[[Any], lb.Label]) -> Drawn:
    """Draw one label with `draw` on a canvas the label's size, recording
    every string (Label.text, Label.rotated), code (Label.qr) and SVG mark
    (Label.svg) in the canvas's own coordinates."""
    d = Drawn()
    real_text, real_rot = lb.Label.text, lb.Label.rotated
    real_qr, real_svg = lb.Label.qr, lb.Label.svg

    def text(self, x, y, s, font=lb.SANS, size=8, align="left", color=lb.black):
        w = self.width(s, font, size)
        left = {"left": x, "right": x - w, "centre": x - w / 2}[align]
        x0, top = self.pt(left, y)
        d.texts.append(Text(s, font, size, (x0, top - size * 0.72, x0 + w, top)))
        return real_text(self, x, y, s, font, size, align, color)

    def rotated(self, x, y, s, font, size, color=lb.black):
        w = self.width(s, font, size)
        px, py = self.pt(x, y)
        d.texts.append(Text(s, font, size, (px, py, px + size * 0.72, py + w)))
        return real_rot(self, x, y, s, font, size, color)

    def qr(self, x, y, size, content, error="m"):
        n = len(list(segno.make(content, error=error, micro=False).matrix))
        x0, top = self.pt(x, y)
        d.qrs.append(Qr(content, size / n, (x0, top - size, x0 + size, top)))
        return real_qr(self, x, y, size, content, error)

    def svg(self, path, x, y, height):
        w = real_svg(self, path, x, y, height)
        x0, top = self.pt(x, y)
        d.marks.append((x0, top - height, x0 + w, top))
        return w

    c = canvas.Canvas(io.BytesIO())
    lab = make(c)
    c.setPageSize((lab.w, lab.h))
    patched = {"text": text, "rotated": rotated, "qr": qr, "svg": svg}
    saved = {k: getattr(lb.Label, k) for k in patched}
    try:
        for k, f in patched.items():
            setattr(lb.Label, k, f)
        draw(lab, rec)
    finally:
        for k, f in saved.items():
            setattr(lb.Label, k, f)
    del real_text, real_rot, real_qr, real_svg
    return d


def full(draw: Callable[[Any, Any], None], rec: Any) -> Drawn:
    return record(draw, rec, lambda c: lb.Label(c, 0, 0))


def on_strip(draw: Callable[[Any, Any], None], rec: Any) -> Drawn:
    return record(draw, rec, lambda c: strip.Strip(c, 0, 0))


def problems(d: Drawn, w: float, h: float) -> list[str]:
    def out(box: Box) -> bool:
        x0, y0, x1, y1 = box
        return x0 < -SLACK or y0 < -SLACK or x1 > w + SLACK or y1 > h + SLACK

    found = []
    for t in d.texts:
        if t.size < TEXT_FLOOR - 1e-9:
            found.append(f"text {t.s!r} at {t.size:.1f} pt, under {TEXT_FLOOR} pt")
        if t.s.strip() and out(t.box):
            found.append(f"text {t.s!r} outside the strip: {t.box}")
    for q in d.qrs:
        if q.module < MODULE_FLOOR - 1e-9:
            found.append(f"QR {q.payload!r}: module {q.module / mm:.3f} mm, "
                         f"under {MODULE_FLOOR / mm} mm")
        if out(q.box):
            found.append(f"QR {q.payload!r} outside the strip: {q.box}")
    for m in d.marks:
        if out(m):
            found.append(f"mark outside the strip: {m}")
    return found


def missing_facts(full_d: Drawn, strip_d: Drawn) -> list[str]:
    """Every string the full label sets (but one it elided) must appear on
    the strip, whole or as part of a longer string there; every QR payload
    must be there exactly."""
    joined = "\n".join(t.s for t in strip_d.texts)
    payloads = {q.payload for q in strip_d.qrs}
    gone = [f"text {t.s!r}" for t in full_d.texts
            if t.s.strip() and not t.s.endswith("…") and t.s not in joined]
    gone += [f"QR {q.payload!r}" for q in full_d.qrs if q.payload not in payloads]
    return gone


def rows_for(docs: Any, draw: Callable[[Any, Any], None]) -> list[tuple[str, str, Any]]:
    """(host, title, record) of every whole label the fixtures make with `draw`."""
    return [(host, title, rec)
            for host, _kind, title, d, rec in lb.whole_labels(docs, set(lb.KINDS))
            if d is draw]
```

(`lb.black` is `reportlab.lib.colors.black`, imported in `labels.py`.)

- [ ] **Step 4: Run them**

Run: `uv run pytest tests/test_strip_checks.py -v`
Expected: PASS. If `pytest` cannot import `strip_checks`, add
`pythonpath = ["tests"]` to `[tool.pytest.ini_options]` in `pyproject.toml`
(check first whether `tests/` already imports a sibling, e.g.
`id_bus_fixtures`, and copy how it does).

- [ ] **Step 5: Commit**

```bash
git add tests/strip_checks.py tests/test_strip_checks.py pyproject.toml
git commit -m "tests: the rules a strip layout is held to: floors, bounds, the full label's facts"
```

---

### Tasks 4-8: the strip layouts

Each layout task follows the same five-part test, written once here as
`tests/test_strip.py` and extended by one parametrize entry per task.

Starting coordinates in the code below are a first layout, inside the
floors. Each task ends by rendering previews and adjusting positions and
sizes **within** the floors until the strip reads well; the tests do not
pin coordinates, only the rules.

Coordinates: strip units, origin top-left, y down; `W, H = s.w, s.h`
(79.5 x 23.3 mm). Sizes in `mm` or points as in `labels.py`.

### Task 4: Board strip (rpi, opi, riscv, x86)

**Files:**
- Modify: `src/rpi_hwid/strip.py`
- Create: `tests/test_strip.py`

**Interfaces:**
- Consumes: `strip.Strip`, `strip.STRIP_OF`; `strip_checks.*`; from
  `labels`: `mark_fitted(lab, mark, x, y, w, h)`, `mark_in_box(lab, path, x, y, w, h)`,
  `artwork(name)`, `mark_fan(lab, x, y, h)`, `mark_clock(lab, x, y, h)`,
  `camera_width(lab, cam, h)`, `mark_camera(lab, cam, x, y, h)`,
  `camera_words(cam)`, `CameraMarkDoesNotFitError`, `riscv.RISCV_MARK`,
  `SANS`, `SANS_BOLD`, `MONO`, `MONO_REGULAR`, `CAPTION`, `GREY`, `NOTE`.
- Produces: `strip.draw_board_strip(s, b)`; `STRIP_OF[labels.draw_board]`.

- [ ] **Step 1: Write the failing tests** — `tests/test_strip.py`:

```python
"""Every strip layout, on every fixture its full layout draws (spec
decisions 2 and 7, tests/strip_checks.py)."""
from __future__ import annotations

import pytest

import strip_checks as sc
from rpi_hwid import labels, strip

# (full draw function, kind name for the test ids); one entry per layout task
LAYOUTS = [
    (labels.draw_board, "board"),
]


def _cases(docs):
    for draw, name in LAYOUTS:
        rows = sc.rows_for(docs, draw)
        assert rows, f"the fixtures make no {name} label"
        for host, title, rec in rows:
            yield pytest.param(draw, rec, id=f"{name}:{host}:{title}")


def pytest_generate_tests(metafunc):
    if {"draw", "rec"} <= set(metafunc.fixturenames):
        import copy

        import conftest
        from rpi_hwid.model import ProbeDocument
        docs = {n: ProbeDocument.from_dict(n, copy.deepcopy(r)) for n, r in conftest.RAW.items()}
        metafunc.parametrize(("draw", "rec"), list(_cases(docs)))


def test_it_has_a_strip(draw, rec):
    assert draw in strip.STRIP_OF


def test_it_keeps_to_the_floors_and_the_strip(draw, rec):
    assert sc.problems(sc.on_strip(strip.STRIP_OF[draw], rec), *sc.STRIP) == []


def test_it_carries_every_fact_of_the_full_label(draw, rec):
    assert sc.missing_facts(sc.full(draw, rec), sc.on_strip(strip.STRIP_OF[draw], rec)) == []


def test_it_draws_the_same_marks(draw, rec):
    assert len(sc.on_strip(strip.STRIP_OF[draw], rec).marks) == len(sc.full(draw, rec).marks)
```

and append to `tests/test_roll.py` (the library API: `render_sheet` already
passes its `stock` to `render`, so a roll needs no change there, only a
strip for the kind):

```python
def test_the_library_api_gets_the_roll_through_render_sheet(data_dir):
    import json

    from rpi_hwid import label_input
    from rpi_hwid.collect import load_collected
    host, doc = sorted(load_collected(data_dir).items())[0]
    inputs = [json.loads(label_input.dumps(label_input.from_probe(host, doc.evidence)))]
    pdf = labels.render_sheet(inputs, only={"rpi"}, stock="dymo-99010")
    assert _pages(pdf) == [pytest.approx(R.page)]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_strip.py tests/test_roll.py -v`
Expected: FAIL (`draw_board` not in `STRIP_OF`; `render_sheet` raises `NoStripError`).

- [ ] **Step 3: Implement** — append to `strip.py` (add
`from reportlab.lib.units import mm` and `from rpi_hwid import riscv`
to its imports):

```python
# --- board: Raspberry Pi, Orange Pi, RISC-V, x86 ------------------------------
#
#  [mark ] Title                         [icons]  | eth MAC 98:fe:..  [QR]
#  [     ] subtitle                               |
#  [ QR  ] HAT ...  /  uuid ...                   | wlan MAC ...      [QR]
#  [ ser ] serial c36b093f773d46b8                |

def _board_corner(s, b, right, y, title_room):
    """The fan, clock and camera marks right-aligned at `right` on the title's
    row, as the full label's corner; returns their width. Refuses a camera
    mark that leaves the title no room, as the full label does."""
    icon, gap = 2.6 * mm, 1.1 * mm
    icons = [m for m, on in ((lb.mark_fan, b.fan), (lb.mark_clock, b.rtc_battery)) if on]
    cams = list(b.cameras or ())
    widths = [icon] * len(icons) + [lb.camera_width(s, cam, icon) for cam in cams]
    used = sum(widths) + gap * max(0, len(widths) - 1)
    if cams and s.width(b.title, lb.SANS_BOLD, 5.5) > title_room - used - 1.5 * mm:
        raise lb.CameraMarkDoesNotFitError(
            "%s: the camera marks (%s) leave the %s no room for its name, "
            "which would be cut short" % (
                b.host, ", ".join(" ".join(w for w in lb.camera_words(c) if w) for c in cams),
                b.title))
    x = right - used
    for draw in icons:
        draw(s, x, y + 0.2 * mm, icon)
        x += icon + gap
    for cam in cams:
        lb.mark_camera(s, cam, x, y + 0.2 * mm, icon)
        x += lb.camera_width(s, cam, icon) + gap
    return used


def _board_macs(s, b, x0):
    """eth over wlan from `x0` to the strip's end: caption and value, the
    code at the end. A MAC not read says why, in grey, with no code."""
    W, H = s.w, s.h
    qr = H / 2 - 1.0 * mm
    tw = W - qr - 1.5 * mm - x0
    macs = dict(b.macs)
    reasons = {"eth": b.eth_note or "not read", "wlan": b.wlan_note or "not read"}
    mac_size = s.fitted_size("00:00:00:00:00:00", lb.MONO, 13, tw)
    for row, kind in enumerate(("eth", "wlan")):
        y = row * (H / 2) + 0.5 * mm
        mac = macs.get(kind)
        s.text(x0, y + 1.0 * mm, kind + " MAC", lb.SANS, lb.CAPTION, color=lb.GREY)
        vy = y + 1.0 * mm + lb.CAPTION * 0.72 + 0.7 * mm
        if mac:
            s.text(x0, vy, mac, lb.MONO, mac_size)
            s.qr(W - qr, y, qr, mac)
        else:
            s.fit(x0, vy, reasons[kind], lb.SANS, 8, tw, color=lb.NOTE)


def draw_board_strip(s, b):
    """The board label along a strip: mark over the serial's code at the
    left; title, subtitle, the HAT (or ISA, or maker) rows and the serial in
    the middle; the two MACs and their codes at the right."""
    W, H = s.w, s.h
    a_w = 9 * mm
    ser_qr = 6.5 * mm                      # 21 modules at 0.31 mm, as on the full label
    lb.mark_fitted(s, b.mark, 0, 0, a_w, H - ser_qr - 1.5 * mm)
    qx = (a_w - ser_qr) / 2
    if b.serial:
        s.qr(qx, H - ser_qr, ser_qr, b.serial, error="l")
    else:
        s.no_code(qx, H - ser_qr, ser_qr, "none")

    bx = a_w + 1.5 * mm
    cx = 43 * mm                           # where the MAC column starts
    right = cx - 1.5 * mm
    head_x = bx
    maker_path = lb.artwork(b.maker_mark) if b.kind == "x86" and b.maker_mark else None
    if maker_path:
        lb.mark_in_box(s, maker_path, bx, 0, 8 * mm, 4.5 * mm)
        head_x = bx + 9.5 * mm
    corner = _board_corner(s, b, right, 0, right - head_x)
    title_w = right - head_x - (corner + 1.5 * mm if corner else 0)
    s.fit(head_x, 0.3 * mm, b.title, lb.SANS_BOLD, 10, title_w)
    sub_min = 4.5 if b.cameras else 5.5
    s.fit(head_x, 4.6 * mm, b.subtitle, lb.SANS, 6.5, right - head_x, min_size=sub_min)

    col_w = right - bx
    val_x = bx + 6.5 * mm
    y = 7.9 * mm
    if b.kind == "riscv":
        path = lb.artwork(riscv.RISCV_MARK)
        if path:
            lb.mark_in_box(s, path, bx, y, 6 * mm, 2.6 * mm)
        s.captioned(bx, val_x, y, "" if path else "ISA", b.isa or "", lb.MONO_REGULAR, 6.5,
                    right - val_x, min_size=4.5)
        if b.riscv_line:
            s.fit(val_x, y + 3.0 * mm, b.riscv_line, lb.SANS, 6.5, right - val_x)
    elif b.kind == "x86":
        if not maker_path:
            s.captioned(bx, val_x, y, "maker", b.maker or "maker not named", lb.SANS, 7,
                        right - val_x)
    else:
        s.captioned(bx, val_x, y, "HAT", "; ".join(b.header) if b.header else "none",
                    lb.SANS, 7, right - val_x)
        if b.hat_uuid:
            s.captioned(bx, val_x, y + 3.0 * mm, "uuid", b.hat_uuid, lb.MONO_REGULAR, 6.5,
                        right - val_x, min_size=5)

    foot = H - 8 * 0.72
    if b.serial:
        s.captioned(bx, val_x, foot, "serial", b.serial, lb.MONO, 8, right - val_x)
    else:
        s.fit(bx, foot, "no serial in firmware", lb.SANS, lb.CAPTION, col_w, color=lb.GREY)
    _board_macs(s, b, cx)


STRIP_OF[lb.draw_board] = draw_board_strip
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_strip.py -v`
Expected: PASS for every board fixture (Pi 5, Pi 4, Zero, CM1, Orange Pi,
the two Unmatched, the two MinnowBoards). A failure names the record and
the rule: fix the layout, never the checker.

- [ ] **Step 5: Look at it, and tune within the floors**

Create `tmp/strips.py`:

```python
"""Every strip of the fixtures, one PNG per label, for looking at."""
import copy
import subprocess
import sys

sys.path.insert(0, "tests")
import conftest  # noqa: E402

from rpi_hwid import labels  # noqa: E402
from rpi_hwid.model import ProbeDocument  # noqa: E402

kinds = set(sys.argv[1:]) or set(labels.KINDS)
docs = {n: ProbeDocument.from_dict(n, copy.deepcopy(r)) for n, r in conftest.RAW.items()}
labels.render(docs, "tmp/strips.pdf", kinds, outline=True, invariant=True, stock="dymo-99010")
subprocess.run(["pdftoppm", "-r", "300", "-png", "tmp/strips.pdf", "tmp/strip"], check=True)
```

Run: `uv run python tmp/strips.py rpi opi riscv x86`
Look at every `tmp/strip-*.png` (rotate 90° to read). Adjust the
coordinates in `draw_board_strip` until nothing crowds, every value is as
large as its room allows, and the MACs are the largest type; re-run the
tests after each change.

- [ ] **Step 6: Commit**

```bash
git add src/rpi_hwid/strip.py tests/test_strip.py tests/test_roll.py
git commit -m "strip: the board label along a 99010 sticker"
```

### Task 5: FPGA strip (netv2, arty, acorn, pcileech, cynthion, jtag, unknown-fpga)

**Files:**
- Modify: `src/rpi_hwid/strip.py`, `tests/test_strip.py`

**Interfaces:**
- Consumes: `labels.mark_maker(lab, board, x, y, h)`, the `FpgaBoard`
  record fields `ident`, `serial`, `kind`, `name`, `part`, `model`, `flash`,
  `flash_uid`, `ident_caption` (as `labels.draw_fpga` uses them).
- Produces: `strip.draw_fpga_strip(s, board)`; `STRIP_OF[labels.draw_fpga]`.

- [ ] **Step 1: Add the failing case** — in `tests/test_strip.py`, `LAYOUTS`:

```python
LAYOUTS = [
    (labels.draw_board, "board"),
    (labels.draw_fpga, "fpga"),
]
```

- [ ] **Step 2: Run to see it fail**

Run: `uv run pytest tests/test_strip.py -v -k fpga`
Expected: FAIL (`draw_fpga` not in `STRIP_OF`).

- [ ] **Step 3: Implement** — append to `strip.py`:

```python
# --- FPGA boards ----------------------------------------------------------------
#
#  [    ] [mark]                     flash  Micron N25Q128 · 16 MiB  [QR]
#  [ QR ] hoopoe                     uid    2353...126b
#  [    ] Arty A7-35T · XC7A35T
#         S/N 210319A43AD3
#  Device DNA  0x0064f5483229085c

def draw_fpga_strip(s, board):
    """The FPGA label along a strip: the identity's code at the left; the
    mark, the board's word, model and die and an Arty's serial beside it;
    the flash rows against the flash's own code at the right; the identifier
    across the foot."""
    W, H = s.w, s.h
    foot_size = 13
    foot_h = foot_size * 0.72
    cap_y = H - foot_h - 0.9 * mm - lb.CAPTION * 0.72
    qr = cap_y - 1.0 * mm                  # the big code, as tall as the room above the foot
    ident = board.ident or board.serial
    if ident:
        s.qr(0, 0, qr, ident)
    x = qr + 2 * mm
    fq = 6.5 * mm                          # the flash's code, as on the full label
    fx = W - fq
    mid = 40 * mm                          # the board's column ends here, the flash's begins
    col_w = mid - x
    mark_h = 4.5 * mm
    lb.mark_maker(s, board, x, 0, mark_h)
    y = mark_h + 0.4 * mm
    word = board.name.split("-", 1)[1] if board.name else (board.part or board.model.split()[0])
    s.fit(x, y, word, lb.SANS_BOLD, 14, col_w)
    y += 14 * 0.72 + 1.0 * mm
    line = ("{}  ·  {}".format(board.model, board.part)
            if board.part and board.part != word else board.model)
    s.fit(x, y, line, lb.SANS, 7, col_w)
    if board.kind == "arty":
        y += 7 * 0.72 + 0.9 * mm
        s.captioned(x, x + 5 * mm, y, "S/N", board.serial, lb.MONO, 7, col_w - 5 * mm)

    cap_x = fx - 1.5 * mm - max(s.width(c, lb.SANS, lb.CAPTION) for c in ("flash", "uid"))
    flash_y = 0.5 * mm
    s.captioned_after(mid + 1 * mm, cap_x, flash_y, "flash", board.flash, lb.SANS, 7,
                      min_size=5)
    if board.flash_uid:
        s.captioned_after(mid + 1 * mm, cap_x, flash_y + 2.3 * mm, "uid", board.flash_uid,
                          lb.MONO_REGULAR, 7, min_size=5)
        s.qr(fx, 0, fq, board.flash_uid, error="l")
    elif board.flash:
        s.no_code(fx, 0, fq)

    s.text(x, cap_y, board.ident_caption, lb.SANS, lb.CAPTION, color=lb.GREY)
    s.fit(x, H - foot_h, board.ident, lb.MONO, foot_size, W - x)


STRIP_OF[lb.draw_fpga] = draw_fpga_strip
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_strip.py -v`
Expected: PASS for every FPGA fixture (NeTV2, Arty, Acorn, the TT-FPGA
host's boards).

- [ ] **Step 5: Look at it, and tune within the floors**

Run: `uv run python tmp/strips.py fpga` and inspect `tmp/strip-*.png`.
Adjust; re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add src/rpi_hwid/strip.py tests/test_strip.py
git commit -m "strip: the FPGA label along a 99010 sticker"
```

### Task 6: USB adapter strip

**Files:**
- Modify: `src/rpi_hwid/strip.py`, `tests/test_strip.py`

**Interfaces:**
- Consumes: `labels.mark_usb(lab, x, y, h)`, `labels.mark_wifi(lab, x, y, h)`,
  `labels.mark_rj45(lab, x, y, h)` (each returns its width); the USB record
  fields `kind`, `title`, `mac`, `lines` (as `labels.draw_usb` uses them).
- Produces: `strip.draw_usb_strip(s, dev)`; `STRIP_OF[labels.draw_usb]`.

- [ ] **Step 1: Add the failing case** — append `(labels.draw_usb, "usb"),` to `LAYOUTS`.

- [ ] **Step 2: Run to see it fail**

Run: `uv run pytest tests/test_strip.py -v -k usb`
Expected: FAIL.

- [ ] **Step 3: Implement** — append to `strip.py`:

```python
# --- USB network adapters ---------------------------------------------------------
#
#  [usb][rj45] ASIX AX88179 Gigabit Ethernet                   [    ]
#  VID:PID 0b95:1790    speed SS 5 Gbit/s                      [ QR ]
#  serial  00000000001B21                                       [    ]
#  MAC 98:fe:54:13:f5:75

def draw_usb_strip(s, dev):
    """The USB adapter label along a strip: glyphs and chip name across the
    top, the facts in up to two columns, the MAC along the foot, and the
    MAC's code at the right end."""
    W, H = s.w, s.h
    qr = 16 * mm
    qx = W - qr
    s.qr(qx, (H - qr) / 2, qr, dev.mac)
    room = qx - 2 * mm
    h = 4.5 * mm
    w = lb.mark_usb(s, 0, h * 0.15, h * 0.7) + 1.5 * mm
    w += (lb.mark_wifi if dev.kind == "wifi" else lb.mark_rj45)(s, w, 0, h)
    tx = w + 2 * mm
    s.fit(tx, (h - 11 * 0.72) / 2, dev.title, lb.SANS_BOLD, 11, room - tx)

    mac_size = 14
    mac_y = H - mac_size * 0.72
    cap_y = mac_y - 0.7 * mm - lb.CAPTION * 0.72
    s.text(0, cap_y, "MAC", lb.SANS, lb.CAPTION, color=lb.GREY)
    s.fit(0, mac_y, dev.mac, lb.MONO, mac_size, room)

    lines = [(k, v) for k, v in dev.lines if v]
    top, pitch, size = h + 1.5 * mm, 3.2 * mm, 7.5
    per_col = max(1, int((cap_y - 0.8 * mm - top - size * 0.72) // pitch) + 1)
    cols = [lines[i:i + per_col] for i in range(0, len(lines), per_col)]
    col_w = room / max(1, len(cols))
    for ci, col in enumerate(cols):
        x = ci * col_w
        for li, (k, v) in enumerate(col):
            font = lb.MONO if k == "VID:PID" else lb.SANS
            y = top + li * pitch
            if k:
                s.captioned(x, x + 8.5 * mm, y, k, v, font, size, col_w - 9.5 * mm)
            else:
                s.fit(x + 8.5 * mm, y, v, font, size, col_w - 9.5 * mm)


STRIP_OF[lb.draw_usb] = draw_usb_strip
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_strip.py -v`
Expected: PASS for the USB fixtures (the Linksys host's adapter, the Wi-Fi host's).

- [ ] **Step 5: Look at it, and tune within the floors**

Run: `uv run python tmp/strips.py usb`; inspect; adjust; re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add src/rpi_hwid/strip.py tests/test_strip.py
git commit -m "strip: the USB adapter label along a 99010 sticker"
```

### Task 7: Tiny Tapeout strip

**Files:**
- Modify: `src/rpi_hwid/strip.py`, `tests/test_strip.py`

**Interfaces:**
- Consumes: `labels.mark_tinytapeout(lab, x, y, h)`, `labels.marks_widths(marks, h)`,
  `labels.marks_row(lab, marks, right_x, y, h)`, `labels.swatch(lab, x, y, w, h, mask, silk, label)`,
  `labels.tinytapeout_rom_line(tt) -> (caption, text, font)`; the TT record
  fields as `labels.draw_tinytapeout` uses them.
- Produces: `strip.draw_tinytapeout_strip(s, tt)`; `STRIP_OF[labels.draw_tinytapeout]`.

- [ ] **Step 1: Add the failing case** — append `(labels.draw_tinytapeout, "tt"),` to `LAYOUTS`.

- [ ] **Step 2: Run to see it fail**

Run: `uv run pytest tests/test_strip.py -v -k tt`
Expected: FAIL.

- [ ] **Step 3: Implement** — append to `strip.py`:

```python
# --- Tiny Tapeout -------------------------------------------------------------------
#
#  [TT] TT06               [carrier] blue / white         [         ]
#  ASIC · sky130 [marks]   [demo   ] black / white        [chip QR  ]
#  board TT06+ demo                                       [         ]
#  ROM   commit 1a2b3c4                                    chip page
#  RP2 id (USB serial) e6614c311b6b5c2f            [id QR]

def draw_tinytapeout_strip(s, tt):
    """The Tiny Tapeout label along a strip: the chip page's code at the
    right end; mark and shuttle, subtitle and shuttle marks, the board and
    ROM rows at the left; the two board samples between; the demo board's
    RP2 id and its code along the foot."""
    W, H = s.w, s.h
    qr = 16.5 * mm
    qx = W - qr
    s.qr(qx, 0, qr, tt.url)
    s.text(qx + qr / 2, qr + 0.4 * mm, "chip page", lb.SANS, lb.CAPTION, align="centre",
           color=lb.GREY)

    id_size, id_qr = 10, 6.5 * mm
    id_y = H - id_size * 0.72
    foot_w = qx - 2 * mm
    cap_y = id_y - 0.7 * mm - lb.CAPTION * 0.72
    s.text(0, cap_y, "%s id (USB serial)" % (tt.mcu or "RP2"), lb.SANS, lb.CAPTION,
           color=lb.GREY)
    if tt.usb_serial:
        qr_x = foot_w - id_qr
        s.qr(qr_x, H - id_qr, id_qr, tt.usb_serial, error="l")
        s.text(qr_x - 0.8 * mm, H - id_qr, "id", lb.SANS, lb.CAPTION, align="right",
               color=lb.GREY)
        s.fit(0, id_y, tt.usb_serial, lb.MONO, id_size, qr_x - 6 * mm)
    else:
        s.rule(0, id_y + id_size * 0.72 + 0.3 * mm, foot_w)

    col_w = 36 * mm
    head = 14
    mark_h = 5 * mm
    w = lb.mark_tinytapeout(s, 0, (head * 0.72 - mark_h) / 2, mark_h)
    tx = w + 1.5 * mm if w else 0
    s.fit(tx, 0, tt.headline, lb.SANS_BOLD, head, col_w - tx)
    y = head * 0.72 + 1.0 * mm
    s.text(0, y, tt.subtitle, lb.SANS, 6.5)
    if tt.marks:
        free = col_w - s.width(tt.subtitle, lb.SANS, 6.5) - 2 * mm
        mh = 3.0 * mm
        while mh > 2 * mm and lb.marks_widths(tt.marks, mh)[1] > free:
            mh -= 0.2 * mm
        if lb.marks_widths(tt.marks, mh)[1] <= free:
            lb.marks_row(s, tt.marks, col_w, y - 0.4 * mm, mh)
    y += 3.0 * mm
    cap_w = 9 * mm
    s.captioned(0, cap_w, y, "board", tt.demoboard_text, lb.SANS, 7, col_w - cap_w)
    caption, text, font = lb.tinytapeout_rom_line(tt)
    s.captioned(0, cap_w, y + 3.0 * mm, caption, text, font, 7, col_w - cap_w)

    sx, sw, sh = col_w + 2 * mm, 9 * mm, 5 * mm
    tw = qx - 2 * mm - (sx + sw + 1 * mm)
    line = lb.CAPTION * 0.72 + 0.5 * mm
    for i, (cap, mask, silk, mask_name, silk_name) in enumerate((
            ("carrier", tt.chip_colour, tt.chip_silk, tt.chip_colour_name, tt.chip_silk_name),
            ("demo\nboard", tt.demoboard_colour, tt.demoboard_silk,
             tt.demoboard_colour_name, tt.demoboard_silk_name))):
        sy = i * (sh + 1.0 * mm)
        lb.swatch(s, sx, sy, sw, sh, mask, silk, cap)
        tx2 = sx + sw + 1 * mm
        if mask_name or silk_name:
            ty = sy + (sh - 2 * line + 0.5 * mm) / 2
            s.fit(tx2, ty, mask_name or "?", lb.SANS, lb.CAPTION, tw, min_size=4.5)
            s.fit(tx2, ty + line, silk_name or "?", lb.SANS, lb.CAPTION, tw, min_size=4.5,
                  color=lb.GREY)
        else:
            s.fit(tx2, sy + (sh - lb.CAPTION * 0.72) / 2, "unknown", lb.SANS, lb.CAPTION,
                  tw, min_size=4.5, color=lb.GREY)


STRIP_OF[lb.draw_tinytapeout] = draw_tinytapeout_strip
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_strip.py -v`
Expected: PASS for the TT fixtures (`rpi4-tt`, `pi-sw2-p33`).

- [ ] **Step 5: Look at it, and tune within the floors**

Run: `uv run python tmp/strips.py tt`; inspect; adjust; re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add src/rpi_hwid/strip.py tests/test_strip.py
git commit -m "strip: the Tiny Tapeout label along a 99010 sticker"
```

### Task 8: SDR strip, and every full layout has a strip

**Files:**
- Modify: `src/rpi_hwid/sdr_labels.py` (after `draw_sdr`), `src/rpi_hwid/strip.py`,
  `tests/test_strip.py`

**Interfaces:**
- Consumes: in `sdr_labels`: `maker_mark(lab, r, x, y, h)`,
  `coverage(lab, x, y, w, r) -> height`, `channels(lab, x, y, size, r) -> width`;
  the SDR record fields as `draw_sdr` uses them.
- Produces: `sdr_labels.draw_sdr_strip(lab, r)`; `STRIP_OF[sdr_labels.draw_sdr]`;
  the guarantee that every full draw function `labels.whole_labels` can
  yield has a strip.

- [ ] **Step 1: Add the failing cases** — in `tests/test_strip.py` append
`(sdr_labels.draw_sdr, "sdr"),` to `LAYOUTS` (import `sdr_labels` from
`rpi_hwid`), and add:

```python
def test_every_full_layout_has_a_strip(docs):
    """Whatever whole label the fixtures make can go on a roll."""
    draws = {d for _h, _k, _t, d, _r in labels.whole_labels(docs, set(labels.KINDS))}
    assert draws <= set(strip.STRIP_OF)
```

If the shared fixtures have no SDR host, add one: copy the SDR document the
SDR tests use (`tests/sdr_fixtures.py`) into `conftest.RAW` under a host
name of its own, and check `uv run pytest -q` still passes with it there.

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_strip.py -v -k "sdr or every_full"`
Expected: FAIL.

- [ ] **Step 3: Implement** — in `sdr_labels.py`, after `draw_sdr`:

```python
def draw_sdr_strip(lab, r):
    """The SDR label along a strip (rpi_hwid.strip): the identity's code at
    the left; mark, name and revision and the chips beside it; the coverage
    band, the channel groups and the facts at the right; the identity, or
    the shared serial, along the foot."""
    W, H = lab.w, lab.h
    size = 8
    foot = H - size * 0.72
    cap_y = foot - 0.7 * lb.mm - lb.CAPTION * 0.72
    qr = min(13 * lb.mm, cap_y - 0.8 * lb.mm)
    if r.ident:
        lab.qr(0, 0, qr, r.ident)
    else:
        lab.no_code(0, 0, qr, "not unique")
    x = qr + 2 * lb.mm
    mid = 44 * lb.mm
    col_w = mid - x
    mark_h = 3.6 * lb.mm
    maker_mark(lab, r, x, 0, mark_h)
    y = mark_h + 0.6 * lb.mm
    variant_w = (lab.width(r.variant, lb.SANS, 7) + 1.5 * lb.mm) if r.variant else 0
    title_size = lab.fitted_size(r.title, lb.SANS_BOLD, 12, col_w - variant_w)
    lab.fit(x, y, r.title, lb.SANS_BOLD, title_size, col_w - variant_w)
    if r.variant:
        tx = x + lab.width(r.title, lb.SANS_BOLD, title_size) + 1.5 * lb.mm
        lab.text(tx, y + (title_size - 7) * 0.72, r.variant, lb.SANS, 7, color=lb.GREY)
    y += title_size * 0.72 + 1.0 * lb.mm
    rows = [("radio", r.radio)] + ([("FPGA", r.fpga)] if r.fpga else [])
    cap_w = max(lab.width(cap, lb.SANS, lb.CAPTION) for cap, _v in rows)
    x_val = x + cap_w + lab.CAPTION_GAP
    for cap, val in rows:
        lab.captioned(x, x_val, y, cap, val, lb.SANS_BOLD, 7, mid - x_val, min_size=5)
        y += 7 * 0.72 + 0.75 * lb.mm

    rx = mid + 1.5 * lb.mm
    rw = W - rx
    y = coverage(lab, rx, 0, rw, r) + 0.8 * lb.mm
    glyph = 3.6 * lb.mm
    channels(lab, rx + 0.3 * lb.mm, y, glyph, r)
    y += glyph + 0.5 * lb.mm + GROUP_CAPTION * 0.72 + 1.0 * lb.mm
    lab.fit(rx, y, r.facts[0], lb.SANS_BOLD, 7, rw, min_size=5)
    y += 7 * 0.72 + 1.0 * lb.mm
    if r.flash:
        cap_x = W - lab.width("flash", lb.SANS, lb.CAPTION)
        lab.captioned_after(rx, cap_x, y, "flash", r.flash, lb.SANS, 6.5, min_size=5)
    else:
        lab.fit(rx, y, r.facts[1], lb.SANS, 6.5, rw, min_size=5)

    if r.ident:
        caption = r.ident_caption + (f"  ·  {r.dna_note}" if r.dna_note else "")
        lab.fit(x, cap_y, caption, lb.SANS, lb.CAPTION, mid - x, min_size=4.5, color=lb.GREY)
        lab.fit(x, foot, r.ident, lb.MONO, size, W - x, min_size=5.5)
    else:
        lab.fit(x, cap_y, r.shared_caption, lb.SANS, lb.CAPTION, mid - x, min_size=4.5,
                color=lb.NOTE)
        lab.fit(x, foot, r.shared or "", lb.MONO, size, W - x)
```

and in `strip.py`, at the end:

```python
from rpi_hwid import sdr_labels  # noqa: E402  (it imports labels, not strip)

STRIP_OF[sdr_labels.draw_sdr] = sdr_labels.draw_sdr_strip
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_strip.py -v && uv run pytest -q`
Expected: all PASS. If the right-hand column overruns (the coverage band,
channels and two fact rows are taller than the strip for a radio with many
channels), the checker says which: move the facts right of the channel
glyphs as the full label does (`fx = rx + used + 2 mm`) and re-run.

- [ ] **Step 5: Look at it, and tune within the floors**

Run: `uv run python tmp/strips.py sdr`; inspect; adjust; re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add src/rpi_hwid/sdr_labels.py src/rpi_hwid/strip.py tests/test_strip.py tests/conftest.py
git commit -m "strip: the SDR label along a 99010 sticker; every full layout now has a strip"
```

---

### Task 9: `--print QUEUE`

**Files:**
- Modify: `src/rpi_hwid/sheet/ipp.py` (value encoders ~line 78-140;
  `Printer.print_job` ~line 372)
- Modify: `src/rpi_hwid/roll.py`, `src/rpi_hwid/labels.py` (`main`, `_main`)
- Modify: `tests/test_sheet_ipp.py`, `tests/test_roll.py`

**Interfaces:**
- Consumes: `ipp.Printer(uri, timeout)`, `.attributes(names)`, `.job(id)`;
  `ipp.Job` (`.id`, `.state`, `.unsupported`); `ipp.JOB_DONE`,
  `ipp.JOB_COMPLETED`, `ipp.JOB_STATES`; `labels.Roll.media_size`.
- Produces:
  - `ipp.resolution(x, y, units=3) -> Value`;
    `ipp.Printer.print_document(pdf, job_name, job, user="rpi-hwid") -> Job`.
  - `roll.queue_uri(queue) -> str`; `roll.PrintError(RuntimeError)`;
    `roll.job_attributes(roll) -> Attrs`;
    `roll.print_pdf(pdf, queue, roll, job_name, poll=1.0, wait=120.0) -> str`
    (the final job-state name; raises `PrintError` otherwise).
  - `rpi-hwid labels ... --print QUEUE`.

- [ ] **Step 1: Write the failing IPP tests** — append to `tests/test_sheet_ipp.py`:

```python
def test_a_resolution_is_two_ints_and_a_unit_byte():
    """RFC 8010 3.9: resolution is SIGNED-INTEGER x, SIGNED-INTEGER y,
    SIGNED-BYTE units (3 = dots per inch)."""
    got = ipp.encode_attribute("printer-resolution", ipp.resolution(300, 600))
    assert got == (b"\x32\x00\x12printer-resolution\x00\x09"
                   + struct.pack(">iib", 300, 600, 3))


def test_print_document_sends_the_attributes_it_is_given(printer):
    p = printer(lambda msg: ok(ipp.JOB_GROUP, {"job-id": ipp.integer(5),
                                               "job-state": ipp.enum(3)}))
    job = ipp.Printer(p.uri).print_document(b"%PDF-roll", "rpi-hwid labels",
                                            {"print-scaling": ipp.keyword("none")})
    assert job.id == 5
    (msg,) = p.requests
    assert msg.data == b"%PDF-roll"
    assert msg.attributes("job") == {"print-scaling": ["none"]}
    assert msg.attributes("operation")["requesting-user-name"] == ["rpi-hwid"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_sheet_ipp.py -v`
Expected: the two new tests FAIL; the existing ones PASS.

- [ ] **Step 3: Implement** — in `ipp.py` after `mime`:

```python
def resolution(x: int, y: int, units: int = 3) -> Value:
    """A resolution, `units` 3 for dots per inch (RFC 8010 3.9)."""
    return Value(RESOLUTION, (x, y, units))
```

in `_plain`, before the `bytes` case:

```python
    if v.tag == RESOLUTION:
        return _field(struct.pack(">iib", *v.value))
```

and `print_job` becomes a caller of a new `print_document`:

```python
    def print_document(self, pdf: bytes, job_name: str, job: Attrs,
                       user: str = "rpi-hwid") -> Job:
        """Print `pdf` with the job attributes `job`."""
        msg = self._call(PRINT_JOB, {
            "requesting-user-name": name(user),
            "job-name": name(job_name),
            "document-format": mime("application/pdf"),
        }, job, data=pdf)
        j = msg.attributes("job")
        if "job-id" not in j:
            raise IppError(f"printer {self.where} accepted the job but gave it no job-id")
        return Job(j["job-id"][0], (j.get("job-state") or [JOB_PENDING])[0],
                   sorted(msg.attributes("unsupported")))

    def print_job(self, pdf: bytes, job_name: str, user: str = "rpi-hwid-sheet") -> Job:
        """Print `pdf` from the manual feed slot onto A4 label stock, at full
        size and in colour."""
        return self.print_document(pdf, job_name, {
            "media-col": collection({
                "media-source": keyword("manual"),
                "media-type": keyword("labels"),
                "media-size": collection({"x-dimension": integer(21000),
                                          "y-dimension": integer(29700)}),
            }),
            "print-scaling": keyword("none"),
            "print-color-mode": keyword("color"),
            "sides": keyword("one-sided"),
        }, user)
```

- [ ] **Step 4: Run the IPP tests**

Run: `uv run pytest tests/test_sheet_ipp.py -v`
Expected: all PASS (the existing manual-feed test proves the sheet's request is unchanged).

- [ ] **Step 5: Write the failing roll-printing tests** — append to `tests/test_roll.py`
(and add `import sys` and `from rpi_hwid.sheet import ipp` at its top;
reuse the fake printer: `from test_sheet_ipp import FakePrinter, ok`):

```python
from test_sheet_ipp import FakePrinter, ok  # noqa: E402


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
    ({"printer_state": 5, "message": "Unplugged or turned off"}, "Unplugged or turned off"),
    ({"accepting": False}, "not accepting"),
    ({"sizes": ((5419, 2227),)}, "27.86 x 88.9 mm"),
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
        with pytest.raises(roll.PrintError, match="job 77"):
            roll.print_pdf(b"%PDF-roll", p.uri, R, "labels", poll=0.01, wait=0.05)
    finally:
        p.close()


def test_print_is_for_a_roll(data_dir):
    with pytest.raises(SystemExit):
        labels.main(["--data", str(data_dir), "--print", "LabelWriter-450"])


def test_the_command_prints_the_roll_it_wrote(data_dir, tmp_path, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(roll, "print_pdf",
                        lambda pdf, q, r, name, **k: sent.append((pdf[:5], q, r.name)) or "completed")
    out = tmp_path / "roll.pdf"
    only = [a for k in micro.kinds() for a in ("--only", k)]
    assert labels.main(["--data", str(data_dir), *only, "--stock", "dymo-99010",
                        "--out", str(out), "--print", "LabelWriter-450"]) == 0
    assert sent == [(b"%PDF-", "LabelWriter-450", "dymo-99010")]
    assert "completed" in capsys.readouterr().out
```

- [ ] **Step 6: Run to see them fail**

Run: `uv run pytest tests/test_roll.py -v -k "queue or print"`
Expected: FAIL (`roll` has no `queue_uri`, `print_pdf`; `--print` unknown).

- [ ] **Step 7: Implement** — append to `roll.py` (add `import time` and
`from rpi_hwid.sheet import ipp` to its imports):

```python
class PrintError(RuntimeError):
    """The roll was not printed; the message says why."""


def queue_uri(queue: str) -> str:
    """A CUPS queue name on this machine, or a printer's own ipp:// URI."""
    return queue if "://" in queue else f"ipp://localhost:631/printers/{queue}"


def job_attributes(roll: labels.Roll) -> ipp.Attrs:
    """The roll's size as CUPS lists it, no scaling, and 300 x 600 dpi --
    which is how CUPS offers the DYMO driver's DymoPrintQuality=Graphics,
    the quality for codes."""
    x, y = roll.media_size
    return {
        "media-col": ipp.collection({"media-size": ipp.collection({
            "x-dimension": ipp.integer(x), "y-dimension": ipp.integer(y)})}),
        "print-scaling": ipp.keyword("none"),
        "printer-resolution": ipp.resolution(300, 600),
    }


def _size(roll: labels.Roll) -> str:
    x, y = roll.media_size
    return f"{x / 100:g} x {y / 100:g} mm"


def print_pdf(pdf: bytes, queue: str, roll: labels.Roll, job_name: str,
              poll: float = 1.0, wait: float = 120.0) -> str:
    """Send `pdf` to `queue` and wait for the job to end; returns
    "completed". Refuses, before sending, a queue that is stopped, not
    accepting jobs, or has no such roll size; raises PrintError for a job
    that ends any other way or is still going after `wait` seconds."""
    pr = ipp.Printer(queue_uri(queue))
    try:
        a = pr.attributes(["printer-state", "printer-state-message",
                           "printer-is-accepting-jobs", "media-size-supported"])
        if (a.get("printer-state") or [3])[0] == 5:
            why = (a.get("printer-state-message") or [""])[0] or "stopped"
            raise PrintError(f"{queue}: the printer is stopped: {why}")
        if not (a.get("printer-is-accepting-jobs") or [True])[0]:
            raise PrintError(f"{queue}: the queue is not accepting jobs")
        sizes = {(m["x-dimension"][0], m["y-dimension"][0])
                 for m in a.get("media-size-supported", []) if isinstance(m, dict)}
        if tuple(roll.media_size) not in sizes:
            raise PrintError(f"{queue}: the queue has no {_size(roll)} size ({roll.ppd_size}): "
                             f"is it on DYMO's driver, printer-driver-dymo's lw450.ppd?")
        job = pr.print_document(pdf, job_name, job_attributes(roll))
        deadline = time.monotonic() + wait
        while True:
            j = pr.job(job.id)
            state = (j.get("job-state") or [ipp.JOB_PENDING])[0]
            if state in ipp.JOB_DONE:
                break
            if time.monotonic() >= deadline:
                raise PrintError(f"{queue}: job {job.id} is still "
                                 f"{ipp.JOB_STATES.get(state, state)} after {wait:g} s: "
                                 f"see lpstat -W all -o {queue}")
            time.sleep(poll)
    except ipp.IppError as exc:
        raise PrintError(str(exc)) from exc
    name = ipp.JOB_STATES.get(state, str(state))
    if state != ipp.JOB_COMPLETED:
        why = ", ".join(map(str, j.get("job-state-reasons", []))) or "no reason given"
        raise PrintError(f"{queue}: job {job.id} {name}: {why}")
    return name
```

In `labels.main` add, after `--stock`:

```python
    ap.add_argument("--print", metavar="QUEUE", dest="print_to",
                    help="with a roll --stock: also send the PDF to this CUPS queue (or ipp:// "
                         "URI) and wait for the job, e.g. --print LabelWriter-450")
```

In `_main`, next to the `--start` check:

```python
    if args.print_to and not on_roll:
        ap.error("--print sends a roll to its printer: a sheet goes through rpi-hwid-sheet")
```

and in the roll branch, after the `"%d label%s on %d sticker%s -> %s"` line:

```python
        if args.print_to:
            try:
                state = roll.print_pdf(args.out.read_bytes(), args.print_to, on_roll,
                                       "rpi-hwid labels %s" % args.out.name)
            except roll.PrintError as exc:
                print(exc, file=sys.stderr)
                return 1
            print("printed on %s: job %s" % (args.print_to, state))
```

- [ ] **Step 8: Run everything**

Run: `uv run ruff check && uv run mypy && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/rpi_hwid/sheet/ipp.py src/rpi_hwid/roll.py src/rpi_hwid/labels.py tests/test_sheet_ipp.py tests/test_roll.py
git commit -m "roll: --print QUEUE sends the roll to its CUPS queue, checked first, and waits"
```

---

### Task 10: On paper

Nothing here is automated: it is the check the spec's "To confirm on the
first printed roll" asks for, on the LabelWriter 450 with a 99010 roll
loaded and the queue on DYMO's driver.

**Files:**
- Modify: `src/rpi_hwid/roll.py` (only if a finding needs it)
- Modify: `docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md` (record the findings)

- [ ] **Step 1: The queue is ready**

Run: `lpstat -p LabelWriter-450 && lpoptions -p LabelWriter-450 -l | grep -E 'PageSize|DymoPrintQuality'`
Expected: `idle`; `PageSize` lists `w79h252.2`; `DymoPrintQuality` exists.
If stopped: `cupsenable LabelWriter-450` after plugging it in.

- [ ] **Step 2: Print one strip of each kind**

Run: `uv run python tmp/strips.py` (writes `tmp/strips.pdf` with every kind),
then pick one page per kind with `qpdf --pages tmp/strips.pdf 1,N,... -- tmp/one-each.pdf`
(or print the whole file if it is short), and send it through the tool's own
path: `uv run rpi-hwid labels --data DIR --stock dymo-99010 --out tmp/roll.pdf --print LabelWriter-450`
for a data directory holding one host of each kind.
Expected: `printed on LabelWriter-450: job completed`.

- [ ] **Step 3: Check each sticker**

For each: nothing cut off at any edge; the strip reads left to right as the
sticker is held with its text upright; every QR scans with a phone and
gives the identifier printed beside it; 4.5 pt text is legible.

- [ ] **Step 4: Act on what it shows**

- Upside down for the way the sticker is peeled: in `roll._place` use
  `c.translate(bx, by + bh); c.rotate(-90)` and in `page_point` return
  `(bx + v, by + bh - u)`; re-run `tests/test_roll.py`.
- Codes do not scan, or the job's `printer-resolution` is listed in the
  job's unsupported attributes (`lpstat -l -o`): send the PPD option
  instead, `"DymoPrintQuality": ipp.keyword("Graphics")`, in
  `job_attributes`, update its test, reprint.
- Text illegible at 4.5 pt: raise `TEXT_FLOOR` in `tests/strip_checks.py`
  and the spec's decision 7 together, and enlarge what then fails.

- [ ] **Step 5: Record the findings in the spec and commit**

Replace the spec's "To confirm on the first printed roll" section with
"Confirmed on paper (DATE)" and what was found.

```bash
git add docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md src/rpi_hwid/roll.py tests/
git commit -m "roll: checked on a 99010 roll: orientation, codes, smallest type"
```

---

### Task 11: Docs and gallery

**Files:**
- Modify: `docs/LABELS.md` (new section after "### Other sheets: `--stock`", line ~73)
- Create: `docs/examples/render_strips.py`
- Modify: `docs/DEVELOPING.md` (the render list, line ~40-47), `README.md` (gallery)

- [ ] **Step 1: LABELS.md** — add, after the `--stock` section:

```markdown
### A DYMO roll: `--stock dymo-99010`

`--stock dymo-99010` lays each label out as a strip for one DYMO 99010
sticker (28 x 89 mm) in a LabelWriter 450: the same facts and codes as the
sheet label, re-flowed along the sticker, one sticker per page, and micro
labels two to a sticker with a line to cut between them. `--print QUEUE`
also sends it to that CUPS queue and waits for the job:

    rpi-hwid labels --data data/ --stock dymo-99010 --print LabelWriter-450

The queue must use DYMO's own driver, which places the print on the
sticker:

    sudo apt install printer-driver-dymo
    lpadmin -p LabelWriter-450 -m dymo:0/cups/model/lw450.ppd -o PageSize=w79h252.2

CUPS's generic `DYMO Label Printer` driver (`sample.drv/dymo.ppd`) prints
about 4.5 mm along and 1.5 mm across off the sticker, and no setting moves
it. The LabelWriter cannot say what roll is loaded, so name it. The strips
keep 1 mm inside the driver's printable area (1.5 mm from the sticker's
edges, 5.8 mm at one end). `--print` refuses a queue that is stopped, not
accepting jobs, or has no 27.86 x 88.9 mm size, before sending anything.
```

- [ ] **Step 2: The gallery script** — `docs/examples/render_strips.py`,
following `docs/examples/render.py`'s pattern (read it first and reuse its
helpers for loading the sample documents and writing PNGs): render one
strip per kind with `labels.render(..., stock="dymo-99010")`, turn each page
a quarter so it reads left to right, and write `docs/examples/strip-<kind>.png`
at the same pixels per mm as the existing crops.

- [ ] **Step 3: Run it and add the images**

Run: `uv run docs/examples/render_strips.py`
Expected: one `docs/examples/strip-*.png` per kind. Add them to the README
gallery under a "On a DYMO roll" heading, and the command to DEVELOPING.md's
render list.

- [ ] **Step 4: The whole gate**

Run: `uv run ruff check && uv run mypy && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/LABELS.md docs/DEVELOPING.md README.md docs/examples/render_strips.py docs/examples/strip-*.png
git commit -m "docs: strip labels on a DYMO roll, and how to set up its queue"
```
