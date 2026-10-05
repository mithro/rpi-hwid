# Printing a few labels at a time

`rpi-hwid labels` fills a sheet from its first sticker. When labels are wanted
one machine at a time — the Pi just set up, the dongle just plugged in — a whole
sheet per label wastes 20 stickers, and printing the next label onto a sheet
already part-used means knowing which of its stickers are gone.
`rpi-hwid-sheet` keeps that record and prints only into a sheet's free slots.

```
$ export RPI_HWID_PRINTER=ipp://10.1.20.222/ipp/print
$ rpi-hwid-sheet print new rpi4-esp
new sheet 65D7
  rpi4-esp: Raspberry Pi 4 Model B Rev 1.4; header bare; power undetermined
1 of 1 host(s) written to ~/.local/state/rpi-hwid/reads/65D7/2026-10-01T154207
Sheet 65D7 (L7160 · started 2026-10-01 15:42 on ten64 by tim · rpi-hwid 0.0.post348), pass 1, printer ipp://10.1.20.222/ipp/print
  first pass: the sheet's id, note and registration ticks go in its margins
  slot  label
  1     rpi4-esp/rpi/Pi 4 Model B 8 GB 1000000053f279e5
  2     rpi4-esp/usb/Realtek 802.11n WLAN Adapter 64:70:02:0c:68:02
  data: ~/.local/state/rpi-hwid/reads/65D7/2026-10-01T154207
  preview: ~/.local/state/rpi-hwid/plans/65D7-p1-2026-10-01T154210/preview.png
  pdf: ~/.local/state/rpi-hwid/plans/65D7-p1-2026-10-01T154210/pass.pdf
Print 2 labels on sheet 65D7 through the manual feed? [y/N] y
job 314 sent to Brother MFC-L3760CDW series.
Put sheet 65D7 in the printer's manual feed slot (one sheet; the job waits for it), the TOP EDGE going in first.
the printer is waiting for sheet 65D7 in its manual feed slot
sheet 65D7 pass 1 printed; 19 free stickers, 0 free quarters
```

That is a real run (2026-10-01, the Welland Brother MFC-L3760CDW), made as
`--prepare` then `commit`; the interactive form prints the same with the
question between. Anything after `--` goes to the collector:
`rpi-hwid-sheet print K7QX rpi5-433mhz -- --esp32`.

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

## More than one sticker of a label

A board that wants its label in two places (on the board and on its case, say)
gets two stickers in one pass:

```
$ rpi-hwid-sheet print K7QX pi5 --copies /acorn/=2
  slot  label
  3     pi5/rpi/Pi 5 2 GB 285df3f84af242d0
  4     pi5/acorn/acorn-holly 0x00200c8664b04854
  5     pi5/acorn/acorn-holly 0x00200c8664b04854
```

`--copies TEXT=N` gives N stickers, one after the other, to every label whose
id contains TEXT; it may be given more than once. The copies are the same
label drawn from the same read, each in a slot of its own, and the record
lists each slot. Without it a label is never placed twice in a pass, which is
how a mistake in a plan is caught (`rpi-hwid labels --place` refuses a plan
that repeats an id unless the plan itself says `"copies": true`).

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

The printer cannot always say how a job ended. It may have forgotten the job
(`follow` then gets "not found"), or the cancelled pass may no longer be the
sheet's last, and only the last pass is given back by itself. A person who has
looked at the sheet settles it:

```
$ rpi-hwid-sheet unprint 7MRC 4 --why "Tim: the page has three empty labels before the two which just printed"
```

The pass's slots, cut guides and, if it was the pass that printed them, the
sheet's margins are free again. The pass is not removed from the record: it
stays, as `not-printed`, with who ran the command, when, the reason given, the
slots it had held and what its job's state had been, and `status` shows that.
Its number is not given to a later pass. A pass whose job the printer reported
completed is refused: the printer's word is that it printed. A pass whose job
was cancelled or aborted part-way, with its slots kept, is accepted: the
person looking at the sheet knows better than the printer's count.

Once a person has said what a pass did, with either command, the printer's
later word does not change it: a `follow` still waiting on that job leaves
the pass as recorded. If the unprinted pass had printed cut guides for micro
labels, they are recorded as not printed too, and the next micro label on
that sticker prints them.

The other way round, a pass that is on the sheet while the printer has
forgotten its job (this Brother forgets a finished job within minutes) would
stay "sent" for ever, and `follow` would keep asking about it:

```
$ rpi-hwid-sheet printed 7MRC 6 --why "Tim: the two labels which just printed"
```

Its slots stay used; the pass becomes `completed` and records who said so,
when, why and what its job's state had been.

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
| `print … --copies TEXT=N` | N stickers of each label whose id contains TEXT; the others get one |
| `print … --only KIND` | as `rpi-hwid labels --only` |
| `print … --tasmota` | read with `rpi-hwid tasmota` |
| `commit PLAN` | send a prepared plan |
| `status SHEET` | the sheet's map, used slots and passes |
| `list` | every sheet and what is free on it |
| `mark SHEET SLOT… [--why TEXT]` | record slots used without printing (a sheet used before this tool) |
| `unprint SHEET PASS --why TEXT` | record, on a person's word, that a pass put nothing on the sheet: its slots are free again |
| `printed SHEET PASS --why TEXT` | record, on a person's word, that a pass is on the sheet, when the printer no longer knows its job |

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
