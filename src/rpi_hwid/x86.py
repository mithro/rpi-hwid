"""What an x86 board's label says, from the DMI/SMBIOS strings the probe read.

A PC has no device tree and no revision code: its firmware names it, in the
SMBIOS tables the kernel publishes under /sys/class/dmi/id. The board's
name is the title ("MinnowBoard Turbot"), and the subtitle carries what is
soldered down and cannot change: the fitted RAM, the CPU, and the platform
revision the firmware reports (the Turbot's "D0", the MAX's "B3").

The BIOS version and date are deliberately not on the label: a firmware
update changes them, and a label carries only what cannot change. Nor is
the product UUID, which on both of the fleet's MinnowBoards is the same
00000000-6462-4524-006a-9b7737e315cf -- a firmware constant, not an
identity. Nor are the disks' serials: the MinnowBoards boot from an mSATA
or SATA drive that can be swapped, so its serial names the drive, not the
board. They are all in the collected document.

Two tables say what the DMI strings alone cannot: which project a board
belongs to, for the mark in the header (the MinnowBoard fish), and who made
it, for the maker's mark in the band a Pi uses for its HAT (ADI Engineering
made the Turbot, CircuitCo the MAX). A board in neither table is still
labelled, in its firmware's own words, with no mark and no claim about a
radio it may or may not have.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rpi_hwid import probe

if TYPE_CHECKING:
    from collections.abc import Callable

    from rpi_hwid.model import Summary

_dmi_value: Callable[[str | None], str | None] = probe.dmi_value

# The project a board belongs to, by the start of its DMI board name (compared
# lower-case): the artwork file for the header's mark, and what is soldered
# to it as (wired port, radio). The MinnowBoard MAX and the Turbot have one
# Gigabit port and no radio: the MAX's page on minnowboard.org (its I/O row,
# and "The MinnowBoard MAX uses a Realtek RTL8111GS-CG"), and Silicom's
# MinnowBoard Turbot datasheet ("Ethernet 1x 1Gb Ethernet RJ45", and no
# wireless anywhere in it). Wi-Fi goes on an M.2 card or a USB dongle, which
# gets a label of its own.
FAMILIES = {
    "minnowboard": ("minnowboard.svg", True, False),
}

# The maker, by DMI board (or system) vendor, lower-case: the name to print
# and the artwork file for its mark. "ADI" is what ADI Engineering's firmware
# writes; "Circuitco" is CircuitCo Electronics, which built the MAX.
MAKERS = {
    "adi": ("ADI Engineering", "adi-engineering.png"),
    "circuitco": ("CircuitCo", "circuitco.png"),
}


@dataclass(frozen=True)
class PcIdentity:
    """What the header and the maker's band of an x86 board's label say."""

    title: str
    subtitle: str
    mark: str                      # "" where no project mark is known
    memory: str
    wired: bool | None
    radio: bool | None
    maker: str | None
    maker_mark: str | None


def cpu_short(model: str | None) -> str | None:
    """The CPU's model name without its trademark signs, "CPU", "Processor"
    and clock: "Intel(R) Atom(TM) CPU  E3826  @ 1.46GHz" -> "Intel Atom E3826"."""
    if not model:
        return None
    s = re.sub(r"\((R|TM|C)\)", "", model, flags=re.I)
    s = re.sub(r"@\s*[\d.]+\s*[GM]Hz", "", s)
    s = re.sub(r"\b(CPU|Processor)\b", "", s)
    return " ".join(s.split()) or None


def identify(s: Summary) -> PcIdentity:
    dmi = s.dmi or {}
    title = (_dmi_value(dmi.get("board_name")) or _dmi_value(dmi.get("product_name"))
             or s.model or "x86 board")
    family = next((v for k, v in FAMILIES.items() if title.lower().startswith(k)), None)
    mark, wired, radio = family if family else ("", None, None)
    vendor = _dmi_value(dmi.get("board_vendor")) or _dmi_value(dmi.get("sys_vendor"))
    maker, maker_mark = MAKERS.get((vendor or "").lower(), (vendor, None))
    memory = s.memory or "RAM not read"
    rev = _dmi_value(dmi.get("product_version"))
    parts = [memory] + [p for p in (cpu_short(s.cpu), "rev " + rev if rev else None) if p]
    return PcIdentity(title=title, subtitle="  ·  ".join(parts), mark=mark, memory=memory,
                      wired=wired, radio=radio, maker=maker, maker_mark=maker_mark)


def unread_serial(s: Summary) -> str | None:
    """The DMI serial field the probe found and could not read, where the
    label has no serial because of it: the board's, first. None where the
    firmware simply has no serial, which is a fact and not a failed read."""
    if s.serial:
        return None
    unread = (s.dmi or {}).get("unread") or ()
    return next((f for f in probe.DMI_SERIALS if f in unread), None)
