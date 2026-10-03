"""The camera mark on a board label: which camera a board is wearing, said
in the corner beside the title."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from reportlab.lib.units import mm

from rpi_hwid import labels
from rpi_hwid.model import Camera

HOST = "pi-sw2-p47"                     # a Pi 5 with a fan and an RTC cell


def with_cameras(docs, cameras, host=HOST):
    """`host`'s document, its summary saying `cameras`."""
    doc = copy.deepcopy(docs[host])
    doc.summary = replace(doc.summary, cameras=cameras)
    return doc


def render(docs, tmp_path, cameras, host=HOST):
    labels.render({host: with_cameras(docs, cameras, host)}, tmp_path / "camera.pdf",
                  only={"rpi"})


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
    # fixed focus is what a camera is assumed to have, so it is not said
    (Camera("ov5647", autofocus=False), ("v1", None, None)),
    (Camera("ov5647", autofocus=True, lens="0x0c"), ("v1", "AF", None)),
    # nobody could look for a lens driver: the same words (board_record
    # refuses such a camera before any are drawn)
    (Camera("ov5647"), ("v1", None, None)),
    (Camera("ov5647", autofocus=False, fov=65), ("v1", None, "65°")),
    (Camera("ov5647", autofocus=False, fov=120), ("v1", None, "120°")),
    (Camera("ov5647", fov=160), ("v1", None, "160°")),
    (Camera("ov5647", autofocus=True, fov=160), ("v1", "AF", "160°")),
    (Camera("imx219", autofocus=True, lens="dw9714"), ("v2", "AF", None)),
    (Camera("imx708", autofocus=True, lens="dw9807"), ("v3", "AF", None)),
    (Camera("imx708", variant="wide", autofocus=True), ("v3", "AF", "wide")),
    (Camera("imx708", variant="noir", autofocus=True), ("v3", "AF", "NoIR")),
    (Camera("imx708", variant="wide_noir", autofocus=True), ("v3", "AF", "wide NoIR")),
    # a person's angle for a lens whose module also names itself: both
    (Camera("imx708", variant="wide", fov=120), ("v3", None, "wide 120°")),
    # an HQ camera's lens is turned by hand: no motor, so no word
    (Camera("imx477", autofocus=False), ("HQ", None, None)),
])
def test_what_a_camera_mark_says(camera, words):
    assert labels.camera_words(camera) == words


def test_fixed_focus_is_never_printed(docs, tmp_path, monkeypatch):
    said = []
    real_text = labels.Label.text
    monkeypatch.setattr(labels.Label, "text", lambda self, x, y, s, *a, **k: (
        said.append(s), real_text(self, x, y, s, *a, **k))[1])
    render(docs, tmp_path, (Camera("ov5647", autofocus=False), Camera("imx477", autofocus=False)))
    assert {"v1", "HQ"} <= set(said)
    assert not [s for s in said if "fixed" in s.lower() or "AF" in s]


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


@pytest.mark.parametrize("cameras", [
    (Camera("ov5647", autofocus=False),),
    (Camera("imx708", variant="wide", autofocus=True),),
    (Camera("imx708", variant="noir", autofocus=True), Camera("ov5647", autofocus=False, fov=160)),
])
def test_one_mark_is_drawn_for_each_camera(docs, tmp_path, monkeypatch, cameras):
    drawn = []
    monkeypatch.setattr(labels, "mark_camera", lambda lab, cam, *a: drawn.append(cam))
    render(docs, tmp_path, cameras)
    assert drawn == list(cameras)


@pytest.mark.parametrize("cameras", [None, ()])
def test_no_camera_draws_nothing(docs, tmp_path, monkeypatch, cameras):
    """Nobody looked, or somebody looked and found none: neither is worth a
    mark, and "no camera" in words would be one more row to read past."""
    drawn, said = [], []
    real_text = labels.Label.text
    monkeypatch.setattr(labels, "mark_camera", lambda lab, cam, *a: drawn.append(cam))
    monkeypatch.setattr(labels.Label, "text", lambda self, x, y, s, *a, **k: (
        said.append(s), real_text(self, x, y, s, *a, **k))[1])
    render(docs, tmp_path, cameras)
    assert drawn == []
    assert not [s for s in said if "camera" in s.lower()]


def test_the_fan_and_clock_keep_their_marks_beside_a_camera(docs, tmp_path, monkeypatch):
    drawn = []
    monkeypatch.setattr(labels, "mark_fan", lambda *a: drawn.append("fan"))
    monkeypatch.setattr(labels, "mark_clock", lambda *a: drawn.append("clock"))
    monkeypatch.setattr(labels, "mark_camera", lambda *a: drawn.append("camera"))
    render(docs, tmp_path, (Camera("imx219", autofocus=False),))
    assert sorted(drawn) == ["camera", "clock", "fan"]


def test_the_marks_do_not_overlap(docs, tmp_path, monkeypatch):
    """The fan and clock sit left of the camera, and nothing runs off the
    label's right edge."""
    camera = Camera("imx708", variant="wide_noir", autofocus=True)
    at = {}
    monkeypatch.setattr(labels, "mark_fan", lambda lab, x, y, size: at.update(fan=x))
    monkeypatch.setattr(labels, "mark_clock", lambda lab, x, y, size: at.update(clock=x + size))
    monkeypatch.setattr(labels, "mark_camera",
                        lambda lab, cam, x, y, size: at.update(
                            camera=x, right=x + labels.camera_width(lab, cam, size)))
    render(docs, tmp_path, (camera,))
    assert at["fan"] < at["clock"] < at["camera"]
    assert at["right"] <= labels.LABEL_W - labels.PAD + 0.01


def test_a_second_camera_goes_under_the_first(docs, tmp_path, monkeypatch):
    """A Pi 5 has two CSI ports. Side by side the two marks would leave the
    title no room, so they stack, sharing a left edge."""
    places = []
    monkeypatch.setattr(labels, "mark_camera",
                        lambda lab, cam, x, y, size: places.append((x, y, size)))
    render(docs, tmp_path, (Camera("imx708", variant="wide", autofocus=True),
                            Camera("ov5647", autofocus=False)))
    (x0, y0, size), (x1, y1, _size) = places
    assert x0 == x1
    assert y1 >= y0 + size            # y grows down the label
    # and both are in the title's band, above the HAT row
    assert y1 + size < labels.PAD + 9.5 * mm


def title_and_subtitle_room(docs, tmp_path, monkeypatch, cameras):
    """The widths the title and subtitle were told to fit."""
    b = labels.board_record(docs[HOST])
    room = {}
    real_fit = labels.Label.fit

    def fit(self, x, y, s, font, size, max_w, *a, **k):
        room[s] = max_w
        return real_fit(self, x, y, s, font, size, max_w, *a, **k)

    monkeypatch.setattr(labels.Label, "fit", fit)
    render(docs, tmp_path, cameras)
    return room[b.title], room[b.subtitle]


def test_the_title_gives_up_the_room_a_camera_takes(docs, tmp_path, monkeypatch):
    bare, bare_sub = title_and_subtitle_room(docs, tmp_path, monkeypatch, None)
    one, one_sub = title_and_subtitle_room(
        docs, tmp_path, monkeypatch, (Camera("ov5647", autofocus=False),))
    wordy, _sub = title_and_subtitle_room(
        docs, tmp_path, monkeypatch, (Camera("imx708", variant="wide_noir", autofocus=True),))
    assert wordy < one < bare
    # one camera sits on the title's line and leaves the subtitle alone
    assert one_sub == bare_sub


def test_a_plain_camera_costs_a_pi_5_title_nothing(docs, tmp_path, monkeypatch):
    """The common case: a fixed-focus camera on a Pi 5 that also has its fan
    and clock. The three marks fit beside "Raspberry Pi 5" at full size."""
    from reportlab.pdfgen import canvas

    title = labels.board_record(docs[HOST]).title
    room, _sub = title_and_subtitle_room(
        docs, tmp_path, monkeypatch, (Camera("imx219", autofocus=False),))
    lab = labels.Label(canvas.Canvas(str(tmp_path / "t.pdf")), 0, 0)
    assert lab.fitted_size(title, labels.SANS_BOLD, 11, room) == 11


def test_the_subtitle_gives_up_room_only_to_a_second_camera(docs, tmp_path, monkeypatch):
    _title, bare = title_and_subtitle_room(docs, tmp_path, monkeypatch, None)
    _title, two = title_and_subtitle_room(
        docs, tmp_path, monkeypatch,
        (Camera("ov5647", autofocus=False), Camera("imx219", autofocus=False)))
    assert two < bare


def test_a_long_title_sends_its_camera_down_a_line(docs, tmp_path, monkeypatch):
    """ "Raspberry Pi Compute Module 5" barely fits beside a fan and a clock.
    A camera beside it as well would cut the name short, so the camera goes
    under those two and the title is set as if it were not there."""
    cm5 = copy.deepcopy(docs[HOST])
    cm5.summary = replace(cm5.summary, revision="c04180",
                          model="Raspberry Pi Compute Module 5 Rev 1.0")
    title = labels.board_record(cm5).title
    assert title == "Raspberry Pi Compute Module 5"

    def title_room_and_places(cameras):
        room, places = {}, []
        real_fit = labels.Label.fit

        def fit(self, x, y, s, font, size, max_w, *a, **k):
            room[s] = max_w
            return real_fit(self, x, y, s, font, size, max_w, *a, **k)

        with monkeypatch.context() as m:
            m.setattr(labels.Label, "fit", fit)
            m.setattr(labels, "mark_camera",
                      lambda lab, cam, x, y, size: places.append((y, size)))
            labels.render({HOST: replace(cm5, summary=replace(cm5.summary, cameras=cameras))},
                          tmp_path / "cm5.pdf", only={"rpi"})
        return room[title], places

    bare, _none = title_room_and_places(None)
    camera = Camera("imx708", variant="wide_noir", autofocus=True)
    dropped, [(y, size)] = title_room_and_places((camera,))
    assert dropped == bare
    # the same camera on a short title stays up on the title's line
    places = []
    monkeypatch.setattr(labels, "mark_camera",
                        lambda lab, cam, x, y, size: places.append((y, size)))
    render(docs, tmp_path, (camera,))
    assert y >= places[0][0] + size


def test_the_widest_pair_of_cameras_leaves_the_subtitle_whole(docs, tmp_path, monkeypatch):
    """The revision code is at the subtitle's end, so an ellipsis there
    costs the one thing on the line that cannot be read off the board."""
    b = labels.board_record(docs[HOST])
    said = []
    real_text = labels.Label.text
    monkeypatch.setattr(labels.Label, "text", lambda self, x, y, s, *a, **k: (
        said.append(s), real_text(self, x, y, s, *a, **k))[1])
    render(docs, tmp_path, (Camera("imx708", variant="wide_noir", autofocus=True),
                            Camera("imx708", variant="wide_noir", autofocus=True)))
    assert b.subtitle in said


def test_a_camera_costs_the_rows_below_the_title_nothing(docs, tmp_path, monkeypatch):
    """The MACs are what the label is for. Whatever is in the corner, they
    and the HAT row are set exactly as they are on a board with no camera."""
    def below_the_title(cameras):
        set_at = []
        real_text = labels.Label.text

        def text(self, x, y, s, font=labels.SANS, size=8, *a, **k):
            set_at.append((s, font, size, round(x, 3), round(y, 3)))
            return real_text(self, x, y, s, font, size, *a, **k)

        with monkeypatch.context() as m:
            m.setattr(labels.Label, "text", text)
            m.setattr(labels, "mark_camera", lambda *a: None)
            render(docs, tmp_path, cameras)
        return [t for t in set_at if t[4] > labels.PAD + 9 * mm]      # under the title band

    bare = below_the_title(None)
    assert any(s == "HAT" for s, *_rest in bare)
    assert any(s == "98:fe:54:13:f5:75" for s, *_rest in bare)
    assert below_the_title((Camera("imx708", variant="wide_noir", autofocus=True),)) == bare
    assert below_the_title((Camera("imx708", variant="wide_noir", autofocus=True),
                            Camera("ov5647", autofocus=False, fov=160))) == bare


def test_a_mark_is_as_wide_as_what_it_says(docs, tmp_path):
    """More words, wider mark: the width is what the title is told to leave."""
    from reportlab.pdfgen import canvas

    lab = labels.Label(canvas.Canvas(str(tmp_path / "w.pdf")), 0, 0)
    size = 2.6 * mm
    bare = labels.camera_width(lab, Camera("ov5647"), size)
    focusing = labels.camera_width(lab, Camera("ov5647", autofocus=True), size)
    wide = labels.camera_width(lab, Camera("imx708", variant="wide_noir", autofocus=True), size)
    assert size < bare < focusing < wide
    # a camera with nothing to say beyond its generation is the body alone
    assert labels.camera_width(lab, Camera("ov5647", autofocus=False), size) == bare
    # the widest thing a Camera Module 3 can say still leaves most of the
    # column to the title
    assert wide < 13 * mm


def test_every_kind_of_camera_renders(docs, tmp_path):
    """The real drawing code, unpatched, over each thing it can be asked for."""
    cameras = [
        Camera("ov5647", autofocus=False), Camera("ov5647", autofocus=True),
        Camera("ov5647", autofocus=False, fov=160),
        Camera("imx708", variant="wide_noir", autofocus=True), Camera("imx477", autofocus=False),
        Camera("imx290", autofocus=False),
    ]
    for cam in cameras:
        render(docs, tmp_path, (cam,))
    render(docs, tmp_path, tuple(cameras[:2]))
    render(docs, tmp_path, (cameras[0],) * 3)       # three plain marks fit
    assert (tmp_path / "camera.pdf").stat().st_size > 0


def test_the_words_are_set_on_the_label(docs, tmp_path, monkeypatch):
    said = []
    real_text = labels.Label.text
    monkeypatch.setattr(labels.Label, "text", lambda self, x, y, s, *a, **k: (
        said.append(s), real_text(self, x, y, s, *a, **k))[1])
    render(docs, tmp_path, (Camera("imx708", variant="wide_noir", autofocus=True),))
    assert {"v3", "AF", "wide NoIR"} <= set(said)


# --- a lens nobody could look for is never drawn as fixed focus ----------------

def test_a_camera_whose_focus_is_unknown_is_refused(docs, tmp_path):
    """No "AF" would read as fixed focus, and nobody looked: the label is
    refused, as an unread header or fan is."""
    with pytest.raises(labels.CameraNotReadError, match=HOST + ".*focus motor"):
        render(docs, tmp_path, (Camera("ov5647"),))


def test_cameras_nobody_looked_for_draw_no_mark(docs, tmp_path):
    """Optional: a document from before the probe looked, or the Pi-only
    read, has none, and the label is the one it always was."""
    render(docs, tmp_path, None)


# --- a camera mark never costs the title or the revision code ------------------

WIDE = Camera("imx708", variant="wide_noir", autofocus=True, fov=120)


@pytest.mark.parametrize(("host", "cameras"), [
    # a Compute Module's long name beside two wordy cameras
    ("rpicm1-serial", (WIDE, Camera("imx219", autofocus=True))),
    ("rpicm1-serial", (WIDE,)),
    # a supplied angle makes the widest marks, which crowd the revision code
    (HOST, (WIDE, WIDE)),
    (HOST, (Camera("ov9281", variant="wide_noir", autofocus=True, fov=160),)),
    # a third and fourth camera leave the title no room at all
    (HOST, (WIDE, WIDE, WIDE)),
])
def test_marks_that_would_cut_the_title_or_subtitle_are_refused(docs, tmp_path, host, cameras):
    """An ellipsis in the title or subtitle is a label with a fact missing:
    refused, like any other."""
    with pytest.raises(labels.CameraMarkDoesNotFitError, match=host):
        render(docs, tmp_path, cameras, host=host)


def test_what_is_drawn_is_never_cut(docs, tmp_path, monkeypatch):
    said = []
    real_text = labels.Label.text
    monkeypatch.setattr(labels.Label, "text", lambda self, x, y, s, *a, **k: (
        said.append(s), real_text(self, x, y, s, *a, **k))[1])
    for cameras in ((Camera("ov5647", autofocus=True),),
                    (Camera("ov5647", autofocus=True), Camera("ov5647", autofocus=False)),
                    (Camera("imx708", variant="wide_noir", autofocus=True),
                     Camera("imx708", variant="wide_noir", autofocus=True))):
        render(docs, tmp_path, cameras)
    render(docs, tmp_path, (Camera("imx219", autofocus=False),) * 2, host="rpicm1-serial")
    assert not [s for s in said if "…" in s]
