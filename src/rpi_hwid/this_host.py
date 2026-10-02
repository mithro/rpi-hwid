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

A Tiny Tapeout board is not in it: reading one here would mean taking its
demo board's port from the service using it. When fpgas-verify reports a
Tiny Tapeout board's identity (contract 21), it will come from there.
"""

from __future__ import annotations

from typing import Any

from rpi_hwid import cli, fpga, label_input


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


def label_input_document(host: str) -> dict[str, Any]:
    """The label input for this host."""
    pi = cli.pi_only_label_input(host)
    # fpgas-verify is asked whenever it is installed (contract 26): a NeTV2
    # on its harness alone, or a Tiny Tapeout board, shows nothing in sysfs
    found = fpga.collect_fpga(identify=True)
    summary = dict(pi["summary"])
    sources = dict(pi["sources"])
    boards = identity_boards(found)
    if boards is not None:
        # fpgas-verify's boards alone: a board only sysfs sees here (a
        # Cynthion) is one the site cannot see, and the two documents must
        # agree
        summary["fpga"] = boards
        sources["fpga"] = "fpgas-verify"
    else:
        summary["fpga"] = [{k: v for k, v in b.items() if k in label_input.FPGA_FIELDS}
                           for b in found["summary"]]
        sources["fpga"] = "rpi-hwid"
    return label_input.build(host, summary, sources)
