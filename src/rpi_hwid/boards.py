"""Which board a document describes, and what its label's header says.

A Raspberry Pi is named from its revision code (``rpi_hwid.revision``),
which stays right however the model string is worded. Other boards have no
such code, so they are named from the device tree: the ``compatible`` list
says which SoC and which board (``xunlong,orangepi-pc allwinner,sun8i-h3``),
and the ``model`` string is the maker's own name for it ("Xunlong Orange Pi
PC"), so the title is the model with the maker's prefix taken off. The
subtitle carries what cannot change and would otherwise be typed: the
fitted RAM, the SoC, and the compatible string that is the board's
canonical machine id (Xunlong sells the Orange Pi PC by product name only;
there is no part number or SKU, so ``xunlong,orangepi-pc`` is the id).

The Armbian release is deliberately not on the label: it changes with
every upgrade, and a label carries only what cannot change.

On the Allwinner boards the wired MAC is a function of the serial too, the
way it is on the Broadcom-OUI Pis: U-Boot (board/sunxi/board.c,
``setup_environment``) sets a locally administered address from the SID,

    eth0 = 02 : serial[6:8] : serial[8:16]

i.e. the serial's fourth byte, then its last four. Held on opi1pc-b, the
one fleet board whose serial was captured (02c00181e1ce7d46 gives
02:81:e1:ce:7d:46); opi1pc-a's recorded MAC 02:81:3c:1a:db:71 fits the
rule (an H3's chip-id word ends in 0x81) but its serial was never read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rpi_hwid import probe
from rpi_hwid.revision import decode_revision

# The probe is annotation-free (it has to run on a Pi's python 3.5), so its
# classifier is bound to a typed name once here rather than called untyped.
_probe_board_kind: Callable[[str, list[str]], str] = probe.board_kind

if TYPE_CHECKING:
    from collections.abc import Callable

    from rpi_hwid.model import Summary

# SoC names from the last entry of the device tree's compatible list.
ALLWINNER_SOC = {
    "allwinner,sun8i-h2-plus": "Allwinner H2+",
    "allwinner,sun8i-h3": "Allwinner H3",
    "allwinner,sun8i-r40": "Allwinner R40",
    "allwinner,sun50i-a64": "Allwinner A64",
    "allwinner,sun50i-h5": "Allwinner H5",
    "allwinner,sun50i-h6": "Allwinner H6",
    "allwinner,sun50i-h616": "Allwinner H616",
    "allwinner,sun50i-h618": "Allwinner H618",
}

# What is soldered to the Orange Pi models the fleet has: (wired port,
# radio). The PC and One have an Ethernet jack and no radio at all, so a
# missing wlan MAC is stated as that rather than as "not read". Every other
# Xunlong board is left open (None: not known here).
ORANGE_PI_PORTS = {"xunlong,orangepi-pc": (True, False), "xunlong,orangepi-one": (True, False)}

MAKER_PREFIX = {"opi": "Xunlong "}


@dataclass(frozen=True)
class BoardIdentity:
    """What a board label's header says, and which mark goes beside it."""

    kind: str                  # rpi | opi
    short: str                 # "Pi 5", "Orange Pi PC": for --list
    title: str                 # "Raspberry Pi 5", "Orange Pi PC"
    subtitle: str              # the immutable facts under the title
    mark: str                  # artwork file name
    memory: str
    wired: bool | None         # has a soldered-down wired port; None = not known
    radio: bool | None         # has a soldered-down radio; None = not known
    radio_derivable: bool      # the radio MAC follows from the serial (Broadcom Pis)


def board_kind(s: Summary) -> str:
    """``rpi``, ``opi`` or ``other``, from the probe's own classifier, so
    the two sides of the wire can never disagree about what a board is."""
    return _probe_board_kind(s.model, s.compatible.split())


def identify(s: Summary) -> BoardIdentity | None:
    """The label header for one document's summary, or None for a board
    this package has no label design for (the probe's ``other``)."""
    kind = board_kind(s)
    if kind == "other":
        return None
    if kind == "rpi":
        rev = decode_revision(s.revision)
        return BoardIdentity(
            kind="rpi", short=f"Pi {rev.model}", title="Raspberry Pi " + rev.model,
            subtitle=f"{rev.memory}  ·  Rev {rev.revision}  ·  rev code {rev.code}",
            mark="raspberry-pi.svg", memory=rev.memory,
            wired="Zero" not in rev.model, radio=None,
            radio_derivable=not (rev.is_pi5 or rev.model.startswith("4")),
        )
    compat = s.compatible.split()
    board = compat[0] if compat else ""
    soc = next((ALLWINNER_SOC[c] for c in compat if c in ALLWINNER_SOC), None)
    if soc is None and len(compat) > 1:
        soc = compat[-1].split(",", 1)[-1]        # "sun8i-h3": still says which die
    title = s.model.removeprefix(MAKER_PREFIX[kind]) or board
    memory = s.memory or "RAM not read"
    # the device-tree id, without its vendor prefix and said to be one:
    # "xunlong,orangepi-pc" pasted raw into the subtitle reads as debris
    dt = ("dt " + board.split(",", 1)[-1]) if board else ""
    parts = [memory] + ([soc] if soc else []) + ([dt] if dt else [])
    wired, radio = ORANGE_PI_PORTS.get(board, (None, None))
    return BoardIdentity(
        kind=kind, short=title, title=title, subtitle="  ·  ".join(parts),
        mark="orange-pi.png", memory=memory, wired=wired, radio=radio,
        radio_derivable=False,
    )


def sunxi_mac(serial: str) -> str:
    """The eth0 MAC U-Boot derives from an Allwinner board's serial."""
    raw = bytes.fromhex(serial)
    if len(raw) != 8:
        raise ValueError(f"{serial!r} is not a 16-digit sunxi serial")
    mac = bytes([0x02, raw[3]]) + raw[4:8]
    return ":".join(f"{b:02x}" for b in mac)
