"""The header's I2C buses without a Pi: the two wires with their pull-ups,
an I2C EEPROM on them that answers as a 24C32 does (or, with one byte of
memory, any device that answers a one-byte read), and the kernel's GPIO
character device in front of them, so the probe's own I2C master and its
GPIO requests run against something that behaves like the hardware.

SimBus is the wires: each line's level is the AND of what the master and
every slave drive, a released line reads high (the HAT's 3.9K pull-ups, or
the Pi's own pull-ups with no HAT), and a stuck line reads low whatever
anyone does. A slave sees START, STOP and the clock's edges from the levels,
as a real one does from the pins.

FakeGpioChip is /dev/gpiochipN: the probe's ioctls (probe.gpio_ioctl) are
decoded with <linux/gpio.h>'s layouts, v2 by default or v1 alone, and two
of its lines (GPIO0/1, the ID bus, unless told otherwise) are the SimBus's
SDA and SCL.
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


class Sniffer:
    """A passive listener on a SimBus (it never drives a line) that writes
    down what the master puts on the wire, as a slave sees it: each START
    and STOP, and each nine clocks as one byte. The first byte after a
    START is the address, with its R/W bit; after an address with the read
    bit, a byte whose data bits the master left released is a read, and one
    it drove a bit of low is the master sending; after the write bit, every
    byte is data the master sends. The transcript, in `events`:

        ("START",), ("STOP",)
        ("address", value, "ACK" | "NACK")    # value = addr << 1 | R/W
        ("read", value, "ACK" | "NACK")       # the master's ACK or NACK
        ("written", value, "ACK" | "NACK")    # a data byte the master sent

    `transfers()` splits it at each START."""

    def __init__(self, bus):
        self.out = [1, 1]
        self.hold = 0
        self.bus = bus
        self.events: list[tuple] = []
        self.bits: list[tuple[int, int]] = []
        self.first = False
        self.reading = False

    def start(self):
        self.events.append(("START",))
        self.bits, self.first, self.reading = [], True, False

    def stop(self):
        self.events.append(("STOP",))
        self.bits = []

    def rising(self, sda):
        # the level on the wire, and whether the master was pulling SDA low
        self.bits.append((sda, self.bus.master[SDA]))
        if len(self.bits) < 9:
            return
        value = sum(level << (7 - i) for i, (level, _) in enumerate(self.bits[:8]))
        ack = "ACK" if self.bits[8][0] == 0 else "NACK"
        master_drove = any(master == 0 for _, master in self.bits[:8])
        if self.first:
            self.events.append(("address", value, ack))
            self.first, self.reading = False, bool(value & 1)
        elif self.reading and not master_drove:
            self.events.append(("read", value, ack))
        else:
            self.events.append(("written", value, ack))
        self.bits = []

    def falling(self):
        pass

    def transfers(self):
        out: list[list[tuple]] = []
        for e in self.events:
            if e[0] == "START":
                out.append([])
            elif out:
                out[-1].append(e)
        return out


def sniffed_bus(*slaves, **kw):
    """A SimBus with `slaves` and a Sniffer on it, as (bus, sniffer)."""
    bus = SimBus(list(slaves), **kw)
    sniffer = Sniffer(bus)
    bus.slaves.append(sniffer)
    return bus, sniffer


def eeprom_bus(addr=0x50, memory=WAVESHARE_BLOB, **kw):
    return SimBus([SimEeprom(addr, memory)], **kw)


# --- the GPIO character device ---------------------------------------------

V2_LINEINFO, V2_GET_LINE = probe.GPIO_V2_GET_LINEINFO_IOCTL, probe.GPIO_V2_GET_LINE_IOCTL
V2_GET, V2_SET = probe.GPIO_V2_LINE_GET_VALUES_IOCTL, probe.GPIO_V2_LINE_SET_VALUES_IOCTL
V2_SET_CONFIG = probe.GPIO_V2_LINE_SET_CONFIG_IOCTL
CHIPINFO = probe.GPIO_CHIPINFO_IOCTL
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
    its lines `offsets` (SDA, SCL): 0 and 1, the ID bus, unless told
    otherwise (2 and 3, the user bus). Its lists (`function`, `direction`,
    `input_on`) are by the bus's line, SDA then SCL. `uapi` "v1" makes every v2 request an
    EINVAL, as a kernel before 5.10 answers. `busy` is {line: consumer}
    for lines already in use; `busy_on_request` makes the request itself
    fail EBUSY with that consumer, the race after a clean line info.
    `fail_release` makes v2's setting of both lines high at once -- the
    close's release, and nothing else -- fail EIO.

    The pins' function and pad follow the header chip's driver, as the
    kernel source has them: on "pinctrl-rp1" GPIO0-3 start as the firmware
    leaves them on a Pi 5 ("none", input off, pulled up), become a GPIO with
    its input on when a direction is set (rp1_gpio_direction_input ->
    rp1_set_fsel), and stay a GPIO when freed (rp1_pmx_free); on a BCM2835
    they are gpio_in or gpio_out by direction and freed to gpio_in
    (bcm2835_pmx_free), with no pull that can be read. `debugfs` writes
    /sys/kernel/debug/pinctrl as the kernel shows them; `pinctrl` installs
    /usr/bin/pinctrl, whose get and set `pinctrl_sh` answers as raspi-utils
    does, `set` failing when `pinctrl_set_fails`. debugfs is root's:
    `pinctrl_sh` also answers `sudo -n ls -1` and `sudo -n cat` on it from
    the tree (recorded in `sudo_reads`), refused as without passwordless
    sudo when `sudo_fails`. Each pad's drive strength is `drive_ma` (mA, the
    RP1's 4 by default); `drive_on_request` is what asking for the lines
    sets it to, which nothing puts back -- `pinctrl set` cannot set it."""

    def __init__(self, root, bus, chips=None, header="pinctrl-bcm2835", uapi="v2",
                 busy=None, busy_on_request=None, fail_release=False, debugfs=True,
                 pinctrl=False, pinctrl_set_fails=False, offsets=(0, 1), sudo_fails=False,
                 drive_on_request=None):
        self.root, self.bus, self.uapi = root, bus, uapi
        self.sudo_fails = sudo_fails
        self.sudo_reads: list[list[str]] = []
        self.drive_ma = [4, 4]
        self.drive_on_request = drive_on_request
        self.offsets = tuple(offsets)
        self.consumer = ""
        self.rp1 = header == "pinctrl-rp1"
        self.function = ["none", "none"] if self.rp1 else ["gpio_in", "gpio_in"]
        self.input_on = [not self.rp1, not self.rp1]
        self.debugfs, self.pinctrl = debugfs, pinctrl
        self.pinctrl_set_fails = pinctrl_set_fails
        self.pinctrl_sets: list[list[str]] = []
        self.chips = chips if chips is not None else {0: header, 1: "raspberrypi-exp-gpio"}
        self.header = header
        self.busy = dict(busy or {})          # by offset
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
        if pinctrl:
            (root / "usr/bin").mkdir(parents=True, exist_ok=True)
            (root / "usr/bin/pinctrl").write_bytes(b"")
        self.sync()

    def driver_muxes(self, function="alt0"):
        """What a kernel I2C driver's transfer does to the pins: its own
        function, input on (a Pi 4's i2c0mux leaves GPIO0/1 alt0)."""
        self.function = [function, function]
        self.input_on = [True, True]
        self.sync()

    def as_gpio(self, i):
        """What setting a direction does to the pin's function and pad."""
        if self.rp1:
            self.function[i], self.input_on[i] = "gpio", True
        else:
            self.function[i] = "gpio_out" if self.direction[i] == "output" else "gpio_in"
        self.sync()

    def sync(self):
        if not self.debugfs:
            return
        name = "1f000d0000.gpio" if self.rp1 else "3f200000.gpio"
        d = self.root / "sys/kernel/debug/pinctrl" / f"{name}-{self.header}"
        d.mkdir(parents=True, exist_ok=True)
        pins = ["registered pins: 54"]
        conf = ["Pin config settings per pin", "Format: pin (name): configs"]
        for n in range(max(self.offsets) + 2):
            fn = (self.function[self.offsets.index(n)] if n in self.offsets
                  else ("alt5" if self.rp1 else "gpio_in"))
            drive = self.drive_ma[self.offsets.index(n)] if n in self.offsets else 4
            shown = f"function {fn} ({fn})" if self.rp1 else f"function {fn}"
            pins.append(f"pin {n} (gpio{n}) {n}:{self.header} {shown} in hi; irq 0 (none)")
            # a flag is printed whatever its value (pinconf_generic_dump_one)
            conf.append(f"pin {n} (gpio{n}): " + (
                "input bias disabled, input bias pull down (0 ohms), input bias pull up "
                "(1 ohms), input enabled, input schmitt enabled, output enabled, slew rate "
                f"(0x0), output drive strength ({drive} mA)" if self.rp1 else ""))
        (d / "pins").write_text("\n".join(pins) + "\n")
        (d / "pinconf-pins").write_text("\n".join(conf) + "\n")
        # another controller, whose lines 0 and 1 are not the header's
        other = self.root / "sys/kernel/debug/pinctrl/107d508500.gpio-pinctrl-bcm2712"
        other.mkdir(parents=True, exist_ok=True)
        (other / "pins").write_text(
            "registered pins: 2\npin 0 (gpio0) 0:gpio-brcmstb@107d508500 function gpio in lo; "
            "irq 0 (none)\npin 1 (gpio1) 1:gpio-brcmstb@107d508500 function gpio in lo; "
            "irq 0 (none)\n")

    def pinctrl_get_line(self, offset):
        n = self.offsets.index(offset)
        if self.function[n].startswith("alt"):          # an I2C driver's, say
            fs = "a" + self.function[n][3:]
        elif self.rp1:
            fs = {"none": "no", "gpio": "op" if self.direction[n] == "output" else "ip"}[
                self.function[n]]
        else:
            fs = {"gpio_in": "ip", "gpio_out": "op"}[self.function[n]]
        pull = "pu" if self.rp1 else "--"
        drive = "dh" if fs == "op" else "  "
        level = "hi" if self.input_on[n] else "--"
        name = {0: "ID_SDA/GPIO0", 1: "ID_SCL/GPIO1", 2: "SDA1/GPIO2", 3: "SCL1/GPIO3"}[offset]
        fname = {"no": "none", "ip": "input", "op": "output"}.get(fs, ("SDA", "SCL")[n])
        return f"{offset:2d}: {fs:>2s} {drive} {pull} | {level} // {name} = {fname}"

    def pinctrl_sh(self, args, timeout=15):
        """`pinctrl get` and `sudo -n pinctrl set` as raspi-utils answers
        them, and `sudo -n ls -1` and `sudo -n cat` on debugfs."""
        self.free_if_closed()
        args = list(args)
        if args[:2] == [probe.PINCTRL, "get"]:
            return "\n".join(self.pinctrl_get_line(int(n)) for n in args[2].split(","))
        if args[:3] == ["sudo", "-n", "cat"] or args[:4] == ["sudo", "-n", "ls", "-1"]:
            assert args[-1].startswith(probe.DEBUGFS_PINCTRL), args
            self.sudo_reads.append(args)
            if self.sudo_fails:
                raise probe.CommandFailedError(
                    f"`{' '.join(args)}` exited 1: sudo: a password is required")
            path = self.root / args[-1].lstrip("/")
            if not path.exists():
                raise probe.CommandFailedError(
                    f"`{' '.join(args)}` exited 1: {args[2]}: {args[-1]}: No such file or "
                    "directory")
            if args[2] == "ls":
                return "\n".join(sorted(e.name for e in os.scandir(path)))
            return path.read_text().strip()
        if args[:4] == ["sudo", "-n", probe.PINCTRL, "set"]:
            self.pinctrl_sets.append(list(args))
            if not (self.root / "usr/bin/pinctrl").exists():
                raise probe.CommandFailedError(
                    f"`{' '.join(args)}` could not be run: sudo: {probe.PINCTRL}: "
                    "command not found")
            if self.pinctrl_set_fails:
                raise probe.CommandFailedError(f"`{' '.join(args)}` exited 1: failed")
            n, fs = self.offsets.index(int(args[4])), args[5]
            if fs == "no":
                self.function[n], self.input_on[n] = "none", False
            elif fs == "ip":
                self.direction[n] = "input"
                self.function[n] = "gpio" if self.rp1 else "gpio_in"
                self.input_on[n] = True
            self.sync()
            return ""
        raise AssertionError(f"unexpected command {args!r}")

    def chip_of(self, fd):
        return int(os.readlink(f"/proc/self/fd/{fd}").rsplit("gpiochip", 1)[1])

    def requested(self):
        return self.line_fd is not None and _is_open(self.line_fd)

    def free_if_closed(self):
        if self.line_fd is not None and not _is_open(self.line_fd):
            # freed: the line keeps the direction it was last given, as
            # gpiolib's flags do; the pin is what the driver's free makes it
            self.line_fd = None
            self.bus.master = [1, 1]
            for i in (0, 1):
                if self.rp1:
                    self.function[i] = "gpio"        # rp1_pmx_free: GPIO input
                else:
                    self.function[i] = "gpio_in"     # bcm2835_pmx_free
            self.sync()

    def info(self, offset):
        self.free_if_closed()
        if self.requested() and offset in self.offsets:
            return True, self.direction[self.offsets.index(offset)], self.consumer
        if offset in self.busy:
            return True, "input", self.busy[offset]
        if offset in self.offsets:
            return False, self.direction[self.offsets.index(offset)], ""
        return False, "input", ""

    def drive(self, i, value):
        self.bus.set(i, value)
        # gpiolib's open-drain emulation: a 1 is an input, a 0 an output low
        self.direction[i] = "input" if value else "output"
        self.as_gpio(i)

    def __call__(self, fd, request, buf):
        buf = bytes(buf)
        if fd == self.line_fd:
            return self.line_ioctl(request, buf)
        chip = self.chip_of(fd)
        if request == CHIPINFO:
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
            return self.grant(buf, offsets, 588, buf[256:288])
        if request == V1_HANDLE:
            assert len(buf) == 364
            n = struct.unpack_from("=I", buf, 356)[0]
            offsets = struct.unpack_from(f"={n}I", buf, 0)
            flags = struct.unpack_from("=I", buf, 256)[0]
            assert flags == probe.GPIOHANDLE_REQUEST_OUTPUT | probe.GPIOHANDLE_REQUEST_OPEN_DRAIN
            assert buf[260:262] == b"\x01\x01", "both start released"
            return self.grant(buf, offsets, 360, buf[324:356])
        raise AssertionError(f"unexpected ioctl {request:#x}")

    def grant(self, buf, offsets, fd_at, consumer):
        assert tuple(offsets) == self.offsets, offsets
        self.requests.append(buf)
        self.consumer = consumer.split(b"\0")[0].decode()
        if self.busy_on_request:
            self.busy = dict.fromkeys(self.offsets, self.busy_on_request)
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY))
        if any(o in self.busy for o in offsets):
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY))
        self.line_fd = os.open(os.devnull, os.O_RDONLY)
        if self.drive_on_request is not None:
            self.drive_ma = [self.drive_on_request] * 2
        for i in (0, 1):
            self.as_gpio(i)       # requested released: direction input
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
            for i in (0, 1):
                self.as_gpio(i)
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
