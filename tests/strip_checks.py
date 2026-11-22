"""What every strip layout is held to (spec decisions 2 and 7): drawn on a
canvas its own size, nothing is set under 4.5 pt, no QR module is under
0.26 mm, no text, code or mark runs outside the strip, and every string and
code the full label carries is on the strip too.

The recorder watches two levels. ``Label.text``, ``Label.rotated``,
``Label.qr`` and ``Label.svg`` are recorded with their meaning (a string, its
size; a code, its module). Underneath, the canvas itself is watched, so a
layout cannot slip something past the helpers: a string drawn straight on the
canvas (``c.drawString`` and friends) or a code made with ``segno`` directly
is reported as stray, a raster image or a vector primitive (rect, line,
circle, arc, path ...) is recorded as ink with its box, and a canvas call the
checker cannot measure is reported rather than ignored."""
from __future__ import annotations

import contextlib
import inspect
import io
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest import mock

import segno
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfgen.pathobject import PDFPathObject

from rpi_hwid import labels as lb
from rpi_hwid import strip

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

TEXT_FLOOR = 4.5          # points
MODULE_FLOOR = 0.26 * mm
STRIP = (strip.STRIP_W, strip.STRIP_H)
SLACK = 0.05              # points: rounding in the layouts' own arithmetic

Box = tuple[float, float, float, float]


@dataclass
class Text:
    s: str
    font: str
    size: float
    box: Box
    # the smallest size the helper that drew it allows: fit/captioned values
    # their min_size, captions lb.CAPTION, everything else TEXT_FLOOR
    floor: float = TEXT_FLOOR


@dataclass
class Qr:
    payload: str
    module: float
    box: Box


@dataclass
class Drawn:
    texts: list[Text] = field(default_factory=list)
    qrs: list[Qr] = field(default_factory=list)
    marks: list[Box] = field(default_factory=list)
    stray: list[str] = field(default_factory=list)


def _box(c: Any, pts: list[tuple[float, float]]) -> Box:
    """The box, in page coordinates, of points given in the canvas's
    current user space."""
    a, b, cc, d, e, f = c._currentMatrix
    xs = [a * x + cc * y + e for x, y in pts]
    ys = [b * x + d * y + f for x, y in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _corners(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y1)]


def _arc_pts(x0: float, y0: float, x1: float, y1: float, startAng: float = 0,  # noqa: N803
             extent: float = 90, wedge: bool = False) -> list[tuple[float, float]]:
    """Points on the swept arc of the ellipse inscribed in (x0,y0,x1,y1), from
    startAng through extent degrees (the centre too for a wedge) -- not the
    whole ellipse's box, which is bigger than the ink."""
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = abs(x1 - x0) / 2, abs(y1 - y0) / 2
    lo, hi = sorted((startAng, startAng + extent))
    angles = {lo, hi}
    angles.update(range(math.ceil(lo), math.floor(hi) + 1))   # every whole degree
    pts = [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a)))
           for a in angles]
    if wedge:
        pts.append((cx, cy))
    return pts


# Canvas calls measured as ink: name -> points (user space) from the arguments.
_INK: dict[str, Callable[..., list[tuple[float, float]]]] = {
    "rect": lambda x, y, w, h, *_a, **_k: _corners(x, y, x + w, y + h),
    "roundRect": lambda x, y, w, h, *_a, **_k: _corners(x, y, x + w, y + h),
    "line": lambda x0, y0, x1, y1: _corners(x0, y0, x1, y1),
    "circle": lambda x, y, r, *_a, **_k: _corners(x - r, y - r, x + r, y + r),
    "ellipse": lambda x0, y0, x1, y1, *_a, **_k: _corners(x0, y0, x1, y1),
    "arc": lambda x0, y0, x1, y1, startAng=0, extent=90, **_k:  # noqa: N803
        _arc_pts(x0, y0, x1, y1, startAng, extent),
    "wedge": lambda x0, y0, x1, y1, startAng=0, extent=90, **_k:  # noqa: N803
        _arc_pts(x0, y0, x1, y1, startAng, extent, wedge=True),
    "bezier": lambda *p: list(zip(p[0::2], p[1::2], strict=True)),
}
# Path-object calls measured the same way.
_PATH_INK: dict[str, Callable[..., list[tuple[float, float]]]] = {
    "moveTo": lambda x, y: [(x, y)],
    "lineTo": lambda x, y: [(x, y)],
    "curveTo": lambda *p: list(zip(p[0::2], p[1::2], strict=True)),
    "rect": _INK["rect"],
    "roundRect": _INK["roundRect"],
    "circle": _INK["circle"],
    "ellipse": lambda x, y, w, h, *_a, **_k: _corners(x, y, x + w, y + h),
    "arc": _INK["arc"],
    "arcTo": _INK["arc"],
}
# Canvas text calls: only Label.text / Label.rotated may make them.
_TEXT_CALLS = ("drawString", "drawRightString", "drawCentredString",
               "drawAlignedString", "drawText", "beginText")
# Calls that put ink down that the checker cannot measure.
_UNMEASURED = ("grid", "lines", "drawInlineImage")


class _Recorder:
    def __init__(self) -> None:
        self.d = Drawn()
        self.inside = 0     # >0 while a recorded call is running: its inner calls are its own
        self.min: float | None = None    # min_size of the fit/captioned call being run
        self.queue: list[str] = []       # what the next Label.text calls are: "cap" or "val"
        # (size, min_size) of every Label.fitted_size result so far
        self.fitted: list[tuple[float, float]] = []

    def floor_of(self, size: float) -> float:
        """The floor of the string a Label.text call is about to draw."""
        kind = self.queue.pop(0) if self.queue else None
        if kind == "cap":
            return lb.CAPTION
        if self.min is not None:
            return self.min
        floor = lb.CAPTION if abs(size - lb.CAPTION) < 1e-9 else TEXT_FLOOR
        # A layout that sizes with Label.fitted_size and then draws with plain
        # Label.text holds that text to the fitted_size call's min_size. The two
        # are tied by size alone, not by string or order: one fitted size is
        # often computed first (from a template such as a zero MAC, or for
        # several strings) and applied to many strings. This cannot flag a text
        # that is not under that min_size: a text exactly at a fitted size that
        # came back at or above its min_size is itself at or above that floor.
        for fitted, min_size in self.fitted:
            if abs(fitted - size) < 1e-9:
                floor = max(floor, min_size)
        return floor

    @contextlib.contextmanager
    def helper(self, min_size: float | None, queue: list[str]) -> Iterator[None]:
        saved = (self.min, self.queue)
        self.min, self.queue = min_size, queue
        try:
            yield
        finally:
            self.min, self.queue = saved

    @contextlib.contextmanager
    def within(self) -> Iterator[None]:
        self.inside += 1
        try:
            yield
        finally:
            self.inside -= 1

    def wrap(self, real: Callable[..., Any], before: Callable[..., None] | None = None
             ) -> Callable[..., Any]:
        def call(*a: Any, **k: Any) -> Any:
            if before is not None and self.inside == 0:
                before(*a, **k)
            with self.within():
                return real(*a, **k)
        return call

    def patches(self) -> list[Any]:
        d = self.d
        real_text, real_rot = lb.Label.text, lb.Label.rotated
        real_qr, real_svg = lb.Label.qr, lb.Label.svg

        def text(self_: Any, x: float, y: float, s: str, font: str = lb.SANS, size: float = 8,
                 align: str = "left", color: Any = lb.black) -> Any:
            # y is the cap-height top; the baseline is 0.72 em below it
            w = self_.width(s, font, size)
            left = {"left": x, "right": x - w, "centre": x - w / 2}[align]
            x0, top = self_.pt(left, y)
            d.texts.append(Text(s, font, size, (x0, top - size * 0.72, x0 + w, top),
                                self.floor_of(size)))
            return real_text(self_, x, y, s, font, size, align, color)

        def rotated(self_: Any, x: float, y: float, s: str, font: str, size: float,
                    color: Any = lb.black) -> Any:
            # y is the bottom of the run; the cap-height top is 0.72 em to its right
            w = self_.width(s, font, size)
            px, py = self_.pt(x, y)
            d.texts.append(Text(s, font, size, (px, py, px + size * 0.72, py + w),
                                self.floor_of(size)))
            return real_rot(self_, x, y, s, font, size, color)

        def qr(self_: Any, x: float, y: float, size: float, content: str, error: str = "m") -> Any:
            # Label.qr draws segno's matrix with no quiet zone, so n modules span `size`
            n = len(list(segno.make(content, error=error, micro=False).matrix))
            x0, top = self_.pt(x, y)
            d.qrs.append(Qr(content, size / n, (x0, top - size, x0 + size, top)))
            return real_qr(self_, x, y, size, content, error)

        def svg(self_: Any, path: str, x: float, y: float, height: float) -> Any:
            w = real_svg(self_, path, x, y, height)
            x0, top = self_.pt(x, y)
            d.marks.append((x0, top - height, x0 + w, top))
            return w

        def helper_patch(name: str, queue: list[str], keeps_min: bool = True) -> Any:
            """Label.fit / captioned / captioned_after / no_code: their text
            calls carry the helper's own floor (min_size, or the caption's)."""
            real = getattr(lb.Label, name)
            sig = inspect.signature(real)

            def call(*a: Any, **k: Any) -> Any:
                bound = sig.bind(*a, **k)
                bound.apply_defaults()
                min_size = bound.arguments.get("min_size") if keeps_min else None
                # a fit inside captioned/captioned_after keeps its caller's queue
                if name == "fit" and self.queue:
                    return real(*a, **k)
                with self.helper(min_size, list(queue)):
                    return real(*a, **k)
            return mock.patch.object(lb.Label, name, call)

        real_fitted = lb.Label.fitted_size
        fitted_sig = inspect.signature(real_fitted)

        def fitted_size(*a: Any, **k: Any) -> Any:
            got = real_fitted(*a, **k)
            bound = fitted_sig.bind(*a, **k)
            bound.apply_defaults()
            self.fitted.append((got, bound.arguments["min_size"]))
            return got

        out: list[Any] = [
            mock.patch.object(lb.Label, "fitted_size", fitted_size),
            helper_patch("fit", []),
            helper_patch("captioned", ["cap", "val"]),
            helper_patch("captioned_after", ["val", "cap"]),
            helper_patch("no_code", ["cap"], keeps_min=False),
            mock.patch.object(lb.Label, "text", self.wrap(text)),
            mock.patch.object(lb.Label, "rotated", self.wrap(rotated)),
            mock.patch.object(lb.Label, "qr", self.wrap(qr)),
            mock.patch.object(lb.Label, "svg", self.wrap(svg)),
        ]
        # a code made with segno outside Label.qr is a code the checker never measured
        real_make = segno.make

        def make_before(*_a: Any, **_k: Any) -> None:
            d.stray.append("QR made with segno outside Label.qr")
        out.append(mock.patch.object(segno, "make", self.wrap(real_make, make_before)))

        for name in _TEXT_CALLS:
            real = getattr(canvas.Canvas, name)

            def text_before(c: Any, *_a: Any, _n: str = name, **_k: Any) -> None:
                d.stray.append(f"canvas.{_n}() outside Label.text / Label.rotated")
            out.append(mock.patch.object(canvas.Canvas, name, self.wrap(real, text_before)))

        for name in _UNMEASURED:
            real = getattr(canvas.Canvas, name)

            def unmeasured(c: Any, *_a: Any, _n: str = name, **_k: Any) -> None:
                d.stray.append(f"canvas.{_n}() draws ink the checker cannot measure")
            out.append(mock.patch.object(canvas.Canvas, name, self.wrap(real, unmeasured)))

        for name, pts_of in _INK.items():
            real = getattr(canvas.Canvas, name)

            def ink(c: Any, *a: Any, _p: Any = pts_of, **k: Any) -> None:
                d.marks.append(_box(c, _p(*a, **k)))
            out.append(mock.patch.object(canvas.Canvas, name, self.wrap(real, ink)))

        real_image = canvas.Canvas.drawImage

        def image(c: Any, img: Any, x: float, y: float, width: float | None = None,
                  height: float | None = None, *_a: Any, **_k: Any) -> None:
            if width is None or height is None:
                d.stray.append("canvas.drawImage() without a width and height")
            else:
                d.marks.append(_box(c, _corners(x, y, x + width, y + height)))
        out.append(mock.patch.object(canvas.Canvas, "drawImage", self.wrap(real_image, image)))

        # paths: collect their points as they are built, measure them when drawn
        for name, pts_of in _PATH_INK.items():
            real = getattr(PDFPathObject, name)

            def build(p: Any, *a: Any, _p: Any = pts_of, **k: Any) -> None:
                p.__dict__.setdefault("_strip_pts", []).extend(_p(*a, **k))
            out.append(mock.patch.object(PDFPathObject, name, self.wrap(real, build)))
        real_draw_path = canvas.Canvas.drawPath

        def drawn_path(c: Any, p: Any, *_a: Any, **_k: Any) -> None:
            pts = p.__dict__.get("_strip_pts", [])
            if pts:
                d.marks.append(_box(c, pts))
        out.append(mock.patch.object(canvas.Canvas, "drawPath",
                                     self.wrap(real_draw_path, drawn_path)))
        return out


def record(draw: Callable[[Any, Any], None], rec: Any,
           make: Callable[[Any], lb.Label]) -> Drawn:
    """Draw one label with `draw` on a canvas the label's size, recording
    every string (Label.text, Label.rotated), code (Label.qr) and SVG mark
    (Label.svg) in the canvas's own coordinates, and watching the canvas
    underneath for anything drawn past those helpers. The fonts are the
    ones a real render registers, whatever ran before."""
    lb.register_fonts()  # type: ignore[no-untyped-call]
    r = _Recorder()
    c = canvas.Canvas(io.BytesIO())
    lab = make(c)
    c.setPageSize((lab.w, lab.h))
    with contextlib.ExitStack() as stack:
        for p in r.patches():
            stack.enter_context(p)
        draw(lab, rec)
    return r.d


def full(draw: Callable[[Any, Any], None], rec: Any) -> Drawn:
    return record(draw, rec, lambda c: lb.Label(c, 0, 0))


def on_strip(draw: Callable[[Any, Any], None], rec: Any) -> Drawn:
    return record(draw, rec, lambda c: strip.Strip(c, 0, 0))


def problems(d: Drawn, w: float, h: float) -> list[str]:
    def out(box: Box) -> bool:
        x0, y0, x1, y1 = box
        return x0 < -SLACK or y0 < -SLACK or x1 > w + SLACK or y1 > h + SLACK

    found = list(d.stray)
    for t in d.texts:
        if t.size < TEXT_FLOOR - 1e-9:
            found.append(f"text {t.s!r} at {t.size:.1f} pt, under {TEXT_FLOOR} pt")
        elif t.size < t.floor - 1e-9:
            found.append(f"text {t.s!r} at {t.size:.1f} pt, under its floor {t.floor} pt")
        if t.s.strip() and out(t.box):
            found.append(f"text {t.s!r} outside the strip: {t.box}")
    for q in d.qrs:
        if q.module < MODULE_FLOOR - 1e-9:
            found.append(f"QR {q.payload!r}: module {q.module / mm:.3f} mm, "
                         f"under {MODULE_FLOOR / mm} mm")
        if out(q.box):
            found.append(f"QR {q.payload!r} outside the strip: {q.box}")
    for m in d.marks:
        if out(m):
            found.append(f"mark outside the strip: {m}")
    return found


def missing_facts(full_d: Drawn, strip_d: Drawn) -> list[str]:
    """Every string the full label sets (but one it elided) must appear on
    the strip, whole or as part of a longer string there; every QR payload
    must be there exactly."""
    joined = "\n".join(t.s for t in strip_d.texts)
    payloads = {q.payload for q in strip_d.qrs}
    gone = [f"text {t.s!r}" for t in full_d.texts
            if t.s.strip() and not t.s.endswith("…") and t.s not in joined]
    gone += [f"QR {q.payload!r}" for q in full_d.qrs if q.payload not in payloads]
    # and not set smaller than the full label's own floor for it: the largest
    # strip string that contains it must be at least as big as that floor
    for t in full_d.texts:
        if not t.s.strip() or t.s.endswith("…") or t.s not in joined:
            continue
        best = max(u.size for u in strip_d.texts if t.s in u.s)
        if best < t.floor - 1e-9:
            gone.append(f"text {t.s!r} at {best:.1f} pt on the strip, "
                        f"under its full-label floor {t.floor} pt")
    return gone


def rows_for(docs: Any, draw: Callable[[Any, Any], None]) -> list[tuple[str, str, Any]]:
    """(host, title, record) of every whole label the fixtures make with `draw`."""
    return [(host, title, rec)
            for host, _kind, title, d, rec in lb.whole_labels(docs, set(lb.KINDS))
            if d is draw]
