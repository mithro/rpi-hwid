"""The rpi-hwid command line.

    rpi-hwid probe [--json] [--fpga] [--jtag] [--flash]   on a Pi: what is this?
    rpi-hwid fpga [--json] [--jtag] [--flash]             on a Pi: which FPGA board?
    rpi-hwid collect --out DIR [-J JUMP] [--fpga] HOST…  over ssh: one JSON per host
    rpi-hwid labels --data DIR --out labels.pdf           print-ready labels from that data
    rpi-hwid name --netv2 DNA… | --arty SERIAL…           the derived board names
    rpi-hwid revision CODE…                               decode Pi revision codes
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rpi_hwid import names, revision


def cmd_probe(args: argparse.Namespace) -> int:
    from rpi_hwid import probe

    doc = probe.collect()
    doc["verdict"] = probe.verdict(doc)
    if args.fpga or args.jtag:
        from rpi_hwid import fpga

        fpga.merge_fpga(doc, fpga.collect_fpga(args.jtag, args.flash))
    if args.json:
        print(json.dumps(doc, indent=1))
        return 0
    print(f"{doc['model']}  serial {doc['serial']}  rev {doc['revision']}")
    v = doc["verdict"]
    for h in v["header"]:
        print("  header : " + h)
    for e in v["evidence"]:
        print("  signal : " + e)
    print("  power  : " + v["power"])
    if "fpga" in v:
        from rpi_hwid import fpga

        fpga.describe(v["fpga"])
    for i in doc["interfaces"]:
        if i["onboard"]:
            print(f"  onboard: {i['kind']:6s} {i['mac']}  {i['driver']}")
    for u in doc["usb_net"]:
        print(f"  usb net: {u['vidpid']} {u['manufacturer'] or ''} {u['product'] or ''}"
              f"  {u['mac']}  {u['kind']}")
    return 0


def cmd_fpga(args: argparse.Namespace) -> int:
    from rpi_hwid import fpga

    f = fpga.collect_fpga(args.jtag, args.flash)
    if args.json:
        print(json.dumps(f, indent=1))
    else:
        fpga.describe(f["boards"])
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    from rpi_hwid.collect import collect

    results = collect(
        args.hosts, args.out, users=tuple(args.users.split(",")), jump=args.jump,
        fpga=args.fpga, jtag_hosts=tuple(args.jtag or ()), flash_hosts=tuple(args.flash or ()),
        workers=args.workers,
    )
    failed = 0
    for r in results:
        if r.ok and r.doc is not None:
            s = r.doc["verdict"]["summary"]
            boards = ", ".join(
                b.get("dna") or b.get("serial") or b["kind"] for b in s.get("fpga", [])
            )
            print(f"  {r.host}: {s['model']}; header {s['header'] or 'bare'}; "
                  f"power {s['power_class']}" + (f"; fpga {boards}" if boards else ""))
        else:
            failed += 1
            print(f"  {r.host}: FAILED ({r.error})")
    print(f"{len(results) - failed} of {len(results)} host(s) written to {args.out}")
    return 1 if failed else 0


def cmd_labels(args: argparse.Namespace) -> int:
    from rpi_hwid import labels

    return labels.main(args.rest)


def cmd_name(args: argparse.Namespace) -> int:
    if args.netv2:
        for dna in args.netv2:
            print(f"{names.netv2_name(dna)}  {names.normalise_dna(dna)}")
    if args.arty:
        pinned = json.loads(Path(args.names).read_text()) if args.names else None
        for serial, name in names.arty_names(args.arty, pinned).items():
            if serial in args.arty:
                print(f"{name}  {serial}")
    return 0


def cmd_revision(args: argparse.Namespace) -> int:
    for code in args.codes:
        r = revision.decode_revision(code)
        print(f"{code}: Raspberry Pi {r.model}, {r.memory}, Rev {r.revision}, {r.soc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rpi-hwid", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="what this Pi wears and what powers it (run on the Pi)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fpga", action="store_true", help="also look for an FPGA board")
    p.add_argument("--jtag", action="store_true", help="also drive JTAG for idcode and DNA")
    p.add_argument("--flash", action="store_true",
                   help="also identify an Arty's SPI flash (reloads the FPGA)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("fpga", help="which FPGA board is attached (run on the Pi)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--jtag", action="store_true")
    p.add_argument("--flash", action="store_true")
    p.set_defaults(func=cmd_fpga)

    p = sub.add_parser("collect", help="probe hosts over ssh, one JSON file each")
    p.add_argument("hosts", nargs="+", help="host or user@host")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("-J", "--jump", help="ssh jump host")
    p.add_argument("--users", default="tim,pi", help="users to try, in order")
    p.add_argument("--fpga", action="store_true", help="append the FPGA module on every host")
    p.add_argument("--jtag", action="append", metavar="HOST",
                   help="drive JTAG on this host (implies --fpga for it)")
    p.add_argument("--flash", action="append", metavar="HOST",
                   help="identify the Arty flash on this host (reloads the FPGA)")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("labels", help="print-ready labels from collected data",
                       add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_labels)

    p = sub.add_parser("name", help="derived board names")
    p.add_argument("--netv2", nargs="*", metavar="DNA")
    p.add_argument("--arty", nargs="*", metavar="SERIAL")
    p.add_argument("--names", help="JSON registry of Arty serial -> name to honour")
    p.set_defaults(func=cmd_name)

    p = sub.add_parser("revision", help="decode Raspberry Pi revision codes")
    p.add_argument("codes", nargs="+")
    p.set_defaults(func=cmd_revision)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
