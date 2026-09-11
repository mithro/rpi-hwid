#!/usr/bin/env python3
"""Which Tiny Tapeout demo board (and which chip on it) is on this Pi's USB?

A separate, dependency-free file like rpi_hwid.fpga, for the same reason
(python3 >= 3.5 on the host, sent over ssh, nothing installed), kept apart
from the Pi probe because few people have a Tiny Tapeout board on their
Pi. Standalone:

    ssh pi@host 'python3 -' --json < src/rpi_hwid/tinytapeout.py
    rpi-hwid tinytapeout [--json] [--no-repl]

or appended to the Pi probe by ``rpi-hwid probe --tinytapeout`` and
``rpi-hwid collect --tinytapeout``, which merge its findings into that
document's ``verdict.summary.tinytapeout``.

What a Tiny Tapeout demo board looks like from the Pi, established from
the public sources on 2026-09-10:

USB
    The demo board's RP2040 (TT04 to TT08 boards) or RP2350 (the "ETR"
    DBv3 boards) runs the Tiny Tapeout MicroPython SDK. The RP2040 UF2
    (github.com/TinyTapeout/tt-micropython-firmware, bin/release.sh, last
    release v2.0.4) is stock MicroPython RPI_PICO with the SDK copied onto
    its filesystem, and the RP2350 UF2 (.github/workflows/release.yml,
    v3.x) is MicroPython built for psychogenic/tinytapeout_rp2350b, whose
    board definition sets only the board name. So both present
    MicroPython's rp2 defaults: 2e8a:0005, manufacturer "MicroPython",
    product "Board in FS mode" (micropython ports/rp2/mpconfigport.h,
    shared/tinyusb/tusb_config.h), and the serial is the RP2's flash
    unique id (ports/rp2/usbd.c). That identifies a *candidate*: any
    MicroPython RP2 board reads the same. The TT02/TT03 boards with a Pico
    soldered on read the same again when they run the SDK.
The serial REPL
    What makes it a Tiny Tapeout board is the SDK on it, reachable over
    the CDC ACM port (/dev/ttyACM*). The SDK's main.py builds a
    ``DemoBoard`` singleton ``tt`` at boot and prints it, which reads the
    chip's ROM (project 0 on every chip since TT05: the text
    "shuttle=tt06\\nrepo=...\\ncommit=..." at bytes 32-127, written by
    tt-support-tools rom.py) into ``tt.shuttle._shuttle_props._contents``.
    It says 'unknown' when no ROM answers (a TT04 chip, or nothing
    mounted) and 'FPGA' on the FPGA breakout (v3 SDK,
    src/ttboard/boot/rom.py). Reading the ROM resets and clocks the chip's
    project mux and drives its ui_in pins, so this probe only ever reports
    that cached copy: when the boot did not fill it (no ``tt``, a custom
    main.py, a ROM read that never happened) the ROM is reported as not
    cached and nothing is driven.
    ``DemoboardDetect.PCB_str()`` names the demo board the SDK probed
    ('TT04/TT05' or 'TT06+' from the v2 SDK's pull-up and mux tests,
    'TTDBv3 [3.2]' from v3; src/ttboard/boot/demoboard_detect.py) and
    ``ttboard.VERSION`` is the SDK release (the /release_vX.Y.Z or
    /VERSION file the UF2 build drops in the filesystem).

The REPL is driven with MicroPython's raw-REPL protocol (Ctrl-C, Ctrl-A,
code, Ctrl-D, Ctrl-B) over the tty opened with os.open and termios: no
pyserial on a Pi. It interrupts whatever the board is running, which at
boot is nothing (the SDK sits at the prompt), never soft-resets, and
touches no pin, so the board's state is otherwise untouched. Every read
and write has a deadline and a board that does not answer is recorded as
such. --no-repl (or repl=False) stops at the USB tree.

The table below is the board data that is not readable from the board:
the chip carrier ("QFN breakout") and demo board colours per shuttle, the
demo board revision that shipped with each kit, and the chip's page on
tinytapeout.com. Colours and TT02-TT06 demo board versions are from Tim's
spreadsheet; TT05/TT07/TT08 demo board versions from tt-demo-pcb
doc/historic/README.md (which calls the TT02/TT03 board "v1.x", where
Tim's sheet has v2.2.5/v2.2.6 off the boards themselves); the URLs from
tinytapeout.com/chips/ (each checked on 2026-09-10; a shuttle with no
page is None). An unknown colour stays None and the label draws an empty
swatch. Shuttles the table does not list get the same all-None row from
shuttle_info().
"""
import fcntl
import glob
import json
import os
import select
import sys
import termios
import time

# Prefix for every absolute path read; the tests point it at a fake tree.
ROOT = ""

CHIPS_INDEX_URL = "https://tinytapeout.com/chips/"

# Board colour names -> a printable hex for the label's swatches.
COLOURS = {
    "green": "#1f7a3f", "red": "#b8232b", "purple": "#5c2d91", "black": "#222222",
    "yellow": "#f2c81a", "pink": "#f28cb3", "white": "#f8f8f8",
}

# shuttle: (chip carrier colour, its silkscreen, demo board colour, its
#           silkscreen, demo board version, chip page URL)
SHUTTLES = {
    "tt01": (None, None, None, None, None, "https://tinytapeout.com/chips/tt01/"),
    "tt02": ("green", "white", "green", "white", "v2.2.5",
             "https://tinytapeout.com/chips/tt02/"),
    "tt03": ("red", "white", "red", "white", "v2.2.6",
             "https://tinytapeout.com/chips/tt03/"),
    "tt03p5": ("purple", "white", "purple", "white", "v1.2.1", None),
    "tt04": ("black", "white", "black", "white", "v1.2.2",
             "https://tinytapeout.com/chips/tt04/"),
    "tt05": ("yellow", "black", "black", "white", "v1.2.3",
             "https://tinytapeout.com/chips/tt05/"),
    "tt06": ("pink", "white", "pink", "white", "v2.0.1",
             "https://tinytapeout.com/chips/tt06/"),
    "tt07": ("black", "pink", "black", "pink", "v2.1.0",
             "https://tinytapeout.com/chips/tt07/"),
    "tt08": ("white", "pink", "white", "pink", "v2.1.2",
             "https://tinytapeout.com/chips/tt08/"),
    "tt09": (None, None, None, None, None, "https://tinytapeout.com/chips/tt09/"),
    "tt10": (None, None, None, None, None, None),          # cancelled
    "ttihp0p2": (None, None, None, None, None, "https://tinytapeout.com/chips/ttihp0p2/"),
    "ttihp0p4": (None, None, None, None, None, "https://tinytapeout.com/chips/ttihp0p4/"),
    "ttihp25a": (None, None, None, None, None, "https://tinytapeout.com/chips/ttihp25a/"),
    "ttihp25b": (None, None, None, None, None, "https://tinytapeout.com/chips/ttihp25b/"),
    "ttihp26a": (None, None, None, None, None, "https://tinytapeout.com/chips/ttihp26a/"),
    "ttihp26b": (None, None, None, None, None, "https://app.tinytapeout.com/shuttles/ttihp26b"),
    "ttcad25a": (None, None, None, None, None, "https://tinytapeout.com/chips/ttcad25a/"),
    "ttsky25a": (None, None, None, None, None, "https://tinytapeout.com/chips/ttsky25a/"),
    "ttsky25b": (None, None, None, None, None, "https://tinytapeout.com/chips/ttsky25b/"),
    "ttsky26a": (None, None, None, None, None, "https://tinytapeout.com/chips/ttsky26a/"),
    "ttsky26b": (None, None, None, None, None, "https://tinytapeout.com/chips/ttsky26b/"),
    "ttsky26c": (None, None, None, None, None, "https://tinytapeout.com/chips/ttsky26c/"),
    "ttgf0p2": (None, None, None, None, None, "https://tinytapeout.com/chips/ttgf0p2/"),
    "ttgf0p3": (None, None, None, None, None, "https://tinytapeout.com/chips/ttgf0p3/"),
    "ttgf26a": (None, None, None, None, None, "https://tinytapeout.com/chips/ttgf26a/"),
    "ttgf26b": (None, None, None, None, None, "https://tinytapeout.com/chips/ttgf26b/"),
}

# Foundry prefixes in a shuttle name -> the PDK, for the label's subtitle.
# "cad" (ttcad25a) is inferred to be sky130 from its CI process code; the
# rest are as the chips list names them.
PDK = {"": "sky130", "sky": "sky130", "ihp": "ihp-sg13g2", "gf": "gf180mcu", "cad": "sky130"}


def shuttle_info(shuttle):
    """The table row for `shuttle` as a dict, all None for a shuttle the
    table does not know (the row is still returned, so callers need no
    special case)."""
    row = SHUTTLES.get((shuttle or "").lower(), (None,) * 6)
    return {"chip_colour": row[0], "chip_silk": row[1], "demoboard_colour": row[2],
            "demoboard_silk": row[3], "demoboard_version": row[4], "url": row[5]}


def _split_shuttle(shuttle):
    """'ttihp25a' -> ('ihp', '25a'); None for a name not starting 'tt'."""
    s = (shuttle or "").lower()
    if not s.startswith("tt"):
        return None
    rest = s[2:]
    i = 0
    while i < len(rest) and rest[i].isalpha():
        i += 1
    return rest[:i], rest[i:]


def shuttle_title(shuttle):
    """'tt06' -> 'Tiny Tapeout 6', 'ttihp25a' -> 'Tiny Tapeout IHP 25a',
    'ttgf0p2' -> 'Tiny Tapeout GF 0.2': the site's own naming."""
    parts = _split_shuttle(shuttle)
    if parts is None:
        return shuttle or ""
    foundry, run = parts
    head, dot, tail = run.replace("p", ".").partition(".")
    run = (head.lstrip("0") or "0") + dot + tail if head else run
    parts = ["Tiny Tapeout"]
    if foundry:
        parts.append(foundry.upper())
    if run:
        parts.append(run)
    return " ".join(parts)


def shuttle_short(shuttle):
    """'tt06' -> 'TT06', 'ttihp25a' -> 'TTIHP25a', 'ttgf0p3' -> 'TTGF0p3':
    the short names the chips list uses."""
    parts = _split_shuttle(shuttle)
    if parts is None:
        return (shuttle or "").upper()
    return "TT" + parts[0].upper() + parts[1]


def shuttle_pdk(shuttle):
    parts = _split_shuttle(shuttle)
    return PDK.get(parts[0]) if parts else None


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read().rstrip(b"\0").decode("ascii", "replace").strip()
    except OSError:
        return None


def usb_candidates():
    """Every MicroPython RP2 (2e8a:0005) device on the USB tree, with the
    tty its CDC ACM interface got."""
    out = []
    for p in sorted(glob.glob(ROOT + "/sys/bus/usb/devices/*")):
        if read(p + "/idVendor") != "2e8a":
            continue
        pid = read(p + "/idProduct") or ""
        ttys = sorted(os.path.basename(t)
                      for t in glob.glob(p + ":1.*/tty/*"))
        out.append({"path": os.path.basename(p), "id": "2e8a:" + pid,
                    "manufacturer": read(p + "/manufacturer"), "product": read(p + "/product"),
                    "serial": read(p + "/serial"),
                    "tty": ("/dev/" + ttys[0]) if ttys else None})
    return out


# --- the raw REPL -------------------------------------------------------------
#
# The snippet run on the board. It reads only what the SDK already holds:
# `tt` as main.py built it at boot, and the ROM copy that boot cached in
# tt.shuttle._shuttle_props (a ChipROM once read, or a HardcodedShuttle
# when config.ini forces the shuttle). ChipROM.contents is lazy and would
# reset and clock the chip's mux and drive ui_in on a cold read, and
# DemoBoard.get() would run a whole board init, so neither is called: a
# missing `tt` or an unread ROM is reported, not fetched.
REPL_SNIPPET = """
import json, os, sys
d = {}
try:
    d['machine'] = os.uname().machine
    d['micropython'] = os.uname().release
except Exception as e:
    d['err_os'] = repr(e)
try:
    import ttboard
    d['sdk'] = ttboard.VERSION
    d['sdk_revision'] = getattr(ttboard, 'REVISION', None)
except Exception as e:
    d['err_sdk'] = repr(e)
try:
    from ttboard.boot.demoboard_detect import DemoboardDetect as D
    d['demoboard'] = D.PCB_str()
    d['carrier_present'] = D.CarrierPresent
    d['carrier_version'] = D.CarrierVersion
except Exception as e:
    d['err_demoboard'] = repr(e)
try:
    t = globals().get('tt')
    d['rom'] = None
    d['rom_cached'] = False
    if t is None:
        d['err_rom'] = 'tt not defined'
    else:
        sp = getattr(t.shuttle, '_shuttle_props', None)
        if sp is None:
            d['err_rom'] = 'chip ROM not read by the boot'
        elif getattr(sp, '_contents', None) is not None:
            d['rom'] = dict(sp._contents)
            d['rom_text'] = getattr(sp, '_rom_data', None)
            d['rom_cached'] = True
        elif hasattr(sp, '_shuttle'):
            d['rom'] = {'shuttle': sp._shuttle, 'repo': sp._repo, 'commit': sp._commit}
            d['rom_cached'] = True
            d['rom_forced'] = True
        else:
            d['err_rom'] = 'chip ROM not read by the boot'
except Exception as e:
    d['err_rom'] = repr(e)
print(json.dumps(d))
"""

RAW_REPL_BANNER = b"raw REPL; CTRL-B to exit\r\n>"


class Tty:
    """A raw tty with a read buffer and a deadline on every read."""

    def __init__(self, fd):
        self.fd = fd
        self.buf = b""

    def _fill(self, until):
        """Read what is there before `until` (a monotonic time); False at
        the deadline or when the device is gone."""
        remaining = until - time.monotonic()
        if remaining <= 0:
            return False
        r, _, _ = select.select([self.fd], [], [], min(remaining, 0.5))
        if not r:
            return True
        try:
            chunk = os.read(self.fd, 4096)
        except OSError:
            return False
        if not chunk:
            return False
        self.buf += chunk
        return True

    def read_until(self, marker, deadline):
        """Bytes up to and including `marker` (consumed from the buffer),
        or None at the deadline."""
        while True:
            i = self.buf.find(marker)
            if i >= 0:
                got, self.buf = self.buf[:i + len(marker)], self.buf[i + len(marker):]
                return got
            if not self._fill(deadline):
                return None

    def drain(self, seconds):
        """Discard whatever arrives in the next `seconds`."""
        end = time.monotonic() + seconds
        while self._fill(end):
            pass
        self.buf = b""

    def write(self, data, deadline):
        """Write all of `data` before `deadline`: 256-byte chunks with a
        pause, as pyboard.py does, so the board's USB CDC buffer keeps up
        with a pasted snippet, and each chunk retried on a short write or
        EAGAIN (the fd is non-blocking). OSError at the deadline."""
        for i in range(0, len(data), 256):
            chunk = data[i:i + 256]
            while chunk:
                if time.monotonic() > deadline:
                    raise OSError("timed out writing to the board")
                try:
                    n = os.write(self.fd, chunk)
                except (BlockingIOError, InterruptedError):
                    n = 0
                if n:
                    chunk = chunk[n:]
                else:
                    select.select([], [self.fd], [], 0.05)
            time.sleep(0.01)


def open_tty(path):
    """The tty as a raw 115200 8N1 file descriptor, held exclusively
    (TIOCEXCL, as pyserial does) so a terminal on it is noticed."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attr = termios.tcgetattr(fd)
        attr[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
                     termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
        attr[1] &= ~termios.OPOST
        attr[2] &= ~(termios.CSIZE | termios.PARENB)
        attr[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
        attr[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG |
                     termios.IEXTEN)
        # 115200 is nominal (USB CDC ignores it); never B1200: MicroPython's
        # rp2 builds treat a 1200 bps touch as reset-to-BOOTSEL
        attr[4] = attr[5] = termios.B115200
        attr[6][termios.VMIN] = 0
        attr[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attr)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except Exception:
        os.close(fd)
        raise
    return fd


def port_holder(tty):
    """"<command> (pid N)" for a process holding `tty` open, else None.

    Only processes this user owns are readable in /proc, which is the case
    that matters: a bridge or console service running as the same user is
    what usually has the port, and it swallows the REPL's answers so the
    board looks mute. Anything unreadable is skipped, not guessed at.
    """
    for proc in glob.glob(ROOT + "/proc/[0-9]*"):
        try:
            for fd in glob.glob(proc + "/fd/*"):
                if os.path.realpath(fd) != tty:
                    continue
                comm = read(proc + "/comm") or "?"
                return "%s (pid %s)" % (comm, os.path.basename(proc))
        except OSError:
            continue
    return None


def raw_repl_exec(fd, code, timeout):
    """Run `code` on a MicroPython board over its raw REPL and return
    (stdout, stderr) as text; raises OSError naming the stage that failed.

    Ctrl-C twice interrupts a running program, Ctrl-A enters the raw REPL
    (the board answers with a banner), the code followed by Ctrl-D runs
    it (the board answers "OK", the output, Ctrl-D, any traceback,
    Ctrl-D, ">"), and Ctrl-B returns to the friendly REPL.
    """
    deadline = time.monotonic() + timeout
    t = Tty(fd)
    t.write(b"\r\x03\x03", deadline)
    t.drain(0.3)
    t.write(b"\r\x01", deadline)
    if t.read_until(RAW_REPL_BANNER, deadline) is None:
        raise OSError("no raw REPL prompt (not MicroPython, or busy)")
    t.write(code.encode("utf-8") + b"\x04", deadline)
    if t.read_until(b"OK", min(deadline, time.monotonic() + 2)) is None:
        raise OSError("board did not accept the snippet")
    out = t.read_until(b"\x04", deadline)
    if out is None:
        raise OSError("timed out waiting for the snippet's output")
    err = t.read_until(b"\x04", deadline)
    if err is None:
        raise OSError("timed out waiting for the snippet to finish")
    t.write(b"\r\x02", deadline)
    return (out[:-1].decode("utf-8", "replace"), err[:-1].decode("utf-8", "replace"))


def read_repl(tty, timeout=10):
    """Ask the SDK on the board what it holds. Always returns a dict: the
    board's answer (see REPL_SNIPPET), or {"error": why}."""
    def blamed(what):
        """`what` went wrong, and who else has the port if anyone does."""
        holder = port_holder(tty)
        if holder:
            return {"error": "%s; %s has the port open" % (what, holder), "holder": holder}
        return {"error": str(what)}

    try:
        fd = open_tty(tty)
    except (OSError, termios.error) as e:      # termios.error is not an OSError
        # EBUSY here is the usual one: a bridge or console service already
        # holds the port, often exclusively (pyserial does that by default).
        return blamed("cannot open %s: %s" % (tty, e))
    try:
        out, err = raw_repl_exec(fd, REPL_SNIPPET, timeout)
    except OSError as e:
        return blamed(e)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if err.strip():
        return {"error": "snippet raised: " + err.strip()[-200:]}
    start = out.find("{")
    if start < 0:
        return {"error": "no JSON from the board: %r" % out[-200:]}
    try:
        return json.loads(out[start:])
    except ValueError:
        return {"error": "bad JSON from the board: %r" % out[-200:]}


# --- verdict ------------------------------------------------------------------

def tinytapeout_verdict(d):
    """Name the Tiny Tapeout board(s) on this Pi, from USB and the REPL."""
    boards = []
    for u in d["usb"]:
        if u["id"] != "2e8a:0005":
            continue
        answer = (d.get("repl") or {}).get(u["path"]) or {}
        if "sdk" not in answer:
            # A MicroPython board that answered without the SDK is some
            # other Pico: not listed. One that could not be asked might be
            # a Tiny Tapeout board, and is listed as a candidate.
            if answer and not answer.get("error"):
                continue
            why = ("REPL: " + answer["error"]) if answer else "REPL not read"
            boards.append({
                "kind": "rp2-micropython", "usb": u["path"], "usb_serial": u["serial"],
                "tty": u["tty"],
                "how": "MicroPython RP2 2e8a:0005 %s %s on USB %s; %s" % (
                    u["manufacturer"] or "", u["product"] or "", u["path"], why)})
            continue
        rom = answer.get("rom") or {}
        raw_shuttle = (rom.get("shuttle") or "").strip()
        machine = answer.get("machine") or ""
        mcu = None
        for name in ("RP2350", "RP2040"):
            if name in machine:
                mcu = name
        shuttle, chip = None, None
        if raw_shuttle.lower() == "fpga":
            chip = "fpga"
        elif raw_shuttle and raw_shuttle.lower() != "unknown":
            shuttle, chip = raw_shuttle.lower(), "asic"
        elif answer.get("carrier_present"):
            chip = "asic"
        info = shuttle_info(shuttle)
        demoboard = answer.get("demoboard")
        if demoboard in ("UNKNOWN", "N/A", ""):
            demoboard = None
        why = []
        if rom:
            why.append("chip ROM shuttle=%s%s" % (
                raw_shuttle or "?", " (forced in config.ini)" if answer.get("rom_forced") else ""))
        else:
            why.append("chip ROM not cached on the board")
        for key in ("err_rom", "err_demoboard"):
            if answer.get(key):
                why.append("%s: %s" % (key[4:], answer[key]))
        why.append("demo board %s" % (demoboard or "not detected"))
        entry = {
            "kind": "tinytapeout", "usb": u["path"], "usb_serial": u["serial"],
            "tty": u["tty"], "shuttle": shuttle, "chip": chip,
            "repo": rom.get("repo") or None, "commit": rom.get("commit") or None,
            "demoboard": demoboard, "demoboard_version": info["demoboard_version"],
            "sdk": answer.get("sdk"), "machine": machine or None, "mcu": mcu,
            "chip_url": info["url"],
            "how": "Tiny Tapeout SDK %s on %s (USB %s); %s" % (
                answer.get("sdk"), machine or "RP2", u["path"], "; ".join(why)),
        }
        boards.append(entry)
    return boards


def tinytapeout_summary(boards):
    """Only the identity keys, in a fixed shape, for the boards that are
    Tiny Tapeout boards."""
    out = []
    for b in boards:
        if b["kind"] != "tinytapeout":
            continue
        out.append({k: b.get(k) for k in (
            "usb_serial", "mcu", "shuttle", "chip", "repo", "commit", "demoboard",
            "demoboard_version", "sdk")})
    return out


def collect_tinytapeout(repl=True, timeout=10):
    t = {"usb": usb_candidates(), "repl": None}
    if repl:
        t["repl"] = {}
        for u in t["usb"]:
            if u["id"] != "2e8a:0005":
                continue
            if not u["tty"]:
                t["repl"][u["path"]] = {"error": "no tty for this device"}
                continue
            t["repl"][u["path"]] = read_repl(u["tty"], timeout)
    t["boards"] = tinytapeout_verdict(t)
    t["summary"] = tinytapeout_summary(t["boards"])
    return t


def merge_tinytapeout(doc, t):
    """Fold a tinytapeout document into a Pi probe document (in place)."""
    doc["tinytapeout"] = {k: t[k] for k in ("usb", "repl")}
    doc["verdict"]["tinytapeout"] = t["boards"]
    doc["verdict"]["summary"]["tinytapeout"] = t["summary"]
    return doc


def describe(boards):
    for b in boards:
        if b["kind"] == "tinytapeout":
            what = shuttle_short(b["shuttle"]) if b["shuttle"] else "shuttle not read"
            if b["chip"] == "fpga":
                what = "FPGA breakout"
            print("  tt     : %s%s (%s)" % (
                what, (" on demo board " + b["demoboard"]) if b["demoboard"] else "",
                b["how"]))
        else:
            print("  tt     : candidate (%s)" % b["how"])
    if not boards:
        print("  tt     : none found")


def main():
    t = collect_tinytapeout(repl="--no-repl" not in sys.argv)
    if "--json" in sys.argv:
        print(json.dumps(t, indent=1))
        return
    describe(t["boards"])


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
