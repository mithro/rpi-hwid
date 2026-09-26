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

import csv
import io
import ipaddress
import re
import urllib.parse
from dataclasses import dataclass

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
