"""A roll of stickers: the labels as strips (rpi_hwid.strip), one per page
of the label printer's own page size, and printing them through its CUPS
queue (labels --stock ROLL [--print QUEUE])."""

from __future__ import annotations

import sys
from collections.abc import Callable
from time import monotonic, sleep
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
            # The sticker is the page moved up the page by feed_offset (the
            # printer puts the page that much further along it); its top
            # edge is beyond the page, so the PDF clips it.
            c.rect(0, roll.feed_offset, *roll.page, stroke=1, fill=0)
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
    """The roll's size as CUPS lists it, no scaling, and 300 x 600 dpi for
    the codes. lw450.ppd sets HWResolution twice: Resolution (order 20),
    which is CUPS's printer-resolution, then DymoPrintQuality (order 21,
    default Text: 300 x 300), so the job names DymoPrintQuality=Graphics as
    well -- CUPS hands a job attribute named after a PPD option to the
    driver's filters as that option."""
    x, y = roll.media_size
    return {
        "media-col": ipp.collection({"media-size": ipp.collection({
            "x-dimension": ipp.integer(x), "y-dimension": ipp.integer(y)})}),
        "print-scaling": ipp.keyword("none"),
        "printer-resolution": ipp.resolution(300, 600),
        "DymoPrintQuality": ipp.keyword("Graphics"),
    }


# A LabelWriter 450 prints ~50 address labels a minute at 300 x 300 dpi and
# about half that at 300 x 600: allow twice that, after a minute to start.
START_S = 60.0
PER_STICKER_S = 2 * 60 / 25


def job_wait(stickers: int) -> float:
    """How long a job of `stickers` may go without visible progress."""
    return START_S + PER_STICKER_S * max(stickers, 1)


def lpstat_hint(queue: str) -> str:
    """Where to look at the job: lpstat for a CUPS queue on this machine (a
    name, or a /printers/NAME URI), the printer itself for any other URI."""
    if "://" not in queue:
        return f"see lpstat -W all -o {queue}"
    path = queue.split("://", 1)[1].partition("/")[2]
    if path.startswith("printers/") and path.count("/") == 1:
        return f"see lpstat -W all -o {path.split('/')[1]}"
    return "check the printer's queue"


def _dim(d: Any) -> str:
    """A dimension in 1/100 mm, or an IPP range of them (lo, hi), in mm."""
    if isinstance(d, tuple):
        return f"{d[0] / 100:g}-{d[1] / 100:g}"
    return f"{d / 100:g}"


def _size(x: Any, y: Any) -> str:
    return f"{_dim(x)} x {_dim(y)} mm"


def _holds(have: Any, want: int) -> bool:
    """Whether a dimension the queue lists, a number or an IPP range
    (lo, hi), covers `want`."""
    if isinstance(have, tuple):
        return bool(have[0] <= want <= have[1])
    return bool(have == want)


def _queue_sizes(a: dict[str, list[Any]]) -> list[tuple[Any, Any]]:
    """The (x, y) of each media-size-supported member that has both; a
    member without one (or a malformed one) is not a size to offer."""
    out: list[tuple[Any, Any]] = []
    for m in a.get("media-size-supported", []):
        if not isinstance(m, dict):
            continue
        x, y = m.get("x-dimension"), m.get("y-dimension")
        if x and y and (x[0], y[0]) not in out:
            out.append((x[0], y[0]))
    return out


def print_pdf(pdf: bytes, queue: str, roll: labels.Roll, job_name: str,
              poll: float = 1.0, wait: float | None = None, stickers: int = 1) -> str:
    """Send `pdf` (`stickers` pages) to `queue` and wait for the job to end;
    returns "completed". Refuses, before sending, a queue that is
    unreachable, stopped, not accepting jobs, or has no such roll size;
    raises PrintError for a job that ends any other way, that the printer
    stops answering about, or that shows no progress (a new job-state or
    more job-impressions-completed) for `wait` seconds -- job_wait(stickers)
    unless given. A job attribute the printer ignored is a warning on stderr."""
    uri = queue_uri(queue)
    pr = ipp.Printer(uri)
    if wait is None:
        wait = job_wait(stickers)
    try:
        a = pr.attributes(["printer-state", "printer-state-message",
                           "printer-is-accepting-jobs", "media-size-supported"])
        if (a.get("printer-state") or [3])[0] == 5:
            why = (a.get("printer-state-message") or [""])[0] or "no reason given"
            raise PrintError(f"{queue}: the printer is stopped: {why}")
        if not (a.get("printer-is-accepting-jobs") or [True])[0]:
            raise PrintError(f"{queue}: the queue is not accepting jobs")
        sizes = _queue_sizes(a)
        rx, ry = roll.media_size
        if not any(_holds(x, rx) and _holds(y, ry) for x, y in sizes):
            has = ", ".join(_size(x, y) for x, y in sizes) or "no sizes"
            raise PrintError(f"{queue}: the queue has no {_size(rx, ry)} size "
                             f"({roll.ppd_size}); it supports {has}: is it on DYMO's driver, "
                             f"printer-driver-dymo's lw450.ppd?")
        job = pr.print_document(pdf, job_name, job_attributes(roll))
    except ipp.IppError as exc:
        raise PrintError(f"{uri}: {exc}") from exc
    if job.ignored:
        print(f"warning: the printer ignored {', '.join(job.ignored)}", file=sys.stderr)
    seen: tuple[Any, Any] = (job.state, None)
    deadline = monotonic() + wait
    while True:
        try:
            j = pr.job(job.id)
        except ipp.IppError as exc:
            raise PrintError(f"{queue}: job {job.id} was sent, then asking after it failed: "
                             f"{exc}; the printer may have the job: {lpstat_hint(queue)} "
                             f"before printing again") from exc
        state = (j.get("job-state") or [ipp.JOB_PENDING])[0]
        if state in ipp.JOB_DONE:
            break
        now = (state, (j.get("job-impressions-completed") or [None])[0])
        if now != seen:
            seen, deadline = now, monotonic() + wait
        elif monotonic() >= deadline:
            raise PrintError(f"{queue}: job {job.id} is still "
                             f"{ipp.JOB_STATES.get(state, state)}, with no progress in "
                             f"{wait:g} s; it may still be printing: {lpstat_hint(queue)} "
                             f"before printing again")
        sleep(poll)
    name = ipp.JOB_STATES.get(state, str(state))
    if state != ipp.JOB_COMPLETED:
        why = ", ".join(map(str, j.get("job-state-reasons", []))) or "no reason given"
        raise PrintError(f"{queue}: job {job.id} {name}: {why}")
    return name
