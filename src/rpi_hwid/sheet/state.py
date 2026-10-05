"""A label sheet's state: which slots are used, and where new labels go.

Each sheet is one JSON file, ``<root>/sheets/<ID>.json``, holding its id,
when, where and by whom it was started, the printer it goes through, every
used slot and what was printed there, the stickers whose micro cut guides
are printed, and each pass that printed on it. The root is
``$XDG_STATE_HOME/rpi-hwid`` (``~/.local/state/rpi-hwid``): this is a
machine's record of the paper in its drawer, not something to commit.

A slot is a sticker, ``"1"``-``"21"``, or a quarter of one, ``"7a"``-``"7d"``,
as ``rpi-hwid labels --place`` takes them.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterator

STOCK = "L7160"
STICKERS = 21
QUARTERS = "abcd"
# No 0/O, 1/I/L or U/V: an id is read off paper and typed back in.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTWXYZ"
ID_LEN = 4


class SheetFullError(RuntimeError):
    """The labels asked for do not fit in what is left of the sheet."""


# a pass a person said put nothing on the sheet (Sheet.unprint)
NOT_PRINTED = "not-printed"


class NoSuchSheetError(LookupError):
    pass


class SlotUsedError(ValueError):
    """A slot asked for is already used."""


def parse_slot(text: str) -> tuple[int, str]:
    """``"7"`` -> (7, ""), ``"7c"`` -> (7, "c"); ValueError off the sheet."""
    t = text.strip().lower()
    q = t[-1] if t and t[-1] in QUARTERS else ""
    n = t[:-1] if q else t
    if not n.isdigit() or not 1 <= int(n) <= STICKERS:
        raise ValueError(f"{text!r} is not a slot: a sticker is 1-{STICKERS}, a quarter "
                         f"of one 1a-{STICKERS}d")
    return int(n), q


class Want(NamedTuple):
    """A label to place: its id, and "sticker" or "quarter"."""

    id: str
    size: str


def new_id(existing: set[str], rng: random.Random | None = None) -> str:
    rng = rng or random.SystemRandom()
    while True:
        i = "".join(rng.choice(ALPHABET) for _ in range(ID_LEN))
        if i not in existing:
            return i


def default_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "rpi-hwid"


@dataclass
class Sheet:
    id: str
    stock: str
    created: str               # ISO 8601, with its offset
    host: str                  # the machine that started it
    user: str
    version: str               # rpi-hwid's
    printer: str               # the IPP printer URI it goes through
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    guides: list[str] = field(default_factory=list)
    marked: bool = False       # its id, note and ticks are printed
    passes: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, sheet_id: str, printer: str, host: str, user: str, version: str,
            now: str) -> Sheet:
        return cls(sheet_id, STOCK, now, host, user, version, printer)

    # --- what is free ---

    def _started(self, sticker: int) -> bool:
        return any(f"{sticker}{q}" in self.slots for q in QUARTERS)

    def free_stickers(self) -> list[int]:
        return [n for n in range(1, STICKERS + 1)
                if str(n) not in self.slots and not self._started(n)]

    def free_quarters(self) -> list[str]:
        """The free quarters of stickers already started with micro labels."""
        return [f"{n}{q}" for n in range(1, STICKERS + 1) if self._started(n)
                for q in QUARTERS if f"{n}{q}" not in self.slots]

    # --- where the next labels go ---

    def allocate(self, wants: list[Want]) -> tuple[list[tuple[str, str]], list[str]]:
        """(label id, slot) for each of `wants`, in their order, and the
        stickers whose cut guides this pass must print. Whole labels take
        the first free stickers; micro labels the free quarters of stickers
        already started, then fresh stickers after the whole labels'. A label
        asked for twice (copies) gets a slot each time."""
        whole = [i for i, w in enumerate(wants) if w.size == "sticker"]
        micro = [i for i, w in enumerate(wants) if w.size == "quarter"]
        free = self.free_stickers()
        quarters = self.free_quarters()
        fresh = len(free) - len(whole)
        need = max(0, len(micro) - len(quarters))
        if fresh < 0 or need > fresh * len(QUARTERS):
            raise SheetFullError(
                f"sheet {self.id} has {len(free)} free sticker{'' if len(free) == 1 else 's'} "
                f"and {len(quarters)} free quarter{'' if len(quarters) == 1 else 's'}; "
                f"this needs {len(whole)} whole and {len(micro)} micro: start a new sheet")
        at: dict[int, str] = {}  # by the want's place in the list, not its id: copies share one
        for i, n in zip(whole, free, strict=False):
            at[i] = str(n)
        spare = free[len(whole):]
        guides: list[str] = []
        slots = list(quarters)
        for n in spare:
            slots += [f"{n}{q}" for q in QUARTERS]
        for i, slot in zip(micro, slots, strict=False):
            at[i] = slot
            sticker = slot[:-1]
            if sticker not in self.guides and sticker not in guides:
                guides.append(sticker)
        return [(w.id, at[i]) for i, w in enumerate(wants)], sorted(guides, key=int)

    # --- recording ---

    def record_pass(self, placed: list[tuple[str, str, str, str]], guides: list[str], *,
                    at: str, host: str, user: str, job: int | None, job_state: str,
                    data: str, marked: bool, plan: str = "") -> None:
        """Mark `placed` (label id, slot, host, title) used by a new pass."""
        n = len(self.passes) + 1
        for label, slot, lhost, title in placed:
            self.slots[slot] = {"label": label, "host": lhost, "title": title, "pass": n}
        new_guides = sorted(set(guides) - set(self.guides), key=int)
        self.guides = sorted(set(self.guides) | set(guides), key=int)
        self.marked = self.marked or marked
        self.passes.append({"pass": n, "at": at, "host": host, "user": user, "job": job,
                            "job_state": job_state, "data": data,
                            "labels": [label for label, *_ in placed],
                            "guides": new_guides, "marked": marked, "plan": plan})

    def pass_(self, n: int) -> dict[str, Any]:
        for p in self.passes:
            if p["pass"] == n:
                return p
        raise KeyError(f"sheet {self.id} has no pass {n}")

    def drop_pass(self, n: int) -> None:
        """Take back pass `n`, the last, whose job printed nothing: its
        slots, guides and the sheet's marking are free again."""
        if not self.passes or self.passes[-1]["pass"] != n:
            raise ValueError(f"only the last pass of sheet {self.id} can be taken back")
        p = self.passes.pop()
        self.slots = {k: v for k, v in self.slots.items() if v.get("pass") != n}
        self.guides = [g for g in self.guides if g not in p.get("guides", [])]
        self._remark()

    def _remark(self) -> None:
        """The margins are printed if a pass that printed them is on the
        sheet: one a person recorded as not printed is not."""
        self.marked = any(q.get("marked") for q in self.passes
                          if q["job_state"] != NOT_PRINTED)

    @staticmethod
    def settled(p: dict[str, Any]) -> bool:
        """A person has said what this pass put on the sheet (unprint,
        printed): the printer's later word does not change it."""
        return p["job_state"] == NOT_PRINTED or "printed" in p

    def unprint(self, n: int, why: str, at: str, user: str) -> list[str]:
        """Record, on a person's word, that pass `n` put nothing on the
        sheet: its slots, its guides and (if it was the pass that printed
        them) the sheet's margins are free again. Returns the slots freed.

        The pass stays in the record, as "not-printed" with who said so,
        when and why: the record is the evidence of what went on the paper,
        and pass numbers are never reused. Any pass can be taken back, not
        only the last. One whose job the printer reported completed cannot:
        the printer's word is that it printed."""
        p = self.pass_(n)
        if p["job_state"] == NOT_PRINTED:
            raise ValueError(f"pass {n} of sheet {self.id} is already recorded as not printed")
        if p["job_state"] == "completed":
            raise ValueError(f"pass {n} of sheet {self.id} printed: the printer reported its "
                             f"job {p['job']} completed")
        freed = sorted((k for k, v in self.slots.items() if v.get("pass") == n),
                       key=parse_slot)
        self.slots = {k: v for k, v in self.slots.items() if v.get("pass") != n}
        self.guides = [g for g in self.guides if g not in p.get("guides", [])]
        p["not_printed"] = {"why": why, "at": at, "user": user, "job_state": p["job_state"],
                            "slots": freed}
        p["job_state"] = NOT_PRINTED
        self._remark()
        return freed

    def printed(self, n: int, why: str, at: str, user: str) -> None:
        """Record, on a person's word, that pass `n` did print, when the
        printer can no longer be asked how its job ended. The slots stay
        used, as they were; the pass becomes "completed" and says who said
        so, when, why and what its job's state had been."""
        p = self.pass_(n)
        if p["job_state"] in ("completed", NOT_PRINTED):
            raise ValueError(f"pass {n} of sheet {self.id} is already recorded as "
                             f"{p['job_state']}")
        p["printed"] = {"why": why, "at": at, "user": user, "job_state": p["job_state"]}
        p["job_state"] = "completed"

    def mark(self, slots: list[str], why: str, at: str) -> None:
        """Record `slots` used without printing: stickers peeled off or
        printed before this sheet was tracked."""
        for text in slots:
            n, q = parse_slot(text)
            slot = f"{n}{q}"
            taken = slot in self.slots or str(n) in self.slots or (
                not q and self._started(n))
            if taken:
                raise SlotUsedError(f"slot {slot} of sheet {self.id} is already used")
            self.slots[slot] = {"why": why, "at": at}

    def revision(self) -> str:
        """Changes whenever a slot is used: a plan made against one revision
        is refused against another."""
        blob = json.dumps([self.slots, self.guides, self.marked, len(self.passes)],
                          sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def note(self) -> str:
        """What the sheet's margins say beside its id."""
        when = self.created[:16].replace("T", " ")
        return f"{self.stock} · started {when} on {self.host} by {self.user} · " \
               f"rpi-hwid {self.version}"


class Store:
    """The sheets under `root`, one file each."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.sheets = self.root / "sheets"

    def path(self, sheet_id: str) -> Path:
        return self.sheets / f"{sheet_id.upper()}.json"

    def ids(self) -> list[str]:
        if not self.sheets.is_dir():
            return []
        return sorted(p.stem for p in self.sheets.glob("*.json"))

    @contextlib.contextmanager
    def lock(self, sheet_id: str) -> Iterator[None]:
        """Hold the sheet's lock: one session at a time reads, changes and
        writes it back."""
        self.sheets.mkdir(parents=True, exist_ok=True)
        with open(self.sheets / f".{sheet_id.upper()}.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def load(self, sheet_id: str) -> Sheet:
        p = self.path(sheet_id)
        if not p.exists():
            raise NoSuchSheetError(f"no sheet {sheet_id.upper()} in {self.sheets} (rpi-hwid-sheet "
                              "list shows the sheets there are; rpi-hwid-sheet new starts one)")
        return Sheet(**json.loads(p.read_text()))

    def save(self, sheet: Sheet) -> None:
        """Write the sheet's file whole or not at all."""
        self.sheets.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.sheets, prefix=f".{sheet.id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(sheet), f, indent=1, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.path(sheet.id))
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
