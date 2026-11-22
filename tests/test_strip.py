"""Every strip layout, on every fixture its full layout draws (spec
decisions 2 and 7, tests/strip_checks.py)."""
from __future__ import annotations

from dataclasses import replace

import pytest

import strip_checks as sc
from rpi_hwid import labels, strip
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


@pytest.mark.parametrize("host", ["pi-sw2-p47", "rpicm1-serial", "pi-sw2-p33"])
@pytest.mark.parametrize("cameras", [
    (V1,), (WIDE,), (Camera("ov5647", autofocus=True), V1), (WIDE, WIDE), (V1,) * 4,
    (Camera("imx708", variant="wide", autofocus=True, fov=120),), (V1,) * 5,
], ids=lambda cams: "+".join(" ".join(w for w in labels.camera_words(c) if w) for c in cams))
def test_a_board_strip_with_cameras_refuses_as_the_full_label_does(docs, host, cameras):
    rec = _board(docs, host, cameras=cameras)
    try:
        sc.full(labels.draw_board, rec)
    except labels.CameraMarkDoesNotFitError:
        with pytest.raises(labels.CameraMarkDoesNotFitError, match=host):
            sc.on_strip(strip.draw_board_strip, rec)
        return
    _rules_kept(labels.draw_board, rec)


def test_cameras_the_full_label_stacks_but_a_strip_has_no_row_for_are_refused(docs):
    """The full label stacks cameras two deep; a strip has one row under the
    title. Four wide marks on a multiplexer fit the one and not the other:
    refused, rather than drawn into the board's mark."""
    cams = (Camera("ov9281", autofocus=False),) * 2 + (Camera("imx477", autofocus=False),) * 2
    rec = _board(docs, "pi-sw1-p10", cameras=cams)
    sc.full(labels.draw_board, rec)
    with pytest.raises(labels.CameraMarkDoesNotFitError, match=r"pi-sw1-p10.*strip"):
        sc.on_strip(strip.draw_board_strip, rec)


@pytest.mark.parametrize("changes", [
    {"serial": ""},                              # a PC whose firmware has none
    {"maker_mark": None},                        # its maker named in type
    {"serial": "", "maker_mark": None},
])
@pytest.mark.parametrize("host", ["minnow-turbot-1", "minnow-turbot-2"])
def test_a_pc_board_strip_without_a_serial_or_a_maker_mark(docs, host, changes):
    _rules_kept(labels.draw_board, _board(docs, host, **changes))
