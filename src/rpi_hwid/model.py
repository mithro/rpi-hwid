"""The data model: what a probe found, as typed, immutable records.

The probe scripts (``rpi_hwid.probe``, ``rpi_hwid.fpga``,
``rpi_hwid.tinytapeout``) emit plain JSON,
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
    signal: str | None = None      # which evidence settled it; see probe


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
    signal: str | None = None      # how firmly removable; see probe

    @property
    def title(self) -> str:
        return " ".join(x for x in (self.manufacturer, self.product) if x) or self.vidpid


@dataclass(frozen=True)
class FpgaBoard:
    """An FPGA board the Pi hosts, by whatever identity was readable."""

    kind: str                      # netv2 | arty | acorn | pcileech | jtag | unknown-fpga
    serial: str | None = None      # Digilent FT2232 serial (Arty)
    dna: str | None = None         # Xilinx Device DNA, 0x-prefixed
    idcode: str | None = None
    flash: str | None = None
    flash_jedec: str | None = None
    gateware: str | None = None    # pcileech-fpga gateware version, "4.14"
    gateware_id: int | None = None  # its FPGA id: a profile class, not a board
    hw_rev: str | None = None      # Cynthion board revision from bcdDevice, "1.4"
    mode: str | None = None        # analyzer | moondancer | apollo (Cynthion)
    # The ECP5's own die identifier, read with UIDCODE_PUB over JTAG and kept
    # masked to its factory 56 bits. Displayed, never keyed on: reaching it
    # costs the board's capture, so a name derived from it could not be
    # recovered without taking the board offline again.
    trace_id: str | None = None
    # Which methods read this board's DNA, and whether they agreed. An
    # identifier read two ways is only worth more than one read twice if the
    # readings are compared; a conflict is recorded rather than resolved,
    # because there is no way to tell which reading is the lie.
    dna_sources: tuple[str, ...] = ()
    dna_agree: bool | None = None
    dna_conflict: dict[str, str] | None = None
    soc_model: str | None = None   # the card its SoC says it was built for
    # The configuration flash's own unique id, with the width and the state
    # of the read beside it. The width matters: it differs by part (112 bits
    # on a Micron N25Q, 128 on a Spansion, 64 on a Winbond), and a value
    # whose length quietly changed between tool versions would be a
    # permanent mislabel. `flash_uid_state` distinguishes a part that gave
    # one up from one that has no such command and from a read that came
    # back all ones -- three different facts, one of them printable.
    flash_uid: str | None = None
    flash_uid_bits: int | None = None
    flash_uid_state: str | None = None   # read | blank | none
    # Why, where the part itself says so. `none` on its own is only the
    # reading tool saying it knows no unique-id command for this part, which
    # is not evidence the silicon has none; `none` with a note is the chip
    # having been asked and answered, as a Macronix does in its security
    # register. Only the second is a fact a label may carry.
    flash_uid_note: str | None = None
    # What stopped a flash read that was attempted, in the reading tool's
    # words, so a refused label can say why the obvious command will not do.
    flash_error: str | None = None
    # RDID bytes 4-6 where the flash's family defines them: what tells an
    # N25Q128 from an MT25QL128, or an S25FL256S from an S25FS256S.
    flash_extended_id: str | None = None
    # The SFDP revision the flash answered with, "none" where it answered
    # without one, None where that is unknown: what tells an S25FL127S (which
    # has RSFDP) from an S25FL128S (which has not).
    flash_sfdp: str | None = None
    # How the flash was read: "pcie" by an Acorn's own SoC, "jtag" through a
    # bridge that replaced the running design.
    flash_source: str | None = None

    @property
    def identity(self) -> str | None:
        """The immutable identifier: the DNA when read, else the serial.

        An ECP5 board has no Xilinx Device DNA, so a Cynthion falls through to
        its serial, which is its configuration flash's unique id.
        """
        return self.dna or self.serial


@dataclass(frozen=True)
class TinyTapeoutBoard:
    """A Tiny Tapeout demo board on the Pi's USB, with the chip it carries
    as its ROM described it."""

    usb_serial: str | None = None      # the demo board's RP2 flash unique id
    mcu: str | None = None             # RP2040 | RP2350
    shuttle: str | None = None         # "tt06"; None when no ROM answered
    chip: str | None = None            # asic | fpga | None
    repo: str | None = None            # from the chip ROM
    commit: str | None = None
    demoboard: str | None = None       # as the SDK detected it: "TT06+"
    demoboard_version: str | None = None   # what shipped with the kit: "v2.0.1"
    sdk: str | None = None             # SDK release on the board

    @property
    def identity(self) -> str | None:
        return self.usb_serial


@dataclass(frozen=True)
class SdrDevice:
    """A software-defined radio the host carries, as ``rpi_hwid.sdr`` read
    it: identity where the device has one, and what it said of its own
    capabilities. Nothing here is derived from a datasheet; that happens
    on the label side, and is marked there."""

    kind: str                      # krakensdr | rtl-sdr | pluto | usdr
    vidpid: str | None = None
    usb_serial: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    channel_serials: tuple[str, ...] = ()   # a KrakenSDR's five, in channel order
    hub: str | None = None                 # the KrakenSDR's own hub, vid:pid
    pcie_id: str | None = None
    pcie_subsystem: str | None = None
    usdr_family: str | None = None         # m2_lm7_1: the LMS7002M M.2 cards
    driver: str | None = None
    pcie_dsn: str | None = None
    pcie_link: str | None = None
    iio_uri: str | None = None
    hw_model: str | None = None
    hw_model_variant: str | None = None
    hw_serial: str | None = None
    fw_version: str | None = None
    rf_chip: str | None = None             # ad9363a, as the IIO context names it
    xo_hz: int | None = None
    rx_lo_hz: tuple[int, int] | None = None     # (min, max), as the driver accepts
    tx_lo_hz: tuple[int, int] | None = None
    rx_rate_hz: tuple[int, int] | None = None
    tx_rate_hz: tuple[int, int] | None = None
    rx_bw_hz: tuple[int, int] | None = None
    tx_bw_hz: tuple[int, int] | None = None
    rx_channels: int | None = None
    tx_channels: int | None = None
    adc_bits: int | None = None
    # Read by opening the radio, which only an opt-in read does (--sdr-open)
    usdr_hwid: str | None = None           # the M2_LM7_1 card's HWID register, hex
    usdr_error: str | None = None          # why the card could not be read: not answering
    fpga_devid: str | None = None          # the IDCODE its golden image was built for
    flash_jedec: str | None = None         # its configuration flash
    flash_uid: str | None = None           # that flash's factory serial, where it has one
    flash_uid_state: str | None = None     # read | none
    flash_uid_note: str | None = None      # why, where the chip itself says so
    tuner: str | None = None               # an RTL2832U's tuner, as librtlsdr names it
    rtl_model: str | None = None           # which RTL2832U dongle, where evidence says

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SdrDevice:
        def pair(v: Any) -> tuple[int, int] | None:
            return (int(v[0]), int(v[1])) if v else None

        fields_ = dict(d)
        fields_["channel_serials"] = tuple(d.get("channel_serials", ()))
        for k in ("rx_lo_hz", "tx_lo_hz", "rx_rate_hz", "tx_rate_hz", "rx_bw_hz", "tx_bw_hz"):
            fields_[k] = pair(d.get(k))
        return cls(**fields_)


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
    tinytapeout: tuple[TinyTapeoutBoard, ...] = ()
    macs: tuple[Mac, ...] = ()
    usb_net: tuple[UsbNetAdapter, ...] = ()
    sdr: tuple[SdrDevice, ...] = ()
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
            fpga=tuple(FpgaBoard(**dict(b, dna_sources=tuple(b.get("dna_sources", ()))))
                       for b in d.get("fpga", ())),
            tinytapeout=tuple(TinyTapeoutBoard(**b) for b in d.get("tinytapeout", ())),
            macs=tuple(Mac(**m) for m in d.get("macs", ())),
            usb_net=tuple(UsbNetAdapter(**u) for u in d.get("usb_net", ())),
            sdr=tuple(SdrDevice.from_dict(r) for r in d.get("sdr", ())),
            rtc_battery=d.get("rtc_battery"), fan=d.get("fan"),
            max_current_ma=d.get("max_current_ma"), ext5v_v=d.get("ext5v_v"),
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("header", "fpga", "tinytapeout", "macs", "usb_net", "sdr"):
            d[key] = list(d[key])
        # nested tuples too, or the document does not round-trip through JSON
        for board in d["fpga"]:
            board["dna_sources"] = list(board["dna_sources"])
        for radio in d["sdr"]:
            for k, v in radio.items():
                if isinstance(v, tuple):
                    radio[k] = list(v)
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
