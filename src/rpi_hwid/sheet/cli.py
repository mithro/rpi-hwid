"""rpi-hwid-sheet: print labels a few at a time onto a part-used sheet.

    rpi-hwid-sheet new --printer ipp://10.1.20.222/ipp/print
    rpi-hwid-sheet print K7QX rpi5-433mhz [-- --esp32]   # collect, show, ask, print
    rpi-hwid-sheet print K7QX rpi5-433mhz --prepare      # collect and show only...
    rpi-hwid-sheet commit K7QX-p3-2026-10-01T101200      # ...then print it
    rpi-hwid-sheet status K7QX
    rpi-hwid-sheet list
    rpi-hwid-sheet mark K7QX 1 2 5c --why "peeled off before the tool"

Each sheet has an id (four characters, printed in its top and bottom
margins on its first pass with when, where and by whom it was started)
and one state file recording which of its slots are used. A print reads
the hosts afresh with ``rpi-hwid collect`` (kept, never deleted, under
the state directory's reads/), asks ``rpi-hwid labels`` which labels that
makes and to draw them in the sheet's free slots, shows what will be
printed and where, and only after a yes sends it to the printer's manual
feed slot. The slots are recorded as used when the printer takes the job;
a job cancelled before anything printed gives them back.

This tool draws nothing itself: every label comes from ``rpi-hwid labels
--place``, run as a separate command (``--rpi-hwid`` says which).

--prepare is for when the yes comes from somewhere other than this
terminal -- a Claude Code session, say, that shows the person the preview
and the values, and runs ``commit`` once they agree. A plan is made
against the sheet as it stood: if anything was printed on the sheet
since, commit refuses it.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rpi_hwid.sheet import ipp, state

# Test hooks for the confirmation prompt: None means the real thing.
ASK: dict[str, Any] = {"input": None, "isatty": None}

FEED = ("Put sheet {id} in the printer's manual feed slot (one sheet; the job waits for it), "
        "the TOP EDGE going in first.")
DEFAULT_WAIT = 600.0


class ToolError(RuntimeError):
    """Something to tell the person, and exit 2."""


def now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def stamp(t: datetime.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H%M%S")


def version() -> str:
    from rpi_hwid import __version__
    return __version__


# --- asking rpi-hwid ---------------------------------------------------------------


class RpiHwid:
    """The rpi-hwid command this tool asks for labels."""

    def __init__(self, command: str) -> None:
        self.argv = shlex.split(command)

    def run(self, *args: str, log: Path | None = None) -> str:
        cmd = [*self.argv, *args]
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if log is not None:
            log.write_text(f"$ {shlex.join(cmd)}\n{r.stdout}{r.stderr}"
                           f"[exit {r.returncode}]\n")
            sys.stderr.write(r.stdout + r.stderr)
        if r.returncode != 0:
            raise ToolError(f"{shlex.join(cmd)} failed (exit {r.returncode}):\n"
                            f"{r.stderr.strip() or r.stdout.strip()}")
        return r.stdout


def collect(rh: RpiHwid, store: state.Store, sheet_id: str, hosts: list[str], tasmota: bool,
            extra: list[str]) -> Path:
    """Read the hosts afresh into reads/<sheet>/<time>/, the log beside it."""
    when = stamp(now())
    out = store.root / "reads" / sheet_id / when
    out.parent.mkdir(parents=True, exist_ok=True)
    log = out.parent / f"{when}.log"
    rh.run("tasmota" if tasmota else "collect", "--out", str(out), *extra, *hosts, log=log)
    missing = [h for h in hosts if not (out / f"{bare(h)}.json").exists()]
    if missing:
        raise ToolError(f"the read has nothing for {', '.join(missing)}: every label printed "
                        f"must come from a fresh read (see {log})")
    return out


def bare(host: str) -> str:
    """user@host -> host, as the read names its documents."""
    return host.rsplit("@", 1)[-1]


def list_labels(rh: RpiHwid, data: Path, only: list[str]) -> list[dict[str, str]]:
    args = ["labels", "--data", str(data), "--list", "--json"]
    for k in only:
        args += ["--only", k]
    return json.loads(rh.run(*args))  # type: ignore[no-any-return]


def choose(labels: list[dict[str, str]], hosts: list[str], picks: list[str]
           ) -> list[dict[str, str]]:
    """The labels of `hosts` (all, if none are named), narrowed to those
    whose id contains one of `picks`, if any."""
    names = {bare(h) for h in hosts}
    chosen = [x for x in labels if not hosts or x["host"] in names]
    if not chosen:
        raise ToolError(f"no labels for {', '.join(hosts) or 'this data'}"
                        " (rpi-hwid labels --list shows what the data makes)")
    if picks:
        for p in picks:
            if not any(p in x["id"] for x in chosen):
                raise ToolError(f"--label {p!r} matches none of: "
                                + "; ".join(x["id"] for x in chosen))
        chosen = [x for x in chosen if any(p in x["id"] for p in picks)]
    return chosen


# --- the plan --------------------------------------------------------------------------


def plan_dir(store: state.Store, sheet: state.Sheet) -> Path:
    base = f"{sheet.id}-p{len(sheet.passes) + 1}-{stamp(now())}"
    d = store.root / "plans" / base
    n = 1
    while d.exists():
        n += 1
        d = store.root / "plans" / f"{base}-{n}"
    d.mkdir(parents=True)
    return d


def preview(rh: RpiHwid, data: Path, only: list[str], plan: dict[str, Any],
            sheet: state.Sheet, d: Path) -> Path | None:
    """preview.png: the pass outlined, on the sheet with its used slots
    greyed. None without pdftoppm."""
    if shutil.which("pdftoppm") is None:
        return None
    (d / "preview-plan.json").write_text(json.dumps({**plan, "outline": True}, indent=1))
    place(rh, data, only, d / "preview-plan.json", d / "preview.pdf")
    dpi = 60
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", str(d / "preview.pdf"),
                    str(d / "preview")], check=True)
    png = d / "preview.png"
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return png
    from rpi_hwid import labels, micro
    img = Image.open(png).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    px = dpi / 72.0

    def box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        return (x * px, img.height - (y + h) * px, (x + w) * px, img.height - y * px)

    for slot in sheet.slots:
        n, q = state.parse_slot(slot)
        x, y = labels.label_origin(n - 1)  # type: ignore[no-untyped-call]
        if q:
            x, y = micro.micro_origin(x, y, state.QUARTERS.index(q))
            r = box(x, y, micro.MICRO_W, micro.MICRO_H)
        else:
            r = box(x, y, labels.LABEL_W, labels.LABEL_H)
        draw.rectangle(r, fill=(150, 150, 150, 110), outline=(90, 90, 90, 255))
        draw.text((r[0] + 4, r[1] + 4), f"{slot} used", fill=(40, 40, 40, 255))
    img.save(png)
    return png


def place(rh: RpiHwid, data: Path, only: list[str], plan: Path, out: Path) -> None:
    args = ["labels", "--data", str(data), "--place", str(plan), "--out", str(out)]
    for k in only:
        args += ["--only", k]
    rh.run(*args)


def summary(sheet: state.Sheet, meta: dict[str, Any], png: Path | None) -> str:
    lines = [f"Sheet {sheet.id} ({sheet.note()}), pass {len(sheet.passes) + 1}, "
             f"printer {meta['printer']}"]
    if meta["marked"]:
        lines.append("  first pass: the sheet's id, note and registration ticks go in its "
                     "margins")
    lines.append("  slot  label")
    for label, slot, _host, _title in meta["placed"]:
        lines.append(f"  {slot:<5} {label}")
    if meta["guides"]:
        lines.append(f"  cut guides on sticker{'s' if len(meta['guides']) > 1 else ''} "
                     + ", ".join(meta["guides"]))
    lines.append(f"  data: {meta['data']}")
    lines.append(f"  preview: {png}" if png else "  preview: none (no pdftoppm)")
    lines.append(f"  pdf: {meta['pdf']}")
    return "\n".join(lines)


# --- commands --------------------------------------------------------------------------


def who() -> tuple[str, str]:
    return socket.gethostname().split(".")[0], getpass.getuser()


def printer_uri(given: str | None) -> str:
    uri = given or os.environ.get("RPI_HWID_PRINTER")
    if not uri:
        raise ToolError("which printer? Give --printer ipp://HOST/ipp/print, or set "
                        "RPI_HWID_PRINTER")
    return uri


def new_sheet(store: state.Store, printer: str | None) -> state.Sheet:
    host, user = who()
    s = state.Sheet.new(state.new_id(set(store.ids())), printer_uri(printer), host, user,
                        version(), now().isoformat(timespec="seconds"))
    store.save(s)
    return s


def cmd_new(args: argparse.Namespace, store: state.Store) -> int:
    s = new_sheet(store, args.printer)
    print(f"{s.id}  {s.note()}  -> {store.path(s.id)}")
    return 0


def free_text(s: state.Sheet) -> str:
    f, q = len(s.free_stickers()), len(s.free_quarters())
    return (f"{f} free sticker{'' if f == 1 else 's'}, "
            f"{q} free quarter{'' if q == 1 else 's'}")


def cmd_list(args: argparse.Namespace, store: state.Store) -> int:
    for i in store.ids():
        s = store.load(i)
        print(f"{s.id}  {s.note()}  {free_text(s)}  {len(s.passes)} pass"
              f"{'' if len(s.passes) == 1 else 'es'}")
    return 0


def cmd_status(args: argparse.Namespace, store: state.Store) -> int:
    s = store.load(args.sheet)
    print(f"Sheet {s.id}: {s.note()}")
    print(f"printer {s.printer}; {free_text(s)}; margins "
          + ("printed" if s.marked else "not yet printed"))
    for row in range(7):
        cells = []
        for col in range(3):
            n = row * 3 + col + 1
            if str(n) in s.slots:
                cells.append(f"{n:>2} [####]")
            elif any(f"{n}{q}" in s.slots for q in state.QUARTERS):
                cells.append(f"{n:>2} [" + "".join(
                    "#" if f"{n}{q}" in s.slots else q for q in state.QUARTERS) + "]")
            else:
                cells.append(f"{n:>2} [    ]")
        print("  " + "   ".join(cells))
    for slot in sorted(s.slots, key=lambda k: state.parse_slot(k)):
        v = s.slots[slot]
        what = (f"pass {v['pass']}  {v['label']}" if "label" in v
                else f"marked used: {v.get('why', '')}")
        print(f"  {slot:<4} {what}")
    for p in s.passes:
        print(f"  pass {p['pass']}: {p['at']} on {p['host']} by {p['user']}, job {p['job']} "
              f"{p['job_state']}, data {p['data']}")
    return 0


def cmd_mark(args: argparse.Namespace, store: state.Store) -> int:
    with store.lock(args.sheet):
        s = store.load(args.sheet)
        try:
            s.mark(args.slots, args.why, now().isoformat(timespec="seconds"))
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        store.save(s)
    print(f"sheet {s.id}: {', '.join(args.slots)} marked used; {free_text(s)}")
    return 0


def cmd_print(args: argparse.Namespace, store: state.Store) -> int:
    rh = RpiHwid(args.rpi_hwid)
    if args.sheet.lower() == "new":
        sheet = new_sheet(store, args.printer)
        print(f"new sheet {sheet.id}")
    else:
        sheet = store.load(args.sheet)
    if not args.hosts and args.data is None:
        raise ToolError("which hosts? Name them, or give --data DIR to print from a read")
    if args.data is not None:
        data = args.data.resolve()
    else:
        data = collect(rh, store, sheet.id, args.hosts, args.tasmota, args.extra)
    chosen = choose(list_labels(rh, data, args.only), args.hosts, args.label)
    for x in chosen:
        before = [k for k, v in sheet.slots.items() if v.get("label") == x["id"]]
        if before:
            print(f"note: {x['id']} is already on this sheet, in {', '.join(before)}",
                  file=sys.stderr)
    try:
        at, guides = sheet.allocate([state.Want(x["id"], x["size"]) for x in chosen])
    except state.SheetFullError as exc:
        raise ToolError(str(exc)) from exc
    slot_of = dict(at)
    plan: dict[str, Any] = {"labels": [{"id": i, "slot": s} for i, s in at], "guides": guides}
    if not sheet.marked:
        plan["sheet"] = {"id": sheet.id, "note": sheet.note()}
    d = plan_dir(store, sheet)
    (d / "plan.json").write_text(json.dumps(plan, indent=1))
    place(rh, data, args.only, d / "plan.json", d / "pass.pdf")
    meta = {"sheet": sheet.id, "revision": sheet.revision(), "data": str(data),
            "only": args.only, "printer": printer_uri(args.printer or sheet.printer),
            "placed": [[x["id"], slot_of[x["id"]], x["host"], x["title"]] for x in chosen],
            "guides": guides, "marked": "sheet" in plan, "pdf": str(d / "pass.pdf"),
            "made": now().isoformat(timespec="seconds"), "committed": None}
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    png = preview(rh, data, args.only, plan, sheet, d)
    print(summary(sheet, meta, png))
    if args.prepare:
        again = "" if store.root == state.default_root() else f" --state {store.root}"
        print(f"rpi-hwid-sheet{again} commit {d.name}")
        return 0
    isatty = ASK["isatty"] or sys.stdin.isatty
    if not isatty():
        raise ToolError("no terminal to ask: use --prepare, check the plan, then "
                        f"rpi-hwid-sheet commit {d.name}")
    ask = ASK["input"] or input
    n = len(chosen)
    reply = ask(f"Print {n} label{'' if n == 1 else 's'} on sheet {sheet.id} through the "
                "manual feed? [y/N] ")
    if reply.strip().lower() not in ("y", "yes"):
        print("not printed; the plan is kept in " + str(d))
        return 1
    return commit(store, d, args.poll, args.wait)


def cmd_commit(args: argparse.Namespace, store: state.Store) -> int:
    d = Path(args.plan)
    if not d.is_absolute() and not d.exists():
        d = store.root / "plans" / args.plan
    if not (d / "meta.json").exists():
        raise ToolError(f"no plan {args.plan} (in {store.root / 'plans'})")
    return commit(store, d, args.poll, args.wait)


def write_meta(d: Path, meta: dict[str, Any]) -> None:
    (d / "meta.json").write_text(json.dumps(meta, indent=1))


def set_pass(store: state.Store, sheet_id: str, n: int, **fields: Any) -> state.Sheet:
    """Update pass `n` of the sheet as it is on disk now, under its lock:
    never a copy held while waiting, which would undo what another session
    recorded meanwhile."""
    with store.lock(sheet_id):
        s = store.load(sheet_id)
        s.pass_(n).update(fields)
        store.save(s)
    return s


def commit(store: state.Store, d: Path, poll: float, wait: float) -> int:
    meta = json.loads((d / "meta.json").read_text())
    pr = ipp.Printer(meta["printer"])
    attrs = pr.attributes(["printer-state", "printer-state-reasons", "printer-make-and-model",
                           "media-source-supported"])
    pstate = (attrs.get("printer-state") or [0])[0]
    if pstate == 5:
        raise ToolError(f"printer {meta['printer']} is stopped: "
                        + ", ".join(map(str, attrs.get("printer-state-reasons", []))))
    if "manual" not in attrs.get("media-source-supported", []):
        raise ToolError(f"printer {meta['printer']} has no manual feed slot (media sources: "
                        + ", ".join(map(str, attrs.get("media-source-supported", []))) + ")")
    host, user = who()
    sid = meta["sheet"]
    # Used from before the printer has it: a slot wrongly kept is a sticker
    # wasted, one wrongly freed is a label printed over another. Checked and
    # recorded under the sheet's lock, so two sessions cannot both send.
    with store.lock(sid):
        meta = json.loads((d / "meta.json").read_text())
        if meta["committed"] is not None:
            raise ToolError(f"plan {d.name} was already sent, as job {meta['committed']}")
        sheet = store.load(sid)
        if sheet.revision() != meta["revision"]:
            raise ToolError(f"sheet {sheet.id} has changed since plan {d.name} was made: "
                            "print again to make a new plan")
        n = len(sheet.passes) + 1
        sheet.record_pass([tuple(p) for p in meta["placed"]], meta["guides"],
                          at=now().isoformat(timespec="seconds"), host=host, user=user,
                          job=None, job_state="sending", data=meta["data"],
                          marked=meta["marked"], plan=d.name)
        store.save(sheet)
        meta["committed"] = "sending"
        write_meta(d, meta)
    slots = ", ".join(slot for _, slot, *_ in meta["placed"])
    try:
        job = pr.print_job((d / "pass.pdf").read_bytes(), f"rpi-hwid-sheet {sid} pass {n}",
                           user=user)
    except ipp.IppRefusedError as exc:
        # the printer answered, and said no: nothing will print
        with store.lock(sid):
            s = store.load(sid)
            if s.passes and s.passes[-1]["pass"] == n:
                s.drop_pass(n)
                store.save(s)
                meta["committed"] = None
                write_meta(d, meta)
        raise ToolError(f"{exc}; nothing printed, slots {slots} are free again") from exc
    except ipp.IppError as exc:
        set_pass(store, sid, n, job_state="unknown")
        raise ToolError(f"{exc}; the printer may have the job, so slots {slots} of sheet "
                        f"{sid} stay used. Check the printer, then rpi-hwid-sheet status "
                        f"{sid}") from exc
    set_pass(store, sid, n, job=job.id, job_state="sent")
    meta["committed"] = job.id
    write_meta(d, meta)
    print(f"job {job.id} sent to {attrs.get('printer-make-and-model', ['the printer'])[0]}.")
    if job.ignored:
        print(f"warning: the printer ignored {', '.join(job.ignored)}", file=sys.stderr)
    print(FEED.format(id=sid))
    return follow(store, sid, d, pr, job.id, n, poll, wait)


def cmd_follow(args: argparse.Namespace, store: state.Store) -> int:
    """Wait again for a sheet's outstanding job, one that outlasted --wait."""
    sheet = store.load(args.sheet)
    open_ = [p for p in sheet.passes if p["job_state"] not in ("completed", "canceled", "aborted")
             and p.get("job") is not None]
    if not open_:
        raise ToolError(f"sheet {sheet.id} has nothing outstanding to follow")
    last = open_[-1]
    d = store.root / "plans" / last.get("plan", "")
    if not last.get("plan"):
        # a pass recorded before passes named their plan: the plan sent as its job
        for m in sorted((store.root / "plans").glob(f"{sheet.id}-*/meta.json")):
            if json.loads(m.read_text()).get("committed") == last["job"]:
                d = m.parent
    if not (d / "meta.json").exists():
        raise ToolError(f"no plan for sheet {sheet.id}'s job {last['job']}")
    meta = json.loads((d / "meta.json").read_text())
    return follow(store, sheet.id, d, ipp.Printer(meta["printer"]), last["job"],
                  last["pass"], args.poll, args.wait)


def follow(store: state.Store, sid: str, d: Path, pr: ipp.Printer, job_id: int, n: int,
           poll: float, wait: float) -> int:
    """Wait for the job to end and record how it did."""
    deadline = time.monotonic() + wait
    js: int = ipp.JOB_PENDING
    done: int | None = None
    told = False
    while True:
        a = pr.job(job_id)
        js = (a.get("job-state") or [ipp.JOB_PENDING])[0]
        # how many sheets it printed, only if the printer says: unknown is not none
        raw = a.get("job-impressions-completed") or [None]
        done = raw[0] if isinstance(raw[0], int) and not isinstance(raw[0], bool) else None
        if js == ipp.JOB_STOPPED and not told:
            why = pr.attributes(["printer-state-reasons"]).get("printer-state-reasons", [])
            if any(str(r).startswith("media-needed") for r in why):
                print(f"the printer is waiting for sheet {sid} in its manual feed slot")
                told = True
        if js in ipp.JOB_DONE or time.monotonic() >= deadline:
            break
        time.sleep(poll)
    name = ipp.JOB_STATES.get(js, str(js))
    if js == ipp.JOB_COMPLETED:
        s = set_pass(store, sid, n, job_state=name)
        print(f"sheet {sid} pass {n} printed; {free_text(s)}")
        return 0
    if js in (ipp.JOB_CANCELED, ipp.JOB_ABORTED) and done == 0:
        with store.lock(sid):
            s = store.load(sid)
            s.pass_(n)["job_state"] = name
            last = s.passes[-1]["pass"] == n
            if last:
                s.drop_pass(n)
            store.save(s)
        if last:
            meta = json.loads((d / "meta.json").read_text())
            meta["committed"] = None
            write_meta(d, meta)
            print(f"job {job_id} {name} before printing anything: the slots are free again "
                  f"and plan {d.name} can be sent again", file=sys.stderr)
        else:
            print(f"job {job_id} {name} before printing anything, but sheet {sid} has passes "
                  f"after pass {n}: its slots stay used (rpi-hwid-sheet status {sid})",
                  file=sys.stderr)
        return 1
    set_pass(store, sid, n, job_state=name)
    if js in ipp.JOB_DONE:
        what = "an unknown number of" if done is None else str(done)
        print(f"job {job_id} {name} after {what} sheet(s): its slots stay used; check the "
              f"sheet (rpi-hwid-sheet status {sid})", file=sys.stderr)
    else:
        print(f"job {job_id} is still {name} after {wait:g}s: its slots stay used; once "
              f"it ends, rpi-hwid-sheet follow {sid} records how", file=sys.stderr)
    return 1


# --- the command line ---------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rpi-hwid-sheet", description=__doc__.split("\n")[0],
                                 epilog="See docs/SHEETS.md.")
    ap.add_argument("--state", type=Path, default=None,
                    help="state directory (default $XDG_STATE_HOME/rpi-hwid)")
    ap.add_argument("--rpi-hwid", default=f"{shlex.quote(sys.executable)} -m rpi_hwid.cli",
                    help="the rpi-hwid command to ask for labels (default: this install's)")
    ap.add_argument("--poll", type=float, default=2.0, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="start a sheet")
    p.add_argument("--printer", help="IPP printer URI (default $RPI_HWID_PRINTER)")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list", help="the sheets and what is free on each")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="one sheet's slots and passes")
    p.add_argument("sheet")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("mark", help="record slots used without printing")
    p.add_argument("sheet")
    p.add_argument("slots", nargs="+", metavar="SLOT", help="1-21, or 1a-21d")
    p.add_argument("--why", default="marked used by hand")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("print", help="collect, show, ask, and print in the free slots "
                                     "(rpi-hwid-sheet print SHEET HOST... [-- COLLECT-ARGS])")
    p.add_argument("sheet", help="the sheet's id, or 'new'")
    p.add_argument("hosts", nargs="*", metavar="HOST")
    p.add_argument("--data", type=Path, help="print from this read instead of collecting")
    p.add_argument("--tasmota", action="store_true",
                   help="read with rpi-hwid tasmota rather than collect")
    p.add_argument("--only", action="append", default=[], metavar="KIND",
                   help="as rpi-hwid labels --only")
    p.add_argument("--label", action="append", default=[], metavar="TEXT",
                   help="only the labels whose id contains this")
    p.add_argument("--printer", help="for a new sheet (default $RPI_HWID_PRINTER)")
    p.add_argument("--prepare", action="store_true",
                   help="make the plan and preview only; rpi-hwid-sheet commit sends it")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT,
                   help="seconds to wait for the job to finish (default %(default)g)")
    p.set_defaults(func=cmd_print)

    p = sub.add_parser("commit", help="send a prepared plan to the printer")
    p.add_argument("plan")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("follow", help="wait again for a sheet's last job and record its end")
    p.add_argument("sheet")
    p.add_argument("--wait", type=float, default=DEFAULT_WAIT)
    p.set_defaults(func=cmd_follow)
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, extra = argv[:at], argv[at + 1:]
    # progress and errors in the order they happen, even into a pipe or a log
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    args = parser().parse_args(argv)
    args.extra = extra
    store = state.Store(args.state or state.default_root())
    try:
        return int(args.func(args, store))
    except (ToolError, ipp.IppError, state.NoSuchSheetError) as exc:
        print(f"rpi-hwid-sheet: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


