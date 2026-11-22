"""Strip labels: each kind of label laid out for one sticker of a label
printer's roll -- for now DYMO 99010, 28 x 89 mm -- carrying the same facts
and codes as its full label, re-flowed along a strip a third as tall
(docs/superpowers/specs/2026-11-22-dymo-strip-labels-design.md).

A layout takes (strip, record), the record its full layout takes, and is
registered in STRIP_OF against that full layout's draw function, so the
rows labels.whole_labels makes for a sheet make a roll unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rpi_hwid import labels as lb

if TYPE_CHECKING:
    from collections.abc import Callable

# The strip is the roll's box turned a quarter: its length runs up the page.
_, _, STRIP_H, STRIP_W = lb.ROLLS["dymo-99010"].strip_box


class Strip(lb.Label):
    """A ``labels.Label`` the strip's size, origin top-left, y down."""

    def __init__(self, c: Any, x0: float, y0: float) -> None:
        super().__init__(c, x0, y0, STRIP_W, STRIP_H)  # type: ignore[no-untyped-call]


# full draw function -> strip draw function; each layout registers itself
STRIP_OF: dict[Callable[..., Any], Callable[..., Any]] = {}
