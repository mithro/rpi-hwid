# Labels

The derived names that go on them, the six layouts, and where the artwork comes
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
sheet 1 row 1 col 3  pi-sw2-p22     opi    Orange Pi PC 1 GB 02c000812eb7a34e
sheet 1 row 3 col 1  pi-sw2-p47     rpi    Pi 5 1 GB c36b093f773d46b8
sheet 1 row 3 col 2  pi-sw2-p48     rpi    Pi 5 2 GB 0cd35697db04a4ab
sheet 1 row 3 col 3  pi-sw2-p48     acorn  acorn-willow 0x0054b48664b04854
sheet 1 row 4 col 1  pi3            rpi    Pi 4 Model B 1 GB 10000000f1b7bb5a
sheet 1 row 4 col 2  pi3            arty   arty-hoopoe 0x0064f5483229085c
sheet 1 row 4 col 3  rpi4-tt        rpi    Pi 4 Model B 4 GB 100000003a7e1c9b
sheet 1 row 5 col 1  rpi4-tt        tt     TT06 E6614C311B7A7A37
sheet 1 row 6 col 2  rpi5-netv2     rpi    Pi 5 4 GB d88100008543dc30
sheet 1 row 6 col 3  rpi5-netv2     netv2  netv2-grove 0x00742c4e63b9085c
sheet 1 row 7 col 1  rpi5-netv2     cynthion cynthion-theodolite 0x1b808604604e0e
sheet 1 row 7 col 2  rpi5-netv2     usb    ASIX Elec. Corp. AX88179 00:0e:c6:82:b5:e1
sheet 1 row 7 col 3  rpib-serial    rpi    Pi Model B 512 MB 00000000110aeed6
sheet 2 row 1 col 1  rpib-serial    usb    Realtek 802.11n NIC 80:3f:5d:13:8e:67
$ rpi-hwid labels --data data/ --out labels.pdf
26 labels on 2 sheets -> labels.pdf
```

`--outline` draws the die-cut edges for an alignment print on plain paper;
`--start N` skips N positions on the first sheet so a partly used sheet can be
finished; `--only rpi|opi|riscv|x86|fpga|tt|usb` limits the kinds, and takes a single FPGA
board kind (`netv2`, `arty`, `acorn`, `pcileech`, `cynthion`) where one host
carries more than one board; `--list` prints what
would be generated and where.

Every label carries only what cannot change, and every identifier that might
otherwise be typed is also a QR code. The six layouts, cropped from a rendered
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
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi4.png" alt="Pi 4; its header carries nothing identifiable" width="49%">
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

## RISC-V boards

The same layout again, for a board whose harts are RISC-V (`--only riscv`).
The maker's mark is in the raspberry's box — the SiFive symbol on a HiFive
Unmatched — and the title and subtitle come from the device tree as on an
Orange Pi: the model without the maker's name, then fitted RAM, SoC and the
device-tree id. The Unmatched has no HAT header, so its band carries what
makes it a RISC-V board instead: the RISC-V logo in the left column, the ISA
beside it, and under that the harts, the MMU mode
and the board's PCB and BOM revisions from its EEPROM. The ISA is the one the
kernel reports, in the ISA manual's short form: the kernel spells out every
extension, implied or not (`rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd`),
too long to print at a readable size, so only the folds the manual defines as
equal are made — G for IMAFD with Zicsr and Zifencei, and C for Zca and Zcd —
giving `RV64GC_Zicntr_Zihpm`, the same extensions.

The spine carries the board's serial from that EEPROM, `SF105SZ212200391` —
the one SiFive printed on the board — and the eth row its MAC, which comes
from the same EEPROM. U-Boot also copies the serial into the device tree, so
the probe reads it twice; the two must agree, as must the EEPROM's MAC and the
port's, and a label is refused rather than printed with one of two different
answers. A serial that was not read at all stops the run with the host and
the command that reads it. The wlan row says `no radio`: the Unmatched's M.2
E-key slot takes a Wi-Fi card, but a card is not the board.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/hifive-unmatched-1.png" alt="SiFive HiFive Unmatched A00 (hifive-unmatched-1); serial and MAC from its board EEPROM, RISC-V logo and ISA in the HAT band" width="49%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/hifive-unmatched-2.png" alt="SiFive HiFive Unmatched A00 (hifive-unmatched-2)" width="49%">

## x86 boards

The same layout again, for a PC that describes itself through DMI/SMBIOS rather
than a device tree — the fleet's two MinnowBoards. The board's project mark (the
MinnowBoard fish) takes the raspberry's box; the title is the DMI board name and
the subtitle the fitted RAM, the CPU and the platform revision the firmware
reports (`rev D0` on a Turbot, `rev B3` on a MAX). The board's maker, from the
DMI vendor string, is a mark the fish's size beside it, with no text: ADI
Engineering's on the Turbot, CircuitCo's on the MAX; the title and subtitle move
over past it. A PC has no HAT header, so that band is empty, unless the maker has
no mark on file, when it names the maker instead. The serial up the
spine is the firmware's DMI serial, which on both MinnowBoards is the Ethernet
MAC without its colons; the wlan row says `no radio`, since neither board has
one.

The BIOS version, the product UUID and the disks' serials are in the document
but not on the label: a firmware update changes the first, the second is the
same `00000000-6462-4524-006a-9b7737e315cf` on both boards (a firmware constant),
and the disk is an mSATA or SATA drive that can be swapped. A board the tables in
`rpi_hwid.x86` do not know is still labelled, in its firmware's own words, with
an empty mark box and `none found` where no radio or wired port was seen.

The DMI serials are root-only. The probe reads them through `sudo -n cat`, and a
serial that neither could read is fatal at label time, naming the host and the
command that reads it — the same rule as an FPGA's Device DNA. A firmware that
simply has no serial (`To be filled by O.E.M.`) still gets a label, with the
spine saying so; the MAC identifies the board.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/minnowboard-turbot.png" alt="ADI Engineering MinnowBoard Turbot; the DMI serial is its eth MAC, no radio" width="49%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/minnowboard-max.png" alt="CircuitCo MinnowBoard MAX; the DMI serial is its eth MAC, no radio" width="49%">
</p>

## FPGA boards

The maker and the derived name, the die, and the immutable identifier full width
with a QR: Device DNA where read, the Digilent serial and flash part on an Arty,
The foot names what it is printing, because not every board has a Device DNA
— an ECP5 has none, so a Cynthion is keyed on its configuration flash's uid
and says so.

**A board whose identifier was never read gets no label at all.** It is a
fatal error naming the host, the board, the identifier and the command that
would read it. There is no "not read" on a sticker and no rule to write a DNA
on by hand: this package exists so that nobody transcribes hex, and a label
with a blank on it still gets printed, peeled and stuck to a board. A fact
that is not an identifier and was not read — an Arty's flash part, say — is
simply left off rather than announced.

Nothing a reflash could change appears on any of them. Which gateware a
Cynthion is running settles whether its USB serial may be trusted as the flash
uid, and then stays in the probe document where it belongs.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/netv2.png" alt="NeTV2" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/arty.png" alt="Arty A7-35T" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/acorn.png" alt="An Acorn CLE-215+ named by its die, keyed on its Device DNA" width="32%">
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

## Micro labels, four to a sticker

A module the size of a thumbnail or a mains plug has no room for a whole
63.5 × 38.1 mm sticker, so `rpi_hwid.micro` lays out a label a quarter that
size: 31.75 × 19.05 mm, two across and two down. It prints on the same L7160
stock and in the same grid, with a dotted guide between the four to cut
along. The synthetic records in `docs/examples/render_micro.py` fill every
slot once:

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/micro-4up.png" alt="One sticker cut into four micro labels, synthetic data: a Wi-Fi module with a chip glyph, a USB bridge, a 433 MHz radio node with an extra section, a wired device whose QR opens a page" width="98%">
</p>

It is the board label's layout, shrunk. The maker's mark and a bold title
run across the top, with glyphs at the right: the Wi-Fi arcs, the USB trident
and the RJ45 jack are the whole labels' own. Two more are drawn for the micro
layout: a chip package lettered with its die, and an antenna. The antenna's
mast can be its band: `Icon("antenna", "433")` sets "433" upright in bold,
reading upwards and standing on the foot, with the waves either side of its
top. At the 3.6 mm header that is about 5.1 pt. A band too long to stand
there at 4 pt or more is refused when the label is made, rather than
shrunk. The
primary identifier is a QR on the left and, again, the largest thing on the
label, in monospace along the whole foot. Beside the QR are a subtitle and up
to four captioned rows (the last runs down beside the foot's caption, whose
line is otherwise empty there), and under the rows is room for a section the
caller draws itself.

In place of the subtitle a label may carry a **spec strip**: a row of glyphs
heading the band beside the QR, for what the device *is* rather than which
one it is. The strip's glyphs are registered like the header's:

| glyph | draws |
|---|---|
| `Icon("riscv")` | the RISC-V mark, the "RV" of RISC-V International's own file without its wordmark |
| `Icon("xtensa")` | "Xt", set from the name in the RISC-V mark's box (Cadence publishes no logo for it) |
| `Icon("cores", "2+1")` | a package with two numbers on its die: the application cores large and black, then past a divider the low-power cores small and grey |
| `Icon("memory", "512K+8M")` | a memory module with its size lettered on it |
| `Icon("tasmota")` | the Tasmota symbol, from the Tasmota repository |
| `Icon("bluetooth")` | the Bluetooth rune |
| `Icon("mesh")` | four linked nodes: an IEEE 802.15.4 (Thread, Zigbee) radio |
| `Icon("wifi", "2.4/5 a/b/g/n/ac/ax")` | the Wi-Fi arcs with the band under them, the single-letter 802.11 standards beside them and the two-letter ones (ac, ax) beside the band, bold at 4 pt (a header glyph) |

A strip too wide for the band is refused when the label is made.

The same rules apply as on every other label. A micro label with no
identifier raises when it is constructed, naming the host (and the command
that reads it, when the caller says). A row with no value is refused rather
than printed blank; `BLANK_ROW` keeps a row's place empty on purpose, so
the rows under it stay where they are on other labels. A row with a `size`
is set at that size on every label rather than shrunk to its value's
length, and one whose value does not fit whole at it is refused. A monospace row is taken to be an identifier someone
might type, so it is never elided: a value that will not fit whole at 4 pt
is an error, not an ellipsis. Too many rows is an error too, not a silent
drop.

The layout knows nothing about any particular device. A device module adds
itself by being named `rpi_hwid/<something>_micro.py` and defining a `KIND`
and `micro_labels(docs)`, which returns `MicroLabel`s from the collected
documents. `rpi-hwid labels` then prints them, four to a sticker, after
every whole label, and `--only KIND` selects them. `render_micro` writes a
sheet of nothing but micro labels, and `pack` and `draw_quad` do the same job
in pieces.

```python
from rpi_hwid.micro import Icon, MicroLabel, MicroRow, render_micro

render_micro([MicroLabel(
    host="bench-1", title="Wi-Fi module", subtitle="rev 1.0  ·  4 MiB flash",
    mark=None, icons=(Icon("wifi"), Icon("chip", "C3")),
    ident_caption="Wi-Fi MAC", ident="02:00:5e:10:00:01",
    rows=(MicroRow("BT", "02:00:5e:10:00:03", mono=True),),
)], "micro.pdf", outline=True)
```

## ESP32s

Each ESP32 that `rpi-hwid esp32 --read` has read gets a micro label (`--only
esp32`), and so does an ESP8266EX or ESP8285, the chip in most Tasmota
plugs, which esptool reads the same way. Every label has the same parts in
the same places:

- **Header:** the Espressif mark, then the part number, which is always
  printed whole: `ESP32-D0WD-V3`, or `ESP32-C3FH4`, where esptool names the
  die and the chip's eFuse the flash in its package (see
  [ESPRESSIF.md](ESPRESSIF.md#which-part-a-read-names)). Then the part's
  radios: Wi-Fi with its bands and 802.11 standards (`2.4` and `b/g/n`;
  `b/g/n` and `ax` on a C6; `2.4/5`, `a/b/g/n` and `ac/ax` on a C5),
  Bluetooth, and the 802.15.4 mesh on a C5, C6 or H2.
- **Spec strip:** what every chip of that part is, from the table in
  `rpi_hwid.espressif` ([ESPRESSIF.md](ESPRESSIF.md), every value cited): the
  ISA (the RISC-V mark, or the Xtensa "Xt" in the same box), the cores as
  two numbers (the application cores, then the ULP or LP cores, small and
  grey: `2|1` on an ESP32, `1|0` on a C3, which has none), the on-chip SRAM with any PSRAM in the package (`512K+8M`),
  and the Tasmota symbol where Tasmota ships a binary for the part.
- **Rows,** read from this chip, always in this order:
  - `chip`: the silicon revision. An ESP8266 reports none, so its row's
    place is left blank. The crystal is read and kept in the document, but
    not printed.
  - `flash`: the flash's part where the read settles it (`GD25Q32x`,
    `XM25QH32D`, or `BY25Q32ES` where a Boya's 128-bit unique id tells it
    from the 64-bit BS), else its JEDEC id (`0x464017`, with its vendor
    where that fits); then its size. The row prints at one size, 4.4 pt, on
    every label, whatever the length of the part's name. In the package or beside it, the flash is on this row.
    [research/esp32-flash.md](research/esp32-flash.md) has the reads and the
    datasheets behind each name.
  - `uid`: the flash's own unique id, 64 bits on one row or 128 over two.
    Where the flash gives none, `eFuse` and the chip's 128-bit
    `OPTIONAL_UNIQUE_ID` over two rows stand in its place. A chip with
    neither has no uid rows. An ESP32-C3 has both; which one is printed is
    `esp32_micro.SERIALS`, one line. The uid rows print at one size too,
    4.3 pt, beside either caption.
- **Foot and QR:** the base MAC, burned into eFuse: the Wi-Fi station MAC,
  or on an H2, which has no Wi-Fi, the MAC.

A fact that does not apply leaves its place empty rather than moving another
into it. One serial is printed beyond the MAC, not two: two 128-bit serials do
not fit a quarter sticker, and since the MAC already identifies the chip's die,
the flash's id, which names a second part, comes first. Both stay in the
collected document. Nothing on the label is derived; the Bluetooth MAC, which
ESP-IDF derives as base+2, is not printed.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/esp32-sticker-1.png" alt="Four ESP32 micro labels from real reads: an ESP32-CAM (ESP32-D0WD-V3) and a devkit (ESP32-D0WDQ6), Xtensa, over two ESP32-C3FH4 SuperMinis, RISC-V" width="98%">
</p>

These are real reads, from `tests/esp32_devices.json`: an ESP32-CAM and a
devkit on rpi4-esp, and two of the three C3 radio nodes on rpi5-433mhz. The
C3s' in-package XMC flash gives a 128-bit unique id, so their uid rows carry
it; the chip's eFuse id is in the document beside it.
`docs/examples/render_esp32.py` regenerates them,
and a sheet of synthetic samples, one for every part in the table:

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/esp32-parts.png" alt="Sample ESP32 micro labels with synthetic data, one for every part in rpi_hwid.espressif" width="98%">
</p>

What is *not* read is refused, as on every other label:

- An ESP32 known only from the USB tree (a MAC and nothing else) is an error.
- So is a chip whose eFuse holds a unique id that was not read.

Either error names the host, the MAC and both ways to read it: `rpi-hwid esp32
--read PORT` on the host, or `rpi-hwid collect --esp32-read HOST=PORT`. Both
reset the chip. A chip the table has no row for is an error too
(`espressif.UnknownPartError`, naming the host, the MAC and the chip). The
chip model is not an identifier, but a label without its spec strip would
break the layout every other ESP32 label keeps, and the fix is one row in the
table with its source.

For a label that builds on this one (an ESP32 with a 433 MHz radio, say),
`esp32_micro.esp32_label(host, device)` returns the plain `MicroLabel` for
`dataclasses.replace` to add to.

## Artwork

The package ships the Raspberry Pi raspberry, the Orange Pi orange, the Alphamax,
Digilent, SQRL, Great Scott Gadgets, SiFive, Tiny Tapeout and Espressif marks, the RISC-V logo and
its "RV" mark alone, and the Tasmota symbol (each its owner's mark, drawn only where it applies: on its
maker's own hardware, on a RISC-V part, on a device Tasmota runs on) and the public-domain USB trident; see
[`src/rpi_hwid/artwork/README.md`](../src/rpi_hwid/artwork/README.md) for the
sources. A `--artwork DIR` overrides any of them and may add a `netv2.svg`. A
board whose maker has no mark gets the name in type.
