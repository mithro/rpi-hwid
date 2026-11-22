"""Every strip layout, on every fixture its full layout draws (spec
decisions 2 and 7, tests/strip_checks.py)."""
from __future__ import annotations

from dataclasses import replace

import pytest

import strip_checks as sc
from rpi_hwid import labels, names, revision, strip
from rpi_hwid.model import Camera

# (full draw function, kind name for the test ids); one entry per layout task
LAYOUTS = [
    (labels.draw_board, "board"),
    (labels.draw_fpga, "fpga"),
    (labels.draw_usb, "usb"),
]


def _cases(docs):
    for draw, name in LAYOUTS:
        rows = sc.rows_for(docs, draw)
        assert rows, f"the fixtures make no {name} label"
        for host, title, rec in rows:
            yield pytest.param(draw, rec, id=f"{name}:{host}:{title}")


def pytest_generate_tests(metafunc):
    if {"draw", "rec"} <= set(metafunc.fixturenames):
        import copy

        import conftest
        from rpi_hwid.model import ProbeDocument
        docs = {n: ProbeDocument.from_dict(n, copy.deepcopy(r)) for n, r in conftest.RAW.items()}
        metafunc.parametrize(("draw", "rec"), list(_cases(docs)))


def test_it_has_a_strip(draw, rec):
    assert draw in strip.STRIP_OF


def test_it_keeps_to_the_floors_and_the_strip(draw, rec):
    assert sc.problems(sc.on_strip(strip.STRIP_OF[draw], rec), *sc.STRIP) == []


def test_it_carries_every_fact_of_the_full_label(draw, rec):
    assert sc.missing_facts(sc.full(draw, rec), sc.on_strip(strip.STRIP_OF[draw], rec)) == []


def test_it_draws_the_same_marks(draw, rec):
    assert len(sc.on_strip(strip.STRIP_OF[draw], rec).marks) == len(sc.full(draw, rec).marks)


def _rules_kept(draw, rec):
    """Everything the fixture-driven tests above hold a strip to."""
    got = sc.on_strip(strip.STRIP_OF[draw], rec)
    full = sc.full(draw, rec)
    assert sc.problems(got, *sc.STRIP) == []
    assert sc.missing_facts(full, got) == []
    assert len(got.marks) == len(full.marks)


# --- board records the fixtures lack: cameras, a PC with no serial or maker mark

def _board(docs, host, **changes):
    rec = next(r for h, _t, r in sc.rows_for(docs, labels.draw_board) if h == host)
    return replace(rec, **changes)


WIDE = Camera("imx708", variant="wide_noir", autofocus=True)
V1 = Camera("ov5647", autofocus=False)
V1_AF = Camera("ov5647", autofocus=True)
OV9281 = Camera("ov9281", autofocus=False)
HQ = Camera("imx477", autofocus=False)


@pytest.mark.parametrize("host", ["pi-sw1-p10", "pi-sw2-p47", "rpicm1-serial", "pi-sw2-p48",
                                  "hifive-unmatched-1", "minnow-turbot-2"])
@pytest.mark.parametrize("cameras", [
    (V1,), (WIDE,), (V1_AF, V1), (WIDE, WIDE), (V1,) * 4,
    (Camera("imx708", variant="wide", autofocus=True, fov=120),), (V1,) * 5,
    # the widest sets the full label takes (stacked two deep there), more
    # than the row under a strip's title holds: some end the subtitle's line
    (OV9281, OV9281, HQ, HQ), (V1_AF, V1_AF, HQ, HQ), (V1_AF, OV9281, HQ, HQ),
    (WIDE, V1_AF, HQ), (OV9281,) * 3,
], ids=lambda cams: "+".join(" ".join(w for w in labels.camera_words(c) if w) for c in cams))
def test_a_board_strip_takes_exactly_the_cameras_the_full_label_takes(docs, host, cameras):
    """Spec decision 2: a strip refuses exactly when the full label
    refuses. A brute force over one to four cameras of eight kinds on every
    board fixture (the full label takes no set of five) found the sets
    above the widest the full label takes."""
    rec = _board(docs, host, cameras=cameras)
    try:
        sc.full(labels.draw_board, rec)
    except labels.CameraMarkDoesNotFitError:
        with pytest.raises(labels.CameraMarkDoesNotFitError, match=host):
            sc.on_strip(strip.draw_board_strip, rec)
        return
    _rules_kept(labels.draw_board, rec)


# --- every title the full label prints whole, the strip prints whole -----------

PI_MODELS = sorted(set(revision.PI_TYPE.values())
                   | {model for model, _rev, _mem in revision.OLD_STYLE.values()})


@pytest.mark.parametrize("cameras", [None, (V1_AF, HQ)], ids=["no camera", "two cameras"])
@pytest.mark.parametrize(("fan", "clock"), [(None, None), (True, None), (True, True)],
                         ids=["no icons", "fan", "fan and clock"])
@pytest.mark.parametrize("model", PI_MODELS)
def test_every_raspberry_pi_title_is_whole_on_its_strip(docs, model, fan, clock, cameras):
    """Every model name revision.py can decode, beside a fan, a clock and
    two cameras (a Compute Module 5 has the Pi 5's fan and RTC): what the
    full label prints whole, the strip prints whole too."""
    rec = _board(docs, "pi-sw2-p47", title="Raspberry Pi " + model, fan=fan,
                 rtc_battery=clock, cameras=cameras)
    try:
        sc.full(labels.draw_board, rec)
    except labels.CameraMarkDoesNotFitError:
        with pytest.raises(labels.CameraMarkDoesNotFitError):
            sc.on_strip(strip.draw_board_strip, rec)
        return
    _rules_kept(labels.draw_board, rec)


def _longest_whole_title(rec, words):
    """The longest prefix of `words` the full label prints as `rec`'s title
    without cutting it: the full label's own limit, found by halving."""
    def whole(n):
        title = words[:n].rstrip()
        got = sc.full(labels.draw_board, replace(rec, title=title))
        return not any(t.s.endswith("…") for t in got.texts)
    lo, hi = 1, len(words)
    assert not whole(hi), "the test's title should be too long for the full label"
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if whole(mid) else (lo, mid)
    return words[:lo].rstrip()


@pytest.mark.parametrize("host", ["pi-sw2-p22", "hifive-unmatched-1", "minnow-turbot-2",
                                  "pi-sw2-p47"])
def test_the_longest_title_the_full_label_prints_whole_is_whole_on_the_strip(docs, host):
    """Orange Pi, RISC-V and PC titles come from the board's own device tree
    or DMI, not a table: any length. Whatever the full label has room for,
    the strip has room for (Pi 5: beside its fan and clock)."""
    words = "Wide Board Name With A Very Long Model String Revision Plus Extras " * 2
    rec = _board(docs, host)
    title = _longest_whole_title(rec, words)
    _rules_kept(labels.draw_board, replace(rec, title=title))


@pytest.mark.parametrize("changes", [
    {"serial": ""},                              # a PC whose firmware has none
    {"maker_mark": None},                        # its maker named in type
    {"serial": "", "maker_mark": None},
])
@pytest.mark.parametrize("host", ["minnow-turbot-1", "minnow-turbot-2"])
def test_a_pc_board_strip_without_a_serial_or_a_maker_mark(docs, host, changes):
    _rules_kept(labels.draw_board, _board(docs, host, **changes))


# --- FPGA: every kind, and the longest value each row can be given -----------

def _overlaps(d):
    """Pairs of things drawn on top of each other: strings (their boxes
    taken down to the descenders), codes and marks. The "no id" square's
    words are inside it by design."""
    items = [(t.s, (t.box[0], t.box[1] - 0.21 * t.size, t.box[2], t.box[3]))
             for t in d.texts if t.s.strip()]
    items += [(q.payload, q.box) for q in d.qrs] + [("<mark>", m) for m in d.marks]

    def over(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
    return [(n1, n2) for i, (n1, b1) in enumerate(items) for n2, b2 in items[i + 1:]
            if over(b1, b2) and {n1, n2} != {"no id", "<mark>"}]


def _fpga_kept(rec):
    _rules_kept(labels.draw_fpga, rec)
    assert _overlaps(sc.on_strip(strip.draw_fpga_strip, rec)) == []


def test_no_fpga_strip_fixture_overlaps(docs):
    for _host, _title, rec in sc.rows_for(docs, labels.draw_fpga):
        assert _overlaps(sc.on_strip(strip.draw_fpga_strip, rec)) == [], rec.kind


def _fpga_docs(*cards):
    from rpi_hwid.model import ProbeDocument
    return {"pi-sw1-p38": ProbeDocument.from_dict("pi-sw1-p38", {"verdict": {"summary": {
        "model": "Raspberry Pi 5 Model B Rev 1.1", "serial": "e8387e35dbce7843",
        "revision": "b04171", "power_class": "undetermined", "fpga": list(cards)}}})}


# pi-sw1-p38's PCILeech card as tests/test_labels.py has it (P38_CARD), and
# the same facts under the kinds no fixture carries: a bare JTAG chain, an
# unrecognised die, a generic PCILeech card
P38_CARD = {"kind": "pcileech", "dna": "0x006425440bc8985c", "idcode": "0x3632093",
            "gateware": "4.14", "gateware_id": 9, "flash_jedec": "0xef4017",
            "flash": "Winbond W25Q64", "flash_uid": "df64ac431b4b202f",
            "flash_uid_bits": 64, "flash_uid_state": "read"}
CARDS = {
    "captaindma": P38_CARD,
    "pcileech": {**P38_CARD, "flash_jedec": "0xef4018"},       # no card matches: generic
    "jtag": {**P38_CARD, "kind": "jtag", "idcode": "0x13636093"},
    "unknown-fpga": {**P38_CARD, "kind": "unknown-fpga", "idcode": "0x0362f093"},
    "jtag-unknown-die": {**P38_CARD, "kind": "jtag", "idcode": "0x0362f093"},
}


@pytest.mark.parametrize("card", CARDS.values(), ids=CARDS.keys())
def test_the_fpga_kinds_no_fixture_carries(card):
    rows = sc.rows_for(_fpga_docs(card), labels.draw_fpga)
    assert len(rows) == 1
    _fpga_kept(rows[0][2])


def _fpga(docs, kind, **changes):
    rec = next(r for _h, _t, r in sc.rows_for(docs, labels.draw_fpga) if r.kind == kind)
    return replace(rec, **changes)


FPGA_KINDS_FIXTURED = ["netv2", "arty", "acorn", "cynthion"]


@pytest.mark.parametrize("kind", FPGA_KINDS_FIXTURED)
def test_an_fpga_strip_with_its_mark_file_removed(docs, kind, monkeypatch):
    """The maker's name in type where its mark is not shipped."""
    rec = _fpga(docs, kind)
    real = labels.artwork
    monkeypatch.setattr(labels, "artwork", lambda name: None if name in (
        "alphamax.png", "digilent.png", "great-scott-gadgets.png", "sqrl.svg") else real(name))
    _fpga_kept(rec)


def _widest(texts, font, n=3):
    from reportlab.pdfbase import pdfmetrics
    return sorted(set(texts), key=lambda t: -pdfmetrics.stringWidth(t, font, 10))[:n]


# every name a board can be given, from the word lists names.py draws from
NAME_WORDS = {"netv2": names.NETV2_WORDS, "arty": names.ARTY_WORDS,
              "acorn": names.ACORN_WORDS, "cynthion": names.CYNTHION_WORDS}


@pytest.mark.parametrize("kind", FPGA_KINDS_FIXTURED)
def test_the_widest_names_of_each_kind(docs, kind):
    for word in _widest(NAME_WORDS[kind], labels.SANS_BOLD):
        _fpga_kept(_fpga(docs, kind, name=f"{kind}-{word}"))
    for word in _widest(names.PCILEECH_WORDS, labels.SANS_BOLD):
        _fpga_kept(_fpga(docs, kind, name=f"pcileech-{word}"))


def _model_lines():
    """(model, part) of every model and die the record tables can pair."""
    models = {m for _maker, m in labels.BOARD_MODEL.values()}
    models |= {m for _maker, m in labels.PCILEECH_BOARD.values()}
    models |= set(labels.ACORN_MODEL.values()) | set(labels.ACORN_SOC_MODEL.values())
    models |= {"Cynthion r" + rev for rev in labels.CYNTHION_PART}
    parts = set(labels.IDCODE_PART.values()) | set(labels.CYNTHION_PART.values())
    models |= {"Arty A7-" + p[len("XC7A"):] for p in labels.IDCODE_PART.values()}
    lines = {(m, p): f"{m}  ·  {p}" for m in models for p in parts}
    return [mp for mp, line in lines.items() if line in _widest(lines.values(), labels.SANS)]


@pytest.mark.parametrize("kind", FPGA_KINDS_FIXTURED)
@pytest.mark.parametrize(("model", "part"), _model_lines())
def test_the_widest_model_and_die(docs, kind, model, part):
    _fpga_kept(_fpga(docs, kind, model=model, part=part))


def _flash_lines():
    """Every flash line the tables can print: labels.py's own and every
    SPI NOR part in spiflash's database (rpi_hwid.spi_flash_parts)."""
    import spiflash
    ids = [(f"0x{j:06x}", None, None) for j in labels.JEDEC_PART]
    ids += [(f"0x{j:06x}", e, None) for j in labels.MICRON_GENERATION
            for e in ("0x100000", "0x104000")]
    ids += [(f"0x{j:06x}", f"0x4d01{fam:02x}", sfdp)
            for j, fams in labels.SPANSION_FAMILY.items()
            for fam in fams for sfdp in (None, "none", "1.6")]
    for f in spiflash.flashes():
        if f.type == "nor":
            ext = "0x" + f.id[3:6].hex() if len(f.id) >= 6 else None
            ids += [("0x" + f.id[:3].hex(), e, None) for e in {None, ext}]
    lines = {text for _rev, (_jedec, text) in labels.CYNTHION_FLASH.items()}
    for jedec, ext, sfdp in ids:
        if 0x10 <= int(jedec, 16) & 0xFF < 0x14:
            continue        # under 1 MiB: flash_from_jedec cannot size it (no FPGA has one)
        lines.add(labels.flash_text(labels.flash_from_jedec(jedec, ext, sfdp)))
    return _widest(lines - {None}, labels.SANS, 4)


UIDS = {"none": None, "64-bit": "df64ac431b4b202f", "112-bit": "235351451900080037091015126b",
        "128-bit": "edcbeececb2b2a88b04f914d2e46af90"}


@pytest.mark.parametrize("uid", UIDS.values(), ids=UIDS.keys())
@pytest.mark.parametrize("flash", _flash_lines())
def test_the_widest_flash_lines_beside_every_uid(docs, flash, uid):
    for kind in ("arty", "netv2"):          # an Arty has the most in its column
        _fpga_kept(_fpga(docs, kind, flash=flash, flash_uid=uid))


def _longest_whole_by(draw, make, text):
    """The longest prefix of `text` the full label (`draw`) prints whole
    in the record `make(prefix)` gives: that label's own limit, found by
    halving."""
    def whole(n):
        got = sc.full(draw, make(text[:n].rstrip()))
        return not any(t.s.endswith("…") for t in got.texts)
    lo, hi = 1, len(text)
    assert not whole(hi), "the test's value should be too long for the full label"
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if whole(mid) else (lo, mid)
    return text[:lo].rstrip()


def _longest_whole(rec, field, text):
    """The longest prefix of `text` the full label prints as `rec`'s
    `field` without cutting it."""
    return _longest_whole_by(labels.draw_fpga, lambda v: replace(rec, **{field: v}), text)


WORDS = "Wide Board Name With A Very Long String Revision Plus Extras " * 3
HEX = "0123456789abcdef" * 8


@pytest.mark.parametrize(("field", "text", "prefix"), [
    ("name", WORDS, "arty-"),           # a pinned Arty name is kept verbatim
    ("model", WORDS, ""),
    ("serial", HEX.upper(), ""),
    ("flash", WORDS, ""),
    # not the flash's uid: its code is the full label's size, which holds 128
    # bits (the most any part has, test_the_widest_flash_lines_beside_every_uid)
    # at the floor's module, and no more
    ("ident", "0x" + HEX, ""),
], ids=lambda v: v if isinstance(v, str) and len(v) < 12 else "")
def test_the_longest_value_the_full_label_prints_whole_is_whole_on_the_strip(
        docs, field, text, prefix):
    """A row's value can come from a document as any string (a pinned Arty
    name, a label input's fields): whatever the full label has room for, the
    strip has room for."""
    rec = _fpga(docs, "arty")
    if prefix:
        # unnamed, the die is the headline: its longest whole value is the
        # longest whole name
        whole = _longest_whole(replace(rec, name=None), "part", text)
        rec = replace(rec, name=prefix + whole)
    else:
        rec = replace(rec, **{field: _longest_whole(rec, field, text)})
    _fpga_kept(rec)


# --- refusal: the strip refuses exactly what the full label refuses -----------

BARE_ARTY = {"kind": "arty", "serial": "210319A43AD3", "dna": "0x0064f5483229085c",
             "idcode": "0x362d093"}
REFUSED = {
    "no identifier": ({"kind": "pcileech", "gateware": "4.14", "gateware_id": 9},
                      labels.IdentifierNotReadError),
    "flash never read": (BARE_ARTY, labels.FlashNotReadError),
    "uid never read": ({**BARE_ARTY, "flash_jedec": "0x20ba18"}, labels.FlashNotReadError),
    "uid read blank": ({**BARE_ARTY, "flash_jedec": "0x20ba18", "flash_uid_state": "blank"},
                       labels.FlashNotReadError),
    "flash part unknown": ({**P38_CARD, "flash_jedec": "0xef9999"},
                           labels.UnknownFlashPartError),
}


@pytest.mark.parametrize(("card", "error"), REFUSED.values(), ids=REFUSED.keys())
def test_a_roll_refuses_the_fpga_boards_a_sheet_refuses(card, error):
    """The full label's refusals are all made before it is drawn
    (labels.whole_labels), and a roll's rows are whole_labels' rows."""
    from rpi_hwid import roll
    docs = _fpga_docs(card)
    with pytest.raises(error) as sheet:
        list(labels.whole_labels(docs, {"fpga"}))
    with pytest.raises(error) as on_roll:
        roll.roll_rows(docs, {"fpga"})
    assert str(on_roll.value) == str(sheet.value)


def test_a_roll_takes_every_fpga_board_a_sheet_takes(docs):
    from rpi_hwid import roll
    sheet = list(labels.whole_labels(docs, set(labels.FPGA_KINDS)))
    rolled = roll.roll_rows(docs, set(labels.FPGA_KINDS))
    assert [(h, k, t, rec) for h, k, t, _d, rec in sheet] == \
        [(h, k, t, rec) for h, k, t, _d, rec in rolled if k != "micro"]
    assert {d for *_x, d, _r in rolled if _x[1] != "micro"} == {strip.draw_fpga_strip}


# --- USB network adapters: wired and Wi-Fi, and the longest value of each row ---

def _usb_overlaps(d):
    """_overlaps, with the bus and link glyphs taken as one mark: the RJ45
    jack's contacts are inside its body, the Wi-Fi glyph's arcs over its
    dot, by design."""
    if not d.marks:
        return _overlaps(d)
    xs0, ys0, xs1, ys1 = zip(*d.marks, strict=True)
    return _overlaps(replace(d, marks=[(min(xs0), min(ys0), max(xs1), max(ys1))]))


def _usb_kept(rec):
    _rules_kept(labels.draw_usb, rec)
    assert _usb_overlaps(sc.on_strip(strip.draw_usb_strip, rec)) == []


def test_no_usb_strip_fixture_overlaps(docs):
    for _host, _title, rec in sc.rows_for(docs, labels.draw_usb):
        assert _usb_overlaps(sc.on_strip(strip.draw_usb_strip, rec)) == [], rec.title


USB_KINDS = ["ethernet", "wifi"]       # an RJ45 jack or the radio glyph by the title


def _usb(docs, kind, **changes):
    rec = next(r for _h, _t, r in sc.rows_for(docs, labels.draw_usb) if r.kind == kind)
    return replace(rec, **changes)


def _with_line(rec, key, value):
    """`rec` with its `key` line's value changed (the line added if absent)."""
    lines = [(k, value if k == key else v) for k, v in rec.lines]
    if key not in dict(rec.lines):
        lines.append((key, value))
    return replace(rec, lines=tuple(lines))


def _usb_lines():
    """The widest USB lines usb_records can make: every bcdUSB a device can
    report with every speed in labels.USB_SPEED, and a speed it does not
    name (sysfs's 1.5, low speed), which is printed as sysfs gives it."""
    bcds = ["1.00", "1.10", "2.00", "2.01", "2.10", "3.00", "3.10", "3.20"]
    speeds = [*labels.USB_SPEED.values(), "1.5"]
    return _widest([f"{b} {s}" for b in bcds for s in speeds], labels.SANS)


@pytest.mark.parametrize("kind", USB_KINDS)
@pytest.mark.parametrize("line", _usb_lines())
def test_the_widest_usb_lines(docs, kind, line):
    _usb_kept(_with_line(_usb(docs, kind), "USB", line))


@pytest.mark.parametrize("kind", USB_KINDS)
@pytest.mark.parametrize("changes", [
    {"driver": ""},                     # no driver bound: its line is left out
    {"USB": ""},                        # neither bcdUSB nor a speed read
    {"driver": "", "USB": ""},
], ids=lambda c: "+".join(c) + " empty")
def test_a_usb_strip_with_lines_left_out(docs, kind, changes):
    rec = _usb(docs, kind)
    for key, value in changes.items():
        rec = _with_line(rec, key, value)
    _usb_kept(rec)


@pytest.mark.parametrize("kind", USB_KINDS)
def test_a_usb_line_without_a_caption(docs, kind):
    """draw_usb sets a line with no caption in the values' column."""
    _usb_kept(_with_line(_usb(docs, kind), "", "an uncaptioned fact"))


USB_WORDS = "Wide Adapter Maker Name With A Very Long Product String Plus Extras " * 3
MACS = "60:38:e0:e3:56:4f:" * 6


@pytest.mark.parametrize("kind", USB_KINDS)
@pytest.mark.parametrize(("field", "text"), [
    ("title", USB_WORDS),               # the manufacturer and product strings: any length
    ("mac", MACS),
    ("USB", USB_WORDS),                 # a bcdUSB or speed sysfs reports as it likes
    ("driver", USB_WORDS.replace(" ", "_")),
    ("VID:PID", "0123456789abcdef:" * 6),
], ids=lambda v: v if len(v) < 12 else "")
def test_the_longest_usb_value_the_full_label_prints_whole_is_whole_on_the_strip(
        docs, kind, field, text):
    """Every value on the label is a string from the adapter's descriptors,
    the kernel, or a label input's fields: whatever the full label has room
    for, the strip has room for."""
    rec = _usb(docs, kind)
    if field in ("title", "mac"):
        def make(v):
            return replace(rec, **{field: v})
    else:
        def make(v):
            return _with_line(rec, field, v)
    _usb_kept(make(_longest_whole_by(labels.draw_usb, make, text)))


def _usb_input_docs(**changes):
    """pi-sw2-p37 as a label input, its adapter changed by `changes` after
    the input was built (a file edited by hand, or written by another tool)."""
    import copy

    import conftest
    from rpi_hwid import label_input
    doc = label_input.to_probe_document(label_input.build(
        "pi-sw2-p37", copy.deepcopy(conftest.RAW["pi-sw2-p37"]["verdict"]["summary"])))
    evidence = copy.deepcopy(doc.evidence)
    evidence["summary"]["usb_net"][0].update(changes)
    return {"pi-sw2-p37": replace(doc, evidence=evidence)}


@pytest.mark.parametrize("changes", [{"mac": None}, {"vidpid": None}],
                         ids=lambda c: next(iter(c)))
def test_a_roll_refuses_the_usb_adapters_a_sheet_refuses(changes):
    """draw_usb never refuses: an adapter's label is refused before it is
    drawn (labels.whole_labels), and a roll's rows are whole_labels' rows."""
    from rpi_hwid import label_input, roll
    docs = _usb_input_docs(**changes)
    with pytest.raises(label_input.InputError) as sheet:
        list(labels.whole_labels(docs, {"usb"}))
    with pytest.raises(label_input.InputError) as on_roll:
        roll.roll_rows(docs, {"usb"})
    assert str(on_roll.value) == str(sheet.value)


def test_a_roll_takes_every_usb_adapter_a_sheet_takes(docs):
    from rpi_hwid import roll
    sheet = list(labels.whole_labels(docs, {"usb"}))
    rolled = roll.roll_rows(docs, {"usb"})
    assert sheet
    assert [(h, k, t, rec) for h, k, t, _d, rec in sheet] == \
        [(h, k, t, rec) for h, k, t, _d, rec in rolled if k != "micro"]
    assert {d for *_x, d, _r in rolled if _x[1] != "micro"} == {strip.draw_usb_strip}
