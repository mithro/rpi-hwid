#!/usr/bin/env python3
"""What radio an ESP32 433 MHz node has, asked of its own firmware.

The nodes esp32-to-433mhz builds (an ESP32-C3 SuperMini wired to a CC1101
or SX1278 board) run Tasmota's "cc1101-node" build, whose radio driver finds
the radio itself: at boot it tries each board's pin map in turn and prints
the one that answered,

    CC1: CC1101 PARTNUM 0x00 VERSION 0x14, SCK=3 MISO=7 MOSI=4 CS=1 GDO0=10 GDO2=6
    CC1: SX1278 present, RegVersion 0x12, SCK=.. MISO=.. MOSI=.. NSS=.. RST=.. DIO0=..
    CC1: no CC1101 on any board map (last PARTNUM 0x00 VERSION 0x00) - check wiring

and on its console it answers ``Radio`` (which radio it drives),
``CcStatus`` (the CC1101's PARTNUM and VERSION registers) and ``SxStatus``
(the SX1278's RegVersion). Those are all a radio chip gives: neither has a
serial number. Which *board* carries the chip -- the blue Ebyte E07-M1101D,
the green D-Sun, the Ai-Thinker Ra-02 breakout -- is told by the pins it
answered on, because each board puts its signals on different header
positions; that is for the label module (rpi_hwid.esp32_433_micro), which
holds the boards' pin maps. This module only reads.

    rpi-hwid esp32 --radio /dev/radio-cc1101-blue

It is disruptive, once: the pin map is printed only at boot, so the read
resets the chip through the port's RTS line (DTR low), the way esptool's
hard reset does, keeps what the firmware prints for `listen` seconds, then
sends the three commands. HUPCL is cleared so that closing the port resets
nothing a second time. Tasmota counts a boot that ends within ten seconds
towards falling back to its safeboot image after a run of them, and the
listen outlasts that; run after ``--read`` on the same port, as the
collector does, the chip has already been up for the esptool read's own
listen before this reset comes.

Like rpi_hwid.esp32: stand-alone, stdlib-only, Python 3.5 grammar, and
embeddable in the probe that ``rpi-hwid collect`` sends over ssh. Only the
ports named are opened.
"""

import fcntl
import json
import os
import re
import select
import struct
import sys
import termios
import time

COMMANDS = ("Radio", "CcStatus", "SxStatus")
LOG_PREFIX = "CC1: "


class NoAnswerError(Exception):
    """The port answered with nothing a cc1101-node firmware says."""


# --- reading what the firmware says ------------------------------------------------

PIN_RE = re.compile(r"\b([A-Z][A-Z0-9]*)=(-?\d+)\b")


def parse_bringup(text):
    """The driver's bring-up lines in `text`: one dict per line naming a
    radio, whether it answered, its identity registers and its pins."""
    out = []
    for raw in (text or "").splitlines():
        i = raw.find(LOG_PREFIX)
        if i < 0:
            continue
        line = raw[i:].strip()
        chip = re.search(r"\b(CC1101|SX127[0-9])\b", line)
        version = re.search(r"\b(?:VERSION|RegVersion) (0x[0-9A-Fa-f]{2})", line)
        # a line that names the chip without its version register is the
        # driver saying how it set the chip up ("SX1278 FSK weather RX: ..."),
        # not the chip answering
        if not chip or not version:
            continue
        absent = bool(re.search(r"\bno (CC1101|SX127[0-9])\b", line))
        partnum = re.search(r"\bPARTNUM (0x[0-9A-Fa-f]{2})", line)
        out.append({
            "line": line, "chip": chip.group(1), "present": not absent,
            "partnum": partnum.group(1) if partnum else None,
            "version": version.group(1),
            "pins": {} if absent else dict((k, int(v)) for k, v in PIN_RE.findall(line)),
        })
    return out


def parse_answers(text):
    """Command -> the JSON the console answered it with. Tasmota prefixes an
    answer with the topic it would publish on (``stat/<topic>/RESULT = ``) or
    ``RSL: RESULT = `` when there is no MQTT; the JSON is the rest of the line."""
    out = {}
    for raw in (text or "").splitlines():
        i = raw.find("= {")
        if i < 0:
            continue
        try:
            value = json.loads(raw[i + 2:])
        except ValueError:
            continue
        if isinstance(value, dict):
            for k in COMMANDS:
                if isinstance(value.get(k), dict):
                    out[k] = value[k]
    return out


def firmware(text):
    """'15.5.0(cc1101-node)' from Tasmota's Project line, or None."""
    m = re.search(r"\bProject \S+ - .* Version (\S+?\([^)]*\))", text or "")
    return m.group(1) if m else None


def summarise(boot, answers):
    """The radio the node drives, from its bring-up line and its answers:
    chip, present, partnum, version, pins, firmware. The answers are what
    the driver holds now and win on anything both give; the bring-up line
    is the only place the pins are said. A node that answered neither way
    is not running the firmware, and that is an error."""
    lines = parse_bringup(boot)
    said = parse_answers(answers)
    if not lines and not said:
        raise NoAnswerError(
            "no radio bring-up line and no answer to %s: is the node running the "
            "cc1101-node firmware?" % ", ".join(COMMANDS))
    active = (said.get("Radio") or {}).get("Active")
    status = {"cc1101": said.get("CcStatus"), "sx1278": said.get("SxStatus")}
    chip, present, partnum, version, pins = None, False, None, None, {}
    for line in lines:
        if line["present"]:
            chip, present = line["chip"], True
            partnum, version, pins = line["partnum"], line["version"], line["pins"]
    if active:
        st = status.get(active.lower())
        if st is not None:
            present = bool(st.get("Present"))
            if present:
                if chip != active.upper():
                    pins = {}
                chip = active.upper()
                partnum = st.get("PARTNUM", partnum if chip == "CC1101" else None)
                version = st.get("VERSION", version)
    if not present:
        chip, partnum, version, pins = None, None, None, {}
    return {"chip": chip, "present": present, "partnum": partnum, "version": version,
            "pins": pins, "firmware": firmware(boot)}


# --- the port ----------------------------------------------------------------------


def open_port(port):
    """The port raw at 115200, non-blocking, with HUPCL cleared. Opening
    raises DTR and RTS together, which resets nothing."""
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        a = termios.tcgetattr(fd)
        a[0] = 0
        a[1] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[3] = 0
        a[4] = a[5] = termios.B115200
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
    except Exception:
        os.close(fd)
        raise
    return fd


def _modem(fd, bits, on):
    fcntl.ioctl(fd, termios.TIOCMBIS if on else termios.TIOCMBIC, struct.pack("I", bits))


def pulse_reset(fd):
    """RTS high with DTR low holds an ESP32 in reset -- on the chip's own
    USB-Serial-JTAG and on a devkit's two-transistor auto-reset alike --
    and RTS low lets it boot its application, as esptool's hard reset does."""
    _modem(fd, termios.TIOCM_DTR, False)
    _modem(fd, termios.TIOCM_RTS, True)
    time.sleep(0.1)
    _modem(fd, termios.TIOCM_RTS, False)


def drain(fd, secs, until=None):
    """What the port says for `secs`, or until `until(text)` is true."""
    buf = b""
    end = time.time() + secs
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            continue
        try:
            chunk = os.read(fd, 4096)
        except (BlockingIOError, InterruptedError):
            continue
        if not chunk:
            time.sleep(0.05)
            continue
        buf += chunk
        if until is not None and until(buf.decode("utf-8", "replace")):
            break
    return buf.decode("utf-8", "replace")


def read_radio(port, listen=12.0, answer_wait=3.0):
    """Reset the node on `port`, keep its boot, ask it the commands:
    {port, radio (see summarise) or None, error, boot, answers}."""
    out = {"port": port, "tty": os.path.realpath(port), "radio": None, "error": None,
           "boot": "", "answers": ""}
    try:
        fd = open_port(port)
    except OSError as exc:
        out["error"] = "cannot open %s: %s" % (port, exc)
        return out
    try:
        drain(fd, 0.2)          # whatever was waiting from before
        pulse_reset(fd)
        out["boot"] = drain(fd, listen)
        answers = ""
        for cmd in COMMANDS:
            os.write(fd, ("\r\n" + cmd + "\r\n").encode("ascii"))
            answers += drain(fd, answer_wait,
                             until=lambda t, cmd=cmd: cmd in parse_answers(t))
        out["answers"] = answers
        out["radio"] = summarise(out["boot"], answers)
    except (OSError, NoAnswerError) as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        os.close(fd)
    # the whole boot is evidence, but not all of it: the start holds the
    # bring-up line, and a chatty node can print a lot in the listen
    out["boot"] = out["boot"][:6000]
    return out


def collect_radios(ports=()):
    """A read of each port named, in turn."""
    return [read_radio(p) for p in ports]


def merge_radios(doc, reads):
    """Hang each read's radio on the ESP32 found on the same tty (in place):
    verdict.esp32[i].radio, with the read's error beside it when it failed,
    and every read kept as evidence under esp32.radio_reads."""
    for r in reads:
        for d in (doc.get("verdict") or {}).get("esp32") or ():
            if d.get("tty") == r["tty"]:
                d["radio"] = r["radio"]
                if r["error"]:
                    d["radio_error"] = r["error"]
    doc.setdefault("esp32", {})["radio_reads"] = reads
    return doc


def describe(reads):
    for r in reads:
        radio = r["radio"]
        if r["error"]:
            print("  radio  : %s: read failed: %s" % (r["port"], r["error"]))
        elif not radio["present"]:
            print("  radio  : %s: none (firmware %s)" % (r["port"], radio["firmware"]))
        else:
            pins = " ".join("%s=%d" % kv for kv in sorted(radio["pins"].items()))
            print("  radio  : %s: %s version %s%s  %s" % (
                r["port"], radio["chip"], radio["version"],
                " partnum %s" % radio["partnum"] if radio["partnum"] else "", pins))


def main():
    args = sys.argv[1:]
    ports = [args[i + 1] for i, a in enumerate(args) if a == "--radio" and i + 1 < len(args)]
    reads = collect_radios(ports)
    if "--json" in args:
        print(json.dumps(reads, indent=1))
        return
    describe(reads)


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
