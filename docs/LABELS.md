# Labels

The derived names that go on them, the five layouts, and where the artwork comes
from. For the gallery and the command itself, see the [README](../README.md); for
the data they are made from, see [COLLECT.md](COLLECT.md).

## Names

Raw identifiers come in near-identical clusters — Device DNAs sharing most of
their digits, Digilent serials differing in the last byte, configuration flash
uids off the same reel — so `rpi-hwid name` hashes them into short, distinct
words:

```
$ rpi-hwid name --netv2 0x00742c4e63b9085c --arty 210319B301DE 210319B0C238 \
      --cynthion 267125df30c460de
netv2-grove  00742c4e63b9085c
cynthion-alidade  267125df30c460de
arty-hawk  210319B301DE
arty-serin  210319B0C238
```

A NeTV2's name is a pure function of its DNA, and a Cynthion's of its flash uid.
Both buy scatter rather than uniqueness: sixteen uids a digit apart land on
twelve different words, so no two boards are misread as each other, but a pure
function into 32 words can collide and it is the identifier under the name that
identifies the board. Arty names are a hash *chain*
resolved against a registry, so two serials never share a word and adding a board
never renames an old one. Keep the registry as a JSON object of serial to name and
pass it with `--names registry.json` to both `name` and `labels`.

## Printing a sheet

`rpi-hwid labels` lays out 63.5 × 38.1 mm labels, 21 to an A4 sheet (the Avery
L7160 grid), from a directory of collected documents. Print at 100 % — "fit to
page" shrinks the grid and every label lands off its sticker.

Labels come out host by host, and within a host the board first and then what
is attached to it — FPGA, Tiny Tapeout, then the USB adapters — so one
machine's labels are peeled off side by side instead of from three different
pages. Positions are still filled tightly, so a host whose group will not fit
is split across the sheet break rather than wasting the stickers before it
(`rpicm1-serial` below). Every line names its host, so a printed page can be
matched back to the machine it came from.

```
$ rpi-hwid labels --data data/ --list
sheet 1 row 1 col 1  pi-sw2-p16     rpi    Pi 4 Model B 2 GB 10000000ce8e3593
sheet 1 row 1 col 2  pi-sw2-p16     arty   arty-hawk
sheet 1 row 1 col 3  pi-sw2-p22     opi    Orange Pi PC 1 GB 02c000812eb7a34e
sheet 1 row 2 col 1  pi-sw2-p47     rpi    Pi 5 1 GB c36b093f773d46b8
sheet 1 row 2 col 2  pi-sw2-p47     acorn  Acorn CLE-215+
sheet 1 row 2 col 3  rpi4-tt        rpi    Pi 4 Model B 4 GB 100000003a7e1c9b
sheet 1 row 3 col 1  rpi4-tt        tt     TT06 E6614C311B7A7A37
sheet 1 row 3 col 2  rpi4-tt        tt     TTGF0p2 E66360B8A3C1D5F2
sheet 1 row 3 col 3  rpi5-netv2     rpi    Pi 5 4 GB d88100008543dc30
sheet 1 row 4 col 1  rpi5-netv2     netv2  netv2-grove
sheet 1 row 4 col 2  rpi5-netv2     usb    ASIX Elec. Corp. AX88179 00:0e:c6:82:b5:e1
sheet 1 row 7 col 3  rpicm1-serial  rpi    Pi Compute Module 1 512 MB 0000000067bdbf54
sheet 2 row 1 col 1  rpicm1-serial  usb    Realtek USB 10/100/1000 LAN 00:e0:4c:68:36:95
$ rpi-hwid labels --data data/ --out labels.pdf
13 labels on 1 sheet -> labels.pdf
```

`--outline` draws the die-cut edges for an alignment print on plain paper;
`--start N` skips N positions on the first sheet so a partly used sheet can be
finished; `--only rpi|opi|fpga|tt|usb` limits the kinds, and takes a single FPGA
board kind (`netv2`, `arty`, `acorn`, `pcileech`, `cynthion`) where one host
carries more than one board; `--list` prints what
would be generated and where.

Every label carries only what cannot change, and every identifier that might
otherwise be typed is also a QR code. The five layouts, cropped from a rendered
sheet:

## Raspberry Pi

The MACs are what people look for, so they are the largest thing on the label,
each with its own QR. Model, memory and revision are decoded from the revision
code, and the HAT band names what the probe found on the header (and the EEPROM
UUID when there is one). The serial is a cross-check rather than the identity
anyone uses, so it runs up the left edge with a small QR of its own at the top.
The layout is always the same, so a stack of them reads at a glance.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi5.png" alt="Pi 5, bare header" width="49%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi5-poe-hat.png" alt="Pi 5 wearing a Waveshare PoE M.2 HAT+ (B); its radio is disabled so the wlan MAC cannot be read" width="49%">
</p>
<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi4-pmod-hat.png" alt="Pi 4 with a Digilent Pmod HAT Adaptor" width="49%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi3bplus.png" alt="Pi 3B+; the wlan MAC is derived from the eth MAC" width="49%">
</p>
<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi-zero-w-bonnet.png" alt="Pi Zero W with the Waveshare PoE-ETH-USB-HUB-HAT; the bonnet's RTL8152 is its eth MAC" width="49%">
</p>

The Zero W's wired port comes from the bonnet, so its MAC is printed as the eth
MAC. On a 3B+ or a Zero the wlan MAC follows from the eth MAC (the Broadcom-OUI
rule: same serial digits, XOR `55:55:55`), so it is printed even when the radio is
off. On a Pi 4 or 5 it cannot be derived, so a disabled radio is stated as such.

The boards older than the packed revision code get the same label, from a lookup
rather than a decode: a Model B is `000f`, a Compute Module 1 `0011`. What they
have *not* got is printed too, because on these boards a blank would be read as
something unrecorded rather than something absent — a Model B says `no radio`
and a Compute Module 1 says both `no radio` and `no wired port`. That is also
why the wlan MAC is not derived for them: the Broadcom rule works off the serial
alone and would happily supply one for a radio that is not there.

A Pi up to the 3B reaches Ethernet through a USB chip soldered beside the SoC,
so its wired MAC sits on the Pi's own label and not on a dongle's — the port is
recognised by carrying a MAC the board derives from its own serial, which a real
removable adapter never does.

## Orange Pi

The same layout, band for band, with the Orange Pi orange in the raspberry's box.
Title and subtitle come from the device tree instead of a revision code: model,
fitted RAM, SoC, and the device-tree id `dt orangepi-pc`, the board's canonical id
since Xunlong sells it by name with no part number. The HAT row is the Pi's row,
read the same way and saying the same thing: the header is probed on any board
that has one, so an Orange Pi wearing a Digilent Pmod HAT Adaptor says
`HAT  Pmod HAT Adaptor` with the adaptor's uuid under it, exactly as a Pi
wearing the same adaptor does. The wlan row says `no radio` on a PC or One. The SoC serial runs
up the spine as on a Pi.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/orange-pi-pc.png" alt="Orange Pi PC; eth MAC derived by U-Boot from the SoC serial, no radio" width="49%">
</p>

## FPGA boards

The maker and the derived name, the die, and the immutable identifier full width
with a QR: Device DNA where read, the Digilent serial and flash part on an Arty,
and a line to write the DNA on when it has not been read yet. The foot names
what it is printing, because not every board has a Device DNA — an ECP5 has
none, so a Cynthion is keyed on its configuration flash's uid and says so.

Nothing a reflash could change appears on any of them. Which gateware a
Cynthion is running settles whether its USB serial may be trusted as the flash
uid, and then stays in the probe document where it belongs.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/netv2.png" alt="NeTV2" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/arty.png" alt="Arty A7-35T" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/acorn.png" alt="Acorn CLE-215+, DNA not yet read" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/cynthion.png" alt="Cynthion r1.4, keyed on its ECP5 configuration flash uid with the die's TraceID above it" width="32%">
</p>

A Cynthion carries two identifiers, because they cost very differently. The
configuration flash's uid is free: the analyzer gateware already publishes it as
the USB serial, so a bare `rpi-hwid fpga` reads it with nothing sent to the
board, and it is what the sticker and its QR are keyed on. The ECP5's own
TraceID — the die's answer to a Device DNA, masked to its factory 56 bits — needs
`rpi-hwid fpga --force-offline`, which hands the USB port to Apollo, reads
`UIDCODE_PUB` over JTAG, reconfigures the FPGA from its flash and then waits to
watch the analyzer come back. That ends the board's capture for a few seconds and
may drop power to whatever is on its TARGET port, which is why it is never part
of `--jtag`. It is printed and never keyed on: a name derived from it could not
be recovered without taking the board offline again. If a board is ever left in
Apollo mode, `rpi-hwid fpga --recover-cynthion` is the way home.

## Tiny Tapeout boards

The shuttle is the headline, with ASIC or FPGA breakout and the PDK under it, and
at the right of that line the marks of whoever ran the shuttle and whose silicon
it is — an Efabless or ChipFoundry chipIgnite run on SkyWater, a wafer.space run
on GlobalFoundries, or IHP's own, which is both. Then
the demo board as the SDK detected it with the revision that shipped in that kit,
and the chip ROM's commit. Then a sample of each board, so the right one is
picked out of a drawer: a box filled with that board's soldermask and lettered,
the way the board itself is, in its silkscreen colour — so the box shows both
colours at once — with the two named beside it, soldermask over silkscreen, for
the reader whose eye the print cannot be trusted by. A TT05 kit, say, is a
yellow carrier lettered in black beside a black demo board lettered in white. A
box whose colours are not recorded is left empty and struck through.

The colours themselves are not readable from the board. They come from the
[published Tiny Tapeout board spreadsheet](https://mith.ro/tt-boards/), pulled into
`src/rpi_hwid/tt_boards.json` by `tools/fetch_tt_boards.py` and kept honest by
a scheduled CI job that re-derives the file and fails when the sheet has moved.
A swatch is the board's own hex out of that sheet rather than one generic hex
per colour name, so TT06's rose and TT08's light blue are the pink and the blue
those boards actually are. Where the sheet names a colour but records no hex,
the name falls back to the palette in `rpi_hwid.tinytapeout`. The large
QR opens the chip's page on tinytapeout.com; the demo board's RP2 unique id —
its USB serial — runs along the foot with a small QR of its own.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout.png" alt="TT06 chip on a TT06+ demo board" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout-gf.png" alt="TTGF0p2 chip on a DBv3 demo board" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout-fpga.png" alt="An FPGA breakout standing in for the ASIC on a DBv3 demo board" width="32%">
</p>

A board with no ASIC on it says so: the FPGA breakout's ROM has no shuttle to
name — `config.ini` forces the string `FPGA` — so the ROM line stays empty and
the QR falls back to the chips index.

Both its boards are still coloured, because neither needs the shuttle to be
found. The demo board names its own revision over the REPL and the sheet has
that revision, so a shuttle the sheet has not listed under any board's "Used by"
is still coloured from the board it is actually sitting on. The carrier is the
sheet's one row for an FPGA rather than a packaged die (FabricFox, an
iCE40UP5K).

Where the sheet records no colours at all, the box is left empty and struck
through rather than guessed at. That is not hypothetical: every IHP shuttle is
in that state today, carrier and silkscreen both, which is why the example above
is a GlobalFoundries part and not an IHP one.

## USB network adapters

The descriptors (USB version and speed, driver, VID:PID) beside the MAC's QR, and
the MAC itself full width along the foot, so a dongle can be matched to a DHCP
lease from across the room. A wireless adapter is drawn with a WiFi glyph in
place of the RJ45, so the two kinds are told apart across the room as well.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-asix.png" alt="ASIX AX88179 USB 3.0 gigabit adapter" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-wifi.png" alt="Realtek 802.11ac USB WiFi adapter" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-linksys.png" alt="Linksys USB3GIGV1 USB 3.0 gigabit adapter" width="32%">
</p>

## Artwork

The package ships the Raspberry Pi raspberry, the Orange Pi orange, the Alphamax,
Digilent, Great Scott Gadgets and Tiny Tapeout marks (each its owner's trademark, drawn only on that
maker's own hardware to identify it) and the public-domain USB trident; see
[`src/rpi_hwid/artwork/README.md`](../src/rpi_hwid/artwork/README.md) for the
sources. A `--artwork DIR` overrides any of them and may add a `netv2.svg`. A
board whose maker has no mark (SQRL) gets the name in type.
