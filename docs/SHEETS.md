# Printing a few labels at a time

`rpi-hwid labels` fills a sheet from its first sticker. When labels are wanted
one machine at a time — the Pi just set up, the dongle just plugged in — a whole
sheet per label wastes 20 stickers, and printing the next label onto a sheet
already part-used means knowing which of its stickers are gone.
`rpi-hwid-sheet` keeps that record and prints only into a sheet's free slots.

```
$ export RPI_HWID_PRINTER=ipp://10.1.20.222/ipp/print
$ rpi-hwid-sheet new
K7QX  L7160 · started 2026-10-01 10:12 on ten64 by tim · rpi-hwid 0.4.post12  -> ~/.local/state/rpi-hwid/sheets/K7QX.json
$ rpi-hwid-sheet print K7QX rpi5-433mhz -- --esp32
  rpi5-433mhz: Raspberry Pi 5 Model B Rev 1.0; header ['hat']; power pd-5a; esp32 …
Sheet K7QX (L7160 · started 2026-10-01 10:12 on ten64 by tim · rpi-hwid 0.4.post12), pass 1, printer ipp://10.1.20.222/ipp/print
  first pass: the sheet's id, note and registration ticks go in its margins
  slot  label
  1     rpi5-433mhz/rpi/Pi 5 4 GB d88100008543dc30
  2a    rpi5-433mhz/esp32-433/ESP32 433 MHz e4:65:b8:0b:4c:20
  cut guides on sticker 2
  data: ~/.local/state/rpi-hwid/reads/K7QX/2026-10-01T101500
  preview: ~/.local/state/rpi-hwid/plans/K7QX-p1-2026-10-01T101502/preview.png
  pdf: ~/.local/state/rpi-hwid/plans/K7QX-p1-2026-10-01T101502/pass.pdf
Print 2 labels on sheet K7QX through the manual feed? [y/N] y
job 312 sent to Brother MFC-L3760CDW series.
Put sheet K7QX in the printer's manual feed slot (one sheet; the job waits for it), the TOP EDGE going in first.
sheet K7QX pass 1 printed; 19 free stickers, 3 free quarters
```

## What it does

1. **Reads the hosts afresh** with `rpi-hwid collect` (or `rpi-hwid tasmota`
   with `--tasmota`), anything after `--` going to the collector as it is:
   `-- --fpga`, `-- --esp32-read HOST=PORT`. A label is only ever printed from
   a read made for it; `--data DIR` prints from an earlier read instead.
   A host the read got nothing for stops the print. Every read is kept, with
   its log, under the state directory's `reads/<sheet>/` and never deleted:
   it is the evidence behind what went on the sticker.
2. **Asks rpi-hwid which labels that makes** (`rpi-hwid labels --list
   --json`), takes the named hosts' labels — or, with `--label TEXT`, those of
   them whose id contains the text — and gives each a free slot: a whole label
   the first free sticker, a micro label the first free quarter of a sticker
   already started with micro labels, then a fresh sticker after the whole
   labels'.
3. **Has rpi-hwid draw them** there (`rpi-hwid labels --place`; this tool
   draws no label itself) and writes a preview with the outlines drawn and the
   sheet's used slots greyed out.
4. **Shows the plan and asks.** Nothing is sent without a yes.
5. **Sends it to the printer's manual feed slot** (IPP `media-col` →
   `media-source: manual`, never the paper tray), A4 label stock, full size,
   colour, and waits for the job to finish.

The first pass on a sheet also prints, in its top and bottom margins, the
sheet's id, when, where and by whom it was started, which edge is which, and
registration ticks in line with every die-cut — see "Chosen labels in chosen
slots" in [LABELS.md](LABELS.md). Feed the sheet the same way round every time;
the margins say which edge is the top.

## The record

Each sheet is one file, `$XDG_STATE_HOME/rpi-hwid/sheets/<ID>.json`
(`~/.local/state/rpi-hwid/sheets/`; `--state DIR` puts the whole store
elsewhere). It holds who started the sheet where and when, its printer, every
used slot with the label in it, the stickers whose micro cut guides are
printed, and each pass: when, on which host, by whom, its print job and how
that ended, and the read it printed from. It is this machine's record of the
paper in its drawer, not something to commit.

A slot is used from the moment the printer accepts the job — a slot wrongly
kept costs a sticker; one wrongly freed prints a label over another. A job
cancelled or aborted before printing anything gives its slots back. One that
fails part-way, or is still running when `--wait` (600 s) runs out, keeps them,
and says so.

A sheet's id is four characters with nothing to misread off paper (no 0/O,
1/I/L or U/V); type it in either case.

## In a Claude Code session

`--prepare` stops before asking: it reads the hosts, makes the plan and the
preview, and prints the command that sends it. That is how a Claude Code
session prints "the label for the Pi we were just working on":

1. Claude runs `rpi-hwid-sheet print SHEET HOST --prepare`.
2. Claude shows you the plan — every label's id and slot — and the preview
   image, and asks whether it is right.
3. Only after your yes, Claude runs `rpi-hwid-sheet commit PLAN`, and tells you
   to feed the sheet.

`commit` refuses a plan made against an older state of the sheet (anything
printed on it since), and a plan already sent.

## Commands

| | |
|---|---|
| `new [--printer URI]` | start a sheet; prints its id |
| `print SHEET\|new HOST… [-- COLLECT-ARGS]` | read, show, ask, print |
| `print … --prepare` | read and show only; prints the `commit` command |
| `print … --data DIR` | print from an earlier read instead of reading |
| `print … --label TEXT` | only the labels whose id contains TEXT |
| `print … --only KIND` | as `rpi-hwid labels --only` |
| `print … --tasmota` | read with `rpi-hwid tasmota` |
| `commit PLAN` | send a prepared plan |
| `status SHEET` | the sheet's map, used slots and passes |
| `list` | every sheet and what is free on it |
| `mark SHEET SLOT… [--why TEXT]` | record slots used without printing (a sheet used before this tool) |

The printer is `--printer` (an IPP URI, `ipp://HOST/ipp/print`) or
`$RPI_HWID_PRINTER`, and is remembered by the sheet. `--rpi-hwid CMD` names the
rpi-hwid to ask for labels (by default the one this tool was installed with).

## What it needs

Nothing beyond rpi-hwid and its labels dependencies. It speaks IPP itself with
the Python standard library: [pyipp](https://pypi.org/project/pyipp/) silently
drops every job attribute it has no table entry for (`media-source`,
`media-type`, `print-scaling`, `print-color-mode`) and cannot encode the
`media-col` collection that chooses a tray, so it could not reach the manual
feed. No CUPS is needed. `pdftoppm` (poppler-utils) makes the preview; without
it the plan is still shown and printed, with no picture.
