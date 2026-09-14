#!/usr/bin/env python3
"""Fetch the Tiny Tapeout board colours from the published spreadsheet.

    uv run tools/fetch_tt_boards.py            # rewrite the committed JSON
    uv run tools/fetch_tt_boards.py --check    # fail if the sheet has moved

The colours of a Tiny Tapeout chip carrier and of the demo board it plugs
into are not readable from the board, and they are not written down
anywhere the label generator can reach except Tim's spreadsheet. This
pulls the two sheets that hold them, keeps only the columns a label uses,
and writes ``src/rpi_hwid/tt_boards.json`` -- inside the package, so it
ships in the wheel.

``--check`` derives the same document from the live sheet and diffs it
against the committed one, so CI notices when the sheet gains a shuttle
or corrects a colour. It compares the *derived* document, not the raw
CSV: the sheets also carry Stock and Buy Link columns that change on
their own, and a check that went red when something sold out would be
noise rather than signal.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "src" / "rpi_hwid" / "tt_boards.json"

# The sheet itself, for a reader: https://mith.ro/tt-boards/
#
# That short link cannot be what is fetched, which is why the long form is
# still here. GitHub Pages serves a redirect as a 200 with a meta refresh in
# it rather than a 3xx, so urlopen would read the redirect page instead of
# the CSV -- and the query string below would be dropped on the way through.
#
# This is the "publish to the web" id of the spreadsheet, not the document
# id: only the published form is readable without a Google login, and the
# published CSV is per sheet, by gid.
PUB = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vSagZmGllw_F_VfjfzbrYuBN-"
       "VWvQ4s5X1grDFA9CIVWBmuUc0ufpccazWXQlNNrLY4rWt6SXy-hN6L/pub")
SHEETS = {
    # "Kits - IC+Carrier & Demoboard" (gid 0) is deliberately not fetched: it
    # is these two joined for the shop, and the join it does is by kit rather
    # than by shuttle.
    "asics": 1439118929,        # "IC + Carrier Board", a row per tapeout
    "demoboards": 1250963370,   # "Demoboards", a row per board revision
}

# Cells that mean "nothing recorded". "TBC" is a shuttle whose boards are not
# made yet; "n/a" a shuttle that never had a carrier. Both become null, which
# is what the label already draws as an empty swatch.
BLANK = {"", "-", "--", "---", "n/a", "N/A", "na", "tbc", "TBC", "?", "none"}


def fetch(gid, timeout=60):
    url = f"{PUB}?gid={gid}&single=true&output=csv"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        if r.status != 200:
            raise SystemExit(f"{url} returned HTTP {r.status}")
        return r.read().decode("utf-8")


def cell(row, index):
    """One cell, with the sheet's several spellings of "blank" as None."""
    if index is None or index >= len(row):
        return None
    v = (row[index] or "").strip()
    return None if v in BLANK else v


def hexcolour(v):
    """A #rrggbb from the sheet, or None. Anything else is left out rather
    than guessed at: the label falls back to the colour's name."""
    if not v:
        return None
    v = v.strip().lower()
    if len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdef" for c in v[1:]):
        return v
    return None


def shuttle_id(name):
    """The spreadsheet's tapeout name as the probe spells a shuttle:
    lower case, no spaces ("TT03p5" -> "tt03p5", "TT08 CoB" -> "tt08cob")."""
    return "".join((name or "").split()).lower()


class Headings:
    """The column headings of a sheet, read from several header rows at once.

    A sheet heading that spans columns ("Soldermask (aka Board) Color") is
    written in the leftmost cell of its span and the rest are blank, so a
    header row is carried forward across its blanks and each column ends up
    with the full path to it: ("Soldermask (aka Board) Color", "Name") is
    one column and ("Silkscreen (aka text) Color", "Name") another, though
    both are called "Name".

    Look a column up by that path, or by its own name where the sheet uses
    that name once. Nothing counts columns, so inserting one changes
    nothing here, and a heading that is renamed or removed fails loudly
    instead of quietly reading the wrong column or none at all."""

    def __init__(self, *header_rows):
        width = max(len(r) for r in header_rows)
        self.paths = []
        for i in range(width):
            path = []
            for row in header_rows:
                for j in range(i, -1, -1):          # carry a spanned heading right
                    v = (row[j] if j < len(row) else "").strip()
                    if v:
                        if j == i or all(not (row[k] if k < len(row) else "").strip()
                                         for k in range(j + 1, i + 1)):
                            path.append(v)
                        break
            self.paths.append(tuple(path))
        self.by_path = {p: i for i, p in enumerate(self.paths)}
        leaves = [p[-1] for p in self.paths if p]
        self.by_leaf = {p[-1]: i for i, p in enumerate(self.paths)
                        if p and leaves.count(p[-1]) == 1}

    def index(self, key):
        """The column's index. `key` is its path, or its own name when the
        sheet uses that name once."""
        found = (self.by_path if isinstance(key, tuple) else self.by_leaf).get(key)
        if found is None:
            raise SystemExit(
                f"the sheet has no column {key!r} any more; it now has:\n  "
                + "\n  ".join(" / ".join(p) for p in self.paths if p))
        return found


def parse_asics(text):
    """A row per tapeout, from the "IC + Carrier Board" sheet."""
    rows = list(csv.reader(io.StringIO(text)))
    head = Headings(rows[0], rows[1])
    out = {}
    for row in rows[2:]:
        tapeout = cell(row, head.index("Tapeout"))
        if not tapeout:
            continue
        out[shuttle_id(tapeout)] = {
            "tapeout": tapeout,
            "colour": cell(row, head.index("Board\nColor\nName")),
            "colour_hex": hexcolour(cell(row, head.index("Board Color\nHex"))),
            "silk": cell(row, head.index("Silk Color\nName")),
            "silk_hex": hexcolour(cell(row, head.index("Silk Color\nHex"))),
            "ic_type": cell(row, head.index("IC Type")),
            "chip_page": cell(row, head.index("Chip page")),
        }
    return out


def parse_demoboards(text):
    """A row per demo board revision, from the "Demoboards" sheet. Its
    colour columns are called Name/Pantone/Hex twice over, under a spanned
    heading for the soldermask and another for the silkscreen, so they are
    told apart by that heading rather than by where they sit."""
    rows = list(csv.reader(io.StringIO(text)))
    head = Headings(rows[0], rows[1])
    mask, silk = "Soldermask (aka Board) Color", "Silkscreen (aka text) Color"
    out = {}
    for row in rows[2:]:
        board = cell(row, head.index("ID"))
        if not board:
            continue
        used_by = cell(row, head.index("Used by")) or ""
        out[board] = {
            "colour": cell(row, head.index((mask, "Name"))),
            "colour_hex": hexcolour(cell(row, head.index((mask, "Hex")))),
            "silk": cell(row, head.index((silk, "Name"))),
            "silk_hex": hexcolour(cell(row, head.index((silk, "Hex")))),
            "text": cell(row, head.index("Text")),
            "version": cell(row, head.index("Version")),
            "mcu": cell(row, head.index("MCU")),
            # One board serves several shuttles, listed one per line in the
            # cell; this is the join back to a shuttle the probe reports.
            "used_by": sorted({shuttle_id(s) for s in used_by.replace("\n", ",").split(",")
                               if shuttle_id(s)}),
        }
    return out


def build():
    """The document, derived from the live sheets."""
    asics = parse_asics(fetch(SHEETS["asics"]))
    demoboards = parse_demoboards(fetch(SHEETS["demoboards"]))
    return {
        "_comment": ("Generated by tools/fetch_tt_boards.py from the published "
                     "Tiny Tapeout board spreadsheet. Do not edit by hand: "
                     "edit the sheet and re-run the script."),
        "_source": PUB,
        "_sheets": SHEETS,
        "asics": asics,
        "demoboards": demoboards,
    }


def dumps(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="compare the live sheet with the committed file and fail on a difference")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    fresh = dumps(build())
    if not args.check:
        args.out.write_text(fresh, encoding="utf-8")
        doc = json.loads(fresh)
        print(f"wrote {args.out}: {len(doc['asics'])} asic rows, "
              f"{len(doc['demoboards'])} demo board rows")
        return 0

    if not args.out.exists():
        print(f"{args.out} does not exist; run this script without --check", file=sys.stderr)
        return 1
    committed = args.out.read_text(encoding="utf-8")
    if committed == fresh:
        print(f"{args.out} matches the spreadsheet")
        return 0
    sys.stdout.writelines(difflib.unified_diff(
        committed.splitlines(True), fresh.splitlines(True),
        fromfile=f"{args.out.name} (committed)", tofile="the spreadsheet, now"))
    print("\nThe spreadsheet has moved. Re-run:  uv run tools/fetch_tt_boards.py\n"
          f"then commit the change to {args.out.name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
