"""RISC-V device-tree boards: which one, and what its label says.

The probe (``rpi_hwid.probe``) recognises a RISC-V board by its harts --
/proc/cpuinfo prints an ``isa`` line for each, which no other architecture
does -- and records them, and on a SiFive HiFive Unmatched the board's PCB
EEPROM too, in the summary's ``riscv`` record. This module turns that into
a board label's header, the same way ``rpi_hwid.boards`` does for an Orange
Pi: the title is the device tree's ``model`` with the maker's name taken
off, the subtitle carries the fitted RAM, the SoC and the device-tree id.
The band where a Pi names its HAT carries the RISC-V mark and the ISA
instead, because the Unmatched has no HAT header and the ISA is the thing
about it that is RISC-V.

The serial is the board EEPROM's (``SF105SZ212200391``), which U-Boot also
writes to the device tree's /serial-number, so the probe normally has it
twice. The two must agree: a label is keyed on one serial, and two readings
that differ are not something to choose between on a sticker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rpi_hwid.model import Summary

# SoC names from the device tree's compatible list. Short: "FU740-C000" in
# full leaves the board's device-tree id no room on the subtitle, and the
# die revision is in the probe document for whoever needs it. An unknown SoC
# still says which die it is, as the entry after the vendor's comma.
SOC_NAMES = {
    "sifive,fu740-c000": "FU740",
    "sifive,fu540-c000": "FU540",
}

# The maker, from the board's compatible vendor: the prefix its `model`
# string opens with (taken off the title) and the artwork for its mark.
MAKERS = {"sifive": ("SiFive ", "sifive.svg")}

# What is soldered to the boards the fleet has: (wired port, radio). The
# Unmatched has one gigabit port and no radio of its own -- its M.2 E-key
# slot takes a Wi-Fi card, which would be a card, not the board.
PORTS = {"sifive,hifive-unmatched-a00": (True, False)}

# The short name for --list, where the model string's revision suffix
# ("A00") says nothing a person picking a sticker needs.
SHORT = {"sifive,hifive-unmatched-a00": "HiFive Unmatched"}

# The mark drawn beside the ISA, on every RISC-V board's label.
RISCV_MARK = "risc-v.svg"


@dataclass(frozen=True)
class RiscvBoard:
    """What a RISC-V board's label header says."""

    title: str
    short: str
    subtitle: str
    mark: str | None
    memory: str
    wired: bool | None
    radio: bool | None
    isa: str | None               # "RV64GC_Zicntr_Zihpm", see short_isa
    line: str                     # "4 harts  ·  sv39  ·  PCB rev 3  ·  BOM B0"


def short_isa(isa: str) -> str:
    """The ISA string the kernel reports, in the ISA manual's short form.

    The kernel spells out every extension it knows, implied or not
    ("rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd", 46 characters, too
    long for the label at a readable size). Only folds the ISA manual
    defines as equal are made: G is IMAFD with Zicsr and Zifencei, and C
    implies Zca, plus Zcd with D (and Zcf with F on RV32). What is left is
    the same set of extensions: "RV64GC_Zicntr_Zihpm"."""
    parts = [p for p in isa.lower().split("_") if p]
    if not parts or not parts[0].startswith(("rv32", "rv64", "rv128")):
        return isa
    base = parts[0]
    xlen = base[:4] if not base.startswith("rv128") else base[:5]
    letters = base[len(xlen):]
    exts = parts[1:]
    if "c" in letters:
        implied = {"zca"} | ({"zcd"} if "d" in letters else set()) \
            | ({"zcf"} if "f" in letters and xlen == "rv32" else set())
        exts = [e for e in exts if e not in implied]
    if set("imafd") <= set(letters) and {"zicsr", "zifencei"} <= set(exts):
        letters = "g" + "".join(c for c in letters if c not in "imafd")
        exts = [e for e in exts if e not in ("zicsr", "zifencei")]
    return "_".join([xlen.upper() + letters.upper()] + [e.capitalize() for e in exts])


def soc_name(compatible: list[str]) -> str | None:
    """The SoC, from the compatible list: a known one by name, else the
    last entry after its vendor's comma; None for an empty list."""
    for c in compatible:
        if c in SOC_NAMES:
            return SOC_NAMES[c]
    if len(compatible) > 1:
        return compatible[-1].split(",", 1)[-1]
    return None


def identify(s: Summary) -> RiscvBoard:
    """The header of a RISC-V board's label."""
    compat = s.compatible.split()
    board = compat[0] if compat else ""
    vendor = board.split(",", 1)[0]
    prefix, mark = MAKERS.get(vendor, ("", None))
    title = s.model.removeprefix(prefix) or board
    memory = s.memory or "RAM not read"
    soc = soc_name(compat)
    dt = ("dt " + board.split(",", 1)[-1]) if board else ""
    parts = [memory] + ([soc] if soc else []) + ([dt] if dt else [])
    rv = s.riscv or {}
    line = []
    if rv.get("harts"):
        line.append(f"{rv['harts']} harts")
    if rv.get("mmu"):
        line.append(rv["mmu"])
    e = rv.get("eeprom")
    if e:
        line += [f"PCB rev {e['pcb_revision']}",
                 f"BOM {e['bom_revision']}{e['bom_variant']}"]
    wired, radio = PORTS.get(board, (None, None))
    return RiscvBoard(
        title=title, short=SHORT.get(board, title), subtitle="  ·  ".join(parts),
        mark=mark, memory=memory, wired=wired, radio=radio,
        isa=short_isa(rv["isa"]) if rv.get("isa") else None,
        line="  ·  ".join(line),
    )


def serial_problem(host: str, s: Summary) -> tuple[str, str] | None:
    """Why this board's serial cannot go on a label, or None when it can.

    ("unread", why) when there is no serial at all, naming the command that
    reads it; ("conflict", why) when the device tree and the EEPROM give two
    different ones, or the EEPROM's own CRC fails."""
    rv = s.riscv or {}
    e = rv.get("eeprom")
    if not s.serial:
        how = rv.get("eeprom_error") or (
            "run `cat /proc/device-tree/serial-number` on the host")
        return "unread", (
            f"{host}: the board's serial was not read, so the label would carry none. "
            "The device tree has no /serial-number and the board EEPROM gave "
            f"nothing: {how}, and collect again.")
    if e and not e["crc_ok"]:
        return "conflict", (
            f"{host}: the board EEPROM's CRC does not hold ({e['crc']} stored), "
            f"so its serial {e['serial']} is not to be trusted")
    if e and e["serial"] != s.serial:
        return "conflict", (
            f"{host}: the device tree says serial {s.serial} and the board "
            f"EEPROM {e['serial']}")
    return None


def eeprom_mac(s: Summary) -> str | None:
    """The MAC the board EEPROM carries, where it was read."""
    e = (s.riscv or {}).get("eeprom")
    return e["mac"] if e else None
