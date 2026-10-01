#!/usr/bin/env python3
# /// script
# dependencies = ["fonttools"]
# ///
"""Draw the "custom" wordmark a custom-made Tasmota device's label carries.

    uv run tools/make_custom_mark.py [FONT]

A device on one of Tasmota's generic modules has no maker, so its label
says "custom" where a maker's mark would be, in the same style as the
athom wordmark and taking the same space (Tim, 2026-09-30): a light
lowercase sans in athom.png's ink (#313131), in a box of athom.png's
proportions (800 x 207), the letters filling its width and standing on
its foot as athom's do.

The letters are Lato Light (SIL Open Font License 1.1; Debian's
fonts-lato, /usr/share/fonts/truetype/lato/Lato-Light.ttf by default),
turned into outlines here so that printing needs no font. Writes
src/rpi_hwid/artwork/custom.svg.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

WORD = "custom"
INK = "#313131"                  # athom.png's ink, measured
ASPECT = 207 / 800               # athom.png's height over its width
FONT = "/usr/share/fonts/truetype/lato/Lato-Light.ttf"
OUT = Path(__file__).resolve().parent.parent / "src/rpi_hwid/artwork/custom.svg"


def main() -> None:
    font = TTFont(sys.argv[1] if len(sys.argv) > 1 else FONT)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    hmtx = font["hmtx"]
    # the word's outlines in font units, y down, laid out by advance width
    pen = SVGPathPen(glyphs)
    bounds = BoundsPen(glyphs)
    x = 0
    for ch in WORD:
        name = cmap[ord(ch)]
        flip = (1, 0, 0, -1, x, 0)
        glyphs[name].draw(TransformPen(pen, flip))
        glyphs[name].draw(TransformPen(bounds, flip))
        x += hmtx[name][0]
    x0, _, x1, y1 = bounds.bounds
    w = x1 - x0
    h = w * ASPECT
    # the ink's foot on the box's foot: the letters stand where athom's do
    top = y1 - h
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="{x0:g} {top:g} {w:g} {h:g}">'
           f'<path fill="{INK}" d="{pen.getCommands()}"/></svg>\n')
    OUT.write_text(svg)
    print(f"{OUT}: {w:g} x {h:g} font units")


if __name__ == "__main__":
    main()
