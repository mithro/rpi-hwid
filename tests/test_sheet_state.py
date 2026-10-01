"""A sheet's state (rpi_hwid.sheet.state): which of its slots are used,
where the next labels go, and the one file per sheet that remembers it."""

from __future__ import annotations

import json
import random

import pytest

from rpi_hwid.sheet import state
from rpi_hwid.sheet.state import Want


def sheet(**kw):
    s = state.Sheet.new("K7QX", printer="ipp://p/ipp/print", host="ten64", user="tim",
                        version="1.2", now="2026-10-01T10:12:00+10:00")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def used(*slots):
    return {s: {"label": f"h/k/{s}"} for s in slots}


# --- ids -----------------------------------------------------------------------


def test_a_sheet_id_is_four_unambiguous_characters():
    rng = random.Random(1)
    for _ in range(200):
        i = state.new_id(set(), rng)
        assert len(i) == 4
        # none of 0/O, 1/I/L, U/V to misread off a printed sheet
        assert not set(i) & set("01ILOUV")
        assert i == i.upper()


def test_a_sheet_id_is_never_one_already_used():
    rng = random.Random(1)
    first = state.new_id(set(), rng)
    rng = random.Random(1)
    assert state.new_id({first}, rng) != first


# --- what is free ----------------------------------------------------------------


def test_a_new_sheet_is_all_free():
    s = sheet()
    assert s.free_stickers() == list(range(1, 22))
    assert s.free_quarters() == []
    assert s.passes == []


def test_a_sticker_with_any_quarter_used_is_not_free_and_offers_the_rest():
    s = sheet(slots=used("3", "5b"), guides=["5"])
    assert 3 not in s.free_stickers()
    assert 5 not in s.free_stickers()
    assert s.free_quarters() == ["5a", "5c", "5d"]


# --- placing the next labels ---------------------------------------------------------


def test_whole_labels_take_the_first_free_stickers():
    s = sheet(slots=used("1", "2", "4"))
    got, guides = s.allocate([Want("a", "sticker"), Want("b", "sticker")])
    assert got == [("a", "3"), ("b", "5")]
    assert guides == []


def test_micro_labels_fill_a_started_sticker_before_a_fresh_one():
    s = sheet(slots=used("1", "2d"), guides=["2"])
    got, guides = s.allocate([Want(c, "quarter") for c in "abcd"])
    assert got == [("a", "2a"), ("b", "2b"), ("c", "2c"), ("d", "3a")]
    # the fresh sticker gets its cut guides with its first label
    assert guides == ["3"]


def test_whole_and_micro_labels_share_the_free_stickers_without_meeting():
    s = sheet()
    got, guides = s.allocate([Want("q1", "quarter"), Want("w1", "sticker"),
                              Want("q2", "quarter")])
    assert dict(got) == {"w1": "1", "q1": "2a", "q2": "2b"}
    assert guides == ["2"]


def test_a_started_sticker_without_its_guides_gets_them():
    """A quarter marked used by hand (mark) has no guides on record."""
    s = sheet(slots=used("7a"))
    got, guides = s.allocate([Want("q", "quarter")])
    assert got == [("q", "7b")]
    assert guides == ["7"]


def test_a_sheet_too_full_says_how_much_room_there_is():
    s = sheet(slots=used(*[str(i) for i in range(1, 21)]))
    with pytest.raises(state.SheetFullError, match="1 free sticker and 0 free quarters"):
        s.allocate([Want("a", "sticker"), Want("b", "sticker")])


def test_a_full_sheet_still_takes_micro_labels_in_its_free_quarters():
    s = sheet(slots={**used(*[str(i) for i in range(1, 21)]), **used("21a")}, guides=["21"])
    got, _ = s.allocate([Want("q", "quarter")])
    assert got == [("q", "21b")]


# --- recording a pass ----------------------------------------------------------------


def test_a_recorded_pass_uses_its_slots_and_guides():
    s = sheet()
    s.record_pass([("pi3/rpi/Pi 4", "1", "pi3", "Pi 4"), ("p/tasmota/Plug", "2a", "p", "Plug")],
                  ["2"], at="2026-10-01T10:20:00+10:00", host="ten64", user="tim",
                  job=312, job_state="completed", data="/state/reads/x", marked=True)
    assert s.slots["1"]["label"] == "pi3/rpi/Pi 4"
    assert s.slots["2a"]["pass"] == 1
    assert s.guides == ["2"]
    assert s.marked
    (p,) = s.passes
    assert p["job"] == 312
    assert p["labels"] == ["pi3/rpi/Pi 4", "p/tasmota/Plug"]


def test_the_revision_changes_with_every_pass():
    s = sheet()
    before = s.revision()
    s.record_pass([("a", "1", "h", "t")], [], at="x", host="h", user="u", job=1,
                  job_state="completed", data="d", marked=True)
    assert s.revision() != before


def test_the_note_names_stock_start_host_user_and_version():
    note = sheet().note()
    assert note == "L7160 · started 2026-10-01 10:12 on ten64 by tim · rpi-hwid 1.2"


# --- the store: one file per sheet ------------------------------------------------------


def test_a_sheet_round_trips_through_its_own_file(tmp_path):
    store = state.Store(tmp_path)
    s = sheet(slots=used("4"), guides=[])
    store.save(s)
    assert (tmp_path / "sheets" / "K7QX.json").exists()
    assert json.loads((tmp_path / "sheets" / "K7QX.json").read_text())["id"] == "K7QX"
    back = store.load("k7qx")                   # typed in lower case off the sheet
    assert back == s
    assert store.ids() == ["K7QX"]


def test_a_sheet_that_is_not_there_is_named(tmp_path):
    with pytest.raises(state.NoSuchSheetError, match=r"K7QX.*rpi-hwid-sheet new"):
        state.Store(tmp_path).load("K7QX")


def test_saving_leaves_no_half_written_file(tmp_path):
    store = state.Store(tmp_path)
    store.save(sheet())
    assert sorted(p.name for p in (tmp_path / "sheets").iterdir()) == ["K7QX.json"]


def test_the_default_store_is_under_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state.default_root() == tmp_path / "rpi-hwid"
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.default_root() == tmp_path / ".local" / "state" / "rpi-hwid"
