"""The spreadsheet fetcher's parsing, against captured sheet rows.

Nothing here goes near the network: the point is the parsing, and the live
sheet is CI's job (tools/fetch_tt_boards.py --check).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "fetch_tt_boards.py"
spec = importlib.util.spec_from_file_location("fetch_tt_boards", SCRIPT)
fetch_tt_boards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_tt_boards)


# Two header rows and a data row, shaped like the Demoboards sheet: the
# soldermask and the silkscreen each have a Name/Pantone/Hex triplet, so the
# column names repeat and only the spanned heading above tells them apart.
DEMOBOARD_CSV = (
    ",Soldermask (aka Board) Color,,,Silkscreen (aka text) Color,,,,,,,,,Production files,,\n"
    "ID,Name,Pantone,Hex,Name,Pantone,Hex,Used by,Text,Version,MCU,Image,Notes,PCB files,,\n"
    'DB 06+ v2.0.1,Pink,230U,#c98599,Teal,909C,,TT06,A Demoboard,v2.0.1,RP2040,,note,x,,\n'
    'DB ETR v3.2,Purple,230U,#c98599,Teal,909C,#bae7c6,'
    '"TT09,\nTTSKY25a,\n",ETR,v3.2,RP2350,,n,x,,\n'
)


def test_headings_tell_repeated_column_names_apart():
    rows = [r.split(",") for r in (
        ",Soldermask (aka Board) Color,,,Silkscreen (aka text) Color,,",
        "ID,Name,Pantone,Hex,Name,Pantone,Hex")]
    head = fetch_tt_boards.Headings(*rows)
    mask, silk = "Soldermask (aka Board) Color", "Silkscreen (aka text) Color"
    assert head.index((mask, "Name")) == 1
    assert head.index((silk, "Name")) == 4
    assert head.index("ID") == 0, "a name used once needs no path"
    with pytest.raises(SystemExit) as e:
        head.index("Name")          # ambiguous: it is two different columns
    assert "no column" in str(e.value)
    with pytest.raises(SystemExit):
        head.index("Stock")         # a column the sheet does not have


def test_demoboards_parse_colours_versions_and_the_shuttles_served():
    got = fetch_tt_boards.parse_demoboards(DEMOBOARD_CSV)
    assert set(got) == {"DB 06+ v2.0.1", "DB ETR v3.2"}
    one = got["DB 06+ v2.0.1"]
    assert (one["colour"], one["colour_hex"]) == ("Pink", "#c98599")
    assert one["silk"] == "Teal"
    assert one["silk_hex"] is None, "the sheet has a Pantone but no hex for it"
    assert one["used_by"] == ["tt06"]
    # One board serves several shuttles, one per line in the cell.
    assert got["DB ETR v3.2"]["used_by"] == ["tt09", "ttsky25a"]
    assert got["DB ETR v3.2"]["silk_hex"] == "#bae7c6"


def test_blank_spellings_and_bad_hex_become_null():
    assert fetch_tt_boards.cell(["TBC"], 0) is None
    assert fetch_tt_boards.cell(["n/a"], 0) is None
    assert fetch_tt_boards.cell(["  Green "], 0) == "Green"
    assert fetch_tt_boards.cell([], 0) is None
    assert fetch_tt_boards.hexcolour("#C98599") == "#c98599"
    assert fetch_tt_boards.hexcolour("909C") is None, "a Pantone is not a hex"
    assert fetch_tt_boards.hexcolour(None) is None


def test_shuttle_id_matches_how_the_probe_spells_a_shuttle():
    assert fetch_tt_boards.shuttle_id("TT03p5") == "tt03p5"
    assert fetch_tt_boards.shuttle_id("TT08 CoB") == "tt08cob"
    assert fetch_tt_boards.shuttle_id(None) == ""
