"""SPI NOR flash parts by JEDEC id, from the ``spiflash`` package.

`spiflash <https://github.com/mithro/spiflash>`_ merges the flash tables of
Linux, U-Boot, flashrom, flashprog, OpenOCD and openFPGALoader into one
database keyed on the id a chip answers to JEDEC read-id (9Fh). This module
asks it about the SPI NOR parts at an id and turns what it says into what a
label prints: one family name, a vendor and a size.

This is the fallback for naming a flash, not the authority. The label
generator's own tables (``rpi_hwid.labels.JEDEC_PART`` and the extended-id
rules beside it) are argued from datasheets and from real reads, and win
wherever they have an entry; an id neither knows is a bare JEDEC id, and
the label generator stops on it rather than print one.

Nothing here reads hardware or knows about any board.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

import spiflash

if TYPE_CHECKING:
    from collections.abc import Iterable

# Where a part missing from spiflash is reported.
ISSUES = "https://github.com/mithro/spiflash/issues"


def version() -> str:
    """The spiflash release whose tables are being read."""
    return spiflash.__version__


def _id_bytes(jedec: int | str, ext: str | bytes | None = None) -> bytes | None:
    """The three id bytes, then the RDID bytes after them where they are
    bytes; None for anything that is not a three-byte id."""
    try:
        value = int(jedec, 16) if isinstance(jedec, str) else int(jedec)
    except ValueError:
        return None
    if not 0 < value <= 0xFFFFFF:
        return None
    if isinstance(ext, str):
        text = ext.lower().removeprefix("0x")
        ext = bytes.fromhex(text) if re.fullmatch(r"(?:[0-9a-f]{2})+", text) else None
    return value.to_bytes(3, "big") + (ext or b"")


def flashes(jedec: int | str, ext: str | bytes | None = None) -> list[spiflash.Flash]:
    """The SPI NOR entries spiflash has for `jedec`, narrowed by the RDID
    bytes after it (`ext`, "0x4d0180" or bytes) where a source keys on them.

    NOR only: a SPI NAND id is two bytes, so a NOR id can start with one
    (0xc22017 also fits the MX35LF2G14AC's c220), and a NAND part is never a
    NOR flash's name."""
    data = _id_bytes(jedec, ext)
    if data is None:
        return []
    return spiflash.lookup(data, type="nor")


# flashrom writes the order-code letters after a part number as a run of
# "." -- S25FL128S......0 is any S25FL128S whose model number (the sector
# layout) ends in 0 -- and the run, with what follows it, is not the part.
ORDER_CODE = re.compile(r"\.{3,}.*$")
# What follows "_" names a configuration of one part rather than another
# part: sector layouts (S25FL128S_UL, _US; S25FL512S_256K), supply
# (N25Q128_3V, N25Q128_1_8V), and interface modes (W25Q256JV_Q, _M, _DTR).
# No maker marks a package with an underscore.
VARIANT = re.compile(r"_.*$")
# A "-" after the part's closing letter does the same (S25FL127S-256KB,
# W25Q64JV-.Q, W25Q01JV-DTR); after a digit it is part of the part number,
# as in ST's M25P05-A.
LETTER_VARIANT = re.compile(r"(?<=[A-Z])-.*$")


def normalise(names: Iterable[str], jedec: int | str) -> set[str]:
    """The part numbers `names` stand for, as a label can print them.

    Placeholders -- a name that is the id's own hex, as MACRONIX-C22019 is
    -- are dropped: printing one is printing the bare id. flashrom's
    order-code padding and the variant tags upstreams key configurations of
    one part on are cut off (ORDER_CODE, VARIANT, LETTER_VARIANT), and so is
    Linux's trailing model digit where the rest is itself a name at the id
    (S25FL128S0 and S25FL128S1 are S25FL128S's two sector layouts). A "."
    inside a name is a letter flashrom leaves open, and stays for family()
    to write as x.
    """
    data = _id_bytes(jedec)
    hexid = data.hex().upper() if data else None
    cut = set()
    for n in (x.upper() for x in names if x):
        if hexid and n.endswith(hexid):
            continue
        n = LETTER_VARIANT.sub("", VARIANT.sub("", ORDER_CODE.sub("", n)))
        if n:
            cut.add(n)
    return {n[:-1] if re.search(r"[A-Z][01]$", n) and n[:-1] in cut else n for n in cut}


def _merge(names: list[str]) -> str:
    """One family's names as one, a letter they disagree on as x."""
    if len(names) == 1:
        return names[0].replace(".", "x")
    shortest = min(len(n) for n in names)
    out = "".join(
        chars[0] if len(set(chars)) == 1 and chars[0] != "." else "x"
        for chars in zip(*(n[:shortest] for n in names), strict=True))
    longer = [len(n) for n in names if len(n) > shortest]
    if longer:
        # the next length up says how many letters the short names leave off
        out += "x" * (min(longer) - shortest)
    return out


def _prefix_fits(prefix: str, other: str) -> bool:
    return len(prefix) == len(other) and all(
        a == b or "." in (a, b) for a, b in zip(prefix, other, strict=True))


def family(names: Iterable[str]) -> str | None:
    """The name a label can print for parts that share an id.

    One part is its own name. Several are merged letter by letter, with
    each letter they do not agree on -- or that flashrom leaves open with
    "." -- written as x: W25Q32 and W25Q32BV..JV are "W25Q32xx",
    MX25L25635F and MX25L25645G "MX25L256x5x". Where the names belong to
    different families -- the letters before the first digit differ, as
    N25Q128 and its successor MT25QL128 do -- each family is merged on its
    own and the families are joined with "/", rather than merged into a row
    of x that names nothing. A "." in those letters matches any letter, so
    flashrom's B.25Q32BS (BoHong's and Boya's) is one family with BY25Q32CS.
    """
    distinct = sorted({n.upper() for n in names if n})
    if not distinct:
        return None
    groups: dict[str, list[str]] = {}
    for n in distinct:
        prefix = re.match(r"[A-Z.]*", n).group(0)  # type: ignore[union-attr]
        home = next((g for g in groups if _prefix_fits(g, prefix)), prefix)
        groups.setdefault(home, []).append(n)
    return "/".join(sorted(_merge(g) for g in groups.values()))


def name(jedec: int | str, ext: str | bytes | None = None) -> str | None:
    """The family name of the NOR parts at `jedec` (narrowed by `ext`), or
    None where spiflash names none."""
    return family(normalise((n for f in flashes(jedec, ext) for n in f.names), jedec))


def vendor(jedec: int | str, ext: str | bytes | None = None) -> str | None:
    """Who makes the NOR parts at `jedec`, as spiflash spells the maker."""
    makers = Counter(f.manufacturer for f in flashes(jedec, ext) if f.manufacturer)
    return makers.most_common(1)[0][0] if makers else None


def size(jedec: int | str, ext: str | bytes | None = None) -> int | None:
    """The size in bytes spiflash gives the NOR parts at `jedec`, or None
    where it gives none or its entries for the id disagree."""
    sizes = {f.size for f in flashes(jedec, ext) if f.size}
    return sizes.pop() if len(sizes) == 1 else None


def manufacturer(code: int) -> str | None:
    """The maker most of spiflash's NOR parts with manufacturer byte `code`
    have, or None.

    A guess, and used only as one: the byte is a JEP106 code, which is
    unique only within a bank and which many chips send without the bank's
    continuation codes, so 0x68 is Boya's on a flash and another company's
    in JEP106's first bank. It helps someone find the datasheet for an id no
    table lists.
    """
    makers = Counter(f.manufacturer for f in spiflash.flashes()
                     if f.type == "nor" and f.family == "jedec" and f.id[:1] == bytes([code])
                     and f.manufacturer)
    return makers.most_common(1)[0][0] if makers else None
