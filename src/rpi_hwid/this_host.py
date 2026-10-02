"""This host's label input, read on the host: the labels ``rpi-hwid labels
--this-host`` makes, whichever tool starts it.

The Pi's facts come from the Pi-only probe (``label-input --pi-only``: no
user bus, no FPGA, no Tiny Tapeout board) and the FPGA boards' from
fpgas-verify's identity where it is installed, with the FPGA module's own
passive reads around it -- and nothing sent to an FPGA when fpgas-verify
started this (``FPGAS_VERIFY_IDENTITY``, rpi_hwid.fpga). This is the
document the fpgas.online site builds from the same facts, so the two can
be compared with ``label_input.comparable``.
"""

from __future__ import annotations

from typing import Any

from rpi_hwid import cli, fpga, label_input


def label_input_document(host: str) -> dict[str, Any]:
    """The label input for this host."""
    pi = cli.pi_only_label_input(host)
    found = fpga.collect_fpga()
    summary = dict(pi["summary"])
    summary["fpga"] = [{k: v for k, v in b.items() if k in label_input.FPGA_FIELDS}
                       for b in found["summary"]]
    sources = dict(pi["sources"])
    sources["fpga"] = ("fpgas-verify" if (found.get("fpgas_verify") or {}).get("read")
                       else "rpi-hwid")
    return label_input.build(host, summary, sources)
