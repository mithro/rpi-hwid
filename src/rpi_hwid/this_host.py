"""This host's label input, read on the host: the labels ``rpi-hwid labels
--this-host`` makes, whichever tool starts it.

The Pi's facts come from the Pi-only probe (``label-input --pi-only``: no
user bus, no FPGA, no Tiny Tapeout board). The FPGA boards come from
fpgas-verify's identity where it is installed and answers -- its boards
alone, as it described them, which is what the fpgas.online site gets in
its ``fpga-board-identified`` events -- and otherwise from the FPGA module's
own passive reads. Nothing is sent to an FPGA either way, and nothing at all
when fpgas-verify started this (``FPGAS_VERIFY_IDENTITY``, rpi_hwid.fpga).
So this is the document the site builds from the same facts, and the two
can be compared with ``label_input.comparable``.

A Tiny Tapeout board is never read here: that would mean taking its demo
board's port from the service using it. fpgas-verify reads it at boot, while
it owns the port (contract 21), and its identity document carries the board
as kind "tt"; that is taken here, checked against the USB tree in sysfs (the
RP2's 2e8a:0005 with that serial: no tty is opened), and one the USB tree
does not show is left out and named in `sources`.
"""

from __future__ import annotations

from typing import Any

from rpi_hwid import cli, fpga, label_input, tinytapeout


def identity_boards(found: dict[str, Any]) -> list[dict[str, Any]] | None:
    """fpgas-verify's FPGA boards as its document gave them, reduced to
    FpgaBoard's fields; None when it is not installed or did not answer
    with a document this reads."""
    fv = found.get("fpgas_verify") or {}
    doc = fv.get("document")
    if not isinstance(doc, dict) or doc.get("schema") != fpga.IDENTITY_SCHEMA:
        return None
    version = doc.get("identity_version")
    if type(version) is not int or version != fpga.IDENTITY_VERSION:
        return None
    return [{k: b[k] for k in label_input.FPGA_FIELDS if b.get(k) is not None}
            for b in doc.get("boards") or ()
            if isinstance(b, dict) and b.get("kind") in fpga.IDENTITY_KINDS]


def identity_tinytapeout(found: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(the Tiny Tapeout boards fpgas-verify's document gives, reduced to
    TinyTapeoutBoard's fields, each one on this host's USB; the usb_serials
    of those that are not). Only a board with a usb_serial is taken: that is
    the one thing sysfs can check, and the label is keyed on it."""
    fv = found.get("fpgas_verify") or {}
    doc = fv.get("document")
    if not isinstance(doc, dict) or identity_boards(found) is None:
        return [], []
    on_usb = {u["serial"] for u in tinytapeout.usb_candidates()
              if u["id"] == "2e8a:0005" and u["serial"]}
    boards, absent = [], []
    for b in doc.get("boards") or ():
        if not isinstance(b, dict) or b.get("kind") != "tt" or not b.get("usb_serial"):
            continue
        if b["usb_serial"] not in on_usb:
            absent.append(b["usb_serial"])
            continue
        boards.append({k: b[k] for k in label_input.TT_FIELDS if b.get(k) is not None})
    return boards, absent


def label_input_document(host: str) -> dict[str, Any]:
    """The label input for this host."""
    pi = cli.pi_only_label_input(host)
    found = fpga.collect_fpga()
    summary = dict(pi["summary"])
    sources = dict(pi["sources"])
    boards = identity_boards(found)
    if boards is not None:
        # fpgas-verify's boards alone: a board only sysfs sees here (a
        # Cynthion) is one the site cannot see, and the two documents must
        # agree
        summary["fpga"] = boards
        sources["fpga"] = "fpgas-verify"
        tt, absent = identity_tinytapeout(found)
        if tt:
            summary["tinytapeout"] = tt
            sources["tinytapeout"] = "fpgas-verify"
        if absent:
            sources["tinytapeout_not_on_usb"] = absent
    else:
        summary["fpga"] = [{k: v for k, v in b.items() if k in label_input.FPGA_FIELDS}
                           for b in found["summary"]]
        sources["fpga"] = "rpi-hwid"
    return label_input.build(host, summary, sources)
