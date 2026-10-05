"""Just enough IPP (RFC 8010/8011) to print a PDF and follow the job.

The standard library only: a request is built here byte by byte and posted
with urllib. pyipp was tried first, and it silently drops every attribute
it has no table entry for -- media-source, media-type, print-scaling,
print-color-mode -- and cannot encode a collection at all, so the one
thing a label sheet needs, the manual feed slot (``media-col`` /
``media-source``), never reached the printer. Here every value carries its
own tag, collections included, and an attribute the printer ignores comes
back named instead of vanishing.
"""

from __future__ import annotations

import struct
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, NamedTuple

VERSION = (2, 0)

# operations (RFC 8011 5.4.15)
PRINT_JOB = 0x0002
VALIDATE_JOB = 0x0004
GET_JOB_ATTRIBUTES = 0x0009
GET_PRINTER_ATTRIBUTES = 0x000B

# delimiter tags (RFC 8010 3.5.1)
OPERATION_GROUP = 0x01
JOB_GROUP = 0x02
END = 0x03
PRINTER_GROUP = 0x04
UNSUPPORTED_GROUP = 0x05
GROUPS = {"operation": OPERATION_GROUP, "job": JOB_GROUP, "printer": PRINTER_GROUP,
          "unsupported": UNSUPPORTED_GROUP}

# value tags (RFC 8010 3.5.2)
UNSUPPORTED, UNKNOWN, NO_VALUE = 0x10, 0x12, 0x13
INTEGER, BOOLEAN, ENUM = 0x21, 0x22, 0x23
OCTET_STRING, DATE_TIME, RESOLUTION, RANGE = 0x30, 0x31, 0x32, 0x33
BEGIN_COLLECTION, TEXT_WITH_LANGUAGE, NAME_WITH_LANGUAGE, END_COLLECTION = 0x34, 0x35, 0x36, 0x37
TEXT, NAME, KEYWORD, URI, URI_SCHEME = 0x41, 0x42, 0x44, 0x45, 0x46
CHARSET, LANGUAGE, MIME_TYPE, MEMBER_NAME = 0x47, 0x48, 0x49, 0x4A

# job-state (RFC 8011 5.3.7)
JOB_PENDING, JOB_HELD, JOB_PROCESSING, JOB_STOPPED = 3, 4, 5, 6
JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED = 7, 8, 9
JOB_STATES = {3: "pending", 4: "pending-held", 5: "processing", 6: "processing-stopped",
              7: "canceled", 8: "aborted", 9: "completed"}
JOB_DONE = (JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED)
PRINTER_STATES = {3: "idle", 4: "processing", 5: "stopped"}


class IppError(RuntimeError):
    """The printer could not be reached, refused a request, or answered
    with something that is not IPP."""


class IppRefusedError(IppError):
    """The printer answered and refused the request: it will not act on it.
    `status` is the IPP status code it gave (0 when not known)."""

    status = 0


# What a printer answers when asked about a job it no longer has:
# client-error-not-found by the standard, client-error-not-possible from the
# Brother MFC-L3760CDW (seen 2026-10-05, minutes after the job had finished).
JOB_FORGOTTEN = (0x0404, 0x0406)


class Value(NamedTuple):
    tag: int
    value: Any


def keyword(v: str) -> Value:
    return Value(KEYWORD, v)


def integer(v: int) -> Value:
    return Value(INTEGER, v)


def enum(v: int) -> Value:
    return Value(ENUM, v)


def boolean(v: bool) -> Value:
    return Value(BOOLEAN, v)


def text(v: str) -> Value:
    return Value(TEXT, v)


def name(v: str) -> Value:
    return Value(NAME, v)


def uri(v: str) -> Value:
    return Value(URI, v)


def charset(v: str) -> Value:
    return Value(CHARSET, v)


def language(v: str) -> Value:
    return Value(LANGUAGE, v)


def mime(v: str) -> Value:
    return Value(MIME_TYPE, v)


def collection(members: dict[str, Value | list[Value]]) -> Value:
    return Value(BEGIN_COLLECTION, members)


Attrs = dict[str, "Value | list[Value]"]


def _field(b: bytes) -> bytes:
    return struct.pack(">H", len(b)) + b


def _plain(v: Value) -> bytes:
    """A non-collection value's length-prefixed bytes."""
    if v.tag in (INTEGER, ENUM):
        return _field(struct.pack(">i", v.value))
    if v.tag == BOOLEAN:
        return _field(b"\x01" if v.value else b"\x00")
    if v.tag in (UNSUPPORTED, UNKNOWN, NO_VALUE):
        return _field(b"")
    if isinstance(v.value, bytes):
        return _field(v.value)
    return _field(str(v.value).encode("utf-8"))


def _one(tag_name: bytes, v: Value) -> bytes:
    """One value, `tag_name` being its name (or b"" for a further value or a
    collection member's)."""
    if v.tag != BEGIN_COLLECTION:
        return bytes([v.tag]) + _field(tag_name) + _plain(v)
    out = bytes([BEGIN_COLLECTION]) + _field(tag_name) + _field(b"")
    for member, mv in v.value.items():
        out += bytes([MEMBER_NAME]) + _field(b"") + _field(member.encode())
        values = mv if isinstance(mv, list) else [mv]
        for x in values:
            out += _one(b"", x)
    return out + bytes([END_COLLECTION]) + _field(b"") + _field(b"")


def encode_attribute(attr: str, value: Value | list[Value]) -> bytes:
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ValueError(f"{attr}: no values")
    return b"".join(_one(attr.encode() if i == 0 else b"", v) for i, v in enumerate(values))


def _encode(code: int, request_id: int, groups: list[tuple[int, Attrs]], data: bytes) -> bytes:
    out = bytes(VERSION) + struct.pack(">HI", code, request_id)
    for tag, attrs in groups:
        out += bytes([tag]) + b"".join(encode_attribute(k, v) for k, v in attrs.items())
    return out + bytes([END]) + data


def encode_request(op: int, request_id: int, operation: Attrs, job: Attrs | None = None,
                   data: bytes = b"") -> bytes:
    groups: list[tuple[int, Attrs]] = [(OPERATION_GROUP, operation)]
    if job:
        groups.append((JOB_GROUP, job))
    return _encode(op, request_id, groups, data)


def encode_response(status: int, request_id: int, groups: list[tuple[int, Attrs]]) -> bytes:
    """A response, as a printer sends one (for a test's fake printer)."""
    head: Attrs = {"attributes-charset": charset("utf-8"),
                   "attributes-natural-language": language("en")}
    if groups and groups[0][0] == OPERATION_GROUP:
        head = {**head, **groups[0][1]}
        groups = groups[1:]
    return _encode(status, request_id, [(OPERATION_GROUP, head), *groups], b"")


# --- decoding ------------------------------------------------------------------


@dataclass
class Message:
    """A decoded request or response: `code` is the operation or the status."""

    version: tuple[int, int]
    code: int
    request_id: int
    groups: list[tuple[int, dict[str, list[Any]]]] = field(default_factory=list)
    data: bytes = b""

    def attributes(self, group: str) -> dict[str, list[Any]]:
        """Every attribute of the groups of that kind, merged."""
        out: dict[str, list[Any]] = {}
        for tag, attrs in self.groups:
            if tag == GROUPS[group]:
                out.update(attrs)
        return out


class _Reader:
    def __init__(self, raw: bytes) -> None:
        self.raw, self.at = raw, 0

    def take(self, n: int) -> bytes:
        if self.at + n > len(self.raw):
            raise IppError(f"IPP message truncated at byte {self.at} (wanted {n} more)")
        b = self.raw[self.at:self.at + n]
        self.at += n
        return b

    def byte(self) -> int:
        return self.take(1)[0]

    def field(self) -> bytes:
        (n,) = struct.unpack(">H", self.take(2))
        return self.take(n)


def _value(tag: int, raw: bytes) -> Any:
    if tag in (INTEGER, ENUM):
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN:
        return raw != b"\x00"
    if tag in (UNSUPPORTED, UNKNOWN, NO_VALUE):
        return None
    if tag == RESOLUTION:
        return struct.unpack(">iib", raw)
    if tag == RANGE:
        return struct.unpack(">ii", raw)
    if tag in (OCTET_STRING, DATE_TIME):
        return raw
    if tag in (TEXT_WITH_LANGUAGE, NAME_WITH_LANGUAGE):
        r = _Reader(raw)
        r.field()
        return r.field().decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def _collection(r: _Reader) -> dict[str, list[Any]]:
    """The members of a collection whose begCollection has been read, up to
    and including its endCollection."""
    out: dict[str, list[Any]] = {}
    member = None
    while True:
        tag = r.byte()
        r.field()                                   # a member's name field is empty
        if tag == END_COLLECTION:
            r.field()
            return out
        if tag == MEMBER_NAME:
            member = r.field().decode()
            out[member] = []
            continue
        if member is None:
            raise IppError("IPP collection value with no member name")
        if tag == BEGIN_COLLECTION:
            r.field()
            out[member].append(_collection(r))
        else:
            out[member].append(_value(tag, r.field()))


def decode(raw: bytes, request: bool = False) -> Message:
    """A response (or, for a fake printer, a request) as a Message."""
    r = _Reader(raw)
    major, minor = r.byte(), r.byte()
    code, request_id = struct.unpack(">HI", r.take(6))
    msg = Message((major, minor), code, request_id)
    attrs: dict[str, list[Any]] | None = None
    last = None
    while True:
        tag = r.byte()
        if tag == END:
            break
        if tag < 0x10:                              # a new group
            attrs = {}
            msg.groups.append((tag, attrs))
            continue
        if attrs is None:
            raise IppError("IPP attribute before any group")
        attr = r.field().decode()
        if attr:
            last = attr
            attrs[last] = []
        elif last is None:
            raise IppError("IPP additional value with no attribute before it")
        if tag == BEGIN_COLLECTION:
            r.field()
            attrs[last].append(_collection(r))
        else:
            attrs[last].append(_value(tag, r.field()))
    msg.data = raw[r.at:]
    return msg


# --- the printer ---------------------------------------------------------------


@dataclass
class Job:
    id: int
    state: int
    ignored: list[str]


class Printer:
    """An IPP printer at `printer_uri` (ipp://host[:631]/path)."""

    def __init__(self, printer_uri: str, timeout: float = 30) -> None:
        if not printer_uri.startswith(("ipp://", "http://")):
            printer_uri = f"ipp://{printer_uri}/ipp/print"
        self.uri = printer_uri
        rest = printer_uri.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        if ":" not in hostport:
            hostport += ":631"
        self.http = f"http://{hostport}/{path}"
        self.where = hostport
        self.timeout = timeout
        self._id = 0

    def _call(self, op: int, operation: Attrs, job: Attrs | None = None,
              data: bytes = b"") -> Message:
        self._id += 1
        head: Attrs = {"attributes-charset": charset("utf-8"),
                       "attributes-natural-language": language("en"),
                       "printer-uri": uri(self.uri)}
        body = encode_request(op, self._id, {**head, **operation}, job, data)
        req = urllib.request.Request(self.http, data=body, method="POST",
                                     headers={"Content-Type": "application/ipp"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, OSError) as exc:
            raise IppError(f"printer {self.where}: {exc}") from exc
        try:
            msg = decode(raw)
        except (struct.error, UnicodeDecodeError, IndexError, ValueError) as exc:
            raise IppError(f"printer {self.where} answered with something that is not IPP: "
                           f"{exc}") from exc
        if msg.code >= 0x0400:
            why = msg.attributes("operation").get("status-message", [""])[0]
            refused = IppRefusedError(f"printer {self.where} refused the request: "
                                      f"status 0x{msg.code:04x}"
                                      + (f", {why}" if why else ""))
            refused.status = msg.code
            raise refused
        return msg

    def attributes(self, names: list[str]) -> dict[str, list[Any]]:
        msg = self._call(GET_PRINTER_ATTRIBUTES,
                         {"requested-attributes": [keyword(n) for n in names]})
        return msg.attributes("printer")

    def job(self, job_id: int) -> dict[str, list[Any]]:
        msg = self._call(GET_JOB_ATTRIBUTES, {
            "job-id": integer(job_id),
            "requested-attributes": [keyword("job-state"), keyword("job-state-reasons"),
                                     keyword("job-impressions-completed")]})
        return msg.attributes("job")

    def print_job(self, pdf: bytes, job_name: str, user: str = "rpi-hwid-sheet") -> Job:
        """Print `pdf` from the manual feed slot onto A4 label stock, at full
        size and in colour."""
        msg = self._call(PRINT_JOB, {
            "requesting-user-name": name(user),
            "job-name": name(job_name),
            "document-format": mime("application/pdf"),
        }, {
            "media-col": collection({
                "media-source": keyword("manual"),
                "media-type": keyword("labels"),
                "media-size": collection({"x-dimension": integer(21000),
                                          "y-dimension": integer(29700)}),
            }),
            "print-scaling": keyword("none"),
            "print-color-mode": keyword("color"),
            "sides": keyword("one-sided"),
        }, data=pdf)
        j = msg.attributes("job")
        if "job-id" not in j:
            raise IppError(f"printer {self.where} accepted the job but gave it no job-id")
        return Job(j["job-id"][0], (j.get("job-state") or [JOB_PENDING])[0],
                   sorted(msg.attributes("unsupported")))
