# rpi-hwid — Raspberry Pi hardware identity

[![PyPI](https://img.shields.io/pypi/v/rpi-hwid)](https://pypi.org/project/rpi-hwid/)
[![CI](https://github.com/mithro/rpi-hwid/actions/workflows/ci.yml/badge.svg)](https://github.com/mithro/rpi-hwid/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

What is this Raspberry Pi wearing, what powers it, and what is soldered to it?
`rpi-hwid` answers from the Pi itself, out of evidence the firmware and kernel
already expose but nothing collects: HAT ID EEPROMs (including the ones the
firmware never reads), the Pi 5's own verdict on its USB-C supply, the PMIC's
input and RTC-cell voltages, the fan header, the USB tree, and which network
interfaces are soldered down.

Around that probe: separate modules for an FPGA board (NeTV2, Acorn, Arty) or a
[Tiny Tapeout](https://tinytapeout.com/) demo board attached to the Pi; a
collector that runs the lot over ssh across a fleet, one JSON document per host;
and a label generator that turns those documents into sticker sheets carrying
only what cannot change — serial numbers, MAC addresses, Device DNA. The same
probe runs unchanged on an Orange Pi PC.

## Install

```sh
uv tool install 'rpi-hwid[labels]'     # everything, including the label generator
pip install rpi-hwid                   # probe, collector, names: no dependencies at all
```

Or as a Debian package on Raspberry Pi OS or Debian bookworm, trixie or sid, from
the signed apt repository at https://mith.ro/rpi-hwid/ (the page has the
three-line setup for each suite):

```sh
sudo apt install python3-rpi-hwid      # provides the rpi-hwid command
```

Nothing needs installing on the Pi being probed. The probe is one dependency-free
file that runs on any `python3` 3.5 or later, so it can be sent over ssh on stdin:

```sh
ssh pi@host 'python3 -' < src/rpi_hwid/probe.py
ssh pi@host 'python3 - --json' < src/rpi_hwid/probe.py
```

On the Pi it wants `i2c-tools` and passwordless `sudo` (for `i2cdetect`,
`i2ctransfer`, `dtparam` and `vcgencmd`). Without them it still reports what it
can.

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

## The labels

`rpi-hwid labels --data DIR --out labels.pdf` lays out 63.5 × 38.1 mm labels, 21
to an A4 sheet (the Avery L7160 grid), from a directory of collected documents.
Every label carries only what cannot change, and every identifier that might
otherwise be typed is also a QR code. Cropped from a rendered sheet:

<table>
<tr>
<td width="33%"><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi5.png" alt="Pi 5, bare header" width="100%"><br>Pi 5, bare header</td>
<td width="33%"><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi5-poe-hat.png" alt="Pi 5 wearing a Waveshare PoE M.2 HAT+ (B); its radio is disabled so the wlan MAC cannot be read" width="100%"><br>Pi 5 with a Waveshare PoE M.2 HAT+ (B); the radio is disabled, so no wlan MAC</td>
<td width="33%"><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi4-pmod-hat.png" alt="Pi 4 with a Digilent Pmod HAT Adaptor" width="100%"><br>Pi 4 with a Digilent Pmod HAT Adaptor</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi3bplus.png" alt="Pi 3B+; the wlan MAC is derived from the eth MAC" width="100%"><br>Pi 3B+; the wlan MAC is derived from the eth MAC</td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/rpi-zero-w-bonnet.png" alt="Pi Zero W with the Waveshare PoE-ETH-USB-HUB-HAT; the bonnet's RTL8152 is its eth MAC" width="100%"><br>Pi Zero W with the Waveshare PoE-ETH-USB-HUB-HAT; the bonnet's RTL8152 is its eth MAC</td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/orange-pi-pc.png" alt="Orange Pi PC; eth MAC derived by U-Boot from the SoC serial, no radio" width="100%"><br>Orange Pi PC; eth MAC derived by U-Boot from the SoC serial, no radio</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/netv2.png" alt="NeTV2" width="100%"><br>NeTV2, carrying the Device DNA and the name derived from it</td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/arty.png" alt="Arty A7-35T" width="100%"><br>Arty A7-35T, with its Digilent serial and flash part</td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/acorn.png" alt="Acorn CLE-215+, DNA not yet read" width="100%"><br>Acorn CLE-215+, with a line to write the DNA on: it has not been read yet</td>
</tr>
<tr>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout.png" alt="TT06 chip on a TT06+ demo board" width="100%"><br>TT06 chip on a TT06+ demo board</td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/tinytapeout-ihp.png" alt="TTIHP25a chip on a DBv3 demo board; no colours recorded for that shuttle yet" width="100%"><br>TTIHP25a on a DBv3 demo board; no colours recorded for that shuttle yet</td>
<td><img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-asix.png" alt="ASIX AX88179 USB 3.0 gigabit adapter" width="100%"><br>ASIX AX88179 USB 3.0 gigabit adapter</td>
</tr>
</table>

Print at 100 % — "fit to page" shrinks the grid and every label lands off its
sticker. The layouts band by band, the other options, the derived board names and
the artwork are in [docs/LABELS.md](docs/LABELS.md).

## The commands

```
rpi-hwid probe [--json] [--fpga] [--jtag] [--flash] [--tinytapeout]
                                                      on a Pi: what is this?
rpi-hwid fpga [--json] [--jtag] [--flash]             on a Pi: which FPGA board?
rpi-hwid tinytapeout [--json] [--no-repl]             on a Pi: which Tiny Tapeout board?
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
