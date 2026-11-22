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
import io
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


# Canvas calls measured as ink: name -> points (user space) from the arguments.
_INK: dict[str, Callable[..., list[tuple[float, float]]]] = {
    "rect": lambda x, y, w, h, *_a, **_k: _corners(x, y, x + w, y + h),
    "roundRect": lambda x, y, w, h, *_a, **_k: _corners(x, y, x + w, y + h),
    "line": lambda x0, y0, x1, y1: _corners(x0, y0, x1, y1),
    "circle": lambda x, y, r, *_a, **_k: _corners(x - r, y - r, x + r, y + r),
    "ellipse": lambda x0, y0, x1, y1, *_a, **_k: _corners(x0, y0, x1, y1),
    "arc": lambda x0, y0, x1, y1, *_a, **_k: _corners(x0, y0, x1, y1),
    "wedge": lambda x0, y0, x1, y1, *_a, **_k: _corners(x0, y0, x1, y1),
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
            d.texts.append(Text(s, font, size, (x0, top - size * 0.72, x0 + w, top)))
            return real_text(self_, x, y, s, font, size, align, color)

        def rotated(self_: Any, x: float, y: float, s: str, font: str, size: float,
                    color: Any = lb.black) -> Any:
            # y is the bottom of the run; the cap-height top is 0.72 em to its right
            w = self_.width(s, font, size)
            px, py = self_.pt(x, y)
            d.texts.append(Text(s, font, size, (px, py, px + size * 0.72, py + w)))
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

        out: list[Any] = [
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
    underneath for anything drawn past those helpers."""
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
    return gone


def rows_for(docs: Any, draw: Callable[[Any, Any], None]) -> list[tuple[str, str, Any]]:
    """(host, title, record) of every whole label the fixtures make with `draw`."""
    return [(host, title, rec)
            for host, _kind, title, d, rec in lb.whole_labels(docs, set(lb.KINDS))
            if d is draw]
