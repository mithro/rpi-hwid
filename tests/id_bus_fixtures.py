"""The HAT ID bus without a Pi: the two wires with their pull-ups, an I2C
EEPROM on them that answers as a 24C32 does, and the kernel's GPIO
character device in front of them, so the probe's own I2C master and its
GPIO requests run against something that behaves like the hardware.

SimBus is the wires: each line's level is the AND of what the master and
every slave drive, a released line reads high (the HAT's 3.9K pull-ups, or
the Pi's own pull-ups with no HAT), and a stuck line reads low whatever
anyone does. A slave sees START, STOP and the clock's edges from the levels,
as a real one does from the pins.

FakeGpioChip is /dev/gpiochipN: the probe's ioctls (probe.gpio_ioctl) are
decoded with <linux/gpio.h>'s layouts, v2 by default or v1 alone, and its
two lines are the SimBus's SDA and SCL.
"""

from __future__ import annotations

import errno
import os
import struct

from rpi_hwid import probe

SDA, SCL = 0, 1

# The first bytes of the Waveshare PoE M.2 HAT+ (B)'s EEPROM, as read at
# 0x52 (tests/test_core.py), then erased cells.
WAVESHARE_BLOB = (bytes.fromhex(
    "522d5069" "0200" "0400" "ee000000"
    "0100" "0000" "39000000"
    "5c522997eeeee998f348a0720f4c16eb" "876d" "0100" "07" "1a"
) + b"vendor " + b"Waveshare PoE M.2 HAT+ (B)" + b"\x00\x00").ljust(256, b"\xff")


class SimEeprom:
    """A 24C32-style I2C EEPROM: 7-bit address `addr`, a 16-bit offset
    written as two bytes, sequential reads from it, and data bytes written
    after the offset recorded (and not committed: there is no STOP logic
    to commit a write, since a reader must never send one). `nack_offset`
    makes it refuse the offset bytes."""

    def __init__(self, addr, memory, nack_offset=False):
        self.addr, self.memory, self.nack_offset = addr, bytes(memory), nack_offset
        self.out = [1, 1]
        self.mode = "idle"
        self.ptr = 0
        self.writes: list[int] = []
        self.hold = 0
        self.bits = self.sr = self.count = self.byte = 0
        self.after_ack = "idle"
        self.master_ack = False

    def start(self):
        self.mode, self.bits, self.sr, self.count = "addr", 0, 0, 0
        self.out[SDA] = 1

    def stop(self):
        self.mode = "idle"
        self.out[SDA] = 1

    def rising(self, sda):
        if self.mode in ("addr", "write"):
            self.sr = ((self.sr << 1) | sda) & 0xFF
            self.bits += 1
        elif self.mode == "ack_in":
            self.master_ack = sda == 0

    def falling(self):
        if self.mode in ("addr", "write") and self.bits == 8:
            if self.mode == "addr":
                if self.sr >> 1 != self.addr:
                    self.mode = "idle"
                    return
                self.after_ack = "read" if self.sr & 1 else "write"
            else:
                if self.count < 2 and self.nack_offset:
                    self.mode = "idle"
                    return
                if self.count == 0:
                    self.ptr = self.sr << 8
                elif self.count == 1:
                    self.ptr |= self.sr
                else:
                    self.writes.append(self.sr)
                self.count += 1
                self.after_ack = "write"
            self.out[SDA] = 0
            self.mode = "ack_out"
        elif self.mode == "ack_out":
            self.out[SDA] = 1
            if self.after_ack == "read":
                self.load()
            else:
                self.mode, self.bits, self.sr = "write", 0, 0
        elif self.mode == "read":
            self.bits += 1
            if self.bits < 8:
                self.out[SDA] = (self.byte >> (7 - self.bits)) & 1
            else:
                self.out[SDA] = 1
                self.mode = "ack_in"
        elif self.mode == "ack_in":
            if self.master_ack:
                self.load()
            else:
                self.mode = "idle"

    def load(self):
        self.byte = self.memory[self.ptr % len(self.memory)]
        self.ptr += 1
        self.bits = 0
        self.mode = "read"
        self.out[SDA] = (self.byte >> 7) & 1


class SimBus:
    """The ID bus's two wires, as the master's open-drain lines (set, get,
    close). `stretch` is how many reads of SCL a slave holds the clock low
    for after the master releases it; `stuck` names lines held low for
    good."""

    def __init__(self, slaves=(), stretch=0, stuck=()):
        self.master = [1, 1]
        self.slaves = list(slaves)
        self.stretch = stretch
        self.stuck = set(stuck)
        self.closed = False
        self.closed_with: BaseException | None = None
        self.released_at_close: list[int] | None = None
        self.ops = 0

    def level(self, i):
        if i in self.stuck:
            return 0
        v = self.master[i]
        for s in self.slaves:
            v &= s.out[i]
        return v

    def levels(self):
        return [self.level(SDA), self.level(SCL)]

    def edges(self, old):
        new = self.levels()
        if old[SCL] and new[SCL]:
            if old[SDA] and not new[SDA]:
                for s in self.slaves:
                    s.start()
            elif not old[SDA] and new[SDA]:
                for s in self.slaves:
                    s.stop()
        elif not old[SCL] and new[SCL]:
            for s in self.slaves:
                s.rising(new[SDA])
        elif old[SCL] and not new[SCL]:
            for s in self.slaves:
                s.falling()

    def set(self, i, value):
        assert not self.closed, "driven after close"
        self.ops += 1
        old = self.levels()
        if i == SCL and value and not self.master[SCL] and self.stretch:
            for s in self.slaves:
                s.out[SCL], s.hold = 0, self.stretch
        self.master[i] = value
        self.edges(old)

    def get(self, i):
        assert not self.closed, "read after close"
        self.ops += 1
        if i == SCL:
            for s in self.slaves:
                if s.hold:
                    s.hold -= 1
                    if not s.hold:
                        old = self.levels()
                        s.out[SCL] = 1
                        self.edges(old)
        return self.level(i)

    def close(self, cause=None):
        self.released_at_close = list(self.master)
        self.master = [1, 1]
        self.closed, self.closed_with = True, cause


def eeprom_bus(addr=0x50, memory=WAVESHARE_BLOB, **kw):
    return SimBus([SimEeprom(addr, memory)], **kw)


# --- the GPIO character device ---------------------------------------------

V2_LINEINFO, V2_GET_LINE = probe.GPIO_V2_GET_LINEINFO_IOCTL, probe.GPIO_V2_GET_LINE_IOCTL
V2_GET, V2_SET = probe.GPIO_V2_LINE_GET_VALUES_IOCTL, probe.GPIO_V2_LINE_SET_VALUES_IOCTL
V2_SET_CONFIG = probe.GPIO_V2_LINE_SET_CONFIG_IOCTL
V1_LINEINFO, V1_HANDLE = probe.GPIO_V1_GET_LINEINFO_IOCTL, probe.GPIO_V1_GET_LINEHANDLE_IOCTL
V1_GET, V1_SET = probe.GPIOHANDLE_GET_LINE_VALUES_IOCTL, probe.GPIOHANDLE_SET_LINE_VALUES_IOCTL
OUTPUT_OPEN_DRAIN_V2 = probe.GPIO_V2_LINE_FLAG_OUTPUT | probe.GPIO_V2_LINE_FLAG_OPEN_DRAIN


def _is_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


class FakeGpioChip:
    """One or more GPIO chips under root/dev, answering the probe's ioctls.

    `chips` is {number: label}; the chip labelled `header` has the bus on
    its lines 0 (SDA) and 1 (SCL). `uapi` "v1" makes every v2 request an
    EINVAL, as a kernel before 5.10 answers. `busy` is {line: consumer}
    for lines already in use; `busy_on_request` makes the request itself
    fail EBUSY with that consumer, the race after a clean line info.
    `fail_release` makes v2's setting of both lines high at once -- the
    close's release, and nothing else -- fail EIO."""

    def __init__(self, root, bus, chips=None, header="pinctrl-bcm2835", uapi="v2",
                 busy=None, busy_on_request=None, fail_release=False):
        self.root, self.bus, self.uapi = root, bus, uapi
        self.chips = chips if chips is not None else {0: header, 1: "raspberrypi-exp-gpio"}
        self.header = header
        self.busy = dict(busy or {})
        self.busy_on_request = busy_on_request
        self.fail_release = fail_release
        self.fds: dict[int, int] = {}          # chip fd -> chip number, by path
        self.line_fd: int | None = None
        self.direction = ["input", "input"]
        self.requests: list[bytes] = []
        self.reconfigured = False
        for n in self.chips:
            path = root / f"dev/gpiochip{n}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")

    def chip_of(self, fd):
        return int(os.readlink(f"/proc/self/fd/{fd}").rsplit("gpiochip", 1)[1])

    def requested(self):
        return self.line_fd is not None and _is_open(self.line_fd)

    def free_if_closed(self):
        if self.line_fd is not None and not _is_open(self.line_fd):
            # freed: the line keeps the direction it was last given, as
            # gpiolib's flags do
            self.line_fd = None
            self.bus.master = [1, 1]

    def info(self, offset):
        self.free_if_closed()
        if self.requested() and offset in (0, 1):
            return True, self.direction[offset], "rpi-hwid HAT ID bus"
        if offset in self.busy:
            return True, "input", self.busy[offset]
        return False, self.direction[offset] if offset in (0, 1) else "input", ""

    def drive(self, i, value):
        self.bus.set(i, value)
        # gpiolib's open-drain emulation: a 1 is an input, a 0 an output low
        self.direction[i] = "input" if value else "output"

    def __call__(self, fd, request, buf):
        buf = bytes(buf)
        if fd == self.line_fd:
            return self.line_ioctl(request, buf)
        chip = self.chip_of(fd)
        if request == probe.ID_GPIO_CHIPINFO_IOCTL:
            return (f"gpiochip{chip}".encode().ljust(32, b"\0")
                    + self.chips[chip].encode().ljust(32, b"\0") + struct.pack("=I", 54))
        assert self.chips[chip] == self.header, "asked a chip that is not the header's"
        if request in (V2_LINEINFO, V2_GET_LINE) and self.uapi != "v2":
            raise OSError(errno.EINVAL, os.strerror(errno.EINVAL))
        if request == V2_LINEINFO:
            assert len(buf) == 256
            assert buf[:64] == b"\0" * 64
            assert buf[68:] == b"\0" * 188, "the rest of the request is zero"
            used, direction, consumer = self.info(struct.unpack_from("=I", buf, 64)[0])
            flags = (probe.GPIO_V2_LINE_FLAG_USED if used else 0) | (
                probe.GPIO_V2_LINE_FLAG_OUTPUT if direction == "output" else 1 << 2)
            return (b"\0" * 32 + consumer.encode().ljust(32, b"\0") + buf[64:68]
                    + b"\0" * 4 + struct.pack("=Q", flags) + b"\0" * 176)
        if request == V1_LINEINFO:
            assert len(buf) == 72
            used, direction, consumer = self.info(struct.unpack_from("=I", buf, 0)[0])
            flags = (probe.GPIOLINE_FLAG_KERNEL if used else 0) | (
                probe.GPIOLINE_FLAG_IS_OUT if direction == "output" else 0)
            return (buf[:4] + struct.pack("=I", flags) + b"\0" * 32
                    + consumer.encode().ljust(32, b"\0"))
        if request == V2_GET_LINE:
            assert len(buf) == 592
            n = struct.unpack_from("=I", buf, 560)[0]
            offsets = struct.unpack_from(f"={n}I", buf, 0)
            flags, nattrs = struct.unpack_from("=QI", buf, 288)
            attr_id, _pad, values, mask = struct.unpack_from("=IIQQ", buf, 320)
            assert flags == OUTPUT_OPEN_DRAIN_V2, "requested open-drain outputs"
            assert (nattrs, attr_id, values, mask) == (1, 2, 3, 3), "both start released"
            return self.grant(buf, offsets, 588)
        if request == V1_HANDLE:
            assert len(buf) == 364
            n = struct.unpack_from("=I", buf, 356)[0]
            offsets = struct.unpack_from(f"={n}I", buf, 0)
            flags = struct.unpack_from("=I", buf, 256)[0]
            assert flags == probe.GPIOHANDLE_REQUEST_OUTPUT | probe.GPIOHANDLE_REQUEST_OPEN_DRAIN
            assert buf[260:262] == b"\x01\x01", "both start released"
            return self.grant(buf, offsets, 360)
        raise AssertionError(f"unexpected ioctl {request:#x}")

    def grant(self, buf, offsets, fd_at):
        assert tuple(offsets) == (0, 1), offsets
        self.requests.append(buf)
        if self.busy_on_request:
            self.busy = {0: self.busy_on_request, 1: self.busy_on_request}
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY))
        if any(o in self.busy for o in offsets):
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY))
        self.line_fd = os.open(os.devnull, os.O_RDONLY)
        return buf[:fd_at] + struct.pack("=i", self.line_fd) + buf[fd_at + 4:]

    def line_ioctl(self, request, buf):
        if request == V2_SET:
            bits, mask = struct.unpack("=QQ", buf)
            if self.fail_release and mask == 3:
                raise OSError(errno.EIO, os.strerror(errno.EIO))
            for i in (0, 1):
                if mask >> i & 1:
                    self.drive(i, bits >> i & 1)
            return buf
        if request == V2_SET_CONFIG:
            assert len(buf) == 272
            flags, nattrs = struct.unpack_from("=QI", buf, 0)
            assert (flags, nattrs) == (probe.GPIO_V2_LINE_FLAG_INPUT, 0), "made inputs"
            assert buf[12:] == b"\0" * 260
            self.direction = ["input", "input"]
            self.bus.master = [1, 1]
            self.reconfigured = True
            return buf
        if request == V2_GET:
            _, mask = struct.unpack("=QQ", buf)
            bits = sum(self.bus.get(i) << i for i in (0, 1) if mask >> i & 1)
            return struct.pack("=QQ", bits, mask)
        if request == V1_SET:
            for i in (0, 1):
                if self.bus.master[i] != buf[i]:
                    self.drive(i, buf[i])
            return buf
        if request == V1_GET:
            return bytes([self.bus.get(0), self.bus.get(1)]) + b"\0" * 62
        raise AssertionError(f"unexpected line ioctl {request:#x}")
