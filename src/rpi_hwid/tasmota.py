"""Read Tasmota devices over their HTTP API, and nothing but read them.

A Tasmota smart plug is not an ssh host, so it is not probed the way a Pi
is: its identity is asked for over the same HTTP API its web page uses,
``http://<ip>/cm?cmnd=<command>``. The devices themselves are found in
the IoT sheet gdoc2netcfg keeps (its published CSV, or the copy in its
``.cache``): every row whose name starts ``tasmota-`` or whose hardware
column says Tasmota.

These plugs power real equipment -- switches, routers, the hosts other
labels are for -- and the same API that reads a plug also switches it,
restarts it and rewrites its firmware. So the collector can only send what
is on an allowlist of exact command strings, each of which only reads:

    Status 0     every status section: firmware, chip, flash, network
    Module       the module in use, with no value: shows it, sets nothing
    Template     likewise the template, whose NAME is the device's model

and fetch only the Information page, ``/in``, which carries the ESP chip
id as the device itself reports it. Anything else -- Power, Restart,
Upgrade, Backlog, a SetOption, a Module or Template with a value, a
read-only command with anything appended -- raises ``NotReadOnlyError``
before a URL exists. A device that does not answer is recorded as such
and not asked again.

A web password, where a device has one, comes from the caller's
environment (``TASMOTA_WEB_PASSWORD``) at run time and is never written
into a document or an error.

    rpi-hwid tasmota --sheet iot.csv --site welland=1 --out data/tasmota
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import http.client
import io
import ipaddress
import json
import os
import re
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rpi_hwid.model import ProbeDocument

if TYPE_CHECKING:
    from collections.abc import Sequence


READ_ONLY_COMMANDS = frozenset({"Status 0", "Module", "Template"})
READ_ONLY_PAGES = frozenset({"/in"})

# Tasmota's web user is always "admin"; only the password is configurable.
WEB_USER = "admin"


class NotReadOnlyError(ValueError):
    """A command or page that is not on the read-only allowlist."""


def _host(ip: str) -> str:
    try:
        return str(ipaddress.IPv4Address(ip))
    except ValueError:
        raise ValueError(f"{ip!r} is not an IPv4 address") from None


def _auth(password: str | None) -> str:
    if not password:
        return ""
    return "&" + urllib.parse.urlencode({"user": WEB_USER, "password": password})


def command_url(ip: str, cmnd: str, password: str | None = None) -> str:
    """The URL that sends `cmnd` to the device at `ip`, for an allowlisted
    command only."""
    if cmnd not in READ_ONLY_COMMANDS:
        raise NotReadOnlyError(
            f"{cmnd!r} refused: only {', '.join(sorted(READ_ONLY_COMMANDS))} may be sent "
            "to a Tasmota device, because these plugs power real equipment")
    host = _host(ip)
    return f"http://{host}/cm?cmnd={urllib.parse.quote(cmnd)}{_auth(password)}"


def page_url(ip: str, path: str) -> str:
    """The URL of an allowlisted web page on the device at `ip`. (A page
    takes the web password as HTTP basic auth, not in the URL.)"""
    if path not in READ_ONLY_PAGES:
        raise NotReadOnlyError(
            f"page {path!r} refused: only {', '.join(sorted(READ_ONLY_PAGES))} may be "
            "fetched from a Tasmota device")
    return f"http://{_host(ip)}{path}"


# --- the devices, from gdoc2netcfg's IoT sheet ---------------------------------

SHEET_COLUMNS = ("Name", "MAC Address", "IP")


@dataclass(frozen=True)
class SheetDevice:
    """One Tasmota row of the IoT sheet, with its IP resolved for a site."""

    host: str                      # the Machine column, else the Name
    name: str                      # tasmota-D7C0E8-0232
    ip: str
    mac: str                       # lower case, colon separated
    site: str = ""
    hardware: str = ""
    device_id: str = ""            # as the sheet has it; not read from the device
    location: str = ""
    controls: tuple[str, ...] = ()


def normalise_mac(mac: str) -> str:
    """``7C:2C:67:D7:C0:E8``, ``7c-2c-...`` or ``7c2c67d7c0e8`` as
    ``7c:2c:67:d7:c0:e8``; anything else raises."""
    digits = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(digits) != 12 or re.search(r"[^0-9a-fA-F:.\-\s]", mac):
        raise ValueError(f"{mac!r} is not a MAC address")
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2)).lower()


def is_tasmota_row(name: str, hardware: str) -> bool:
    """A device gdoc2netcfg names ``tasmota-...``, or whose hardware column
    says it was flashed with Tasmota (the Sonoff bridges keep their ESP_ name)."""
    return name.lower().startswith("tasmota-") or "tasmota" in hardware.lower()


def sheet_devices(csv_text: str, sites: dict[str, int]
                  ) -> tuple[list[SheetDevice], list[tuple[str, str]]]:
    """The Tasmota devices in the IoT sheet's CSV, and (host, why) for each
    Tasmota row that cannot be reached. `sites` maps a site name to the
    second octet its ``10.X.`` addresses take; a row with no site is the
    first site's."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_at = next((i for i, r in enumerate(rows)
                      if all(c in [h.strip() for h in r] for c in SHEET_COLUMNS)), None)
    if header_at is None:
        raise ValueError(f"no header row with the columns {', '.join(SHEET_COLUMNS)} "
                         "-- is this gdoc2netcfg's IoT sheet?")
    header = [h.strip() for h in rows[header_at]]
    site_octets = {k.lower(): v for k, v in sites.items()}
    default_site = next(iter(sites), "")

    devices: list[SheetDevice] = []
    skipped: list[tuple[str, str]] = []
    for r in rows[header_at + 1:]:
        cell = dict(zip(header, (c.strip() for c in r), strict=False))
        name, hardware = cell.get("Name", ""), cell.get("Hardware", "")
        if not name or not is_tasmota_row(name, hardware):
            continue
        host = cell.get("Machine") or name
        site = cell.get("Site") or default_site
        octet = site_octets.get(site.lower())
        if octet is None:
            skipped.append((host, f"site {site} was not asked for "
                                  f"(--site {site.lower()}=OCTET)"))
            continue
        ip = cell.get("IP", "")
        if not ip:
            skipped.append((host, "no IP address in the sheet"))
            continue
        ip = ip.replace("X", str(octet))
        try:
            _host(ip)
            mac = normalise_mac(cell.get("MAC Address", ""))
        except ValueError as exc:
            skipped.append((host, f"the sheet's row is unusable: {exc}"))
            continue
        controls = tuple(c.strip() for c in re.split(r"[,\r\n]", cell.get("Controls", ""))
                         if c.strip())
        devices.append(SheetDevice(
            host=host, name=name, ip=ip, mac=mac, site=site, hardware=hardware,
            device_id=cell.get("Device ID", ""), location=cell.get("Physical Location", ""),
            controls=controls))
    return devices, skipped


# --- reading one device --------------------------------------------------------

Fetch = Callable[[str, float, Mapping[str, str]], bytes]

ENV_PASSWORD = "TASMOTA_WEB_PASSWORD"


def urllib_fetch(url: str, timeout: float, headers: Mapping[str, str]) -> bytes:
    """GET `url`; raises OSError (URLError is one) when there is no answer."""
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return bytes(resp.read())


class DeviceError(Exception):
    """A device that did not give the answer a document needs."""


@dataclass
class Result:
    """One device's outcome: a document, or the reason there is none."""

    host: str
    ip: str
    ok: bool
    doc: ProbeDocument | None = None
    error: str = ""


class _Reader:
    """The allowlisted reads of one device."""

    def __init__(self, dev: SheetDevice, fetch: Fetch, password: str | None,
                 timeout: float) -> None:
        self.dev, self.fetch, self.password, self.timeout = dev, fetch, password, timeout
        self.headers: dict[str, str] = {}
        if password:
            token = base64.b64encode(f"{WEB_USER}:{password}".encode()).decode()
            self.headers["Authorization"] = "Basic " + token

    def _get(self, url: str, what: str) -> bytes:
        try:
            return self.fetch(url, self.timeout, self.headers)
        except (OSError, http.client.HTTPException) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise DeviceError(f"no answer to {what} from {self.dev.ip}: {reason}") from None

    def command(self, cmnd: str) -> dict[str, Any]:
        body = self._get(command_url(self.dev.ip, cmnd, self.password), cmnd)
        try:
            data = json.loads(body)
        except ValueError:
            raise DeviceError(f"{self.dev.ip} answered {cmnd} with something that is "
                              f"not JSON: {body[:80]!r}") from None
        if not isinstance(data, dict):
            raise DeviceError(f"{self.dev.ip} answered {cmnd} with {data!r}")
        if "WARNING" in data and "password" in str(data["WARNING"]).lower():
            raise DeviceError(f"{self.dev.ip} wants its web password for {cmnd}: set "
                              f"{ENV_PASSWORD} in the environment")
        return data

    def page(self, path: str) -> str:
        return self._get(page_url(self.dev.ip, path), path).decode("utf-8", "replace")


def parse_info_page(page: str) -> dict[str, str]:
    """The Information page's table as label -> value. Tasmota sends it as one
    JavaScript string with ``}1`` between rows and ``}2`` between a row's
    label and its value, which the browser turns into a table."""
    m = re.search(r"<table[^>]*>(.*?)</td></tr></table>", page, re.S)
    if m is None:
        raise DeviceError("the Information page has no table")
    out: dict[str, str] = {}
    for row in m.group(1).split("}1"):
        label, sep, value = row.partition("}2")
        label = re.sub(r"<[^>]*>", "", label).strip()
        if sep and label:
            out[label] = html.unescape(value).strip()
    return out


def read_device(dev: SheetDevice, fetch: Fetch = urllib_fetch, password: str | None = None,
                timeout: float = 5.0) -> Result:
    """Read one device: Status 0, then Module, Template and the Information
    page, one request at a time. A device that does not answer Status 0 is
    asked nothing more; one of the other three failing is recorded in the
    document, and whether that costs the label is the label's business."""
    reader = _Reader(dev, fetch, password, timeout)
    try:
        status = reader.command("Status 0")
    except DeviceError as exc:
        return Result(dev.host, dev.ip, False, error=str(exc))
    raw: dict[str, Any] = {"status": status}
    errors: dict[str, str] = {}
    reads: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("module", lambda: reader.command("Module")),
        ("template", lambda: reader.command("Template")),
        ("info", lambda: parse_info_page(reader.page("/in"))),
    )
    for key, read in reads:
        try:
            raw[key] = read()
        except DeviceError as exc:
            errors[key] = str(exc)
    try:
        doc = document(dev, raw, errors)
    except DeviceError as exc:
        return Result(dev.host, dev.ip, False, error=str(exc))
    return Result(dev.host, dev.ip, True, doc)


# --- the document ---------------------------------------------------------------

# A module whose name says only which chip it is for: Tasmota's own generic
# modules, where the device's make and model are not known to the firmware.
GENERIC_MODULES = frozenset({"generic", "esp32c3", "esp32s2", "esp32s3", "esp32c6",
                             "esp32-devkit", "esp32", "esp8266"})


def _chip(hardware: str) -> tuple[str, str | None]:
    """``ESP32-C3 rev.4`` -> (``ESP32-C3``, ``rev.4``); ``ESP8266EX`` ->
    (``ESP8266EX``, None). The revision is kept as the firmware wrote it:
    Tasmota 13 says ``rev.4`` where Tasmota 15 says ``v0.4``."""
    chip, _, rev = hardware.strip().partition(" ")
    return chip, (rev.strip() or None)


def _flash_jedec(chip_id: str) -> str | None:
    """Tasmota's FlashChipId is the RDID answer read little-endian, so
    ``164020`` is manufacturer 0x20, type 0x40, capacity 0x16: the JEDEC
    id ``204016``."""
    try:
        v = int(chip_id, 16)
    except ValueError:
        return None
    return f"{v & 0xFF:02x}{(v >> 8) & 0xFF:02x}{(v >> 16) & 0xFF:02x}"


def _module(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    m = (raw.get("module") or {}).get("Module")
    if not isinstance(m, dict) or len(m) != 1:
        return None
    (mid, name), = m.items()
    return {"id": int(mid), "name": str(name)}


def document(dev: SheetDevice, raw: Mapping[str, Any], errors: Mapping[str, str]
             ) -> ProbeDocument:
    """The document for one device: what it answered, as it answered it,
    and a verdict drawn from that. It carries a probe document's summary so
    that ``rpi-hwid labels`` loads it beside the Pis; the summary names no
    board this package labels, so only the Tasmota micro label is drawn."""
    status = raw["status"]
    net = status.get("StatusNET") or {}
    try:
        mac = normalise_mac(str(net.get("Mac", "")))
    except ValueError:
        raise DeviceError(f"{dev.ip} answered Status 0 without a MAC in StatusNET "
                          f"({net.get('Mac')!r})") from None
    if mac != dev.mac:
        raise DeviceError(f"{dev.ip} answered as {mac}, but the sheet has {dev.mac} for "
                          f"{dev.host} there: another device has its address")
    fwr = status.get("StatusFWR") or {}
    mem = status.get("StatusMEM") or {}
    sts = status.get("StatusSTS") or {}
    hardware = str(fwr.get("Hardware") or "")
    chip, revision = _chip(hardware)
    module = _module(raw)
    template = raw.get("template") or {}
    template_name = template.get("NAME") if isinstance(template, dict) else None
    # Module 0 is the user template, whose NAME is the device's; any other
    # module is one Tasmota ships, named for the device (Sonoff S31) or, for
    # the generic ones, only for the chip.
    if module is None:
        model = None
    elif module["id"] == 0:
        model = template_name or module["name"]
    else:
        model = module["name"]
    generic = model is None or model.lower() in GENERIC_MODULES
    info = raw.get("info") or {}
    chip_id_text = info.get("ESP Chip Id", "")
    esp_chip_id = int(chip_id_text.split()[0]) if chip_id_text[:1].isdigit() else None
    flash_chip_id = str(mem.get("FlashChipId") or "") or None
    verdict = {
        "mac": mac,
        "model": model,
        "module": module,
        "template_name": template_name,
        "generic": generic,
        "hardware": hardware or None,
        "chip": chip or None,
        "chip_revision": revision,
        "esp_chip_id": esp_chip_id,
        "flash_chip_id": flash_chip_id,
        "flash_jedec": _flash_jedec(flash_chip_id) if flash_chip_id else None,
        "flash_size_kb": mem.get("FlashSize"),
        # a relay shows as a POWER (or POWER1, POWER2...) state; only how
        # many there are is kept, never whether they are on
        "relays": sum(1 for k in sts if re.fullmatch(r"POWER\d*", k)),
        "sheet": asdict(dev) | {"controls": list(dev.controls)},
        "read_errors": dict(errors),
    }
    summary = {
        "model": model or hardware or "Tasmota device", "serial": "", "revision": "",
        "power_class": "undetermined",
        "macs": [{"kind": "wlan", "mac": mac, "signal": "tasmota Status 0 StatusNET.Mac"}],
    }
    evidence = {
        "tasmota": {k: raw[k] for k in ("status", "module", "template", "info") if k in raw},
        "verdict": {"summary": summary, "tasmota": verdict},
        "_collected": {"host": dev.host, "ip": dev.ip, "user": "http", "via": "tasmota"},
    }
    return ProbeDocument.from_dict(dev.host, evidence)


def collect(devices: Sequence[SheetDevice], out_dir: Path, fetch: Fetch = urllib_fetch,
            password: str | None = None, workers: int = 4, timeout: float = 5.0
            ) -> list[Result]:
    """Read every device, a few at a time, and write ``<out_dir>/<host>.json``
    for each that answered. Each device is read once; one that does not
    answer is reported, not retried."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def one(dev: SheetDevice) -> Result:
        return read_device(dev, fetch, password, timeout)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(one, devices))
    for r in results:
        if r.ok and r.doc is not None:
            (out_dir / f"{r.host.replace('/', '_')}.json").write_text(r.doc.to_json())
    return sorted(results, key=lambda r: r.host)


# --- the command ------------------------------------------------------------------


def gdoc2netcfg_source(config: Path) -> tuple[str, dict[str, int]]:
    """The IoT sheet's published CSV URL and the site (name -> octet) from a
    gdoc2netcfg.toml. Only read: gdoc2netcfg's own ``fetch`` writes its cache,
    so the sheet is fetched here, into memory, instead."""
    with open(config, "rb") as f:
        cfg = tomllib.load(f)
    url = (cfg.get("sheets") or {}).get("iot")
    if not url:
        raise ValueError(f"{config} has no [sheets] iot URL")
    site = cfg.get("site") or {}
    sites = {site["name"]: int(site["site_octet"])} if "name" in site else {}
    return url, sites


def read_sheet(source: str) -> str:
    """The IoT sheet's CSV from a file, or from a URL (gdoc2netcfg's
    published one), fetched into memory and never written anywhere."""
    if re.match(r"https?://", source):
        with urllib.request.urlopen(source, timeout=60) as resp:
            return bytes(resp.read()).decode("utf-8")
    with open(source, encoding="utf-8") as f:
        return f.read()


def _site(text: str) -> tuple[str, int]:
    name, sep, octet = text.partition("=")
    if not sep or not name or not octet.isdigit() or not 0 <= int(octet) <= 255:
        raise argparse.ArgumentTypeError(f"--site wants NAME=OCTET (welland=1), not {text!r}")
    return name, int(octet)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="rpi-hwid tasmota",
        description="read Tasmota devices over HTTP (read-only), one JSON document each")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--sheet", metavar="CSV_OR_URL",
                     help="gdoc2netcfg's IoT sheet: its .cache/iot.csv or the published URL")
    src.add_argument("--gdoc2netcfg", type=Path, metavar="TOML",
                     help="a gdoc2netcfg.toml: fetch its [sheets] iot URL, for its [site]")
    ap.add_argument("--site", type=_site, action="append", default=[], metavar="NAME=OCTET",
                    help="a site and its second octet (10.X.); a row with no site is the "
                         "first one's. Repeat for more sites")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4,
                    help="devices read at once (default 4)")
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="seconds to wait for each answer (default 5)")
    ap.add_argument("hosts", nargs="*", help="only these devices (Machine or Name column)")
    args = ap.parse_args(argv)

    sites: dict[str, int] = {}
    source = args.sheet
    if args.gdoc2netcfg:
        source, sites = gdoc2netcfg_source(args.gdoc2netcfg)
    sites.update(dict(args.site))
    if not sites:
        ap.error("no site: give --site NAME=OCTET")
    devices, skipped = sheet_devices(read_sheet(source), sites)
    if args.hosts:
        wanted = set(args.hosts)
        known = {d.host for d in devices} | {d.name for d in devices} | {h for h, _ in skipped}
        missing = sorted(wanted - known)
        if missing:
            ap.error(f"not a Tasmota device in the sheet: {', '.join(missing)}")
        devices = [d for d in devices if d.host in wanted or d.name in wanted]
        skipped = [(h, why) for h, why in skipped if h in wanted]

    password = os.environ.get(ENV_PASSWORD) or None
    results = collect(devices, args.out, fetch=urllib_fetch, password=password,
                      workers=args.workers, timeout=args.timeout)
    failed = 0
    for r in results:
        if r.ok and r.doc is not None:
            t = r.doc.evidence["verdict"]["tasmota"]
            print(f"  {r.host}: {t['model'] or t['hardware']} {t['mac']}"
                  + ("" if not t["read_errors"] else
                     f"; not read: {', '.join(sorted(t['read_errors']))}"))
        else:
            failed += 1
            print(f"  {r.host}: FAILED ({r.error})")
    for host, why in skipped:
        print(f"  {host}: SKIPPED ({why})")
    print(f"{len(results) - failed} of {len(results)} device(s) written to {args.out}"
          + (f"; {len(skipped)} skipped" if skipped else ""))
    return 1 if failed else 0
