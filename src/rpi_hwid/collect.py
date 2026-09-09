"""Run the probe on many Pis over ssh and keep one JSON file per host.

The probe (``rpi_hwid.probe``) is fed to ``python3 -`` on the host over
stdin, so nothing is installed there; it needs passwordless sudo for
i2c-tools and vcgencmd. Each host is tried with each user in turn, and a
host that no user can reach is reported, never skipped silently.

    rpi-hwid collect --out data/ rpi5-netv2 pi@10.21.1.10 -J welland.fpgas.online
    rpi-hwid collect --out data/ --fpga --jtag rpi5-netv2      # with the FPGA module

The output files are the input to ``rpi-hwid labels``.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

DEFAULT_USERS = ("tim", "pi")


def probe_source(fpga: bool = False, jtag: bool = False, flash: bool = False) -> str:
    """The script to feed to ``python3 -`` on a host.

    The Pi probe alone, or the Pi probe followed by the FPGA module and a
    few lines that run both and merge the FPGA findings into the Pi
    document. Both files skip their own ``main()`` when embedded.
    """
    pkg = resources.files("rpi_hwid")
    probe = pkg.joinpath("probe.py").read_text()
    if not fpga:
        return probe
    fpga_src = pkg.joinpath("fpga.py").read_text()
    glue = (
        "\n\n_doc = collect()\n_doc['verdict'] = verdict(_doc)\n"
        f"merge_fpga(_doc, collect_fpga({jtag!r}, {flash!r}))\n"
        "print(json.dumps(_doc, indent=1))\n"
    )
    return "RPI_HWID_EMBEDDED = True\n" + probe + "\n" + fpga_src + glue


@dataclass
class Result:
    host: str
    ok: bool
    user: str = ""
    doc: dict | None = None
    error: str = ""


def parse_probe_json(stdout: str) -> dict:
    """The probe's ``--json`` output; a login banner before it is skipped."""
    start = stdout.find("{")
    if start < 0:
        raise ValueError(f"no JSON in probe output: {stdout[-200:]!r}")
    doc = json.loads(stdout[start:])
    if "verdict" not in doc or "summary" not in doc["verdict"]:
        raise ValueError("probe output has no verdict.summary")
    return doc


def probe_host(
    host: str,
    users: Sequence[str] = DEFAULT_USERS,
    jump: str | None = None,
    fpga: bool = False,
    jtag: bool = False,
    flash: bool = False,
    timeout: int = 180,
) -> Result:
    """Run the probe on one host; `host` may carry its own ``user@``."""
    source = probe_source(fpga, jtag, flash)
    args = ["--json"]
    if "@" in host:
        user_list: Sequence[str] = [host.split("@", 1)[0]]
        target = host.split("@", 1)[1]
    else:
        user_list, target = users, host
    last = ""
    for user in user_list:
        cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=accept-new"]
        if jump:
            cmd += ["-J", jump]
        cmd += [f"{user}@{target}", "python3 - " + " ".join(args)]
        try:
            r = subprocess.run(cmd, input=source, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Result(host, False, error="timed out")
        if r.returncode == 0:
            try:
                return Result(host, True, user, parse_probe_json(r.stdout))
            except ValueError as exc:
                return Result(host, False, user, error=str(exc))
        last = r.stderr.strip()[-200:]
        if "Permission denied" not in last and "Connection closed" not in last:
            break
    return Result(host, False, error=last or "no user could log in")


def collect(
    hosts: Sequence[str],
    out_dir: Path,
    users: Sequence[str] = DEFAULT_USERS,
    jump: str | None = None,
    fpga: bool = False,
    jtag_hosts: Sequence[str] = (),
    flash_hosts: Sequence[str] = (),
    workers: int = 4,
) -> list[Result]:
    """Probe every host and write ``<out_dir>/<host>.json`` for each success.

    `fpga` appends the FPGA module for every host; `jtag_hosts` and
    `flash_hosts` name the hosts whose JTAG may be driven (and whose Arty
    flash may be read, which reloads the FPGA).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    def one(host: str) -> Result:
        return probe_host(host, users, jump, fpga or host in jtag_hosts,
                          host in jtag_hosts, host in flash_hosts)

    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, h): h for h in hosts}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)
            if res.ok and res.doc is not None:
                name = res.host.split("@", 1)[-1].replace("/", "_")
                res.doc["_collected"] = {"host": res.host, "user": res.user}
                (out_dir / f"{name}.json").write_text(json.dumps(res.doc, indent=1) + "\n")
    return sorted(results, key=lambda r: r.host)


def load_collected(data_dir: Path) -> dict[str, dict]:
    """Every ``*.json`` under `data_dir` as host -> probe document."""
    docs: dict[str, dict] = {}
    for path in sorted(data_dir.glob("*.json")):
        doc = json.loads(path.read_text())
        if "verdict" not in doc or "summary" not in doc["verdict"]:
            raise ValueError(f"{path}: not a probe document")
        docs[path.stem] = doc
    if not docs:
        raise ValueError(f"no probe documents (*.json) in {data_dir}")
    return docs
