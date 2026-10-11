#!/usr/bin/env python3
"""What GPS receiver an ESP32 GPS node has, asked of its own firmware.

The nodes esp32-to-gps builds (an ESP32-C3 SuperMini wired to a GPS
receiver board: the u-blox 7 GT-U7, a u-blox MAX-M10S breakout, the Quectel
LC29H(AA) board or the u-blox LEA-M8T card) run Tasmota's "gps" build. Its
driver finds the receiver itself the first time -- the speed, then UBX
MON-VER, then Quectel's $PQTMVERNO -- remembers it, and logs

    GPS: identified u-blox M10 (m10) at 38400 baud

and on its console it answers ``GpsConfig`` (the receiver type it remembers)
and ``GpsStatus`` (everything it knows, with the receiver's own model,
firmware, hardware and protocol strings under ``Receiver``). Those strings
are what the receiver reports about itself: a u-blox part its MON-VER, the
LC29H its $PQTMVERNO. None carries a serial number. The board the receiver
sits on is not reported either (a MAX-M10S's MON-VER has no MOD= extension),
so it is not guessed here.

    rpi-hwid esp32 --gps /dev/gps-max-m10s

Unlike the 433 MHz node's read (rpi_hwid.esp32_radio) this one resets
nothing: the firmware answers both commands whenever it is asked, so the
port is opened with DTR and RTS raised together, which an ESP32's
USB-Serial-JTAG takes as no reset, the commands are sent, and HUPCL is
cleared so that closing the port resets nothing either. ``Status 2`` is
asked as well, for the Tasmota build's version, which says it is the gps
build.

Like rpi_hwid.esp32: stand-alone, stdlib-only, Python 3.5 grammar, and
embeddable in the probe that ``rpi-hwid collect`` sends over ssh. Only the
ports named are opened. Its helpers are named apart from esp32_radio's,
because the probe embeds both modules in one script.
"""

import json
import os
import select
import sys
import termios
import time

GPS_COMMANDS = (("GpsConfig", "GpsConfig"), ("GpsStatus", "GpsStatus"),
                ("Status 2", "StatusFWR"))


class NoGpsAnswerError(Exception):
    """The port answered with nothing an esp32-to-gps firmware says."""


# --- reading what the firmware says ------------------------------------------------


def parse_gps_answers(text):
    """Answer key -> the JSON the console answered with. Tasmota prefixes an
    answer with the topic it would publish on (``stat/<topic>/RESULT = ``)
    or ``RSL: RESULT = `` when there is no MQTT; the JSON is the rest of the
    line."""
    out = {}
    keys = [k for _cmd, k in GPS_COMMANDS]
    for raw in (text or "").splitlines():
        i = raw.find("= {")
        if i < 0:
            continue
        try:
            value = json.loads(raw[i + 2:])
        except ValueError:
            continue
        if isinstance(value, dict):
            for k in keys:
                if isinstance(value.get(k), dict):
                    out[k] = value[k]
    return out


def summarise_gps(answers):
    """The receiver the node drives, from its answers: module (the driver's
    name for its type: ublox7, m8, m10, lc29h, or None before it has found
    one), model, firmware, hardware, protocol, baud, and the Tasmota build
    the node runs. A node that answered neither GPS command is not running
    the esp32-to-gps firmware, and that is an error."""
    said = parse_gps_answers(answers)
    if "GpsConfig" not in said and "GpsStatus" not in said:
        raise NoGpsAnswerError(
            "no answer to GpsConfig or GpsStatus: is the node running the "
            "esp32-to-gps firmware?")
    module = ((said.get("GpsConfig") or {}).get("Remembered") or {}).get("Module")
    receiver = (said.get("GpsStatus") or {}).get("Receiver") or {}
    return {
        "module": None if module in (None, "none") else module,
        "model": receiver.get("Model"),
        "firmware": receiver.get("Firmware"),
        "hardware": receiver.get("Hardware"),
        "protocol": receiver.get("Protocol"),
        "baud": receiver.get("Baud"),
        "tasmota": (said.get("StatusFWR") or {}).get("Version"),
    }


# --- the port ----------------------------------------------------------------------


def gps_open_port(port):
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


def gps_drain(fd, secs, until=None):
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


def read_gps(port, answer_wait=3.0):
    """Ask the node on `port` the commands, without a reset:
    {port, tty, gps (see summarise_gps) or None, error, answers}."""
    out = {"port": port, "tty": os.path.realpath(port), "gps": None, "error": None,
           "answers": ""}
    try:
        fd = gps_open_port(port)
    except OSError as exc:
        out["error"] = "cannot open %s: %s" % (port, exc)
        return out
    try:
        gps_drain(fd, 0.2)      # whatever was waiting from before
        answers = ""
        for cmd, key in GPS_COMMANDS:
            os.write(fd, ("\r\n" + cmd + "\r\n").encode("ascii"))
            answers += gps_drain(fd, answer_wait,
                                 until=lambda t, key=key: key in parse_gps_answers(t))
        out["answers"] = answers
        out["gps"] = summarise_gps(answers)
    except (OSError, NoGpsAnswerError) as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    finally:
        os.close(fd)
    # the answers are evidence, but a chatty node logs a lot in the wait
    out["answers"] = out["answers"][:8000]
    return out


def collect_gps(ports=()):
    """A read of each port named, in turn."""
    return [read_gps(p) for p in ports]


def merge_gps(doc, reads):
    """Hang each read's receiver on the ESP32 found on the same tty (in
    place): verdict.esp32[i].gps, with the read's error beside it when it
    failed, and every read kept as evidence under esp32.gps_reads."""
    for r in reads:
        for d in (doc.get("verdict") or {}).get("esp32") or ():
            if d.get("tty") == r["tty"]:
                d["gps"] = r["gps"]
                if r["error"]:
                    d["gps_error"] = r["error"]
    doc.setdefault("esp32", {})["gps_reads"] = reads
    return doc


def describe_gps(reads):
    for r in reads:
        gps = r["gps"]
        if r["error"]:
            print("  gps    : %s: read failed: %s" % (r["port"], r["error"]))
        elif not gps["module"]:
            print("  gps    : %s: no receiver found yet (firmware %s)" % (
                r["port"], gps["tasmota"]))
        else:
            print("  gps    : %s: %s firmware %s hardware %s (%s, %s baud)" % (
                r["port"], gps["model"], gps["firmware"], gps["hardware"],
                gps["module"], gps["baud"]))


def main():
    args = sys.argv[1:]
    ports = [args[i + 1] for i, a in enumerate(args) if a == "--gps" and i + 1 < len(args)]
    reads = collect_gps(ports)
    if "--json" in args:
        print(json.dumps(reads, indent=1))
        return
    describe_gps(reads)


if __name__ == "__main__" and not globals().get("RPI_HWID_EMBEDDED"):
    main()
