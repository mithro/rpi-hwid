"""A header bus whose controller is up but has no /dev node is missing only
the i2c-dev module: the probe loads that first, and reaches for dtparam
only when the module alone does not bring the bus up (rpi3-netv2,
2026-10-01: i2c_arm on in its config.txt, no i2c-dev, and the probe's
dtparam hung in the kernel, unkillable, until the Pi was power-cycled)."""

from __future__ import annotations

import pytest
from test_probe_collect import (  # noqa: F401  (fixtures)
    _no_bus_settle,
    _w,
    fake_root,
)

from rpi_hwid import probe

MODPROBE = ["sudo", "modprobe", "i2c-dev"]
UNLOAD = ["sudo", "modprobe", "-r", "i2c-dev"]


class Host:
    """A Pi's I2C as the kernel keeps it: adapters (controllers enabled by
    config.txt or dtparam) and, only with i2c-dev loaded, their /dev nodes."""

    def __init__(self, root, adapters=(), loaded=False, overlay_makes=None):
        self.root, self.calls = root, []
        self.adapters, self.loaded = set(adapters), loaded
        # what `dtparam <param>` enables: param -> bus
        self.overlay_makes = overlay_makes or {"i2c_vc=on": 0, "i2c_arm=on": 1}
        self.overlays: list[int] = []
        self.sync()

    def sync(self):
        for bus in range(3):
            adapter = self.root / f"sys/class/i2c-adapter/i2c-{bus}"
            node = self.root / f"dev/i2c-{bus}"
            if bus in self.adapters:
                adapter.mkdir(parents=True, exist_ok=True)
            elif adapter.exists():
                adapter.rmdir()
            if bus in self.adapters and self.loaded:
                _w(self.root, f"/dev/i2c-{bus}", "")
            elif node.exists():
                node.unlink()
        module = self.root / "sys/module/i2c_dev"
        if self.loaded:
            module.mkdir(parents=True, exist_ok=True)
        elif module.exists():
            module.rmdir()

    def sh(self, args, timeout=15):
        args = list(args)
        self.calls.append(args)
        if args == MODPROBE:
            self.loaded = True
        elif args == UNLOAD:
            self.loaded = False
        elif args[:2] == ["sudo", "dtparam"]:
            if args[2] == "-r":
                self.adapters.discard(self.overlays.pop())
            else:
                bus = self.overlay_makes[args[2]]
                self.overlays.append(bus)
                self.adapters.add(bus)
        elif args == ["vcgencmd", "get_throttled"]:
            return "throttled=0x0"
        self.sync()
        return ""


@pytest.fixture
def pi(fake_root, monkeypatch):
    monkeypatch.setattr(probe, "i2c_scan", lambda bus, **kw: [])
    monkeypatch.setattr(probe, "eeprom_read", lambda bus, addr, length=256: None)

    def make(**kw):
        host = Host(fake_root, **kw)
        monkeypatch.setattr(probe, "sh", host.sh)
        return host
    return make


def test_a_bus_missing_only_i2c_dev_is_opened_by_loading_it(pi):
    """rpi3-netv2: both controllers up, no i2c-dev. Loading the module is
    the whole fix; no dtparam is run, and the module is unloaded after."""
    host = pi(adapters={0, 1})
    d = probe.collect()
    assert d["header_buses_read"] == {"id": True, "user": True}
    assert MODPROBE in host.calls
    assert not any(c[:2] == ["sudo", "dtparam"] for c in host.calls)
    assert host.calls[-1] == UNLOAD or UNLOAD in host.calls
    assert not host.loaded, "left as it was found"


def test_dtparam_is_the_fallback_when_the_module_brings_nothing_up(pi):
    """The user bus's controller is off: i2c-dev alone makes no node for it,
    so its overlay is applied -- and both are put back."""
    host = pi(adapters={0})
    d = probe.collect()
    assert d["header_buses_read"] == {"id": True, "user": True}
    assert host.calls.index(MODPROBE) < host.calls.index(["sudo", "dtparam", "i2c_arm=on"])
    assert host.overlays == []
    assert not host.loaded
    assert 1 not in host.adapters


def test_a_loaded_i2c_dev_is_neither_loaded_again_nor_unloaded(pi):
    host = pi(adapters={0}, loaded=True)
    d = probe.collect()
    assert d["header_buses_read"] == {"id": True, "user": True}
    assert MODPROBE not in host.calls
    assert UNLOAD not in host.calls
    assert host.loaded, "it was loaded before the probe, so it stays"
    assert ["sudo", "dtparam", "i2c_arm=on"] in host.calls
    assert host.overlays == []


def test_buses_already_there_need_no_command(pi):
    host = pi(adapters={0, 1}, loaded=True)
    probe.collect()
    assert MODPROBE not in host.calls
    assert not any(c[:2] == ["sudo", "dtparam"] for c in host.calls)
