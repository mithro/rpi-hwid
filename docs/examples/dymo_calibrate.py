"""One calibration sticker for a DYMO LabelWriter roll: a millimetre scale
along the page, numbered in mm from the PAGE TOP (raster line 0, the first
line the printer receives), so the mark at the sticker's leading edge says how
far along the sticker the printer puts the page. Also outlines the driver's
ImageableArea (dashed) and the roll's strip box (solid).

    uv run docs/examples/dymo_calibrate.py [ROLL] [--out dymo-calibrate.pdf]
    lp -d QUEUE -o PageSize=w79h252.2 -o print-scaling=none dymo-calibrate.pdf

For dymo-99010 on a LabelWriter 450 the page top landed 5.5 mm into the
sticker (2026-11-23): Roll.feed_offset.
"""
import argparse

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from rpi_hwid.labels import ROLLS


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roll", nargs="?", default="dymo-99010", choices=sorted(ROLLS))
    ap.add_argument("--out", default="dymo-calibrate.pdf")
    args = ap.parse_args()
    roll = ROLLS[args.roll]
    pw, ph = roll.page
    c = canvas.Canvas(args.out, pagesize=(pw, ph))

    x0, y0, x1, y1 = roll.printable           # ImageableArea, dashed
    c.setLineWidth(0.4)
    c.setDash(2, 2)
    c.rect(x0, y0, x1 - x0, y1 - y0)
    c.setDash()
    bx, by, bw, bh = roll.strip_box           # strip box, solid
    c.setLineWidth(0.6)
    c.rect(bx, by, bw, bh)

    # Scale: a spine down the page, a tick every mm measured from the page top.
    spine = pw * 0.45
    c.setLineWidth(0.3)
    c.line(spine, 0, spine, ph)
    c.setFont("Helvetica-Bold", 6)
    n = 0
    while n * mm <= ph + 0.01:
        y = ph - n * mm
        c.line(spine - (4 * mm if n % 5 == 0 else 2.5 * mm), y, spine, y)
        if n % 5 == 0:
            c.saveState()
            c.translate(spine + 1 * mm, y)
            c.rotate(90)
            c.drawCentredString(0, -2.2 * mm, str(n))
            c.restoreState()
        n += 1
    # Half-mm ticks on the other side, near both ends (first and last 15 mm).
    for k in range(int(ph / mm * 2) + 1):
        d = k * 0.5
        if d <= 15 or d >= ph / mm - 15:
            y = ph - d * mm
            c.line(spine, y, spine + (1.5 * mm if k % 2 == 0 else 0.8 * mm), y)
    c.save()
    print(args.out)


if __name__ == "__main__":
    main()
