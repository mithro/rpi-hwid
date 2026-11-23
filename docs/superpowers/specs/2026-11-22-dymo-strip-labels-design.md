# Strip labels for a DYMO LabelWriter roll

`rpi-hwid labels` gains a second output beside the A4/US Letter sheets: a
roll of DYMO 99010 address labels (28 x 89 mm) in a LabelWriter 450, with a
new **strip** layout for every label kind, and `--print QUEUE` to send the
result to the printer's CUPS queue.

Status: design, not built. Branch `dymo-strip-labels`. The implementation
plan is `docs/superpowers/plans/2026-11-22-dymo-strip-labels.md`.

## Why

A label is wanted one machine at a time, at the bench, and a thermal roll
printer prints one sticker at a time with no sheet to track and no manual
feed. The existing labels do not fit the roll: a full label is 63.5 x 38.1 mm
and a 99010 sticker's printable area is 25.3 x 81.5 mm. Turned lengthwise a
full label would have to shrink to ~66 % to fit across, taking its 5.5 pt
values and 6 pt captions to 3.6-4 pt and its 0.31 mm QR modules to 0.2 mm
(2.4 dots at 300 dpi): unreadable and unscannable. A strip is about the same
area as a full label (2,060 vs 2,420 mm² printable) but 3.2:1 instead of
1.7:1, so each kind is re-laid out for it, not scaled.

## Decisions

Made with the user (2026-11-21/22):

1. **Every kind gets a strip layout**: board (rpi, opi, riscv, x86), FPGA
   (netv2, arty, acorn, pcileech, cynthion, jtag, unknown-fpga), USB adapter,
   Tiny Tapeout, SDR; and micro labels go on the roll too.
2. **Same facts, re-flowed.** A strip carries every fact and every QR code
   its kind's full label carries, needs exactly the same fields, and refuses
   exactly when the full label refuses (`label_input.missing`, the draw
   functions' own refusals). Nothing is dropped to make room.
3. **Micro labels two to a sticker**, at their true size (31.75 x 19.05 mm),
   end to end with a cut line between them, as a sheet's quarters are cut
   apart. An odd one out leaves the second half blank.
4. **PDF, plus `--print QUEUE`**: the roll is written as a PDF like every
   other stock, and `--print` also sends it to a CUPS queue over IPP and
   waits for the job. `rpi-hwid-sheet` is not changed: a roll has no slots
   to track.

Made while writing this spec (each may be overturned in review):

5. **One roll only: `dymo-99010`.** Other DYMO stock (99014 54 x 101 mm,
   11354 57 x 32 mm, ...) is not designed for now. The roll's geometry lives
   in one `Roll` record, so another is a record and a test, not a redesign.
6. **The strip box is the driver's printable area, less 1 mm, moved by the
   feed offset.** Strip layouts draw into a 79.5 x 23.3 mm box: the 99010
   printable area (below) less a 1.0 mm pad, because the sticker's corners
   are rounded. *Amended 2026-11-23:* the box keeps that size but is moved up
   the page (+y) by `Roll.feed_offset`, 5.5 mm for the 99010, because paper
   showed the printer puts the page 5.5 mm further along the sticker than
   the driver's page says; so on the sticker the box lands where the
   printable area says (see "Confirmed on paper").
7. **The smallest sizes are the full labels' smallest.** Each element of a
   strip may shrink no further than the same element may on the full label
   (the `min_size` the full layout passes for it), and nothing anywhere goes
   below the full labels' floors: 4.5 pt text (a swatch's colour names, a
   crowded board subtitle), 6 pt captions (`CAPTION`), 0.26 mm QR modules (a
   128-bit flash uid). Eliding with "…" (`fit`) is allowed only where the full
   label also elides.
8. **The PDF page is the driver's page.** One page per sticker, 79 x 252 pt
   portrait (the DYMO driver's `w79h252.2`), the strip drawn rotated onto it.
   `lp -d QUEUE file.pdf` then needs no size or orientation option.
9. **Strip layouts live in a new module, `rpi_hwid/strip.py`**, as micro's
   live in `micro.py`; the SDR strip sits beside the SDR label in
   `sdr_labels.py`, as the SDR label does. `labels.py` (2,543 lines) gains
   only the plumbing.

## The roll, as measured

From the LabelWriter 450 on this desk, 2026-11-21, with DYMO's own CUPS
driver (`printer-driver-dymo` 1.4.0, `lw450.ppd`, filter `raster2dymolw`):

| | value | source |
|---|---|---|
| sticker | 28 x 89 mm, 130 per roll | DYMO 99010; Farnell/AOS listings |
| driver page size | `w79h252.2` "99010 Standard Address", 79 x 252 pt (`PaperDimension` 78.96 x 252.00) | `lw450.ppd` |
| printable area | `ImageableArea` 4.32 4.32 76.08 235.44 pt, i.e. 25.3 x 81.5 mm; margins 1.5 mm, except 1.0 mm on the x = W side and 5.8 mm at the y = H end | `lw450.ppd` |
| registration | *Amended 2026-11-23:* the page top lands 5.5 mm into the sticker (the whole page is printed 5.5 mm further along it, toward its trailing end; the page's bottom ~5 mm falls off the sticker). The 2026-11-21 reading, "within ~1 mm", was taken on the previous host computer (the docs do not record whether it was the same LabelWriter) and disagrees; the 2026-11-23 one was on ten64 with raster2dymolw. The difference may come from the printer or from the host chain: re-measure with the calibration sticker when either changes | a mm-ladder test label, photographed (2026-11-21); `docs/examples/dymo_calibrate.py` on ten64 (2026-11-23) |
| resolution | 300 x 300 dpi (`DymoPrintQuality=Text`), 300 x 600 dpi (`Graphics`) | `lw450.ppd` |
| as IPP sees it | `media-size-supported` has `{x-dimension: 2786, y-dimension: 8890}` (1/100 mm); `media-supported` names it `custom_27.86x88.9mm_27.86x88.9mm`; `media-col-database` gives it margins 152 / 102 / 152 / 584 (left / right / bottom / top); `printer-resolution-supported` 300x300 and 300x600 dpi; no `DymoPrintQuality` attribute; `media-default` `unknown` | Get-Printer-Attributes on `ipp://localhost:631/printers/LabelWriter-450`, 2026-11-22 |

CUPS's generic `drv:///sample.drv/dymo.ppd` must not be used: it puts the
image ~4.5 mm along and ~1.5 mm across off the sticker, and a `/PageOffset`
in its PPD cannot correct that (cups-filters runs Ghostscript itself and
never executes PPD page-size code). DYMO's PPD compensates with asymmetric
margins. LABELS.md says so, and says to install `printer-driver-dymo`.

The printer cannot report what is loaded (the LabelWriter 450's `ESC A`
status byte has no size field), so the stock is named by the user.

## What it looks like

Strip coordinates are the label's own: origin top-left, x along the
79.5 mm, y down the 23.3 mm, as `Label` uses. Sketches are to scale in
proportion, not in exact mm; the final sizes are settled against rendered
previews and a printed roll, inside the minimums of decision 7.

**Board** (Raspberry Pi shown; Orange Pi, RISC-V and x86 swap the mark and
the model decoding exactly as the full label does):

```
+-----------------------------------------------------------------------------+
| [mark] Raspberry Pi 5                 [icons] | eth MAC                [QR] |
| [    ] 1 GB · Rev 1.1 · rev code a04171       | 98:fe:54:13:f5:75           |
| [QR ] HAT Waveshare PoE M.2 HAT+ (B)          | wlan MAC radio disabled,    |
| [ser] serial c36b093f773d46b8                 |          not readable       |
+-----------------------------------------------------------------------------+
```

The serial runs horizontally under its QR's column instead of up the left
edge; the right half is the MAC column, its value the strip's largest type.

**FPGA** (Arty shown; NeTV2, Acorn, PCILeech, Cynthion as their full labels):

```
+-----------------------------------------------------------------------------+
| [   ] [mark] hoopoe                   Micron N25Q128 · 16 MiB  flash  [QR]  |
| [QR ]        Arty A7-35T · XC7A35T    235351451900080037091015126b   uid    |
| [   ]        S/N 210319A43AD3                                               |
| Device DNA  0x0064f5483229085c                                              |
+-----------------------------------------------------------------------------+
```

**USB adapter, Tiny Tapeout, SDR**: the same pattern. The mark and the
big QR at the left end, the title and its detail lines in the middle, the
primary identifier as the longest line along the bottom, a secondary
identifier and its small QR at the right end. Each gets its sketch in the
plan's task for it, approved from a rendered preview before it is printed.

**Micro, two to a sticker:**

```
+-----------------------------------------------------------------------------+
|  +-------------------------------+  :  +-------------------------------+    |
|  |  micro label A (true size)    |  :  |  micro label B (true size)    |    |
|  +-------------------------------+  :  +-------------------------------+    |
+-----------------------------------------------------------------------------+
```

63.5 x 19.05 mm of micro labels centred in the 79.5 x 23.3 mm box, a grey
dashed cut line between them (micro's own `cut_guides` style).

## How it fits together

### `Label` takes its size (`labels.py`)

`Label.pt`, `outline` and `cut_line` use `LABEL_H`/`LABEL_W` directly
today; micro's `Cell` overrides them. `Label` gains `w`, `h` (defaulting to
`LABEL_W`, `LABEL_H`) and uses them, and `Cell` passes its size instead of
overriding. Every full and micro label must render **byte-identical** before
and after. No test in the suite pins it: it was checked task by task by
hashing every sheet each stock and `--outline` makes before and after
(`tmp/golden.py` against `tmp/golden-before.txt`), and per label kind in
review.

### `strip.py`

- `STRIP_W`, `STRIP_H` (79.5 x 23.3 mm). The strip box is already 1 mm
  inside the printable area, so a layout draws from its own edge: there is
  no further pad.
- `class Strip(labels.Label)` with `w, h = STRIP_W, STRIP_H`.
- `draw_board_strip`, `draw_fpga_strip`, `draw_usb_strip`,
  `draw_tinytapeout_strip`: each takes `(strip, record)`, the same record its
  full draw function takes, and reuses the full labels' helpers (marks,
  `qr`, `fit`, `captioned`, the model decodings) rather than copying them.
  Shared pieces of a full layout that a strip also needs (e.g. a board's
  title and detail-line text) are pulled out of the full draw function into
  a function both call, without changing the full label's output.
- `STRIP_OF = {labels.draw_board: draw_board_strip, ...}`: full draw function
  to strip draw function, so `labels.whole_labels`'s
  `(host, kind, title, draw, record)` tuples are reused unchanged. A full
  draw function with no strip is a test failure, not a runtime surprise.

`sdr_labels.draw_sdr_strip` is registered in `STRIP_OF` the same way.

### `micro.py`

`pack(micros, start)` groups micro labels four to a sticker; it takes
`per_sticker` (default 4, every existing caller unchanged), and the roll
asks for 2. `draw_pair(strip, group)` draws a group of two centred in a
strip with a cut line between them, as `draw_quad` draws four on a sheet's
sticker. Micro labels keep their ordering; only the group size changes.

### The roll stock (`labels.py`)

```python
@dataclass(frozen=True)
class Roll:
    name: str                 # "dymo-99010"
    page: tuple[float, float] # (79, 252) pt, the driver's PaperDimension
    printable: tuple[float, float, float, float]  # ImageableArea, pt
    ppd_size: str             # "w79h252.2", the driver's PageSize name (docs, lp)
    media_size: tuple[int, int]  # (2786, 8890), 1/100 mm, how IPP names it
    note: str                 # what LABELS.md and --help say about it

ROLLS = {"dymo-99010": Roll(...)}
```

The record is data and lives beside STOCKS; drawing and printing a roll
live in a new module, `rpi_hwid/roll.py`, which `labels` imports only when
a roll is asked for (`roll` imports `labels` and `strip`).

`--stock` takes a sheet (STOCKS) or a roll (ROLLS); one namespace, checked
not to collide. `render(...)` hands a roll to `roll.render_roll(docs, out,
roll, only, outline, pinned_names, order, invariant)`, which writes one page
per sticker:
whole labels first, host by host, exactly in `all_labels` order, then the
micro pairs. Each page: the `Strip` placed in the strip box (the printable area less 1
mm, moved up the page by the feed offset; amended 2026-11-23) and rotated 90°
onto the portrait page.

`--outline` on a roll draws the sticker's edge and the strip box, for a
plain-paper proof. `--start` has no meaning on a roll and is refused; so are
`--place` and `--list --json` (the existing "L7160 sheet tool's" refusal).
`--list` prints one line per sticker, `sticker N  host  kind  title`, instead
of `sheet/row/col`; a sticker of two micro labels lists both titles joined by
` | `, as the sheet listing does for a micro sticker.

`render_sheet(inputs, ..., stock="dymo-99010")` returns the roll PDF, so the
library API gets the roll for free. `render_label` is unchanged (a strip
form of it is a follow-up, if the fpgas.online site wants one).

### Printing: `--print QUEUE`

`rpi-hwid labels --stock dymo-99010 --print LabelWriter-450` writes the PDF
as usual, then sends it to the queue and waits:

- `QUEUE` is a CUPS queue name (sent to
  `ipp://localhost:631/printers/QUEUE`) or a full `ipp://` URI.
- `roll.print_pdf` uses the existing IPP client, `rpi_hwid.sheet.ipp.Printer`.
  `print_job` there is hard-wired to A4 label stock in the manual feed; it
  becomes `print_document(pdf, job_name, job_attributes)`, with the sheet's
  `print_job` a thin caller of it (its bytes on the wire unchanged, tested).
- Job attributes for the roll: `media-col` with `media-size` = the roll's
  size in 1/100 mm (2786 x 8890: CUPS names PPD sizes by dimension, not by
  the PPD's `w79h252.2`), `print-scaling` = `none`, `printer-resolution`
  = 300 x 600 dpi, and `DymoPrintQuality` = `Graphics`. The resolution
  alone is not enough: `lw450.ppd` sets `HWResolution` from `*Resolution`
  (OrderDependency 20; CUPS builds `printer-resolution-supported` from it)
  and then from `*DymoPrintQuality` (OrderDependency 21, default `Text`,
  `<</HWResolution[300 300]>>`), so a job that names only the resolution
  is reset to 300 x 300. The job therefore sends the PPD option by name as a
  job attribute, which CUPS hands to the filters; the setup docs also set it
  as the queue's default (`lpadmin -o DymoPrintQuality=Graphics`). That
  CUPS picks the 99010 size from the dimensions and prints at `Graphics` is
  checked on paper (the plan's on-paper task).
- Before sending: Get-Printer-Attributes, and refuse with the reason when
  the queue is stopped (`printer-state` 5, with its `printer-state-message`,
  e.g. "Unplugged or turned off"), not accepting jobs, or its
  `media-size-supported` lacks the roll's size (a queue on the generic
  driver, or another printer, is caught here).
- It polls the job to `completed`, `aborted` or `canceled` and says which.
  It waits as long as the job shows progress (a new `job-state` or more
  `job-impressions-completed`); one that shows none for 60 s plus 4.8 s a
  sticker (a LabelWriter 450 does ~25 a minute at 300 x 600) is given up
  on, saying the job may still be printing and naming its id. Exit 0 only
  on `completed`.
- `--print` with a sheet stock is refused: sheets go through
  `rpi-hwid-sheet`, which knows the manual feed and the slots.

## Errors

Option combinations are refused by argparse, like the command's other
option errors (the usage line and the reason, exit status 2): unknown
stock; `--start`/`--place`/`--list --json` with a roll; `--print` with a
sheet; `--print` with `--list` or `--check`, which write no roll.

Every other refusal is one line on stderr naming the cause, exit status 1:
queue unreachable (the URI tried); queue stopped; media not supported (what
the queue does support); job aborted or canceled (the job's
`job-state-reasons`); no progress before the deadline, or the printer lost
after sending (the job id, that it may still be printing, and `lpstat` for
a CUPS queue). Job attributes the printer ignored are a warning, not a
refusal.
A label short of a field is refused exactly as today, before anything is
printed.

## Testing

- **Geometry** (`tests/test_stock.py`): the roll's page is the driver's
  page; the strip box, placed and rotated, lies 1 mm inside the printable
  area as the printer puts it (the printable area shifted by the feed
  offset; amended 2026-11-23) and inside the page, and its size is still
  the printable area less 1 mm; two micro labels and their cut line fit the strip box.
- **Full labels unchanged**: the existing sheets render byte-identical
  before and after the `Label` refactor (golden hashes taken first).
- **Every strip, every fixture**: each record in `tests/data` that makes a
  full label also makes a strip; the same records refuse. A recording canvas
  checks every strip against decision 7's floors (text under 4.5 pt, a
  caption under 6 pt, a QR module under 0.26 mm) and for any mark drawn
  outside the strip box.
- **Same facts**: for each fixture, every identifier string the full label
  draws is also drawn on its strip (recorded `drawString` text, the same
  way), and every QR payload too.
- **Roll document**: page count = whole labels + ceil(micro labels / 2);
  page size; `--list` lines; refusals.
- **Printing**: against the fake IPP printer the sheet tests already use
  (`tests/test_sheet_ipp.py`): the request's attributes, the pre-flight
  refusals, each terminal job state, the timeout.
- **On paper**: each kind printed once on a real 99010 roll and checked by
  eye and with a phone's QR scanner, before the docs gallery shows it.

## Docs

LABELS.md gets "A DYMO roll" under "Printing a sheet": installing
`printer-driver-dymo`, pointing the queue at `lw450.ppd`, `--stock
dymo-99010`, `--print`, the printable area, and why not the generic driver.
The gallery gains a strip of each kind (`docs/examples/render.py`).

## Out of scope

- Other rolls and other label printers.
- `rpi-hwid-sheet` printing to the roll.
- A strip form of `render_label` for the fpgas.online site.
- Strip labels on sheets (no sheet stock has 28 x 89 mm stickers here).

## Confirmed on paper (2026-11-23)

Printed on ten64, LabelWriter 450, DYMO's own driver, 19 stickers:

- CUPS honoured `printer-resolution` 300x600 and `DymoPrintQuality=Graphics`
  (Get-Job-Attributes on the job).
- 19 stickers printed in 86 s, 4.5 s per sticker, inside `job_wait`'s 4.8 s
  per sticker.
- The strips came out cut off at their x = 0 end and with ~11 mm blank at the
  other where the PDF has 6.8 mm. The CUPS raster and the byte stream
  `raster2dymolw` sends (label length 2100 lines = 88.9 mm at 600 dpi,
  leading blank lines as `ESC f` skips, no trimming) keep the PDF's
  positions exactly. A calibration sticker (`docs/examples/dymo_calibrate.py`,
  a mm scale numbered from the page top) showed the page top landing 5.5 mm
  into the sticker (measured 5.56) and the sticker's trailing edge at ~84.0
  mm from the page top. The whole page is printed 5.5 mm further along the
  sticker, so the strip box moved up the page by 5.5 mm (`Roll.feed_offset`,
  decision 6). The 28 mm (cross) axis was right.

## To confirm on the first printed roll

Settled by the 2026-11-23 print: the quality attributes were honoured; 4.5 s
per sticker; the feed offset (5.5 mm); and the strips read the right way
round (the cut was at the strip's left end, and the text was upright and not
mirrored).

Still open, awaiting a check of the reprinted roll:

- Which end of the sticker comes out of the printer first, so the strip
  reads the right way up as it is peeled (the rotation is one constant).
- That QR codes scan.
- That 5.5 pt type and 0.31 mm modules are crisp on this thermal head at
  `Graphics` quality; if not, the minimums move up and the layouts with them.
