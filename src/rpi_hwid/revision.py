"""Raspberry Pi revision codes and the MACs that follow from a serial.

The new-style (2012+) revision code packs the board identity into bit
fields, so ``c04170`` alone says "Pi 5, 4 GB, Rev 1.0" and stays right even
when a host's ``/proc/device-tree/model`` string is worded differently:

    bits 0-3   board revision (a020d3 -> 3 -> "Rev 1.3")
    bits 4-11  board type
    bits 12-15 processor
    bits 16-19 manufacturer
    bits 20-22 memory size
    bit  23    "new-style" flag, always set on these codes

The models before that were given sequential codes from ``0002`` to
``0015`` with no fields in them at all, so bit 23 is clear and the board
has to be looked up (``OLD_STYLE``). Both kinds decode to the same
``Revision``, and the old boards borrow ``PI_TYPE``'s spellings of their
own names, so a Model B reads the same however it reports itself.

From https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
under "Raspberry Pi revision codes".

On the Broadcom-OUI boards (3B+, Zero W and earlier) both onboard MACs are
functions of the serial number, which is how a board whose radio is
disabled in config.txt still gets its radio MAC on a label:

    eth  = b8:27:eb : serial[-6:]
    wlan = b8:27:eb : serial[-6:] XOR 55:55:55

That held on every one of eleven such boards where a MAC was read back.
The Pi 4 and 5 use other OUIs with no usable rule (wlan is eth + 1 on some
and eth + 2 on others), so their radio MAC has to be read.
"""

from __future__ import annotations

from dataclasses import dataclass

from rpi_hwid import probe

PI_TYPE = {
    0x00: "Model A", 0x01: "Model B", 0x02: "Model A+", 0x03: "Model B+",
    0x04: "2 Model B", 0x06: "Compute Module 1", 0x08: "3 Model B",
    0x09: "Zero", 0x0A: "Compute Module 3", 0x0C: "Zero W",
    0x0D: "3 Model B+", 0x0E: "3 Model A+", 0x10: "Compute Module 3+",
    0x11: "4 Model B", 0x12: "Zero 2 W", 0x13: "400", 0x14: "Compute Module 4",
    0x15: "Compute Module 4S", 0x17: "5", 0x18: "Compute Module 5",
    0x19: "500", 0x1A: "Compute Module 5 Lite",
}
PI_MEMORY = {0: "256 MB", 1: "512 MB", 2: "1 GB", 3: "2 GB", 4: "4 GB",
             5: "8 GB", 6: "16 GB"}
PI_SOC = {0: "BCM2835", 1: "BCM2836", 2: "BCM2837", 3: "BCM2711", 4: "BCM2712"}

# The sequential codes, as (board, revision, memory). Every one of these
# boards is a BCM2835, which is why no SoC is carried here. The 0015 A+
# shipped with either memory size and the code does not say which, which is
# what the "/" is: it is the one code whose answer MemTotal has to settle.
OLD_STYLE = {
    0x0002: ("Model B", "1.0", "256 MB"),
    0x0003: ("Model B", "1.0", "256 MB"),
    0x0004: ("Model B", "2.0", "256 MB"),
    0x0005: ("Model B", "2.0", "256 MB"),
    0x0006: ("Model B", "2.0", "256 MB"),
    0x0007: ("Model A", "2.0", "256 MB"),
    0x0008: ("Model A", "2.0", "256 MB"),
    0x0009: ("Model A", "2.0", "256 MB"),
    0x000D: ("Model B", "2.0", "512 MB"),
    0x000E: ("Model B", "2.0", "512 MB"),
    0x000F: ("Model B", "2.0", "512 MB"),
    0x0010: ("Model B+", "1.2", "512 MB"),
    0x0011: ("Compute Module 1", "1.0", "512 MB"),
    0x0012: ("Model A+", "1.1", "256 MB"),
    0x0013: ("Model B+", "1.2", "512 MB"),
    0x0014: ("Compute Module 1", "1.0", "512 MB"),
    0x0015: ("Model A+", "1.1", "256 MB / 512 MB"),
}

BROADCOM_OUI = "b8:27:eb"


@dataclass(frozen=True)
class Revision:
    code: str
    model: str
    revision: str
    soc: str
    memory: str

    @property
    def is_pi5(self) -> bool:
        return self.model.startswith("5") or self.model.startswith("500") \
            or "Module 5" in self.model


def decode_revision(code: str) -> Revision:
    """Turn a revision code like 'c04170' or '000f' into its named fields."""
    n = int(code, 16)
    if not n & (1 << 23):
        return _old_style(code, n)
    try:
        model = PI_TYPE[(n >> 4) & 0xFF]
    except KeyError as exc:
        raise ValueError(f"{code}: unknown board type 0x{(n >> 4) & 0xFF:02x}") from exc
    return Revision(
        code=code.lower(), model=model, revision=f"1.{n & 0xF}",
        soc=PI_SOC[(n >> 12) & 0xF], memory=PI_MEMORY[(n >> 20) & 0x7],
    )


def _old_style(code: str, n: int) -> Revision:
    """A pre-2012 sequential code, looked up rather than unpacked.

    Only the low 16 bits are looked up, so a code carrying flag bits above
    them still names its board; the documentation gives these codes no
    fields, so nothing is read out of those bits either.
    """
    try:
        model, board_rev, memory = OLD_STYLE[n & 0xFFFF]
    except KeyError:
        raise ValueError(f"{code}: no model is listed for this old-style code") from None
    return Revision(code=code.lower(), model=model, revision=board_rev,
                    soc=PI_SOC[0], memory=memory)


def broadcom_macs(serial: str) -> tuple[str, str]:
    """The (eth, wlan) MAC pair a Broadcom-OUI Pi derives from its serial.

    The probe owns the rule, because it has to sort a board's own ports
    from its dongles by it while standing on the board; this is the same
    answer in the order a label wants it, so the two sides of the wire
    cannot come to disagree about which MACs are the board's.
    """
    by_kind = {kind: mac for mac, kind in probe.board_macs(serial).items()}
    if len(by_kind) != 2:
        raise ValueError(f"{serial!r} is not a serial a MAC pair follows from")
    return by_kind["eth"], by_kind["wlan"]


def derived_wlan_mac(serial: str, macs: list[dict[str, str]]) -> str | None:
    """A Broadcom board's radio MAC when only its wired MAC was read.

    Returns None when a wlan MAC is already present, or when the board is
    not a Broadcom-OUI board (nothing can be derived there).
    """
    if any(m["kind"] == "wlan" for m in macs):
        return None
    eth = next((m["mac"] for m in macs if m["kind"] == "eth"), None)
    if eth is None or not eth.lower().startswith(BROADCOM_OUI):
        return None
    return broadcom_macs(serial)[1]
