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

import ipaddress
import urllib.parse

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
