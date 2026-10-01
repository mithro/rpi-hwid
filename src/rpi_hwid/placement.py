"""Chosen labels in chosen slots of a part-used sheet.

``rpi-hwid labels`` normally fills a sheet from its first sticker. A sheet
that has already had some stickers peeled off, or printed, is fed through
again with only its free slots printed: ``--list --json`` names every label
the collected data makes, one by one, and ``--place PLAN`` draws the ones a
plan names in the slots it gives them and nothing else. The plan comes from
whatever keeps track of which slots are used (rpi-hwid-sheet); this module
only draws, and refuses a plan that would print one label over another.

A slot is a sticker, ``1``-``21`` in reading order on the L7160 grid, or a
quarter of one for a micro label, ``7a``-``7d`` (a and b across the top, c
and d under them). A plan is JSON::

    {"labels": [{"id": "<an id --list --json gave>", "slot": "7"}, ...],
     "guides": ["9"],                  # stickers to draw micro cut guides on
     "sheet": {"id": "K7QX", "note": "..."},   # printed in both margins
     "outline": false}

Only ``labels`` is required. A sticker's cut guides cross all four of its
quarters, so the plan asks for them once, with the sticker's first micro
label, and never again on a sheet that already has them.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Any

from reportlab.lib.colors import black
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from rpi_hwid import labels, micro

if TYPE_CHECKING:
    from pathlib import Path

STICKERS = labels.COLS * labels.ROWS
QUARTERS = "abcd"
# The Brother's unprintable edge, from its media-*-margin (432 hundredths
# of a millimetre): the sheet's marking keeps inside it.
PRINTER_EDGE = 4.32 * mm
SHEET_ID_SIZE = 11.0
NOTE_SIZE = 6.5
EDGE_SIZE = 6.5
# The margin text's middle, this far in from the printer's edge: clear of
# it, and of the registration ticks nearer the grid.
TEXT_IN = 3.2 * mm
# A registration tick stops this far short of the grid (so no sticker is
# inked) and of the printer's edge, and is at most TICK_LEN long.
TICK_CLEAR = 0.4 * mm
TICK_LEN = 2.5 * mm
TICK_WEIGHT = 0.4


class PlacementError(ValueError):
    """A plan that names a label the data does not make, a slot off the
    sheet, or would print over itself."""


@dataclass(frozen=True)
class Entry:
    """One label the collected data makes, as the listing names it."""

    id: str
    host: str
    kind: str
    title: str
    size: str                  # "sticker" or "quarter"
    draw: Any
    record: Any

    def listing(self) -> dict[str, str]:
        return {"id": self.id, "host": self.host, "kind": self.kind,
                "title": self.title, "size": self.size}


def unique_ids(ids: list[str]) -> list[str]:
    """`ids` with the second and later of any repeated one numbered
    (``#2``, ``#3``), so that two labels alike can still be told apart."""
    seen: Counter[str] = Counter()
    out = []
    for i in ids:
        seen[i] += 1
        out.append(i if seen[i] == 1 else f"{i}#{seen[i]}")
    return out


def entries(docs: Any, only: set[str], pinned_names: Any = None) -> list[Entry]:
    """Every label the documents make for the kinds in `only`, one by one:
    the whole labels in ``labels.all_labels`` order, then each micro label."""
    raw: list[tuple[str, str, str, str, Any, Any]] = []
    for host, kind, title, draw, record in labels.whole_labels(docs, only, pinned_names):
        raw.append((host, kind, title, "sticker", draw, record))
    for kind, m in micro.selected(docs, only):
        raw.append((m.host, kind, m.listing_title, "quarter", None, m))
    ids = unique_ids([f"{host}/{kind}/{title}" for host, kind, title, *_ in raw])
    return [Entry(i, *r) for i, r in zip(ids, raw, strict=True)]


def parse_slot(text: str) -> tuple[int, int | None]:
    """``"7"`` -> (7, None), a whole sticker; ``"7c"`` -> (7, 2), its third
    quarter."""
    m = re.fullmatch(r"(\d+)([a-dA-D]?)", text.strip())
    if not m or not 1 <= int(m.group(1)) <= STICKERS:
        raise PlacementError(f"{text!r} is not a slot: a sticker is 1-{STICKERS}, a quarter "
                             "of one 1a-21d")
    q = m.group(2).lower()
    return int(m.group(1)), (QUARTERS.index(q) if q else None)


def check(plan: dict[str, Any], by_id: dict[str, Entry]
          ) -> list[tuple[Entry, int, int | None]]:
    """The plan's labels as (entry, sticker, quarter), or PlacementError
    for the first thing wrong with it."""
    placed: list[tuple[Entry, int, int | None]] = []
    whole: set[int] = set()
    quarters: set[tuple[int, int]] = set()
    names = Counter(p["id"] for p in plan.get("labels", ()))
    again = sorted(i for i, n in names.items() if n > 1)
    if again:
        raise PlacementError(f"placed more than once: {', '.join(again)}")
    for p in plan.get("labels", ()):
        e = by_id.get(p["id"])
        if e is None:
            raise PlacementError(f"no label {p['id']!r} in this data (rpi-hwid labels --list "
                                 "--json names the ones there are)")
        sticker, q = parse_slot(str(p["slot"]))
        if e.size == "sticker" and q is not None:
            raise PlacementError(f"{e.id} takes a whole sticker, not the quarter {p['slot']}")
        if e.size == "quarter" and q is None:
            raise PlacementError(f"{e.id} is a micro label: give it a quarter, "
                                 f"{sticker}a-{sticker}d, not sticker {sticker}")
        if q is None:
            if sticker in whole or any(s == sticker for s, _ in quarters):
                raise PlacementError(f"sticker {sticker} is printed twice")
            whole.add(sticker)
        else:
            if sticker in whole or (sticker, q) in quarters:
                raise PlacementError(f"slot {p['slot']} is printed twice")
            quarters.add((sticker, q))
        placed.append((e, sticker, q))
    for g in plan.get("guides", ()):
        sticker, q = parse_slot(str(g))
        if q is not None or sticker in whole:
            raise PlacementError(f"cut guides go on a sticker of micro labels, not {g}")
    return placed


def draw_margins(c: canvas.Canvas, sheet: dict[str, str]) -> None:
    """The sheet's id and note in its top and bottom margins, clear of the
    label grid and of the printer's unprintable edge, each saying which
    edge it is so the sheet goes back through the right way round, and the
    registration ticks."""
    left = labels.MARGIN_X
    right = labels.PAGE_W - labels.MARGIN_X
    for edge, mid in (("TOP EDGE", labels.PAGE_H - PRINTER_EDGE - TEXT_IN),
                      ("BOTTOM EDGE", PRINTER_EDGE + TEXT_IN)):
        c.setFillColor(black)
        c.setFont(labels.MONO, SHEET_ID_SIZE)
        c.drawString(left, mid - SHEET_ID_SIZE * 0.36, sheet["id"])
        c.setFillColor(labels.GREY)
        if sheet.get("note"):
            c.setFont(labels.SANS, NOTE_SIZE)
            c.drawCentredString(labels.PAGE_W / 2, mid - NOTE_SIZE * 0.36, sheet["note"])
        c.setFont(labels.SANS_BOLD, EDGE_SIZE)
        c.drawRightString(right, mid - EDGE_SIZE * 0.36, edge)
    c.setFillColor(black)
    draw_ticks(c)


def draw_ticks(c: canvas.Canvas) -> None:
    """A tick in the margin in line with every die-cut edge: each column's
    two sides above and below the grid, each row's top and bottom either
    side of it. Printed true, every tick meets its cut; a sheet printed
    shifted shows it in every tick alike, and one scaled (a printer's "fit
    to page") in ticks that drift further off the further they are from the
    page's middle."""
    top = labels.PAGE_H - labels.MARGIN_Y
    bottom = top - labels.ROWS * labels.LABEL_H
    c.saveState()
    c.setStrokeColor(black)
    c.setLineWidth(TICK_WEIGHT)
    for col in range(labels.COLS):
        x, _ = labels.label_origin(col)
        for edge in (x, x + labels.LABEL_W):
            c.line(edge, top + TICK_CLEAR, edge, top + TICK_CLEAR + TICK_LEN)
            c.line(edge, bottom - TICK_CLEAR, edge, bottom - TICK_CLEAR - TICK_LEN)
    # the side margins are narrower than a tick: each runs from the
    # printer's edge to the grid, clear of both
    near = PRINTER_EDGE + TICK_CLEAR
    far = labels.MARGIN_X - TICK_CLEAR
    for row in range(labels.ROWS + 1):
        y = top - row * labels.LABEL_H
        c.line(near, y, far, y)
        c.line(labels.PAGE_W - near, y, labels.PAGE_W - far, y)
    c.restoreState()


def render(docs: Any, plan: dict[str, Any], out: str | Path | IO[bytes],
           only: set[str] | None = None, pinned_names: Any = None) -> int:
    """Draw the plan's labels in its slots, one A4 page; returns how many."""
    if only is None:
        only = set(labels.KINDS) | set(micro.kinds())
    by_id = {e.id: e for e in entries(docs, only, pinned_names)}
    placed = check(plan, by_id)
    labels.register_fonts()
    target = out if hasattr(out, "write") else str(out)
    c = canvas.Canvas(target, pagesize=A4)
    c.setTitle("Hardware identity labels, placed")
    c.setAuthor("rpi-hwid labels")
    outline = bool(plan.get("outline"))
    for e, sticker, q in placed:
        lab = labels.Label(c, *labels.label_origin(sticker - 1))
        if q is None:
            if outline:
                lab.outline()
            e.draw(lab, e.record)
        else:
            cell = micro.Cell(c, *micro.micro_origin(lab.x0, lab.y0, q))
            if outline:
                cell.outline()
            micro.draw_micro(cell, e.record)
    for g in plan.get("guides", ()):
        sticker, _ = parse_slot(str(g))
        micro.cut_guides(labels.Label(c, *labels.label_origin(sticker - 1)))
    if plan.get("sheet"):
        draw_margins(c, plan["sheet"])
    c.showPage()
    c.save()
    return len(placed)


def list_json(docs: Any, only: set[str], pinned_names: Any = None) -> str:
    return json.dumps([e.listing() for e in entries(docs, only, pinned_names)], indent=1)


def place_main(docs: Any, plan_path: Path, out: Path, only: set[str],
               pinned_names: Any = None) -> int:
    """``rpi-hwid labels --place``: 0, or 2 with the plan's fault on stderr."""
    plan = json.loads(plan_path.read_text())
    try:
        n = render(docs, plan, out, only, pinned_names)
    except PlacementError as exc:
        print(f"{plan_path}: {exc}", file=sys.stderr)
        return 2
    sheet = plan.get("sheet", {}).get("id")
    on = f" on sheet {sheet}" if sheet else ""
    print(f"{n} label{'' if n == 1 else 's'} placed{on} -> {out}")
    return 0
