"""A roll of stickers: the labels as strips (rpi_hwid.strip), one per page
of the label printer's own page size, and printing them through its CUPS
queue (labels --stock ROLL [--print QUEUE])."""

from __future__ import annotations

from collections.abc import Callable
from typing import IO, Any

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
    for host, kind, title, draw, record in labels.whole_labels(  # type: ignore[no-untyped-call]
            docs, only, pinned_names, order):
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
    labels.register_fonts()  # type: ignore[no-untyped-call]
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
            s.outline()  # type: ignore[no-untyped-call]
        draw(s, record)
        c.restoreState()
    c.showPage()
    c.save()
    n = sum(sum(m is not None for m in r[4]) if r[1] == "micro" else 1 for r in rows)
    return n, len(rows)
