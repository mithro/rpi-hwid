"""Run the probe on many Pis over ssh and keep one JSON file per host.

The probe (``rpi_hwid.probe``) is fed to ``python3 -`` on the host over
stdin, so nothing is installed there; it wants passwordless sudo for the
Pi's firmware tools (vcgencmd). It reads the 40-pin header straight from
/dev/i2c-* (the i2c group), leaving those pins to their driver, or, where a
bus has no node, by driving its pins through /dev/gpiochip* (the gpio
group), for which it also wants passwordless sudo: to read the pins' state
from debugfs (`sudo -n cat`) and to put them back (`sudo -n pinctrl set`). Each host is
tried with each user in turn, and a host that no user can reach is
reported, never skipped silently.

    rpi-hwid collect --out data/ rpi5-netv2 pi@10.21.1.10 -J welland.fpgas.online
    rpi-hwid collect --out data/ --fpga --jtag rpi5-netv2      # with the FPGA module
    rpi-hwid collect --out data/ --tinytapeout rpiz-tt          # with the Tiny Tapeout module

The output files are the input to ``rpi-hwid labels``.
"""

from __future__ import annotations

import getpass
import json
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

from rpi_hwid import label_input
from rpi_hwid.model import ProbeDocument

DEFAULT_USERS = (getpass.getuser(), "pi")


def probe_source(
    fpga: bool = False, jtag: bool = False, flash: bool = False, tinytapeout: bool = False,
    take_port: bool = True, esp32: bool = False, esp32_read: Sequence[str] = (),
    esp32_radio: Sequence[str] = (), force_offline: bool = False, read_tt_rom: bool = False,
    esp32_gps: Sequence[str] = (),
    sdr: bool = False, sdr_open: bool = False,
) -> str:
    """The script to feed to ``python3 -`` on a host.

    The Pi probe alone, or the Pi probe followed by the FPGA module, the
    Tiny Tapeout module or both, and a few lines that run them all and
    merge the extra findings into the Pi document. Every file skips its
    own ``main()`` when embedded.
    """
    pkg = resources.files("rpi_hwid")
    probe = pkg.joinpath("probe.py").read_text()
    if not fpga and not tinytapeout and not esp32 and not sdr:
        return probe
    extra = ""
    glue = "\n\n_doc = collect()\n_doc['verdict'] = verdict(_doc)\n"
    if fpga:
        extra += "\n" + pkg.joinpath("fpga.py").read_text()
        # force_offline written out only when asked, so the script a host is
        # sent otherwise reads the same as it always has
        args = f"{jtag!r}, {flash!r}" + (", True" if force_offline else "")
        glue += f"merge_fpga(_doc, collect_fpga({args}))\n"
    if tinytapeout:
        extra += "\n" + pkg.joinpath("tinytapeout.py").read_text()
        # Written out only when it differs from the module's default, so the
        # script a host is sent reads the same as it always has otherwise.
        kwargs = ", ".join((["take_port=False"] if not take_port else [])
                           + (["read_rom=True"] if read_tt_rom else []))
        call = f"collect_tinytapeout({kwargs})"
        glue += f"merge_tinytapeout(_doc, {call})\n"
    if esp32:
        # the ESP32 module: the USB tree, and a reset-and-read of the ports
        # named for this host only
        extra += "\n" + pkg.joinpath("esp32.py").read_text()
        glue += f"merge_esp32(_doc, collect_esp32({list(esp32_read)!r}))\n"
    if esp32_radio:
        # the radio a 433 MHz node's firmware drives: after the esptool read,
        # which resets the same chips and must find them first
        extra += "\n" + pkg.joinpath("esp32_radio.py").read_text()
        glue += f"merge_radios(_doc, collect_radios({list(esp32_radio)!r}))\n"
    if esp32_gps:
        # the receiver a GPS node's firmware drives: asked without a reset,
        # after the reads that do reset the same chips
        extra += "\n" + pkg.joinpath("esp32_gps.py").read_text()
        glue += f"merge_gps(_doc, collect_gps({list(esp32_gps)!r}))\n"
    if sdr:
        # the radios: sysfs and a Pluto's network IIO context, nothing opened
        extra += "\n" + pkg.joinpath("sdr.py").read_text()
        # opening a free usdr card is its own opt-in, written out only when asked
        glue += ("merge_sdr(_doc, collect_sdr(open_radios=True))\n" if sdr_open
                 else "merge_sdr(_doc, collect_sdr())\n")
    glue += "print(json.dumps(_doc, indent=1))\n"
    return "RPI_HWID_EMBEDDED = True\n" + probe + extra + glue


# The ssh timeout for a probe that reads nothing disruptive: the Pi probe
# and whatever modules are appended to it.
BASE_TIMEOUT = 180


def probe_timeout(base: int = BASE_TIMEOUT, esp32_read: Sequence[str] = (),
                  esp32_radio: Sequence[str] = (), esp32_gps: Sequence[str] = ()) -> int:
    """The ssh timeout for one host's probe: `base`, and on top of it the
    longest each read asked for of it may take, since they run one after
    another on the host. Each read gives up on its own before then and
    says so in the document, so a slow chip costs that read, never the
    whole host's record. (rpiz-gps, a Pi Zero W, took 191 s to read three
    ESP32-C3s with esptool and ask each a few console questions; the flat
    180 s this replaces killed that probe and lost the whole record.)"""
    from rpi_hwid import esp32
    from rpi_hwid import esp32_gps as gps
    from rpi_hwid import esp32_radio as radio

    read = esp32.ESPTOOL_CHECK_TIMEOUT + esp32.READ_TIMEOUT
    asked = (radio.SETTLE + radio.RESET_PULSE + radio.LISTEN
             + len(radio.COMMANDS) * radio.ANSWER_WAIT)
    # a GPS node is not reset, so there is no boot to listen to: only its answers
    fixed = gps.GPS_SETTLE + len(gps.GPS_COMMANDS) * gps.GPS_ANSWER_WAIT
    return (base + len(esp32_read) * read + math.ceil(len(esp32_radio) * asked)
            + math.ceil(len(esp32_gps) * fixed))


@dataclass
class Result:
    """One host's outcome: a document, or the reason there is none."""

    host: str
    ok: bool
    user: str = ""
    doc: ProbeDocument | None = None
    error: str = ""


def probe_host(
    host: str,
    users: Sequence[str] = DEFAULT_USERS,
    jump: str | None = None,
    fpga: bool = False,
    jtag: bool = False,
    flash: bool = False,
    timeout: int | None = None,
    tinytapeout: bool = False,
    take_port: bool = True,
    esp32: bool = False,
    esp32_read: Sequence[str] = (),
    esp32_radio: Sequence[str] = (),
    force_offline: bool = False,
    esp32_gps: Sequence[str] = (),
    sdr: bool = False,
    sdr_open: bool = False,
    read_tt_rom: bool = False,
) -> Result:
    """Run the probe on one host; `host` may carry its own ``user@``.
    `timeout` is the ssh timeout; by default ``probe_timeout()`` of the
    reads asked for."""
    if timeout is None:
        timeout = probe_timeout(BASE_TIMEOUT, esp32_read, esp32_radio, esp32_gps)
    source = probe_source(fpga, jtag, flash, tinytapeout, take_port, esp32=esp32,
                          esp32_read=esp32_read, esp32_radio=esp32_radio,
                          force_offline=force_offline, esp32_gps=esp32_gps,
                          sdr=sdr or sdr_open, sdr_open=sdr_open, read_tt_rom=read_tt_rom)
    args = ["--json"]
    if "@" in host:
        user_list: Sequence[str] = [host.split("@", 1)[0]]
        target = host.split("@", 1)[1]
    else:
        user_list, target = users, host
    last = ""
    for user in user_list:
        cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=accept-new"]
        if jump:
            cmd += ["-J", jump]
        cmd += [f"{user}@{target}", "python3 - " + " ".join(args)]
        try:
            r = subprocess.run(cmd, input=source, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(host, False, error=f"timed out after {timeout} s")
        if r.returncode == 0:
            try:
                doc = ProbeDocument.from_json(target, r.stdout)
            except ValueError as exc:
                return Result(host, False, user, error=str(exc))
            doc.collected_by = user
            doc.evidence["_collected"] = {"host": host, "user": user}
            return Result(host, True, user, doc)
        last = r.stderr.strip()[-200:]
        if "Permission denied" not in last and "Connection closed" not in last:
            break
    return Result(host, False, error=last or "no user could log in")


def esp32_reads(hosts: Sequence[str], pairs: Sequence[str],
                flag: str = "--esp32-read") -> dict[str, list[str]]:
    """`--esp32-read HOST=PORT` pairs (or `flag`'s) as host -> ports, keyed on the host
    exactly as it was given for collection. HOST may be written with or
    without its ``user@``; a HOST that is not being collected is an error,
    because a read that silently does nothing looks just like one that found
    nothing to read."""
    out: dict[str, list[str]] = {}
    for pair in pairs:
        h, sep, port = pair.partition("=")
        if not sep or not h or not port:
            raise ValueError(f"{flag} wants HOST=PORT, not {pair!r}")
        out.setdefault(resolve_host(hosts, h, f"{flag} {pair}"), []).append(port)
    return out


def resolve_host(hosts: Sequence[str], h: str, what: str) -> str:
    """The host in `hosts` that `h` names, with or without its ``user@``;
    ValueError for one that is not being collected, or that matches more
    than one, since an option that silently does nothing looks just like
    one that found nothing."""
    def bare(x: str) -> str:
        return x.split("@", 1)[-1]

    matches = [x for x in hosts if x == h] or [x for x in hosts if bare(x) == bare(h)]
    if len(matches) != 1:
        raise ValueError(
            f"{what}: {h} is not one of the hosts being collected ({', '.join(hosts)})"
            if not matches else
            f"{what}: {h} matches more than one host ({', '.join(matches)})")
    return matches[0]


def offline_read_problem(doc: ProbeDocument) -> str | None:
    """What went wrong with a --force-offline TraceID read, or None.

    ``rpi-hwid fpga --force-offline`` says these on the terminal; a collect
    has to say them too, above all a board left offline, its capture stopped
    and its TARGET port perhaps unpowered until someone puts it back."""
    read = (doc.evidence.get("fpga") or {}).get("cynthion_jtag")
    if not read:
        return "no Cynthion with a flash uid answered, so no TraceID was read"
    if not read.get("restored"):
        return ("THE ANALYZER DID NOT COME BACK. Recover with: "
                "rpi-hwid fpga --recover-cynthion")
    if read.get("error"):
        return str(read["error"])
    return None


def collect(
    hosts: Sequence[str],
    out_dir: Path,
    users: Sequence[str] = DEFAULT_USERS,
    jump: str | None = None,
    fpga: bool = False,
    jtag_hosts: Sequence[str] = (),
    flash_hosts: Sequence[str] = (),
    workers: int = 4,
    tinytapeout: bool = False,
    take_port: bool = True,
    esp32: bool = False,
    esp32_read: Sequence[str] = (),
    esp32_radio: Sequence[str] = (),
    force_offline_hosts: Sequence[str] = (),
    esp32_gps: Sequence[str] = (),
    sdr: bool = False,
    sdr_open_hosts: Sequence[str] = (),
    base_timeout: int = BASE_TIMEOUT,
    read_tt_rom_hosts: Sequence[str] = (),
) -> list[Result]:
    """Probe every host and write ``<out_dir>/<host>.json`` for each success.

    `fpga` appends the FPGA module for every host; `jtag_hosts` and
    `flash_hosts` name the hosts whose JTAG may be driven (and whose Arty
    flash may be read, which reloads the FPGA). `tinytapeout` appends the
    Tiny Tapeout module for every host (its REPL read interrupts whatever
    a demo board is running). `take_port` False leaves a service that holds
    a demo board's port running, where the module would otherwise stop it for
    the read -- only fpgas-tt.service, and only on a Raspberry Pi.
    `esp32` appends the ESP32 module for every host (the USB tree only, from
    sysfs); `esp32_read` is HOST=PORT pairs, the ports whose chip may be reset
    and read on that host, and `esp32_radio` the same for the ports whose
    433 MHz node firmware may be reset and asked what radio it drives, and
    `esp32_gps` the ports whose GPS node firmware may be asked (no reset)
    what receiver it drives.
    `force_offline_hosts` are the hosts whose Cynthion is taken offline to
    read its ECP5 TraceID: its capture stops for a few seconds and its TARGET
    port may lose power, so only on the hosts named. `base_timeout` is each
    host's ssh timeout before the reads asked of it are allowed for
    (``probe_timeout``). `read_tt_rom_hosts` are the hosts whose Tiny Tapeout chip ROM is read by
    the probe where the board's SDK would not trust it (a TT04): it selects
    project 0 and drives ui_in, so only on the hosts named.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    reads = esp32_reads(hosts, esp32_read)
    radios = esp32_reads(hosts, esp32_radio, "--esp32-radio")
    gpses = esp32_reads(hosts, esp32_gps, "--esp32-gps")

    def one(host: str) -> Result:
        offline = host in force_offline_hosts
        return probe_host(host, users, jump, fpga or host in jtag_hosts or offline,
                          host in jtag_hosts, host in flash_hosts,
                          tinytapeout=tinytapeout or host in read_tt_rom_hosts,
                          take_port=take_port,
                          esp32=esp32 or host in reads or host in radios or host in gpses,
                          esp32_read=reads.get(host, ()), esp32_radio=radios.get(host, ()),
                          force_offline=offline, esp32_gps=gpses.get(host, ()),
                          sdr=sdr, sdr_open=host in sdr_open_hosts,
                          read_tt_rom=host in read_tt_rom_hosts,
                          timeout=probe_timeout(base_timeout, reads.get(host, ()),
                                                radios.get(host, ()), gpses.get(host, ())))

    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, h): h for h in hosts}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            if res.ok and res.doc is not None:
                name = res.doc.host.replace("/", "_")
                (out_dir / f"{name}.json").write_text(res.doc.to_json())
    return sorted(results, key=lambda r: r.host)


def load_collected(data_dir: Path) -> dict[str, ProbeDocument]:
    """Every ``*.json`` under `data_dir` as host -> document: a probe
    document, or a label input (``rpi_hwid.label_input``), keyed on the
    file's name either way."""
    docs: dict[str, ProbeDocument] = {}
    for path in sorted(data_dir.glob("*.json")):
        text = path.read_text()
        try:
            raw = json.loads(text)
        except ValueError:
            raw = None  # a login banner before a probe document, say
        if label_input.is_label_input(raw):
            try:
                docs[path.stem] = label_input.to_probe_document(raw)
            except label_input.InputError as exc:
                raise ValueError(f"{path}: {exc}") from exc
        else:
            docs[path.stem] = ProbeDocument.from_json(path.stem, text)
    if not docs:
        raise ValueError(f"no probe documents (*.json) in {data_dir}")
    return docs
