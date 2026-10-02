"""The camera mark on a board label: which camera a board is wearing, said
in the corner beside the title."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from rpi_hwid import labels
from rpi_hwid.model import Camera

HOST = "pi-sw2-p47"                     # a Pi 5 with a fan and an RTC cell


def with_cameras(docs, cameras, host=HOST):
    """`host`'s document, its summary saying `cameras`."""
    doc = copy.deepcopy(docs[host])
    doc.summary = replace(doc.summary, cameras=cameras)
    return doc


@pytest.mark.parametrize(("sensor", "generation"), [
    ("ov5647", "v1"),
    ("imx219", "v2"),
    ("imx708", "v3"),
    ("imx477", "HQ"),
    ("imx296", "GS"),
    ("imx500", "AI"),
    # a sensor with no Raspberry Pi module name is called what its driver
    # calls it, which is at least something to search for
    ("imx290", "imx290"),
    ("ov9281", "ov9281"),
])
def test_a_sensor_is_named_by_its_camera_generation(sensor, generation):
    assert labels.camera_generation(sensor) == generation


@pytest.mark.parametrize(("camera", "words"), [
    (Camera("ov5647", autofocus=False), ("v1", "fixed", None)),
    (Camera("ov5647", autofocus=True, lens="0x0c"), ("v1", "AF", None)),
    # nobody could look for a lens driver: no word at all, which is neither
    # of the two answers
    (Camera("ov5647"), ("v1", None, None)),
    (Camera("ov5647", autofocus=False, fov=65), ("v1", "fixed", "65°")),
    (Camera("ov5647", autofocus=False, fov=120), ("v1", "fixed", "120°")),
    (Camera("ov5647", fov=160), ("v1", None, "160°")),
    (Camera("imx219", autofocus=True, lens="dw9714"), ("v2", "AF", None)),
    (Camera("imx708", autofocus=True, lens="dw9807"), ("v3", "AF", None)),
    (Camera("imx708", variant="wide", autofocus=True), ("v3", "AF", "wide")),
    (Camera("imx708", variant="noir", autofocus=True), ("v3", "AF", "NoIR")),
    (Camera("imx708", variant="wide_noir", autofocus=True), ("v3", "AF", "wide NoIR")),
    # a person's angle for a lens whose module also names itself: both
    (Camera("imx708", variant="wide", fov=120), ("v3", None, "wide 120°")),
    (Camera("imx477", autofocus=False), ("HQ", "fixed", None)),
])
def test_what_a_camera_mark_says(camera, words):
    assert labels.camera_words(camera) == words


def test_the_lens_driver_is_never_printed():
    """`lens` is how the autofocus was found, a driver name or a bus address:
    evidence for the document, not something to read off a sticker."""
    said = " ".join(w for w in labels.camera_words(
        Camera("imx708", autofocus=True, lens="dw9807")) if w)
    assert "dw9807" not in said


def test_board_record_carries_the_cameras(docs):
    cameras = (Camera("ov5647", autofocus=True, lens="0x0c"),
               Camera("imx708", variant="wide_noir", autofocus=True, lens="dw9807"))
    assert labels.board_record(with_cameras(docs, cameras)).cameras == cameras
    # looked and found none, and never looked: both carried as they came
    assert labels.board_record(with_cameras(docs, ())).cameras == ()
    assert labels.board_record(docs[HOST]).cameras is None
