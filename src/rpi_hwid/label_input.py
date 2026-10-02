"""The label input document: what the labels need, in a documented,
versioned shape that something other than rpi-hwid's own probe can write.

    {"schema": "rpi-hwid/label-input", "version": 1, "host": "pi-sw2-p48",
     "summary": {...}, "sources": {"serial": "registration", "fpga": "fpgas-verify"}}

``summary`` uses the field names of ``rpi_hwid.model.Summary`` (and, in its
``fpga`` and ``tinytapeout`` lists, of ``FpgaBoard`` and
``TinyTapeoutBoard``) verbatim. ``null`` is "not read". A field that is
absent is filled in with the record's default -- the value rpi-hwid's own
probe writes for it, ``[]`` for a list and ``""`` for ``compatible`` -- so a
document built from only the fields that were sent reads, and writes, the
same as the Pi's. The one exception is ``header``, whose ``[]`` means "read,
and nothing is on the header": an absent ``header`` is ``null``, not read.
``sources`` says, per top-level summary field, who read it.
docs/LABEL-INPUT.md is the reference; the JSON Schema ships beside this
module (``schema_path()``).

The document is written only by ``dumps``: normalised, sorted keys,
one-space indent, a trailing newline. A Pi and the fpgas.online site that
build the same document therefore write the same bytes; ``comparable`` is
that text without ``sources``, which is what the two compare.

Nothing here touches hardware or starts a process: the site calls it from
its web workers.
"""

from __future__ import annotations

import json
import math
import types
import typing
from dataclasses import MISSING, fields, is_dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from rpi_hwid.model import FpgaBoard, Mac, ProbeDocument, Summary, TinyTapeoutBoard, UsbNetAdapter

SCHEMA = "rpi-hwid/label-input"
VERSION = 1
SCHEMA_FILE = "label-input-v1.schema.json"

# What rpi-hwid itself writes in `sources`, per summary field. Other builders
# write what they like there (contract 20): the site, its event names.
# Who read a field. "rpi-hwid": this package's probe, on the host.
# "fpgas-verify": the fpgas.online verifier, on the host. "registration":
# what the host told the fpgas.online site when it registered. "site": typed
# in on the site by a person.
SOURCES = ("rpi-hwid", "fpgas-verify", "registration", "site")

# The field names each part of `summary` takes, for a builder that has more
# than this (fpgas-verify's per-board dict carries extras such as `variant`
# and `bdf`) and must keep only these.
PI_FIELDS = tuple(f.name for f in fields(Summary))
FPGA_FIELDS = tuple(f.name for f in fields(FpgaBoard))
TT_FIELDS = tuple(f.name for f in fields(TinyTapeoutBoard))

# The free-form objects in a summary, by what the probe writes in them:
# key -> the types its value may have (None: may be null), a nested object
# as a dict of its own, a list as [item type]. A key not listed is refused,
# as anywhere else; the label code reads these keys and no others.
_STR, _INT, _BOOL = (str, None), (int, None), (bool, None)
DMI_KEYS = ("sys_vendor", "product_name", "product_version", "product_serial",
            "product_uuid", "board_vendor", "board_name", "board_version", "board_serial",
            "chassis_serial", "bios_vendor", "bios_version", "bios_date")
OBJECTS: dict[str, dict[str, Any]] = {
    "dmi": dict(dict.fromkeys(DMI_KEYS, _STR), unread=[str]),
    "riscv": {
        "harts": _INT, "isa": _STR, "mmu": _STR, "uarch": _STR,
        "mvendorid": _STR, "marchid": _STR, "mimpid": _STR, "eeprom_error": _STR,
        "eeprom": ({
            "format": _INT, "product_id": _STR, "product": _STR, "pcb_revision": _INT,
            "bom_revision": _STR, "bom_variant": _INT, "serial": _STR,
            "manuf_test_status": _STR, "mac": _STR, "crc": _STR, "crc_ok": _BOOL,
        }, None),
    },
}
# FpgaBoard.dna_conflict: method -> the DNA it read
CONFLICT = "dna_conflict"

# Summary fields for which an absent key is "not read" rather than the
# default: an empty header is a reading ("HAT none"), so it cannot also be
# what a builder that never read the header leaves out.
ABSENT_IS_UNREAD = ("header",)

# The records each list in `summary` holds.
ITEM_TYPES: dict[str, type] = {
    "fpga": FpgaBoard, "tinytapeout": TinyTapeoutBoard, "macs": Mac, "usb_net": UsbNetAdapter,
}


class InputError(ValueError):
    """A document that is not a label input this version can read; `problems`
    lists every reason, not only the first."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def schema_path() -> Path:
    """The JSON Schema for version 1, as installed with the package."""
    return Path(str(resources.files("rpi_hwid").joinpath(SCHEMA_FILE)))


def _default(f: Any, cls: type) -> Any:
    """What an absent field of `cls` is: its default, as JSON; null where it
    has none, or where absence means not read (ABSENT_IS_UNREAD)."""
    if f.default is MISSING or (cls is Summary and f.name in ABSENT_IS_UNREAD):
        return None
    return list(f.default) if isinstance(f.default, tuple) else f.default


def _record(d: dict[str, Any], cls: type) -> dict[str, Any]:
    """`d` with every field of `cls` present, in no particular order: an
    absent one is its default (``_default``), an explicit null stays null, a
    tuple is a list, and a whole number in a float field is a float (5 and
    5.0 are one value, and must be one text). Unknown keys are kept, for
    check() to name."""
    hints = typing.get_type_hints(cls)
    out: dict[str, Any] = {}
    for f in fields(cls):
        v = d[f.name] if f.name in d else _default(f, cls)
        if isinstance(v, tuple):
            v = list(v)
        elif (isinstance(v, int) and not isinstance(v, bool)
              and float in typing.get_args(hints[f.name])):
            v = float(v)
        out[f.name] = v
    for key in set(d) - set(out):
        out[key] = d[key]
    return out


def normalise(doc: dict[str, Any]) -> dict[str, Any]:
    """`doc` with its summary filled out to every field of Summary, and each
    record in its lists to every field of that record (``_record``). A
    free-form object (`dmi`, `riscv`) is left as it is. Two documents that
    say the same thing normalise to the same dict, whether their builders
    wrote a default out or left it to be filled in."""
    out = dict(doc)
    summary = doc.get("summary")
    if isinstance(summary, dict):
        filled = _record(summary, Summary)
        for key, cls in ITEM_TYPES.items():
            if isinstance(filled.get(key), (list, tuple)):
                filled[key] = [_record(item, cls) if isinstance(item, dict) else item
                               for item in filled[key]]
        out["summary"] = filled
    if isinstance(doc.get("sources"), dict):
        out["sources"] = dict(doc["sources"])
    return out


def _parse(doc: dict[str, Any] | str | bytes) -> Any:
    if isinstance(doc, (str, bytes)):
        try:
            return json.loads(doc)
        except ValueError as exc:
            raise InputError([f"not JSON: {exc}"]) from exc
    return doc


def load(doc: dict[str, Any] | str | bytes) -> dict[str, Any]:
    """A label input, from a dict or its JSON text, checked and normalised;
    InputError naming every problem when it is not one this version reads."""
    raw = _parse(doc)
    problems = check(raw)
    if problems:
        raise InputError(problems)
    return normalise(raw)


def build(host: str, summary: dict[str, Any],
          sources: dict[str, Any] | None = None) -> dict[str, Any]:
    """A version-1 label input for `host` from a summary dict, checked and
    normalised: what the builder left out is filled in as ``normalise``
    says, and an explicit null stays "not read"."""
    return load({"schema": SCHEMA, "version": VERSION, "host": host,
                 "summary": dict(summary), "sources": dict(sources or {})})


def from_probe(host: str, probe_doc: dict[str, Any]) -> dict[str, Any]:
    """The label input for a probe document (``rpi-hwid probe --json``, or a
    file ``rpi-hwid collect`` wrote): its ``verdict.summary``, every field
    of which rpi-hwid read."""
    try:
        summary = probe_doc["verdict"]["summary"]
    except (KeyError, TypeError) as exc:
        raise InputError([f"{host}: not a probe document (no verdict.summary)"]) from exc
    if not isinstance(summary, dict):
        raise InputError([f"{host}: verdict.summary is not an object"])
    return build(host, summary, dict.fromkeys(summary, "rpi-hwid"))


def _text(d: dict[str, Any]) -> str:
    try:
        return json.dumps(d, sort_keys=True, indent=1, ensure_ascii=True,
                          allow_nan=False) + "\n"
    except ValueError as exc:     # a NaN or an infinity in a free-form object
        raise InputError([f"not writable as JSON: {exc}"]) from exc


def dumps(doc: dict[str, Any]) -> str:
    """The one serialisation of a label input: checked, normalised, sorted
    keys, one-space indent, ASCII, a trailing newline."""
    return _text(load(doc))


# What two reads of one host legitimately differ on (contract 25): the 5 V
# input as the PMIC's ADC measured it, the USB-C current the firmware last
# negotiated, and which evidence settled each MAC. They stay in the document
# and on the labels as read; they are left out of the comparison.
MEASURED = ("ext5v_v", "max_current_ma")
MEASURED_MAC = ("signal",)


def comparable(doc: dict[str, Any]) -> str:
    """``dumps`` without ``sources`` and without what is measured afresh on
    each read (MEASURED, each MAC's ``signal``): what two builders of one
    host's label input must agree on byte for byte. Who read a field is
    provenance, not data, and a reading taken at boot and one taken at
    label time differ on a voltage without either being wrong."""
    out = load(doc)
    del out["sources"]
    summary = out["summary"]
    for key in MEASURED:
        del summary[key]
    if summary.get("macs"):
        summary["macs"] = [{k: v for k, v in m.items() if k not in MEASURED_MAC}
                           for m in summary["macs"]]
    return _text(out)


def is_label_input(doc: Any) -> bool:
    """Whether `doc` says it is a label input (of any version)."""
    return isinstance(doc, dict) and doc.get("schema") == SCHEMA


def to_probe_document(doc: dict[str, Any] | str | bytes) -> ProbeDocument:
    """The record the label code reads, from a label input."""
    d = load(doc)
    summary = d["summary"]
    # not read is None on the record too, header included: a header nobody
    # read must never be drawn as a bare one
    kept = {k: v for k, v in summary.items() if v is not None or k in ABSENT_IS_UNREAD}
    for key in ITEM_TYPES:
        if kept.get(key) is not None:
            kept[key] = [{k: v for k, v in item.items() if v is not None}
                         for item in kept[key]]
    return ProbeDocument(host=d["host"], summary=Summary.from_dict(kept, partial=True),
                         evidence=d)


def check(doc: Any) -> list[str]:
    """Every reason `doc` is not a version-1 label input, or []."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    if doc.get("schema") != SCHEMA:
        return [f"schema is {doc.get('schema')!r}, not {SCHEMA!r}"]
    if type(doc.get("version")) is not int or doc.get("version") != VERSION:
        # a version this code was not taught is refused, not read hopefully
        return [f"version {doc.get('version')!r}: this rpi-hwid reads version {VERSION} only"]
    problems = []
    extra = set(doc) - {"schema", "version", "host", "summary", "sources"}
    if extra:
        problems.append(f"unknown top-level keys {sorted(extra)}")
    if not isinstance(doc.get("host"), str) or not doc.get("host"):
        problems.append("host: a non-empty string is required")
    summary = doc.get("summary")
    if not isinstance(summary, dict):
        problems.append("summary: an object is required")
    else:
        problems += _check_record("summary", summary, Summary)
    sources = doc.get("sources")
    if not isinstance(sources, dict):
        problems.append("sources: an object is required")
    else:
        # Provenance, free-form (contract 20): any keys, any JSON values. It
        # is not compared and no label reads it; only its JSON-ness counts.
        try:
            json.dumps(sources, allow_nan=False)
        except (TypeError, ValueError) as exc:
            problems.append(f"sources: not writable as JSON: {exc}")
    return problems


def _check_record(where: str, d: dict[str, Any], cls: type) -> list[str]:
    """What is wrong with `d` as the JSON form of dataclass `cls`."""
    hints = typing.get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    problems = [f"{where}.{k}: not a field of {cls.__name__}" for k in sorted(set(d) - known)]
    for f in fields(cls):
        # A record in a list is nothing without the fields it cannot be
        # built without (a board's kind, an adapter's MAC). The summary's own
        # are different: any of them may be missing, and missing() says
        # which label that costs.
        if cls is not Summary and f.default is MISSING and d.get(f.name) is None:
            problems.append(f"{where}.{f.name}: required in every {cls.__name__}")
            continue
        if f.name not in d:
            continue
        problems += _check_value(f"{where}.{f.name}", d[f.name], hints[f.name], f.name)
    return problems


_TYPE_WORDS = {str: "a string", int: "an integer", bool: "true or false"}


def _check_object(where: str, value: Any, spec: Any) -> list[str]:
    """What is wrong with `value` against a free-form object's spec
    (OBJECTS): a tuple of the types allowed (None for null), a list of one
    item type, or a dict of keys."""
    if isinstance(spec, list):
        if not isinstance(value, list):
            return [f"{where}: a list is required"]
        return [p for i, v in enumerate(value)
                for p in _check_object(f"{where}[{i}]", v, (spec[0],))]
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return [f"{where}: an object is required"]
        out = [f"{where}.{k}: not a field here" for k in sorted(set(value) - set(spec))]
        for key in sorted(set(value) & set(spec)):
            out += _check_object(f"{where}.{key}", value[key], spec[key])
        return out
    if value is None:
        return [] if None in spec else [f"{where}: null is not allowed here"]
    for t in spec:
        if isinstance(t, dict):
            return _check_object(where, value, t)
        if t is not None and isinstance(value, t) and not (t is int and isinstance(value, bool)):
            return []
    words = " or ".join(_TYPE_WORDS[t] for t in spec if t in _TYPE_WORDS)
    return [f"{where}: {words} is required"]


def _check_value(where: str, value: Any, hint: Any, name: str) -> list[str]:
    """What is wrong with `value` as JSON for a field annotated `hint`."""
    if value is None:
        return []  # not read
    if name in OBJECTS:
        return _check_object(where, value, OBJECTS[name])
    if name == CONFLICT:
        return _check_object(where, value, {}) if not isinstance(value, dict) else [
            p for k, v in sorted(value.items()) for p in _check_object(f"{where}.{k}", v, (str,))]
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        options = [h for h in typing.get_args(hint) if h is not type(None)]
        if len(options) != 1:
            raise TypeError(f"{where}: no checker for {hint}")
        return _check_value(where, value, options[0], name)
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            return [f"{where}: a list is required"]
        item = typing.get_args(hint)[0]
        out = []
        for i, v in enumerate(value):
            if v is None:
                # a list holds what was read; nothing unread goes in one
                out.append(f"{where}[{i}]: null is not allowed in a list")
            elif name in ITEM_TYPES:
                if not isinstance(v, dict):
                    out.append(f"{where}[{i}]: an object is required")
                else:
                    out += _check_record(f"{where}[{i}]", v, ITEM_TYPES[name])
            else:
                out += _check_value(f"{where}[{i}]", v, item, name)
        return out
    if origin is dict or hint is dict:
        return [] if isinstance(value, dict) else [f"{where}: an object is required"]
    if is_dataclass(hint):
        raise TypeError(f"{where}: no checker for {hint}")
    if hint is bool:
        return [] if isinstance(value, bool) else [f"{where}: true or false is required"]
    if hint is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
        return [] if ok else [f"{where}: an integer is required"]
    if hint is float:
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value))
        return [] if ok else [f"{where}: a finite number is required"]
    if hint is str:
        return [] if isinstance(value, str) else [f"{where}: a string is required"]
    raise TypeError(f"{where}: no checker for {hint}")


def _json_type(hint: Any, name: str) -> dict[str, Any]:
    """The JSON Schema for a field annotated `hint`; null is always allowed,
    and means not read."""
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        (inner,) = [h for h in typing.get_args(hint) if h is not type(None)]
        return _json_type(inner, name)
    if origin is tuple:
        if name in ITEM_TYPES:
            item = _record_schema(ITEM_TYPES[name])
        else:
            item = _json_type(typing.get_args(hint)[0], name)
            item["type"] = [t for t in item["type"] if t != "null"]   # no null items
        return {"type": ["array", "null"], "items": item}
    if name in OBJECTS:
        out = _object_schema(OBJECTS[name])
        out["type"] = ["object", "null"]
        return out
    if name == CONFLICT:
        return {"type": ["object", "null"], "additionalProperties": {"type": "string"}}
    if origin is dict or hint is dict:
        return {"type": ["object", "null"]}
    simple = {bool: "boolean", int: "integer", float: "number", str: "string"}
    if hint in simple:
        return {"type": [simple[hint], "null"]}
    raise TypeError(f"{name}: no schema for {hint}")


def _object_schema(spec: Any) -> dict[str, Any]:
    """The JSON Schema of a free-form object's spec (OBJECTS)."""
    names = {str: "string", int: "integer", bool: "boolean", None: "null"}
    if isinstance(spec, list):
        return {"type": "array", "items": _object_schema((spec[0],))}
    if isinstance(spec, dict):
        return {"type": "object", "additionalProperties": False,
                "properties": {k: _object_schema(v) for k, v in spec.items()}}
    nested = [t for t in spec if isinstance(t, dict)]
    if nested:
        out = _object_schema(nested[0])
        out["type"] = ["object"] + (["null"] if None in spec else [])
        return out
    return {"type": [names[t] for t in spec]}


def _record_schema(cls: type) -> dict[str, Any]:
    hints = typing.get_type_hints(cls)
    out: dict[str, Any] = {
        "type": "object", "additionalProperties": False,
        "properties": {f.name: _json_type(hints[f.name], f.name) for f in fields(cls)}}
    # a list's records need the fields they cannot be built without (check())
    required = [f.name for f in fields(cls) if cls is not Summary and f.default is MISSING]
    for name in required:
        out["properties"][name]["type"] = [
            t for t in out["properties"][name]["type"] if t != "null"]
    if required:
        out["required"] = required
    return out


def json_schema() -> dict[str, Any]:
    """The JSON Schema of a version-1 label input, derived from the records
    it carries; the shipped file (``schema_path()``) is exactly this."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/mithro/rpi-hwid/blob/main/src/rpi_hwid/" + SCHEMA_FILE,
        "title": "rpi-hwid label input, version 1",
        "description": "What rpi-hwid's labels need for one host (docs/LABEL-INPUT.md). "
                       "A null field was not read; an absent one is its default "
                       "([] for a list, \"\" for compatible), except header, "
                       "which absent is not read. The version is the integer 1 "
                       "(1.0 passes this schema and is refused by the reader).",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "version", "host", "summary", "sources"],
        "properties": {
            "schema": {"const": SCHEMA},
            "version": {"type": "integer", "const": VERSION, "multipleOf": 1},
            "host": {"type": "string", "minLength": 1},
            "summary": _record_schema(Summary),
            "sources": {"type": "object",
                        "description": "Provenance, free-form: any keys, any JSON values."},
        },
    }


# --- what each label needs ------------------------------------------------------
#
# docs/LABEL-INPUT.md has these as a table: change the two together.

# The FPGA boards keyed on a Xilinx Device DNA, whose die the IDCODE names.
XILINX_KINDS = ("netv2", "arty", "acorn", "pcileech", "jtag", "unknown-fpga")
# The FPGA boards that have a label (contract 10). A Fomu has none yet, and a
# Tiny Tapeout board has its own; either is kept in the document, and makes
# no fpga[i] label.
FPGA_LABEL_KINDS = (*XILINX_KINDS, "cynthion")
TT_NEEDS = ("usb_serial", "mcu", "chip", "demoboard")


def missing(doc: dict[str, Any] | str | bytes) -> dict[str, list[str]]:
    """Every label this document describes, each with the fields it still
    needs: ``{"board": [], "fpga[0]": ["dna", "flash_uid"], ...}``.

    The keys are ``board`` (the Pi's, or the Orange Pi's, RISC-V board's or
    PC's own label), and ``fpga[i]``, ``tinytapeout[i]`` and ``usb_net[i]``
    by position in those lists; field names are the record's own. A label
    whose list is empty can be made. A list that is empty or null describes no
    labels at all, so a document of the Pi's facts alone has no ``fpga[i]``
    keys until the FPGA's are added. A summary that names no board this
    package labels has no ``board`` key.

    The labels refuse what this reports (``rpi-hwid labels``, and the
    rendering calls): a label is never made with a field missing.
    """
    d = load(doc)
    s = d["summary"]
    out: dict[str, list[str]] = {}
    board = _board_needs(s, to_probe_document(d).summary)
    if board is not None:
        out["board"] = board
    for i, b in enumerate(s["fpga"] or ()):
        if b["kind"] in FPGA_LABEL_KINDS:
            out[f"fpga[{i}]"] = _fpga_needs(b)
    for i, t in enumerate(s["tinytapeout"] or ()):
        need = [k for k in TT_NEEDS if t[k] is None]
        if t["chip"] == "asic" and t["shuttle"] is None:
            need.append("shuttle")       # an ASIC is named by its shuttle
        out[f"tinytapeout[{i}]"] = need
    for i in range(len(s["usb_net"] or ())):
        out[f"usb_net[{i}]"] = []        # its required fields are the record's own
    return out


def _board_needs(s: dict[str, Any], record: Summary) -> list[str] | None:
    """What the board label still needs, or None when there is no such label."""
    from rpi_hwid import boards
    from rpi_hwid.revision import decode_revision

    if not s["model"] and not s["compatible"] and not s["dmi"] and not s["riscv"]:
        return ["model"]                 # nothing says what the board is
    kind = boards.board_kind(record)     # the probe's own classifier
    if kind == "other":
        return None
    need = []
    # a PC's firmware may carry no serial at all, which its label says
    if kind != "x86" and not s["serial"]:
        need.append("serial")
    if s["macs"] is None:
        need.append("macs")
    if kind == "rpi":
        pi5 = "Pi 5" in (s["model"] or "")
        try:
            pi5 = decode_revision(s["revision"]).is_pi5 or pi5
        except (TypeError, ValueError):
            need.append("revision")      # absent, or no board this code names
        if s["header"] is None:
            need.append("header")        # [] is a header read as bare; null is unread
        if pi5:
            need += [k for k in ("fan", "rtc_battery") if s[k] is None]
    elif kind == "opi":
        need += [k for k in ("compatible", "memory") if not s[k]]
        if s["header"] is None:
            need.append("header")
    elif kind == "riscv":
        need += [] if s["riscv"] else ["riscv"]
    elif kind == "x86":
        need += [] if s["dmi"] else ["dmi"]
    return need


def _fpga_needs(b: dict[str, Any]) -> list[str]:
    """What one FPGA board's label still needs."""
    kind = b["kind"]
    need: list[str] = []
    if kind == "cynthion":
        # named by its die's TraceID; its flash from its revision's BOM, and
        # its flash uid is its USB serial
        need += [k for k in ("trace_id", "hw_rev", "serial") if b[k] is None]
        return need
    if kind in XILINX_KINDS:
        need += [k for k in ("dna", "idcode") if b[k] is None]
    if kind == "arty" and b["serial"] is None:
        need.append("serial")            # the FT2232's, which names an Arty
    if b["flash_jedec"] is None:
        need.append("flash_jedec")
    state = b["flash_uid_state"]
    if state is None or state == "blank":
        need.append("flash_uid_state")   # never read, or read as all ones/zeroes
    elif state == "read" and not b["flash_uid"]:
        need.append("flash_uid")
    elif state == "none" and not b["flash_uid_note"]:
        # "none" alone is the reading tool knowing no command for the part,
        # not the part saying it has no id
        need.append("flash_uid_note")
    return need
