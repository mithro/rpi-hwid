"""A roll of stickers: the labels as strips (rpi_hwid.strip), one per page
of the label printer's own page size, and printing them through its CUPS
queue (labels --stock ROLL [--print QUEUE])."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import IO, Any

from reportlab.lib.colors import HexColor, black
from reportlab.pdfgen import canvas

from rpi_hwid import labels, micro, strip
from rpi_hwid.sheet import ipp

Row = tuple[str, str, str, Callable[[Any, Any], None], Any]


class NoStripError(ValueError):
    """A label kind has no strip layout, so it cannot go on a roll."""


def roll_rows(docs: Any, only: set[str], pinned_names: Any = None,
              order: Any = None) -> list[Row]:
    """(host, kind, title, strip draw, record) per sticker: the whole labels
    in labels.all_labels' order, then the micro labels two to a sticker."""
    rows: list[Row] = []
    for host, kind, title, draw, record in labels.whole_labels(  # type: ignore[no-untyped-call]
            docs, only, pinned_names, order):
        if draw not in strip.STRIP_OF:
            raise NoStripError(f"{host}: the {kind} label has no strip layout, "
                               f"so it cannot go on a roll")
        rows.append((host, kind, title, strip.STRIP_OF[draw], record))
    micros = [m for _kind, m in micro.selected(docs, only)]
    for group in micro.pack(micros, per_sticker=2):
        present = [m for m in group if m is not None]
        rows.append((present[0].host, "micro", " | ".join(m.listing_title for m in present),
                     micro.draw_pair, group))
    return rows


def page_point(roll: labels.Roll, u: float, v: float) -> tuple[float, float]:
    """Where the strip's point (u along it, v up it; reportlab's y-up
    coordinates) lands on the page: the strip turned a quarter
    anticlockwise into the roll's strip box."""
    bx, by, bw, _bh = roll.strip_box
    return bx + bw - v, by + u


def _place(c: Any, roll: labels.Roll) -> None:
    """Turn the canvas so the strip draws at (0, 0) in its own coordinates;
    page_point is the same transform, written out for the tests."""
    bx, by, bw, _bh = roll.strip_box
    c.translate(bx + bw, by)
    c.rotate(90)


def render_roll(docs: Any, out: str | IO[bytes], roll: labels.Roll, only: set[str],
                outline: bool = False, pinned_names: Any = None, order: Any = None,
                invariant: bool = False) -> tuple[int, int]:
    """Write the roll's PDF to `out`: one page per sticker. Returns (labels,
    stickers). `outline` draws the sticker's edge and the strip's, for a
    proof on plain paper."""
    labels.register_fonts()  # type: ignore[no-untyped-call]
    rows = roll_rows(docs, set(only), pinned_names, order)
    c = canvas.Canvas(out if hasattr(out, "write") else str(out), pagesize=roll.page,
                      invariant=1 if invariant else 0)
    c.setTitle("Hardware identity labels")
    c.setAuthor("rpi-hwid labels")
    for i, (_host, _kind, _title, draw, record) in enumerate(rows):
        if i:
            c.showPage()
        if outline:
            c.setStrokeColor(HexColor("#bbbbbb"))
            c.setLineWidth(0.3)
            c.rect(0, 0, *roll.page, stroke=1, fill=0)
            c.setStrokeColor(black)
        c.saveState()
        _place(c, roll)
        s = strip.Strip(c, 0, 0)
        if outline:
            s.outline()  # type: ignore[no-untyped-call]
        draw(s, record)
        c.restoreState()
    c.showPage()
    c.save()
    n = sum(sum(m is not None for m in r[4]) if r[1] == "micro" else 1 for r in rows)
    return n, len(rows)


class PrintError(RuntimeError):
    """The roll was not printed; the message says why."""


def queue_uri(queue: str) -> str:
    """A CUPS queue name on this machine, or a printer's own ipp:// URI."""
    return queue if "://" in queue else f"ipp://localhost:631/printers/{queue}"


def job_attributes(roll: labels.Roll) -> ipp.Attrs:
    """The roll's size as CUPS lists it, no scaling, and 300 x 600 dpi --
    which is how CUPS offers the DYMO driver's DymoPrintQuality=Graphics,
    the quality for codes."""
    x, y = roll.media_size
    return {
        "media-col": ipp.collection({"media-size": ipp.collection({
            "x-dimension": ipp.integer(x), "y-dimension": ipp.integer(y)})}),
        "print-scaling": ipp.keyword("none"),
        "printer-resolution": ipp.resolution(300, 600),
    }


def _size(x: int, y: int) -> str:
    """A media size given in 1/100 mm."""
    return f"{x / 100:g} x {y / 100:g} mm"


def print_pdf(pdf: bytes, queue: str, roll: labels.Roll, job_name: str,
              poll: float = 1.0, wait: float = 120.0) -> str:
    """Send `pdf` to `queue` and wait for the job to end; returns
    "completed". Refuses, before sending, a queue that is unreachable,
    stopped, not accepting jobs, or has no such roll size; raises PrintError
    for a job that ends any other way or is still going after `wait`
    seconds."""
    uri = queue_uri(queue)
    pr = ipp.Printer(uri)
    try:
        a = pr.attributes(["printer-state", "printer-state-message",
                           "printer-is-accepting-jobs", "media-size-supported"])
        if (a.get("printer-state") or [3])[0] == 5:
            why = (a.get("printer-state-message") or [""])[0] or "no reason given"
            raise PrintError(f"{queue}: the printer is stopped: {why}")
        if not (a.get("printer-is-accepting-jobs") or [True])[0]:
            raise PrintError(f"{queue}: the queue is not accepting jobs")
        sizes = sorted({(m["x-dimension"][0], m["y-dimension"][0])
                        for m in a.get("media-size-supported", []) if isinstance(m, dict)})
        if tuple(roll.media_size) not in sizes:
            has = ", ".join(_size(x, y) for x, y in sizes) or "no sizes"
            raise PrintError(f"{queue}: the queue has no {_size(*roll.media_size)} size "
                             f"({roll.ppd_size}); it supports {has}: is it on DYMO's driver, "
                             f"printer-driver-dymo's lw450.ppd?")
        job = pr.print_document(pdf, job_name, job_attributes(roll))
        deadline = time.monotonic() + wait
        while True:
            j = pr.job(job.id)
            state = (j.get("job-state") or [ipp.JOB_PENDING])[0]
            if state in ipp.JOB_DONE:
                break
            if time.monotonic() >= deadline:
                raise PrintError(f"{queue}: job {job.id} is still "
                                 f"{ipp.JOB_STATES.get(state, state)} after {wait:g} s: "
                                 f"see lpstat -W all -o {queue}")
            time.sleep(poll)
    except ipp.IppError as exc:
        raise PrintError(f"{uri}: {exc}") from exc
    name = ipp.JOB_STATES.get(state, str(state))
    if state != ipp.JOB_COMPLETED:
        why = ", ".join(map(str, j.get("job-state-reasons", []))) or "no reason given"
        raise PrintError(f"{queue}: job {job.id} {name}: {why}")
    return name
