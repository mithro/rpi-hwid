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
    """Turn a revision code like 'c04170' into its named fields."""
    n = int(code, 16)
    if not n & (1 << 23):
        raise ValueError(f"{code} is an old-style revision code")
    try:
        model = PI_TYPE[(n >> 4) & 0xFF]
    except KeyError as exc:
        raise ValueError(f"{code}: unknown board type 0x{(n >> 4) & 0xFF:02x}") from exc
    return Revision(
        code=code.lower(), model=model, revision=f"1.{n & 0xF}",
        soc=PI_SOC[(n >> 12) & 0xF], memory=PI_MEMORY[(n >> 20) & 0x7],
    )


def broadcom_macs(serial: str) -> tuple[str, str]:
    """The (eth, wlan) MAC pair a Broadcom-OUI Pi derives from its serial."""
    tail = bytes.fromhex(serial)[-3:]
    eth = bytes.fromhex(BROADCOM_OUI.replace(":", "")) + tail
    wlan = bytes.fromhex(BROADCOM_OUI.replace(":", "")) + bytes(b ^ 0x55 for b in tail)
    return tuple(":".join(f"{b:02x}" for b in m) for m in (eth, wlan))  # type: ignore[return-value]


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
