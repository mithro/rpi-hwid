"""CSI cameras: the record a summary carries for each, and how the probe
finds them."""

from __future__ import annotations

from conftest import RAW
from rpi_hwid.model import Camera, Summary


def summary(**extra):
    d = dict(RAW["pi-sw2-p47"]["verdict"]["summary"])
    d.update(extra)
    return d


def test_a_summary_carries_its_cameras_as_records():
    s = Summary.from_dict(summary(cameras=[
        {"sensor": "ov5647", "variant": None, "autofocus": True, "lens": "0x0c"},
        {"sensor": "imx708", "variant": "wide_noir", "autofocus": True, "lens": "dw9807"},
    ]))
    assert s.cameras == (
        Camera("ov5647", autofocus=True, lens="0x0c"),
        Camera("imx708", variant="wide_noir", autofocus=True, lens="dw9807"),
    )
    # and back to what the probe wrote, lists not tuples, so it survives JSON
    assert s.to_dict()["cameras"] == [
        {"sensor": "ov5647", "variant": None, "autofocus": True, "lens": "0x0c", "fov": None},
        {"sensor": "imx708", "variant": "wide_noir", "autofocus": True, "lens": "dw9807",
         "fov": None},
    ]


def test_a_lens_angle_is_carried_when_a_person_supplies_one():
    s = Summary.from_dict(summary(cameras=[{"sensor": "ov5647", "fov": 160}]))
    assert s.cameras == (Camera("ov5647", fov=160),)


def test_no_camera_found_is_not_the_same_as_not_looked_for():
    """An older document has no `cameras` at all: nobody looked. A probe that
    looked and found none says so with an empty list."""
    assert Summary.from_dict(summary()).cameras is None
    assert Summary.from_dict(summary()).to_dict()["cameras"] is None
    assert Summary.from_dict(summary(cameras=[])).cameras == ()
    assert Summary.from_dict(summary(cameras=[])).to_dict()["cameras"] == []
