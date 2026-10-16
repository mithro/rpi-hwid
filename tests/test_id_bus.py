"""The HAT ID bus read without changing the kernel (mithro/rpi-hwid issue
#91): the probe's own I2C master on the GPIO0/1 lines, the GPIO character
device requests behind it, which path a Pi takes, and sh_checked, through
which every command that does change a host now goes."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from id_bus_fixtures import (
    SCL,
    SDA,
    WAVESHARE_BLOB,
    FakeGpioChip,
    SimBus,
    SimEeprom,
    eeprom_bus,
)
from rpi_hwid import probe
from test_probe_collect import _w

# a 24C32's worth of cells, each its own offset's low byte, so a read that
# started anywhere but offset 0 shows it
COUNTING = bytes(i & 0xFF for i in range(4096))


def master(bus, **kw):
    return probe.BitBangI2C(bus, half_period=0, **kw)


# --- the I2C master against a simulated EEPROM --------------------------------


def test_a_good_read_is_the_eeproms_first_bytes_from_offset_0():
    eeprom = SimEeprom(0x50, COUNTING)
    eeprom.ptr = 1234                       # wherever the last reader left it
    bus = SimBus([eeprom])
    assert master(bus).read_eeprom(0x50, 256) == COUNTING[:256]
    assert eeprom.writes == [], "nothing is written but the offset"
    assert eeprom.mode == "idle", "a STOP ended it"
    assert bus.master == [1, 1], "both lines released after the STOP"


def test_the_bytes_decode_as_eeprom_read_would_have_them():
    found = probe.eeprom_decode(master(eeprom_bus(0x52)).read_eeprom(0x52, 256))
    assert found["product"] == "Waveshare PoE M.2 HAT+ (B)"
    assert found["pid"] == "0x6d87"


def test_a_slave_that_stretches_the_clock_is_waited_for():
    eeprom = SimEeprom(0x50, COUNTING)
    bus = SimBus([eeprom], stretch=5)
    assert master(bus).read_eeprom(0x50, 32) == COUNTING[:32]


def test_an_address_with_nothing_there_is_a_nack_not_an_error():
    bus = SimBus([SimEeprom(0x52, COUNTING)])
    i2c = master(bus)
    assert i2c.read_eeprom(0x50, 256) is None
    assert i2c.read_eeprom(0x52, 4) == COUNTING[:4], "the bus is left usable"
    assert bus.master == [1, 1]


def test_an_offset_that_is_not_acked_is_no_read():
    bus = SimBus([SimEeprom(0x50, COUNTING, nack_offset=True)])
    assert master(bus).read_eeprom(0x50, 256) is None
    assert bus.master == [1, 1]


def test_a_bare_bus_nacks_every_address_quickly():
    """No HAT: the pull-ups answer every bit with a 1, so every address is
    a NACK and the scan is eight short transfers."""
    bus = SimBus()
    i2c = master(bus)
    assert [i2c.read_eeprom(a, 256) for a in probe.ID_EEPROM_ADDRS] == [None] * 8
    assert bus.ops < 8 * 60


def test_a_clock_held_low_is_an_error_within_its_deadline():
    bus = SimBus(stuck={SCL})
    t = time.monotonic()
    with pytest.raises(probe.IdBusError, match=r"SCL \(GPIO1, pin 28\) stayed low for 25 ms"):
        master(bus).read_eeprom(0x50, 256)
    assert time.monotonic() - t < 1.0


def test_a_data_line_held_low_is_clocked_nine_times_then_an_error():
    bus = SimBus(stuck={SDA})
    with pytest.raises(probe.IdBusError, match=r"SDA \(GPIO0, pin 27\) stayed low through "
                       "nine clocks"):
        master(bus).read_eeprom(0x50, 256)


def test_a_slave_left_part_way_through_a_byte_is_clocked_free():
    """An earlier reader killed mid-read leaves the EEPROM driving a 0: the
    bus clear's clocks finish its byte, it lets go, and the read goes on."""
    eeprom = SimEeprom(0x50, COUNTING)
    eeprom.ptr = 0
    eeprom.load()
    eeprom.out[SDA] = 0
    eeprom.byte = 0                              # a byte of zeroes, part sent
    bus = SimBus([eeprom])
    assert master(bus).read_eeprom(0x50, 16) == COUNTING[:16]


def test_another_driver_on_the_bus_is_an_error():
    """SDA reads low while the master releases it to send a 1."""
    class Contender(SimEeprom):
        def falling(self):
            self.out[SDA] = 0                     # drives the data line, always
    bus = SimBus([Contender(0x50, COUNTING)])
    bus.slaves[0].out[SDA] = 1
    with pytest.raises(probe.IdBusError, match="something else drives the HAT ID bus"):
        master(bus).read_eeprom(0x50, 256)


# --- the GPIO character device, behind open_id_lines --------------------------


@pytest.fixture
def chip(tmp_path, monkeypatch, real_gpio):
    """A Pi 3B+'s tree with no I2C bus on the ID pins, its GPIO chips as the
    kernel answers for them, and a bus behind lines 0 and 1."""
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "ID_BUS_HALF_PERIOD_S", 0)

    def make(bus=None, **kw):
        fake = FakeGpioChip(tmp_path, bus if bus is not None else SimBus(), **kw)
        monkeypatch.setattr(probe, "gpio_ioctl", fake)
        monkeypatch.setattr(probe, "sh", fake.pinctrl_sh)
        return fake
    return make


def test_a_hat_is_read_through_the_gpio_lines(chip):
    fake = chip(eeprom_bus(0x50))
    found, readable = probe.id_bus_scan(0, (0, 1))
    assert readable is True
    assert list(found) == ["0x50"]
    assert found["0x50"]["product"] == "Waveshare PoE M.2 HAT+ (B)"
    assert len(fake.requests) == 1, "one request for both lines, kept for the whole scan"


def test_the_lines_are_released_and_freed_after_a_read(chip):
    fake = chip(eeprom_bus(0x50))
    probe.id_bus_scan(0, (0, 1))
    assert not fake.requested(), "the line fd is closed"
    assert fake.bus.master == [1, 1]
    assert fake.direction == ["input", "input"]
    assert fake.reconfigured, "made inputs before they were freed"
    assert [fake.info(o) for o in (0, 1)] == [(False, "input", "")] * 2


def test_the_lines_are_released_and_freed_after_a_bus_error(chip):
    fake = chip(SimBus(stuck={SCL}))
    with pytest.raises(probe.IdBusError, match="SCL"):
        probe.id_bus_scan(0, (0, 1))
    assert not fake.requested()
    assert fake.direction == ["input", "input"]


def test_v1_where_the_kernel_has_no_v2(chip):
    """A kernel before 5.10 answers a v2 request EINVAL: the same read is
    made through the v1 line handle."""
    fake = chip(eeprom_bus(0x51), uapi="v1")
    found, readable = probe.id_bus_scan(0, (0, 1))
    assert readable is True
    assert list(found) == ["0x51"]
    assert not fake.requested()


def test_v2_is_used_where_there_is_v2(chip, monkeypatch):
    chip(eeprom_bus(0x50))
    seen = []
    real = probe.open_id_lines

    def spy(chip_path, offsets):
        lines = real(chip_path, offsets)
        seen.append(lines.uapi)
        return lines
    monkeypatch.setattr(probe, "open_id_lines", spy)
    probe.id_bus_scan(0, (0, 1))
    assert seen == ["v2"]


@pytest.mark.parametrize("uapi", ["v2", "v1"])
def test_lines_another_driver_has_are_not_taken(chip, uapi):
    """pinctrl-bcm2835 is not strict, so the kernel would hand over a pin a
    driver has muxed; a GPIO driver's request shows in the line info."""
    fake = chip(uapi=uapi, busy={0: "i2c-gpio", 1: "i2c-gpio"})
    with pytest.raises(probe.IdBusError, match="GPIO0/1 are in use by i2c-gpio: the HAT ID "
                       "bus was not read"):
        probe.id_bus_scan(0, (0, 1))
    assert fake.requests == [], "not even asked for"


def test_a_request_refused_ebusy_names_who_has_the_lines(chip):
    fake = chip(busy_on_request="w1-gpio")
    with pytest.raises(probe.IdBusError, match="GPIO0/1 are in use by w1-gpio"):
        probe.id_bus_scan(0, (0, 1))
    assert len(fake.requests) == 1


def test_a_release_that_fails_is_a_host_not_as_found(chip):
    chip(eeprom_bus(0x50), fail_release=True)
    with pytest.raises(probe.HostNotAsFoundError, match="after reading the HAT ID bus on "
                       "GPIO0/1: releasing them failed"):
        probe.id_bus_scan(0, (0, 1))


def test_a_line_left_an_output_is_a_host_not_as_found(chip, monkeypatch):
    """A read that dies in its START, with both lines pulled low, and a
    release that cannot happen: the kernel then shows both outputs, and
    that is said."""
    chip(SimBus(), fail_release=True)
    real_set = probe.GpioLinesV2.set

    def set_then_die(self, i, value):
        real_set(self, i, value)
        if i == SCL and value == 0:
            raise OSError(5, "Input/output error")
    monkeypatch.setattr(probe.GpioLinesV2, "set", set_then_die)
    with pytest.raises(probe.HostNotAsFoundError) as err:
        probe.id_bus_scan(0, (0, 1))
    msg = str(err.value)
    assert "the read failed: [Errno 5] Input/output error" in msg
    assert "releasing them failed: Input/output error" in msg
    assert "they were GPIO0 input, GPIO1 input and are now GPIO0 output, GPIO1 output" in msg


def test_the_header_chip_is_found_by_its_label_not_its_number(chip):
    """A Pi 5 on 6.12: the RP1 is gpiochip15, after the SoC's four."""
    chip(eeprom_bus(0x50), header="pinctrl-rp1",
         chips={11: "gpio-brcmstb@107d517c00", 12: "gpio-brcmstb@107d517c20",
                13: "gpio-brcmstb@107d508500", 14: "gpio-brcmstb@107d508520",
                15: "pinctrl-rp1"}, pinctrl=True)
    path, label = probe.id_bus_gpiochip()
    assert path.endswith("/dev/gpiochip15")
    assert label == "pinctrl-rp1"
    assert list(probe.id_bus_scan(0, (0, 1))[0]) == ["0x50"]


def test_no_header_chip_is_an_error_naming_what_there_is(chip):
    chip(chips={3: "some-other-gpio"}, header="pinctrl-bcm2835")
    with pytest.raises(probe.IdBusError, match=r"no GPIO chip labelled pinctrl-rp1 or "
                       r"pinctrl-bcm2711 or pinctrl-bcm2835 .*some-other-gpio at /dev/gpiochip3"):
        probe.id_bus_scan(0, (0, 1))


def test_a_board_with_no_gpio_chips_at_all_is_an_error(tmp_path, monkeypatch, real_gpio):
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    with pytest.raises(probe.IdBusError, match=r"\(found no chip\)"):
        probe.id_bus_scan(0, (0, 1))


# --- the pins' function, before and after ------------------------------------
#
# Requesting GPIO0/1 makes them GPIOs. On a BCM2835 that is what they were
# (the firmware leaves them gpio_in), and freeing them makes them gpio_in
# again. On a Pi 5's RP1 the firmware leaves them with no function ("none",
# input off, pulled up) and freeing them leaves them GPIO inputs
# (pinctrl-rp1.c rp1_pmx_free), so they must be put back.

RP1_CHIPS = {0: "gpio-brcmstb@107d508500", 15: "pinctrl-rp1"}


def test_a_bcm2835s_pins_are_as_they_were_with_nothing_to_put_back(chip):
    fake = chip(eeprom_bus(0x50), pinctrl=True)
    found, _ = probe.id_bus_scan(0, (0, 1))
    assert list(found) == ["0x50"]
    assert fake.pinctrl_sets == []
    assert fake.function == ["gpio_in", "gpio_in"]


def test_an_rp1s_pins_are_put_back_to_no_function(chip):
    """The Pi 5 in the #92 proof: `pinctrl get 0-1` gave "no pu | --"."""
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS, pinctrl=True)
    assert fake.pinctrl_get_line(0) == " 0: no    pu | -- // ID_SDA/GPIO0 = none"
    found, readable = probe.id_bus_scan(0, (0, 1))
    assert readable is True
    assert list(found) == ["0x50"]
    assert fake.pinctrl_sets == [["sudo", probe.PINCTRL, "set", "0", "no"],
                                 ["sudo", probe.PINCTRL, "set", "1", "no"]]
    assert fake.function == ["none", "none"]
    assert fake.input_on == [False, False], "the pads' input is off again"


def test_with_debugfs_alone_a_changed_function_is_seen_and_said(chip):
    """No raspi-utils: the kernel's view shows the function changed, and with
    nothing to put it back with, that is said, not passed over."""
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS)
    with pytest.raises(probe.HostNotAsFoundError) as err:
        probe.id_bus_scan(0, (0, 1))
    msg = str(err.value)
    assert ("putting it back failed: `sudo /usr/bin/pinctrl set 0 no` could not be run"
            in msg)
    assert "GPIO0 was function none (" in msg
    assert "and is now function gpio (" in msg
    assert fake.function == ["gpio", "gpio"]


def test_with_pinctrl_alone_the_function_is_still_put_back(chip):
    """No debugfs (not root): raspi-utils' `pinctrl get` reads the pins."""
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS, debugfs=False,
                pinctrl=True)
    probe.id_bus_scan(0, (0, 1))
    assert fake.function == ["none", "none"]
    assert fake.input_on == [False, False]


def test_a_put_back_that_fails_is_loud_and_names_the_pins(chip):
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS, pinctrl=True,
                pinctrl_set_fails=True)
    with pytest.raises(probe.HostNotAsFoundError) as err:
        probe.id_bus_scan(0, (0, 1))
    msg = str(err.value)
    assert msg.startswith("the host is not as it was found, after reading the HAT ID bus on "
                          "GPIO0/1: putting it back failed: `sudo /usr/bin/pinctrl set 0 no` "
                          "exited 1: failed")
    assert ("GPIO0 was function none (input bias disabled, input bias pull down (0 ohms), "
            "input bias pull up (1 ohms)") in msg
    assert "pinctrl no pu, input off, and is now function gpio (" in msg
    assert "pinctrl ip pu, input on" in msg
    assert fake.function == ["gpio", "gpio"]


def test_a_put_back_that_changes_nothing_is_loud(chip, monkeypatch):
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS, pinctrl=True)
    real = fake.pinctrl_sh

    def set_does_nothing(args, timeout=15):
        if args[2:3] == ["set"]:
            fake.pinctrl_sets.append(list(args))
            return ""
        return real(args, timeout)
    monkeypatch.setattr(probe, "sh", set_does_nothing)
    with pytest.raises(probe.HostNotAsFoundError, match="`pinctrl set` did not put them back; "
                       r"GPIO0 was function none"):
        probe.id_bus_scan(0, (0, 1))


def test_pins_that_cannot_be_read_at_all_are_not_touched(chip):
    """No debugfs and no pinctrl: nothing says what the pins were, so the
    lines are not even asked for -- never "assumed fine"."""
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS, debugfs=False)
    with pytest.raises(probe.IdBusError) as err:
        probe.id_bus_scan(0, (0, 1))
    msg = str(err.value)
    assert msg.startswith("the function of GPIO0/1 cannot be read, so it could not be put "
                          "back after the HAT ID bus was read: debugfs: "
                          "/sys/kernel/debug/pinctrl: No such file or directory")
    assert "pinctrl: /usr/bin/pinctrl is not there" in msg
    assert fake.requests == []
    assert fake.function == ["none", "none"]


def test_pins_that_cannot_be_read_again_are_loud(chip, monkeypatch):
    fake = chip(eeprom_bus(0x50), header="pinctrl-rp1", chips=RP1_CHIPS, pinctrl=True)
    real_open = probe.open_id_lines

    def open_then_lose_pinctrl(path, offsets):
        lines = real_open(path, offsets)
        (fake.root / "usr/bin/pinctrl").unlink()
        return lines
    monkeypatch.setattr(probe, "open_id_lines", open_then_lose_pinctrl)
    with pytest.raises(probe.HostNotAsFoundError, match=r"the pins could not be read again: "
                       r".*pinctrl: /usr/bin/pinctrl is not there"):
        probe.id_bus_scan(0, (0, 1))


def test_a_read_that_fails_still_puts_the_pins_back_and_raises_its_own_error(chip):
    fake = chip(SimBus(stuck={SCL}), header="pinctrl-rp1", chips=RP1_CHIPS, pinctrl=True)
    with pytest.raises(probe.IdBusError, match="SCL"):
        probe.id_bus_scan(0, (0, 1))
    assert fake.function == ["none", "none"]
    assert not fake.requested()


def test_a_read_that_fails_and_pins_not_put_back_say_both(chip):
    chip(SimBus(stuck={SCL}), header="pinctrl-rp1", chips=RP1_CHIPS, pinctrl=True,
         pinctrl_set_fails=True)
    with pytest.raises(probe.HostNotAsFoundError, match=r"the read failed: SCL \(GPIO1.*"
                       "putting it back failed"):
        probe.id_bus_scan(0, (0, 1))


@pytest.mark.parametrize(("line", "want"), [
    # the #92 proof's Pi 5 and Pi 3B+, as printed
    ("0: no    pu | -- // ID_SDA/GPIO0 = none", (0, "no", "pu", "--")),
    (" 1: ip    -- | hi // ID_SCL/GPIO1 = input", (1, "ip", "--", "hi")),
    (" 4: op dh pd | hi // GPIO4 = output", (4, "op", "pd", "hi")),
    ("27: a3    pn | lo // GPIO27 = SD0_DAT3", (27, "a3", "pn", "lo")),
])
def test_pinctrl_get_lines_are_read_as_raspi_utils_prints_them(line, want):
    m = probe.PINCTRL_GET_LINE.match(line)
    assert (int(m.group(1)), m.group(2), m.group(3), m.group(4)) == want


@pytest.mark.parametrize(("config", "pull"), [
    # the RP1: every bias item, the one set at 1 (pinctrl-rp1.c rp1_pinconf_get)
    ("input bias disabled, input bias pull down (0 ohms), input bias pull up (1 ohms), "
     "input enabled", "pu"),
    ("input bias disabled, input bias pull down (1 ohms), input bias pull up (0 ohms)", "pd"),
    ("input bias disabled, input bias pull down (0 ohms), input bias pull up (0 ohms)", "pn"),
    # a BCM2711: only the one that is set (bcm2711_pinconf_get)
    ("input bias pull up (50000 ohms)", "pu"),
    ("input bias disabled", "pn"),
    # a BCM2835: no pull can be read back (bcm2835_pinconf_get)
    ("", None),
    (None, None),
])
def test_the_pull_is_read_from_the_generic_pinconf_dump(config, pull):
    assert probe.debugfs_pull(config) == pull


# --- which way a Pi's ID bus is read ------------------------------------------


@pytest.fixture
def no_commands(monkeypatch):
    """Reading the ID bus runs no command: neither overlay nor module."""
    def refuse(args, timeout=15):
        pytest.fail(f"the ID bus read ran {args!r}")
    monkeypatch.setattr(probe, "sh", refuse)
    monkeypatch.setattr(probe, "sh_checked", refuse)


def test_an_i2c_0_already_there_is_read_through_and_the_gpio_left_alone(
        tmp_path, monkeypatch, no_commands):
    """A camera Pi (its overlay brings up the i2c0mux) or one with
    dtparam=i2c_vc=on in config.txt."""
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    _w(tmp_path, "/dev/i2c-0", "")
    read = []
    monkeypatch.setattr(probe, "eeprom_read", lambda bus, addr, length: read.append(
        (bus, addr, length)) or (WAVESHARE_BLOB if addr == 0x52 else None))
    monkeypatch.setattr(probe, "open_id_lines", lambda *a: pytest.fail("GPIO requested"))
    found, readable = probe.id_bus_scan(0, (0, 1))
    assert readable is True
    assert list(found) == ["0x52"]
    assert read == [(0, a, 256) for a in range(0x50, 0x58)]


def test_with_no_i2c_0_the_gpio_lines_are_read(tmp_path, monkeypatch, no_commands):
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "eeprom_read", lambda *a: pytest.fail("i2c-dev read"))
    asked = []
    monkeypatch.setattr(probe, "open_id_lines",
                        lambda chip, offsets: asked.append((chip, offsets)) or eeprom_bus(0x50))
    found, readable = probe.id_bus_scan(0, (0, 1))
    assert readable is True
    assert list(found) == ["0x50"]
    assert asked == [(str(tmp_path) + "/dev/gpiochip0", (0, 1))]


@pytest.mark.parametrize("loaded", [False, True])
def test_a_kernel_adapter_with_no_node_is_an_error_and_its_pins_are_left_alone(
        tmp_path, monkeypatch, no_commands, loaded):
    """The i2c0mux bound with no i2c-dev: the pins are the kernel's, and the
    probe neither loads the module nor takes them."""
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "BUS_SETTLE_S", 0.0)
    _w(tmp_path, "/sys/bus/i2c/devices/i2c-0/name", "i2c-11-mux (chan_id 0)\n")
    if loaded:
        (tmp_path / "sys/module/i2c_dev").mkdir(parents=True)
    monkeypatch.setattr(probe, "open_id_lines", lambda *a: pytest.fail("GPIO requested"))
    with pytest.raises(probe.IdBusError, match=r"the kernel's I2C adapter i2c-0 \(i2c-11-mux "
                       r"\(chan_id 0\)\) is on GPIO0/1, but there is no /dev/i2c-0"):
        probe.id_bus_scan(0, (0, 1))


def test_a_node_udev_is_still_making_is_waited_for(tmp_path, monkeypatch, no_commands):
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    _w(tmp_path, "/sys/bus/i2c/devices/i2c-0/name", "i2c-11-mux (chan_id 0)\n")
    (tmp_path / "sys/module/i2c_dev").mkdir(parents=True)
    monkeypatch.setattr("time.sleep", lambda s: _w(tmp_path, "/dev/i2c-0", ""))
    monkeypatch.setattr(probe, "eeprom_read", lambda bus, addr, length: None)
    assert probe.id_bus_scan(0, (0, 1)) == ({}, True)


def test_a_board_without_gpio_lines_for_it_takes_the_bus_it_has(tmp_path, monkeypatch,
                                                                no_commands):
    """The Orange Pi: no node, no enable, no GPIO read -- not read."""
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "open_id_lines", lambda *a: pytest.fail("GPIO requested"))
    assert probe.id_bus_scan(1, None) == ({}, False)


def test_no_board_declares_an_enable_for_the_id_bus():
    for buses in probe.HEADER_BUSES.values():
        assert "enable" not in buses
    assert probe.HEADER_BUSES["rpi"]["id_gpio"] == (0, 1)


# --- sh_checked ---------------------------------------------------------------


def test_sh_checked_returns_stdout(real_sh_checked):
    assert probe.sh_checked([sys.executable, "-c", "print(' ok ')"]) == "ok"


def test_sh_checked_raises_on_a_non_zero_exit_naming_the_command(real_sh_checked):
    cmd = [sys.executable, "-c", "import sys; sys.stderr.write('no such overlay'); sys.exit(3)"]
    with pytest.raises(probe.CommandFailedError) as err:
        probe.sh_checked(cmd)
    assert str(err.value) == f"`{' '.join(cmd)}` exited 3: no such overlay"


def test_sh_checked_raises_on_a_timeout_and_says_it_may_still_run(real_sh_checked):
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    t = time.monotonic()
    with pytest.raises(probe.CommandFailedError, match=r"did not finish within 1 s; it was "
                       "killed, and what it started may still be running"):
        probe.sh_checked(cmd, timeout=1)
    assert time.monotonic() - t < 10


def test_a_timeout_returns_while_a_grandchild_still_holds_the_pipes(real_sh_checked):
    """sudo killed, the `dtparam -r` it started still stuck in the kernel
    with sudo's stdout: the probe does not wait for that to end."""
    cmd = [sys.executable, "-c", "import subprocess, sys, time; "
           "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)']); "
           "time.sleep(8)"]
    t = time.monotonic()
    with pytest.raises(probe.CommandFailedError, match="did not finish within 1 s"):
        probe.sh_checked(cmd, timeout=1)
    assert probe.sh(cmd, timeout=1) == ""
    assert time.monotonic() - t < 6


class _StuckChild:
    """A child that does not finish: its kill fails (`kill_error`), or it is
    killed and never exits (stuck in the kernel)."""

    def __init__(self, kill_error=None):
        self.kill_error, self.waited = kill_error, []
        self.stdout, self.stderr = _Pipe(), _Pipe()

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired("dtparam", timeout)

    def kill(self):
        if self.kill_error:
            raise self.kill_error

    def wait(self, timeout=None):
        self.waited.append(timeout)
        raise subprocess.TimeoutExpired("dtparam", timeout)


class _Pipe:
    closed = False

    def close(self):
        self.closed = True


def test_a_killed_child_that_never_exits_is_not_waited_for_unbounded(real_sh_checked,
                                                                      monkeypatch):
    """The wait after the kill has its own deadline (#92 review): a child
    stuck in the kernel must not hang the probe one line later."""
    child = _StuckChild()
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **kw: child)
    with pytest.raises(probe.CommandFailedError, match=r"did not finish within 1 s; it was "
                       r"killed but had not exited 5 s later"):
        probe.sh_checked(["sudo", "dtparam", "-r", "0"], timeout=1)
    assert child.waited == [probe.KILL_REAP_S]
    assert child.stdout.closed
    assert child.stderr.closed


def test_a_child_that_cannot_be_killed_is_a_timeout_not_could_not_be_run(real_sh_checked,
                                                                       monkeypatch):
    child = _StuckChild(kill_error=PermissionError(1, "Operation not permitted"))
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **kw: child)
    with pytest.raises(probe.CommandFailedError, match=r"did not finish within 1 s; it could "
                       r"not be killed \(Operation not permitted\)"):
        probe.sh_checked(["sudo", "dtparam", "-r", "0"], timeout=1)
    assert child.waited == []
    assert child.stdout.closed


def test_the_chip_is_closed_whatever_the_release_raises(monkeypatch):
    closed = []
    lines = probe.GpioLinesV2(41, "gpiochip0", (0, 1))
    lines.fd = 42
    monkeypatch.setattr(lines, "set_all_released", lambda: (_ for _ in ()).throw(ValueError("bug")))
    monkeypatch.setattr(probe.os, "close", closed.append)
    with pytest.raises(ValueError, match="bug"):
        lines.close()
    assert closed == [42, 41]


def test_sh_checked_raises_on_a_command_that_is_not_there(tmp_path, real_sh_checked):
    with pytest.raises(probe.CommandFailedError, match="could not be run"):
        probe.sh_checked([str(tmp_path / "dtparam")])


def test_sh_stays_lenient_for_reads():
    """vcgencmd and the lens reader: an answer that is not there is
    reported as not read by their callers."""
    assert probe.sh([sys.executable, "-c", "import sys; sys.exit(1)"]) == ""
    assert probe.sh([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1) == ""


def test_no_command_that_changes_the_host_goes_through_the_lenient_sh():
    import inspect
    import re

    src = inspect.getsource(probe)
    lenient = [m.group(0) for m in re.finditer(r"(?<![\w.])sh\([^\n]*", src)]
    assert lenient, "the reads are found"
    for call in lenient:
        assert not re.search(r"dtparam|modprobe|I2C_DEV|DTPARAM|enable", call), call
