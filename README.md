# rpi-hwid — Raspberry Pi hardware identity

[![PyPI](https://img.shields.io/pypi/v/rpi-hwid)](https://pypi.org/project/rpi-hwid/)
[![Debian packages](https://github.com/mithro/rpi-hwid/actions/workflows/deb.yml/badge.svg)](https://github.com/mithro/rpi-hwid/actions/workflows/deb.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

What is this Raspberry Pi wearing, what powers it, and what is soldered to it?
`rpi-hwid` answers from the Pi itself, out of evidence the firmware and kernel
already expose but nothing collects: HAT ID EEPROMs (including the ones the
firmware never reads), the Pi 5's own verdict on its USB-C supply, the PMIC's
input and RTC-cell voltages, the fan header, the USB tree, and which network
interfaces are soldered down.

Around that probe: separate modules for an FPGA board (NeTV2, Acorn, Arty,
Cynthion) or a [Tiny Tapeout](https://tinytapeout.com/) demo board attached to
the Pi; a collector that runs the lot over ssh across a fleet, one JSON document
per host; and a label generator that turns those documents into sticker sheets
carrying only what cannot change — serial numbers, MAC addresses, Device DNA,
an ECP5's TraceID. The same probe runs unchanged on an Orange Pi PC.

## The labels

`rpi-hwid labels --data DIR --out labels.pdf` lays out 63.5 × 38.1 mm labels, 21
to an A4 sheet (the Avery L7160 grid), from a directory of collected documents.
Every label carries only what cannot change, and every identifier that might
otherwise be typed is also a QR code. Cropped from a rendered sheet:

<!-- Three columns, each image at a fixed pixel width, td widths on the first
     row, and the captions hard-wrapped with <br>. All four are load bearing,
     because the two renderers keep opposite halves and size the images by
     different rules:
       GitHub keeps the td widths, which make the columns exactly equal, and
       caps every image at its cell (.markdown-body img { max-width: 100% }).
       PyPI strips the td widths (readme_renderer.clean allows only
       colspan/rowspan/align on td, and no attributes at all on col), so there
       nothing can equalise the columns and the caption text sizes them. A
       column is as wide as its widest cell, so a caption in its own row still
       votes for its column, and CSS auto layout hands out the surplus in
       proportion to each column's max-content -- the caption unwrapped onto
       one line. Measured on the live page, that pulled one column to 298px
       and starved its neighbours' images to 217px while others held 250px.
       The <br>s cap max-content at the longest line instead of the whole
       sentence, which is what makes three columns possible at all.
       Hence the rule when editing a caption: NO LINE MAY BE WIDER THAN THE
       IMAGE. Once every line fits inside 180px all three columns want
       exactly the image's width, ask for the same thing, and split the
       surplus evenly. Count pixels, not characters: "Pi Zero W with the
       Waveshare" is 28 characters but 196px, and on its own it dragged its
       column to 298px and its neighbours' images down to 217px.
       Check the width in GitHub's font, not PyPI's -- GitHub sets 16px in a
       188px cell against PyPI's ~14px, so it is the tighter of the two and a
       line that fits there fits both. Wrapping for PyPI alone left GitHub
       soft-wrapping the hard lines a second time and orphaning words.
       PoE-ETH-USB-HUB-HAT is split at a hyphen because no wrap can shorten a
       single word: below a 780px column the images stop fitting and each
       column's floor becomes its caption's longest unbreakable word, which
       no amount of <br> changes. The last defence is an image narrow enough
       to sit inside the narrowest column: at 180px all twelve stay identical
       down to a 600px column, the range the old two-column 250px gallery
       held, and the residue below that is far smaller (0.2% at 580px, 0.8%
       at 560px, against the old layout's 1.2%). PyPI's column is 780px at a
       window of 1150px or wider and narrows with it below (654px at 1024px).
     The crops are all 500 x 300, so a single width covers them all.
     The captions sit in their own row under each image rather than in the
     cell with it, so a long one cannot push its image's row taller than the
     ones beside it.
     A row per kind, three to a row: the boards the probe reads, then the
     FPGA boards, then the Tiny Tapeout boards, then the USB adapters. -->
<table>
<tr>
<td width="33%"><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi5-poe-hat.png" alt="Pi 5 wearing a Waveshare PoE M.2 HAT+ (B); its radio is disabled so the wlan MAC cannot be read" width="180"></td>
<td width="33%"><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi-zero-w-bonnet.png" alt="Pi Zero W with the Waveshare PoE-ETH-USB-HUB-HAT; the bonnet's RTL8152 is its eth MAC" width="180"></td>
<td width="33%"><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/orange-pi-pc.png" alt="Orange Pi PC; eth MAC derived by U-Boot from the SoC serial, no radio" width="180"></td>
</tr>
<tr>
<td>Pi 5 with a Waveshare<br>PoE M.2 HAT+ (B);<br>the radio is disabled,<br>so no wlan MAC</td>
<td>Pi Zero W with the<br>Waveshare PoE-<br>ETH-USB-HUB-HAT;<br>the bonnet's RTL8152<br>is its eth MAC</td>
<td>Orange Pi PC; eth MAC<br>derived by U-Boot from<br>the SoC serial, no radio</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/netv2.png" alt="NeTV2" width="180"></td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/arty.png" alt="Arty A7-35T" width="180"></td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/acorn.png" alt="An Acorn CLE-215+ running gateware that hides its PCIe id, so it is named by its die and keyed on its Device DNA" width="180"></td>
</tr>
<tr>
<td>NeTV2, carrying the<br>Device DNA and the<br>name derived from it</td>
<td>Arty A7-35T, with<br>its Digilent serial and<br>its flash's unique id</td>
<td>An Acorn CLE-215+<br>under gateware that<br>hides its PCIe id:<br>named by its die</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/cynthion.png" alt="Cynthion r1.4; an ECP5 board, keyed on the TraceID burned into its die, with its configuration flash's UID among the flash facts" width="180"></td>
<td></td>
<td></td>
</tr>
<tr>
<td>Cynthion r1.4, an<br>ECP5 board, keyed on<br>the die's TraceID; its<br>flash UID sits below</td>
<td></td>
<td></td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout.png" alt="TT06 chip on a TT06+ demo board" width="180"></td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout-gf.png" alt="TTGF0p2 chip on a DBv3 demo board" width="180"></td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout-fpga.png" alt="An FPGA breakout standing in for the ASIC on a DBv3 demo board" width="180"></td>
</tr>
<tr>
<td>TT06 chip on a<br>TT06+ demo board</td>
<td>TTGF0p2 on a DBv3 board;<br>wafer.space ran it on<br>GlobalFoundries</td>
<td>An FPGA breakout<br>standing in for the ASIC,<br>on the same DBv3 board</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-asix.png" alt="ASIX AX88179 USB 3.0 gigabit adapter" width="180"></td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-wifi.png" alt="Realtek 802.11ac USB WiFi adapter" width="180"></td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-linksys.png" alt="Linksys USB3GIGV1 USB 3.0 gigabit adapter" width="180"></td>
</tr>
<tr>
<td>ASIX AX88179 USB<br>3.0 gigabit adapter</td>
<td>Realtek 802.11ac<br>USB WiFi adapter</td>
<td>Linksys USB3GIGV1<br>USB 3.0 gigabit adapter</td>
</tr>
</table>

Print at 100 % — "fit to page" shrinks the grid and every label lands off its
sticker. The layouts band by band, the other options, the derived board names and
the artwork are in [docs/LABELS.md](docs/LABELS.md).

## Install

```sh
uv tool install 'rpi-hwid[labels]'     # everything, including the label generator
pip install rpi-hwid                   # probe, collector, names: no dependencies at all
```

Or as a Debian package on Raspberry Pi OS or Debian, from the signed apt
repository at https://mith.ro/rpi-hwid/. There is one per suite (bookworm,
trixie, forky and sid), and the package is `Architecture: all`, so it installs
on any Raspberry Pi. Put your suite's name in place of `trixie` below
(Raspberry Pi OS uses Debian's codenames):

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://mith.ro/rpi-hwid/rpi-hwid.gpg | sudo tee /etc/apt/keyrings/rpi-hwid.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/rpi-hwid.gpg] https://mith.ro/rpi-hwid/trixie/ ./" \
  | sudo tee /etc/apt/sources.list.d/rpi-hwid.list
sudo apt update
sudo apt install python3-rpi-hwid      # provides the rpi-hwid command
```

The repository's signing key is
`9C51 CAE0 CF1C 4C08 A63C  8A6A 2599 D5E0 285B 902F`
(`gpg --show-keys /etc/apt/keyrings/rpi-hwid.gpg` shows it).

Nothing needs installing on the Pi being probed. The probe is one dependency-free
file that runs on any `python3` 3.5 or later, so it can be sent over ssh on stdin:

```sh
ssh pi@host 'python3 -' < src/rpi_hwid/probe.py
ssh pi@host 'python3 - --json' < src/rpi_hwid/probe.py
```

On the Pi it wants passwordless `sudo` for the firmware tools (`dtparam` and
`vcgencmd`). The 40-pin header is read straight from `/dev/i2c-*`, so it needs
neither `sudo` nor `i2c-tools` — only membership of the `i2c` group. Without any
of it the probe still reports what it can.

## Quick start

A Pi 5 powered through a PoE splitter, with a USB Ethernet adapter:

```
$ rpi-hwid probe
Raspberry Pi 5 Model B Rev 1.0  serial d88100008543dc30  rev c04170
  header : nothing identifiable on the header
  signal : USB-C as the firmware sees it: max_current 900 mA, no PD contract; 5 V input 4.83 V
  signal : fan header: disabled
  signal : RTC battery: none (0.00 V)
  signal : power port: throttled=0x0
  power  : external supply on USB-C advertising 900 mA by resistor: a PoE splitter or a USB-A lead
  onboard: eth    2c:cf:67:16:bd:98  macb
  onboard: wlan   2c:cf:67:16:bd:99  brcmfmac
  usb net: 0b95:1790 ASIX Elec. Corp. AX88179  00:0e:c6:82:b5:e1  ethernet
```

Revision codes decode offline, on any machine:

```
$ rpi-hwid revision c04170 9000c1 a020d3
c04170: Raspberry Pi 5, 4 GB, Rev 1.0, BCM2712
9000c1: Raspberry Pi Zero W, 512 MB, Rev 1.1, BCM2835
a020d3: Raspberry Pi 3 Model B+, 1 GB, Rev 1.3, BCM2837
```

## The commands

```
rpi-hwid probe [--json] [--fpga] [--jtag] [--flash] [--tinytapeout]
                                                      on a Pi: what is this?
rpi-hwid fpga [--json] [--jtag] [--flash]             on a Pi: which FPGA board?
rpi-hwid tinytapeout [--json] [--no-repl]             on a Pi: which Tiny Tapeout board?
rpi-hwid esp32 [--json] [--read PORT…] [--radio PORT…]
                                                      on a Pi: which ESP32s are on its USB?
rpi-hwid collect --out DIR [-J JUMP] [--fpga] [--tinytapeout] HOST…
                                                      over ssh: one JSON per host
rpi-hwid labels --data DIR --out labels.pdf           print-ready labels from that data
rpi-hwid name --netv2 DNA… | --arty SERIAL…           the derived board names
rpi-hwid revision CODE…                               decode Pi revision codes
```

## Reference

- [docs/PROBE.md](docs/PROBE.md) — what each signal proves, how the power verdict
  is reached, the Orange Pi, and the FPGA and Tiny Tapeout modules.
- [docs/COLLECT.md](docs/COLLECT.md) — collecting a fleet over ssh, the JSON
  document the rest of the package consumes, and reading it from Python.
- [docs/LABELS.md](docs/LABELS.md) — the derived names, the five label layouts,
  the label options and the artwork.
- [docs/DEVELOPING.md](docs/DEVELOPING.md) — contributor notes.
- [RELEASING.md](RELEASING.md) — how a version reaches PyPI and apt.

## Development

```sh
uv sync --all-extras --group dev
uv run ruff check && uv run mypy && uv run pytest
```

The rest — the Python 3.5 rule for the probe files, what the tests need installed,
regenerating the images, building the Debian package — is in
[docs/DEVELOPING.md](docs/DEVELOPING.md), and how a version reaches PyPI and apt
is in [RELEASING.md](RELEASING.md).

## Origin

Worked out on a fleet of Pi Zero W, 3B+, 4 and 5 hosts carrying NeTV2, Acorn and
Arty boards, powered by a mix of Waveshare PoE HATs and external PoE splitters,
plus Orange Pi PCs on the welland.fpgas.online pool, in September 2026. The rules
above are what those boards showed; a board that behaves differently is a bug
report. The Tiny Tapeout module was written from the SDK's public sources
(tt-micropython-firmware, tt-demo-pcb, tt-support-tools) and tested against an
emulated board; a report from a real demo board is welcome.
