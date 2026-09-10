"""The data model: what a probe found, as typed, immutable records.

The probe scripts (``rpi_hwid.probe``, ``rpi_hwid.fpga``) emit plain JSON,
because they run on a Pi's python3 3.5 where dataclasses do not exist. Every
consumer on this side of the wire works with the records here instead, built
by ``ProbeDocument.from_dict`` from that JSON and written back with
``to_dict``; a field the probe does not know is an error at load time, so a
probe that changed shape is noticed rather than silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class Mac:
    """A soldered-down network interface's MAC: ``eth`` or ``wlan``."""

    kind: str
    mac: str


@dataclass(frozen=True)
class UsbNetAdapter:
    """A removable USB network adapter, with the descriptors a label needs."""

    iface: str
    mac: str
    vidpid: str
    kind: str                      # ethernet | wifi
    driver: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    usb_serial: str | None = None
    bcd_usb: str | None = None     # "3.00"
    usb_speed: str | None = None   # "5000", as sysfs reports it

    @property
    def title(self) -> str:
        return " ".join(x for x in (self.manufacturer, self.product) if x) or self.vidpid


@dataclass(frozen=True)
class FpgaBoard:
    """An FPGA board the Pi hosts, by whatever identity was readable."""

    kind: str                      # netv2 | arty | acorn | jtag | unknown-fpga
    serial: str | None = None      # Digilent FT2232 serial (Arty)
    dna: str | None = None         # Xilinx Device DNA, 0x-prefixed
    idcode: str | None = None
    flash: str | None = None
    flash_jedec: str | None = None

    @property
    def identity(self) -> str | None:
        """The immutable identifier: the DNA when read, else the serial."""
        return self.dna or self.serial


@dataclass(frozen=True)
class Summary:
    """The probe's fixed-shape verdict for one board: a Raspberry Pi, or
    another single-board computer the probe knows (an Orange Pi PC), which
    has no revision code, no header verdict and an undetermined power
    class, and is told apart by ``compatible`` and ``model``."""

    model: str
    serial: str
    revision: str
    power_class: str
    compatible: str = ""           # the device tree's compatible list, space-joined
    memory: str | None = None      # the fitted RAM, "1 GB", from MemTotal
    header: tuple[str, ...] = ()
    hat_uuid: str | None = None
    fpga: tuple[FpgaBoard, ...] = ()
    macs: tuple[Mac, ...] = ()
    usb_net: tuple[UsbNetAdapter, ...] = ()
    rtc_battery: bool | None = None
    fan: bool | None = None
    max_current_ma: int | None = None
    ext5v_v: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Summary:
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"summary has fields this model does not know: {sorted(unknown)}")
        return cls(
            model=d["model"], serial=d["serial"] or "", revision=d["revision"] or "",
            power_class=d["power_class"],
            compatible=d.get("compatible") or "", memory=d.get("memory"),
            header=tuple(d.get("header", ())), hat_uuid=d.get("hat_uuid"),
            fpga=tuple(FpgaBoard(**b) for b in d.get("fpga", ())),
            macs=tuple(Mac(**m) for m in d.get("macs", ())),
            usb_net=tuple(UsbNetAdapter(**u) for u in d.get("usb_net", ())),
            rtc_battery=d.get("rtc_battery"), fan=d.get("fan"),
            max_current_ma=d.get("max_current_ma"), ext5v_v=d.get("ext5v_v"),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("header", "fpga", "macs", "usb_net"):
            d[key] = list(d[key])
        return d


@dataclass
class ProbeDocument:
    """Everything the probe wrote for one host: the summary as a record,
    the evidence it was drawn from as it came off the wire."""

    host: str
    summary: Summary
    evidence: dict[str, Any] = field(default_factory=dict)
    collected_by: str = ""

    @classmethod
    def from_dict(cls, host: str, d: dict[str, Any]) -> ProbeDocument:
        try:
            summary = Summary.from_dict(d["verdict"]["summary"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{host}: not a probe document (no verdict.summary)") from exc
        collected = d.get("_collected") or {}
        return cls(host=host, summary=summary, evidence=d, collected_by=collected.get("user", ""))

    @classmethod
    def from_json(cls, host: str, text: str) -> ProbeDocument:
        """From the probe's ``--json`` output; a login banner before it is skipped."""
        start = text.find("{")
        if start < 0:
            raise ValueError(f"{host}: no JSON in probe output: {text[-200:]!r}")
        return cls.from_dict(host, json.loads(text[start:]))

    def to_json(self) -> str:
        return json.dumps(self.evidence, indent=1) + "\n"
