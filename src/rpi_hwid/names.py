"""Human names for FPGA boards, derived from their immutable identifiers.

A NeTV2's only identifier that survives everything is its Xilinx Device DNA;
an Arty's is the serial its onboard FT2232 reports (Digilent prints it on the
board). Both are unpleasant to say aloud and, worse, come in near-identical
clusters (DNAs from adjacent wafer positions differ in one hex digit; Arty
serials share a ``210319`` prefix and differ in one trailing character), so a
short slice of either fails silently. Instead the identifier is hashed and
the hash indexes a word list: neighbouring identifiers get names that look
nothing alike, and the name is short enough for a label, a console or a
phone call.

NeTV2 names are a pure function of the DNA (``netv2-<word>``). Arty names use
a hash *chain* against a registry, because thirteen serials collided on any
short list: a serial's candidates come in hash order and the first not held
by an earlier-registered board wins, so the registration order is part of
the name and the caller's record of (serial, name) pairs is the authority.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

NETV2_WORDS = [
    "amber", "basil", "cedar", "delta", "ember", "flint", "grove", "hazel",
    "indigo", "jasper", "kelp", "lilac", "maple", "nutmeg", "onyx", "poppy",
    "quartz", "rowan", "sage", "topaz", "umber", "violet", "walnut", "xenon",
    "yarrow", "zinc", "anvil", "bronze", "coral", "dune", "elm", "fern",
]

# A Cynthion's identifier is its ECP5 configuration flash's unique id, which
# has the same problem as the others and worse: flashes come off a reel, so
# boards built together carry near-consecutive uids. Instruments, to keep the
# three lists apart at a glance as much as to keep the boards apart.
CYNTHION_WORDS = [
    "sextant", "caliper", "gnomon", "astrolabe", "vernier", "theodolite",
    "plumb", "alidade", "lodestone", "quadrant", "compass", "dial",
    "sundial", "chronometer", "sounder", "logline", "armilla", "orrery",
    "octant", "transit", "level", "pendulum", "barograph", "aneroid",
    "hygrometer", "manometer", "galvanometer", "bolometer", "etalon",
    "prism", "reticle", "micrometer",
]

# An Acorn is keyed on its Device DNA like a NeTV2, and DNAs off one wafer
# differ in a digit, so it needs the same treatment. Trees, for the acorn.
ACORN_WORDS = [
    "oak", "rowan", "alder", "birch", "hazel", "holly", "willow", "aspen",
    "beech", "elm", "hawthorn", "juniper", "larch", "linden", "maple", "poplar",
    "spruce", "yew", "cedar", "cypress", "fir", "hornbeam", "ash", "chestnut",
    "walnut", "sycamore", "laurel", "myrtle", "olive", "plane", "teak", "ebony",
]

# A PCILeech card is keyed on its Device DNA too. Nothing read off one says
# who made it, so the name is from the DNA alone and claims nothing more;
# pond and stream invertebrates, for the leech.
PCILEECH_WORDS = [
    "mayfly", "caddis", "stonefly", "damsel", "skater", "boatman", "mussel", "limpet",
    "snail", "shrimp", "crayfish", "hydra", "planarian", "rotifer", "tardigrade", "daphnia",
    "cyclops", "copepod", "isopod", "amphipod", "midge", "gnat", "whirligig", "diver",
    "scorpion", "strider", "spinner", "dun", "nymph", "larva", "bristleworm", "lugworm",
]

ARTY_WORDS = [
    "otter", "heron", "finch", "raven", "wren", "stork", "swift", "kite",
    "robin", "egret", "crane", "gull", "lark", "owl", "hawk", "dove",
    "quail", "teal", "ibis", "jay", "swan", "goose", "eider", "coot",
    "tern", "plover", "snipe", "curlew", "godwit", "knot", "ruff", "stint",
    "avocet", "stilt", "crake", "grebe", "loon", "puffin", "auk", "gannet",
    "petrel", "fulmar", "skua", "pipit", "thrush", "ouzel", "siskin", "linnet",
    "twite", "serin", "bunting", "shrike", "magpie", "jackdaw", "rook", "chough",
    "starling", "dipper", "kinglet", "martin", "swallow", "cuckoo", "hoopoe", "kestrel",
]


def normalise_dna(dna: str) -> str:
    """Accept 0x-prefixed, bare, upper or lower case; emit 16 lowercase hex."""
    s = dna.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    s = s.replace("_", "").replace(" ", "")
    if not s or any(c not in "0123456789abcdef" for c in s):
        raise ValueError(f"not a hex Device DNA: {dna!r}")
    return s.rjust(16, "0")


def netv2_name(dna: str) -> str:
    """The name for a NeTV2's Device DNA. Pure: same DNA, same name, forever."""
    digest = hashlib.sha256(normalise_dna(dna).encode()).hexdigest()
    return "netv2-" + NETV2_WORDS[int(digest, 16) % len(NETV2_WORDS)]


def cynthion_name(flash_uid: str) -> str:
    """The name for a Cynthion's configuration flash uid. Pure, like a NeTV2's.

    No registry: the Arty's hash chain exists only because Digilent serials
    collide, and flash uids are 64 bits of vendor-assigned identifier.
    ``normalise_dna`` is reused because the shape is the same -- 16 hex
    digits, however they are spelled.
    """
    digest = hashlib.sha256(normalise_dna(flash_uid).encode()).hexdigest()
    return "cynthion-" + CYNTHION_WORDS[int(digest, 16) % len(CYNTHION_WORDS)]


def acorn_name(dna: str) -> str:
    """The name for an Acorn's Device DNA. Pure, like a NeTV2's."""
    digest = hashlib.sha256(normalise_dna(dna).encode()).hexdigest()
    return "acorn-" + ACORN_WORDS[int(digest, 16) % len(ACORN_WORDS)]


def pcileech_name(dna: str) -> str:
    """The name for a PCILeech card's Device DNA. Pure, like a NeTV2's."""
    digest = hashlib.sha256(normalise_dna(dna).encode()).hexdigest()
    return "pcileech-" + PCILEECH_WORDS[int(digest, 16) % len(PCILEECH_WORDS)]


def arty_candidates(serial: str) -> Iterator[str]:
    """The hash chain of words for a Digilent serial, best first."""
    key = serial.strip().upper().encode()
    for round_ in range(len(ARTY_WORDS)):
        digest = hashlib.sha256(key + b":%d" % round_).hexdigest()
        yield ARTY_WORDS[int(digest, 16) % len(ARTY_WORDS)]


def arty_name(serial: str, taken: Iterable[str] = ()) -> str:
    """The name for an Arty: the first candidate not already in `taken`."""
    held = {t.replace("arty-", "") for t in taken}
    for word in arty_candidates(serial):
        if word not in held:
            return "arty-" + word
    raise ValueError("the Arty word list is exhausted")


def arty_names(serials: Iterable[str], pinned: dict[str, str] | None = None) -> dict[str, str]:
    """Name every serial in registration order, honouring `pinned` names.

    `pinned` is the caller's registry (serial -> name); its names are kept
    verbatim and count as taken before any new serial is named, so adding
    a board never renames an old one.
    """
    names: dict[str, str] = dict(pinned or {})
    taken = list(names.values())
    for serial in serials:
        if serial in names:
            continue
        name = arty_name(serial, taken)
        names[serial] = name
        taken.append(name)
    return names
