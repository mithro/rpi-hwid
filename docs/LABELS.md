# Labels

The derived names that go on them, the five layouts, and where the artwork comes
from. For the gallery and the command itself, see the [README](../README.md); for
the data they are made from, see [COLLECT.md](COLLECT.md).

## Names

Raw identifiers come in near-identical clusters — Device DNAs sharing most of
their digits, Digilent serials differing in the last byte — so `rpi-hwid name`
hashes them into short, distinct words:

```
$ rpi-hwid name --netv2 0x00742c4e63b9085c --arty 210319B301DE 210319B0C238
netv2-grove  00742c4e63b9085c
arty-hawk  210319B301DE
arty-serin  210319B0C238
```

A NeTV2's name is a pure function of its DNA. Arty names are a hash *chain*
resolved against a registry, so two serials never share a word and adding a board
never renames an old one. Keep the registry as a JSON object of serial to name and
pass it with `--names registry.json` to both `name` and `labels`.

## Printing a sheet

`rpi-hwid labels` lays out 63.5 × 38.1 mm labels, 21 to an A4 sheet (the Avery
L7160 grid), from a directory of collected documents. Print at 100 % — "fit to
page" shrinks the grid and every label lands off its sticker.

```
$ rpi-hwid labels --data data/ --list
sheet 1 row 1 col 1  arty   arty-hawk
sheet 1 row 1 col 2  acorn  Acorn CLE-215+
sheet 1 row 1 col 3  netv2  netv2-grove
sheet 1 row 2 col 1  tt     TT06 E6614C311B7A7A37
sheet 1 row 2 col 2  tt     TTIHP25a E66360B8A3C1D5F2
sheet 1 row 2 col 3  rpi    Pi 3 Model B+ 1 GB 000000004fe3e7e4
sheet 1 row 3 col 1  rpi    Pi 4 Model B 2 GB 10000000ce8e3593
sheet 1 row 3 col 2  opi    Orange Pi PC 1 GB 02c000812eb7a34e
sheet 1 row 3 col 3  rpi    Pi 5 1 GB c36b093f773d46b8
sheet 1 row 4 col 1  rpi    Pi 4 Model B 4 GB 100000003a7e1c9b
sheet 1 row 4 col 2  rpi    Pi 5 4 GB d88100008543dc30
sheet 1 row 4 col 3  rpi    Pi Zero W 512 MB 000000005157f671
sheet 1 row 5 col 1  usb    ASIX Elec. Corp. AX88179 00:0e:c6:82:b5:e1
$ rpi-hwid labels --data data/ --out labels.pdf
13 labels on 1 sheet -> labels.pdf
```

`--outline` draws the die-cut edges for an alignment print on plain paper;
`--start N` skips N positions on the first sheet so a partly used sheet can be
finished; `--only rpi|opi|fpga|tt|usb` limits the kinds; `--list` prints what
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

## Orange Pi

The same layout, band for band, with the Orange Pi orange in the raspberry's box.
Title and subtitle come from the device tree instead of a revision code: model,
fitted RAM, SoC, and the device-tree id `dt orangepi-pc`, the board's canonical id
since Xunlong sells it by name with no part number. The HAT row is kept but reads
`header  40-pin` — nothing to probe, and the distribution is left off because
it changes — and the wlan row says `no radio` on a PC or One. The SoC serial runs
up the spine as on a Pi.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/orange-pi-pc.png" alt="Orange Pi PC; eth MAC derived by U-Boot from the SoC serial, no radio" width="49%">
</p>

## FPGA boards

The maker and the derived name, the die, and the immutable identifier full width
with a QR: Device DNA where read, the Digilent serial and flash part on an Arty,
and a line to write the DNA on when it has not been read yet.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/netv2.png" alt="NeTV2" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/arty.png" alt="Arty A7-35T" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/acorn.png" alt="Acorn CLE-215+, DNA not yet read" width="32%">
</p>

## Tiny Tapeout boards

The shuttle is the headline, with ASIC or FPGA breakout and the PDK under it; then
the demo board as the SDK detected it with the revision that shipped in that kit,
and the chip ROM's commit. Then four colour boxes, so the right board is picked
out of a drawer: for the chip carrier and for the demo board, a wide box in the
board's soldermask and a narrow one in the silkscreen printed on it, in that
proportion so no caption is needed to tell them apart, with both colours named
beside. A TT05 kit, say, is a yellow carrier lettered in black on a black demo
board lettered in white. A box is left empty and struck through where the colour
is not recorded. The large QR opens the chip's page on tinytapeout.com; the demo
board's RP2 unique id — its USB serial — runs along the foot with a small QR of
its own.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout.png" alt="TT06 chip on a TT06+ demo board" width="49%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout-ihp.png" alt="TTIHP25a chip on a DBv3 demo board; no colours recorded for that shuttle yet" width="49%">
</p>

## USB network adapters

The descriptors (USB version and speed, driver, VID:PID) beside the MAC's QR, and
the MAC itself full width along the foot, so a dongle can be matched to a DHCP
lease from across the room.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-asix.png" alt="ASIX AX88179 USB 3.0 gigabit adapter" width="49%">
</p>

## Artwork

The package ships the Raspberry Pi raspberry, the Orange Pi orange, the Alphamax,
Digilent and Tiny Tapeout marks (each its owner's trademark, drawn only on that
maker's own hardware to identify it) and the public-domain USB trident; see
[`src/rpi_hwid/artwork/README.md`](../src/rpi_hwid/artwork/README.md) for the
sources. A `--artwork DIR` overrides any of them and may add a `netv2.svg`. A
board whose maker has no mark (SQRL) gets the name in type.
