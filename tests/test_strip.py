"""Every strip layout, on every fixture its full layout draws (spec
decisions 2 and 7, tests/strip_checks.py)."""
from __future__ import annotations

from dataclasses import replace

import pytest

import strip_checks as sc
from rpi_hwid import labels, revision, strip
from rpi_hwid.model import Camera

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


def _rules_kept(draw, rec):
    """Everything the fixture-driven tests above hold a strip to."""
    got = sc.on_strip(strip.STRIP_OF[draw], rec)
    full = sc.full(draw, rec)
    assert sc.problems(got, *sc.STRIP) == []
    assert sc.missing_facts(full, got) == []
    assert len(got.marks) == len(full.marks)


# --- board records the fixtures lack: cameras, a PC with no serial or maker mark

def _board(docs, host, **changes):
    rec = next(r for h, _t, r in sc.rows_for(docs, labels.draw_board) if h == host)
    return replace(rec, **changes)


WIDE = Camera("imx708", variant="wide_noir", autofocus=True)
V1 = Camera("ov5647", autofocus=False)
V1_AF = Camera("ov5647", autofocus=True)
OV9281 = Camera("ov9281", autofocus=False)
HQ = Camera("imx477", autofocus=False)


@pytest.mark.parametrize("host", ["pi-sw1-p10", "pi-sw2-p47", "rpicm1-serial", "pi-sw2-p48",
                                  "hifive-unmatched-1", "minnow-turbot-2"])
@pytest.mark.parametrize("cameras", [
    (V1,), (WIDE,), (V1_AF, V1), (WIDE, WIDE), (V1,) * 4,
    (Camera("imx708", variant="wide", autofocus=True, fov=120),), (V1,) * 5,
    # the widest sets the full label takes (stacked two deep there), more
    # than the row under a strip's title holds: some end the subtitle's line
    (OV9281, OV9281, HQ, HQ), (V1_AF, V1_AF, HQ, HQ), (V1_AF, OV9281, HQ, HQ),
    (WIDE, V1_AF, HQ), (OV9281,) * 3,
], ids=lambda cams: "+".join(" ".join(w for w in labels.camera_words(c) if w) for c in cams))
def test_a_board_strip_takes_exactly_the_cameras_the_full_label_takes(docs, host, cameras):
    """Spec decision 2: a strip refuses exactly when the full label
    refuses. A brute force over one to four cameras of eight kinds on every
    board fixture (the full label takes no set of five) found the sets
    above the widest the full label takes."""
    rec = _board(docs, host, cameras=cameras)
    try:
        sc.full(labels.draw_board, rec)
    except labels.CameraMarkDoesNotFitError:
        with pytest.raises(labels.CameraMarkDoesNotFitError, match=host):
            sc.on_strip(strip.draw_board_strip, rec)
        return
    _rules_kept(labels.draw_board, rec)


# --- every title the full label prints whole, the strip prints whole -----------

PI_MODELS = sorted(set(revision.PI_TYPE.values())
                   | {model for model, _rev, _mem in revision.OLD_STYLE.values()})


@pytest.mark.parametrize("cameras", [None, (V1_AF, HQ)], ids=["no camera", "two cameras"])
@pytest.mark.parametrize(("fan", "clock"), [(None, None), (True, None), (True, True)],
                         ids=["no icons", "fan", "fan and clock"])
@pytest.mark.parametrize("model", PI_MODELS)
def test_every_raspberry_pi_title_is_whole_on_its_strip(docs, model, fan, clock, cameras):
    """Every model name revision.py can decode, beside a fan, a clock and
    two cameras (a Compute Module 5 has the Pi 5's fan and RTC): what the
    full label prints whole, the strip prints whole too."""
    rec = _board(docs, "pi-sw2-p47", title="Raspberry Pi " + model, fan=fan,
                 rtc_battery=clock, cameras=cameras)
    try:
        sc.full(labels.draw_board, rec)
    except labels.CameraMarkDoesNotFitError:
        with pytest.raises(labels.CameraMarkDoesNotFitError):
            sc.on_strip(strip.draw_board_strip, rec)
        return
    _rules_kept(labels.draw_board, rec)


def _longest_whole_title(rec, words):
    """The longest prefix of `words` the full label prints as `rec`'s title
    without cutting it: the full label's own limit, found by halving."""
    def whole(n):
        title = words[:n].rstrip()
        got = sc.full(labels.draw_board, replace(rec, title=title))
        return not any(t.s.endswith("…") for t in got.texts)
    lo, hi = 1, len(words)
    assert not whole(hi), "the test's title should be too long for the full label"
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if whole(mid) else (lo, mid)
    return words[:lo].rstrip()


@pytest.mark.parametrize("host", ["pi-sw2-p22", "hifive-unmatched-1", "minnow-turbot-2",
                                  "pi-sw2-p47"])
def test_the_longest_title_the_full_label_prints_whole_is_whole_on_the_strip(docs, host):
    """Orange Pi, RISC-V and PC titles come from the board's own device tree
    or DMI, not a table: any length. Whatever the full label has room for,
    the strip has room for (Pi 5: beside its fan and clock)."""
    words = "Wide Board Name With A Very Long Model String Revision Plus Extras " * 2
    rec = _board(docs, host)
    title = _longest_whole_title(rec, words)
    _rules_kept(labels.draw_board, replace(rec, title=title))


@pytest.mark.parametrize("changes", [
    {"serial": ""},                              # a PC whose firmware has none
    {"maker_mark": None},                        # its maker named in type
    {"serial": "", "maker_mark": None},
])
@pytest.mark.parametrize("host", ["minnow-turbot-1", "minnow-turbot-2"])
def test_a_pc_board_strip_without_a_serial_or_a_maker_mark(docs, host, changes):
    _rules_kept(labels.draw_board, _board(docs, host, **changes))
