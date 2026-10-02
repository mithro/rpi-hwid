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
          sources: dict[str, str] | None = None) -> dict[str, Any]:
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


def comparable(doc: dict[str, Any]) -> str:
    """``dumps`` without ``sources``: what two builders of one host's label
    input must agree on byte for byte. Who read a field is provenance, not
    data, and a Pi and the site legitimately differ on it."""
    out = load(doc)
    del out["sources"]
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
        for key, who in sorted(sources.items()):
            if key not in PI_FIELDS:
                problems.append(f"sources.{key}: not a summary field")
            elif who not in SOURCES:
                problems.append(f"sources.{key}: {who!r} is not one of {list(SOURCES)}")
    return problems


def _check_record(where: str, d: dict[str, Any], cls: type) -> list[str]:
    """What is wrong with `d` as the JSON form of dataclass `cls`."""
    hints = typing.get_type_hints(cls)
    known = {f.name for f in fields(cls)}
    problems = [f"{where}.{k}: not a field of {cls.__name__}" for k in sorted(set(d) - known)]
    for f in fields(cls):
        # A record in a list is nothing without the fields it cannot be
        # built without (a board's kind, an adapter's MAC). The summary's own
        # are different: any of them may be missing, and the label that
        # needs one says so.
        if cls is not Summary and f.default is MISSING and d.get(f.name) is None:
            problems.append(f"{where}.{f.name}: required in every {cls.__name__}")
            continue
        if f.name not in d:
            continue
        problems += _check_value(f"{where}.{f.name}", d[f.name], hints[f.name], f.name)
    return problems


def _check_value(where: str, value: Any, hint: Any, name: str) -> list[str]:
    """What is wrong with `value` as JSON for a field annotated `hint`."""
    if value is None:
        return []  # absent: the same as the key missing
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
            if name in ITEM_TYPES:
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
        item = (_record_schema(ITEM_TYPES[name]) if name in ITEM_TYPES
                else _json_type(typing.get_args(hint)[0], name))
        return {"type": ["array", "null"], "items": item}
    if origin is dict or hint is dict:
        return {"type": ["object", "null"]}
    simple = {bool: "boolean", int: "integer", float: "number", str: "string"}
    if hint in simple:
        return {"type": [simple[hint], "null"]}
    raise TypeError(f"{name}: no schema for {hint}")


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
                       "A field that is null or absent was not read.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "version", "host", "summary", "sources"],
        "properties": {
            "schema": {"const": SCHEMA},
            "version": {"type": "integer", "const": VERSION},
            "host": {"type": "string", "minLength": 1},
            "summary": _record_schema(Summary),
            "sources": {"type": "object", "additionalProperties": False,
                        "properties": {k: {"enum": list(SOURCES)} for k in PI_FIELDS}},
        },
    }
