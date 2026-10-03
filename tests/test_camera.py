"""CSI cameras: the record a summary carries for each, and how the probe
finds them."""

from __future__ import annotations

import os

import pytest

from conftest import RAW
from rpi_hwid import probe
from rpi_hwid.model import Camera, Summary
from test_core import _evidence


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


# --- the probe ----------------------------------------------------------------
#
# The trees below are the four cameras read at Welland on 2026-10-02:
# pi-sw2-p48 and rpi5-pantilt (a fixed-focus v1), pi-sw2-p47 (an autofocus v1
# with no lens driver configured) and rpi5-netv2 (an autofocus v1 with
# dtoverlay=ov5647,cam0,vcm, and an HDMI bridge on the other port).

def client(root, slot, name, driver, power="active", lens_focus=False):
    """An I2C client as sysfs shows it, bound to `driver`."""
    path = root / "sys/bus/i2c/devices" / slot
    (path / "power").mkdir(parents=True)
    (path / "name").write_text(name + "\n")
    (path / "power/runtime_status").write_text(power + "\n")
    drivers = root / "sys/bus/i2c/drivers" / driver
    drivers.mkdir(parents=True, exist_ok=True)
    os.symlink(str(drivers), str(path / "driver"))
    if lens_focus:
        (path / "of_node").mkdir()
        (path / "of_node/lens-focus").write_bytes(b"\0\0\0\x2a")
    return path


def subdev(root, n, name, device=None):
    path = root / "sys/class/video4linux" / f"v4l-subdev{n}"
    path.mkdir(parents=True)
    (path / "name").write_text(name + "\n")
    if device is not None:
        os.symlink(str(device), str(path / "device"))


class Host:
    """A Pi's sysfs under `root`, and the commands the probe ran on it.
    `lens` is what the root reader finds at the lens address: "ack", "nak",
    or None for a reader that could not run."""

    def __init__(self, root):
        self.root = root
        self.lens = "nak"
        self.ran = []

    def sh(self, args, timeout=15):
        self.ran.append(list(args))
        module = self.root / "sys/module/i2c_dev"
        if args == probe.I2C_DEV_LOAD:
            module.mkdir()
        if args == probe.I2C_DEV_UNLOAD:
            module.rmdir()
        if probe.LENS_READER in args:
            if not module.exists():
                return f"ERROR=cannot open /dev/i2c-{args[-2]}"
            return f"LENS={self.lens}" if self.lens else ""
        return ""

    def reads(self):
        return [a for a in self.ran if probe.LENS_READER in a]


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = Host(tmp_path)
    (tmp_path / "sys/module").mkdir(parents=True)
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "sh", h.sh)
    return h


def v1(host, power="active"):
    sensor = client(host.root, "10-0036", "ov5647", "ov5647", power)
    subdev(host.root, 0, "csi2")
    subdev(host.root, 1, "pisp-fe")
    subdev(host.root, 2, "ov5647 10-0036", sensor)


def test_a_fixed_focus_camera_is_named_by_its_sensor(host):
    v1(host)
    cams = probe.collect_cameras()
    assert probe.camera_summary(cams) == [
        {"sensor": "ov5647", "variant": None, "autofocus": False, "lens": None}]
    assert cams[0]["slot"] == "10-0036"
    assert cams[0]["lens_probe"] == {"addr": "0x0c", "result": "nak"}


def test_an_unbound_lens_chip_beside_the_sensor_is_autofocus(host):
    """pi-sw2-p47: nothing in the kernel knows the lens, but its driver chip
    answers on the camera's bus."""
    v1(host)
    host.lens = "ack"
    assert probe.camera_summary(probe.collect_cameras()) == [
        {"sensor": "ov5647", "variant": None, "autofocus": True, "lens": "0x0c"}]
    # asked on the sensor's own bus, at the lens address, and nowhere else
    assert [a[-2:] for a in host.reads()] == [["10", "0x0c"]]


def test_silence_from_an_unpowered_camera_settles_nothing(host):
    """A lens chip fed from the sensor's supply is off while the sensor is:
    no answer then is not a fixed-focus lens."""
    v1(host, power="suspended")
    cam, = probe.collect_cameras()
    assert probe.camera_summary([cam]) == [
        {"sensor": "ov5647", "variant": None, "autofocus": None, "lens": None}]
    assert cam["lens_probe"]["result"] == "nak"
    assert cam["power"] == "suspended"


def test_an_answer_from_an_unpowered_camera_still_counts(host):
    v1(host, power="suspended")
    host.lens = "ack"
    assert probe.camera_summary(probe.collect_cameras())[0]["autofocus"] is True


def test_a_read_that_could_not_be_made_settles_nothing(host):
    v1(host)
    host.lens = None                    # no sudo: the reader printed nothing
    cam, = probe.collect_cameras()
    assert probe.camera_summary([cam])[0]["autofocus"] is None
    assert cam["lens_probe"]["result"].startswith("unread")


def run_reader(monkeypatch, capsys, error):
    """The root reader itself, with the read failing with `error`."""
    import errno
    import fcntl

    def ioctl(fd, request, arg):
        raise OSError(getattr(errno, error), os.strerror(getattr(errno, error)))
    monkeypatch.setattr(os, "open", lambda path, flags: 99)
    monkeypatch.setattr(os, "close", lambda fd: None)
    monkeypatch.setattr(fcntl, "ioctl", ioctl)
    monkeypatch.setattr("sys.argv", ["-c", "10", "0x0c"])
    exec(probe.LENS_READER, {})
    return capsys.readouterr().out.strip()


@pytest.mark.parametrize("error", ["ENXIO", "EREMOTEIO"])
def test_only_an_address_nak_is_silence(monkeypatch, capsys, error):
    assert run_reader(monkeypatch, capsys, error) == "LENS=nak"


@pytest.mark.parametrize("error", ["ETIMEDOUT", "EAGAIN", "EIO"])
def test_a_bus_fault_is_not_silence(monkeypatch, capsys, error):
    """A timeout or a stuck bus says nothing about the lens: unread, so a
    powered camera's autofocus stays unknown rather than false."""
    out = run_reader(monkeypatch, capsys, error)
    assert out.startswith("ERROR=")
    assert os.strerror(getattr(__import__("errno"), error)) in out


def test_an_owned_address_is_not_silence(monkeypatch, capsys):
    assert run_reader(monkeypatch, capsys, "EBUSY") == "ERROR=address owned by a kernel driver"


def test_the_module_that_makes_the_bus_readable_is_put_back(host):
    v1(host)
    probe.collect_cameras()
    assert host.ran[0] == probe.I2C_DEV_LOAD
    assert host.ran[-1] == probe.I2C_DEV_UNLOAD
    assert not (host.root / "sys/module/i2c_dev").exists()


def test_a_module_already_loaded_is_left_loaded(host):
    v1(host)
    (host.root / "sys/module/i2c_dev").mkdir()
    probe.collect_cameras()
    assert probe.I2C_DEV_LOAD not in host.ran
    assert probe.I2C_DEV_UNLOAD not in host.ran


def test_a_lens_driver_the_kernel_bound_is_autofocus_without_a_read(host):
    """rpi5-netv2: dtoverlay=ov5647,cam0,vcm. The address belongs to the
    driver, so nothing is read from it; and the HDMI bridge on the other
    port is not a camera."""
    sensor = client(host.root, "10-0036", "ov5647", "ov5647", "suspended", lens_focus=True)
    lens = client(host.root, "10-000c", "ad5398", "ad5398", "unsupported")
    bridge = client(host.root, "11-000f", "tc358743", "tc358743", "unsupported")
    subdev(host.root, 2, "ov5647 10-0036", sensor)
    subdev(host.root, 3, "ad5398 focus", lens)
    subdev(host.root, 6, "tc358743 11-000f", bridge)
    cams = probe.collect_cameras()
    assert probe.camera_summary(cams) == [
        {"sensor": "ov5647", "variant": None, "autofocus": True, "lens": "ad5398"}]
    assert cams[0]["lens_probe"] is None
    assert host.ran == []


def test_a_lens_driver_on_another_bus_belongs_to_another_camera(host):
    v1(host)
    client(host.root, "11-000c", "ad5398", "ad5398", "unsupported")
    assert probe.camera_summary(probe.collect_cameras())[0]["lens"] is None


@pytest.mark.parametrize(("name", "variant"), [
    ("imx708", None), ("imx708_wide", "wide"), ("imx708_noir", "noir"),
    ("imx708_wide_noir", "wide_noir")])
def test_a_camera_module_3_says_what_its_optics_are(host, name, variant):
    """The imx708 driver reads the module's own memory and names the
    subdevice after it."""
    sensor = client(host.root, "10-001a", "imx708", "imx708")
    lens = client(host.root, "10-000c", "dw9817", "dw9807", "unsupported")
    subdev(host.root, 2, name + " 10-001a", sensor)
    subdev(host.root, 3, "dw9817 10-000c", lens)
    assert probe.camera_summary(probe.collect_cameras()) == [
        {"sensor": "imx708", "variant": variant, "autofocus": True, "lens": "dw9807"}]


def test_two_cameras_are_listed_in_bus_order(host):
    a = client(host.root, "11-0010", "imx219", "imx219")
    b = client(host.root, "10-0036", "ov5647", "ov5647")
    subdev(host.root, 2, "imx219 11-0010", a)
    subdev(host.root, 5, "ov5647 10-0036", b)
    assert [c["sensor"] for c in probe.camera_summary(probe.collect_cameras())] == [
        "ov5647", "imx219"]


def test_a_board_with_no_camera_has_an_empty_list_and_runs_nothing(host):
    assert probe.collect_cameras() == []
    assert probe.camera_summary([]) == []
    assert host.ran == []


def test_evidence_from_before_the_probe_looked_gives_no_list():
    assert probe.camera_summary(None) is None


# --- into the verdict -----------------------------------------------------------

def test_the_verdict_carries_the_cameras_and_says_how_each_was_settled(host):
    v1(host)
    host.lens = "ack"
    v = probe.verdict(_evidence(cameras=probe.collect_cameras()))
    assert v["summary"]["cameras"] == [
        {"sensor": "ov5647", "variant": None, "autofocus": True, "lens": "0x0c"}]
    line, = [e for e in v["evidence"] if e.startswith("camera")]
    assert "ov5647" in line
    assert "0x0c" in line
    # and the record the rest of the package reads it into
    assert Summary.from_dict(v["summary"]).cameras == (
        Camera("ov5647", autofocus=True, lens="0x0c"),)


@pytest.mark.parametrize(("power", "lens", "says"), [
    ("active", "nak", "no lens driver"),
    ("suspended", "nak", "not powered"),
    ("active", None, "unread"),
])
def test_the_evidence_says_why_autofocus_is_or_is_not_known(host, power, lens, says):
    v1(host, power)
    host.lens = lens
    v = probe.verdict(_evidence(cameras=probe.collect_cameras()))
    line, = [e for e in v["evidence"] if e.startswith("camera")]
    assert says in line


def test_a_verdict_on_evidence_without_cameras_has_none():
    v = probe.verdict(_evidence())
    assert v["summary"]["cameras"] is None
    assert not [e for e in v["evidence"] if e.startswith("camera")]


def test_a_board_with_no_camera_says_so_in_the_evidence():
    v = probe.verdict(_evidence(cameras=[]))
    assert v["summary"]["cameras"] == []
    assert "camera: none bound" in v["evidence"]
