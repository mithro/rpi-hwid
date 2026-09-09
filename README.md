# rpi-hwid — Raspberry Pi hardware identity

[![PyPI](https://img.shields.io/pypi/v/rpi-hwid)](https://pypi.org/project/rpi-hwid/)
[![CI](https://github.com/mithro/rpi-hwid/actions/workflows/ci.yml/badge.svg)](https://github.com/mithro/rpi-hwid/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

What is this Raspberry Pi wearing, what powers it, and what is soldered to
it? `rpi-hwid` answers from the Pi itself, using evidence the firmware and
kernel already expose but nothing collects: HAT ID EEPROMs (including the
ones the firmware never reads), the Pi 5's own verdict on its USB-C supply,
the PMIC's input and RTC-cell voltages, the fan header, the USB tree, and
which network interfaces are soldered down.

Around that probe:

- a separate **FPGA probe** for a NeTV2, Acorn or Arty board attached to the Pi,
- a **collector** that runs both over ssh across a fleet, one JSON document per host,
- **names** derived from each board's immutable identifier,
- a **label generator** that turns the documents into sticker sheets carrying
  only what cannot change: serial numbers, MAC addresses, Device DNA.

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [The probe: what each signal proves](#the-probe-what-each-signal-proves)
- [FPGA boards](#fpga-boards)
- [Collecting a fleet](#collecting-a-fleet)
- [The document](#the-document)
- [Names](#names)
- [Labels](#labels)
- [Using it from Python](#using-it-from-python)
- [Development](#development)

## Install

```
uv tool install 'rpi-hwid[labels]'     # everything, including the label generator
pip install rpi-hwid                   # probe, collector, names: no dependencies at all
```

Or as a Debian package, on Raspberry Pi OS or Debian bookworm, trixie or
sid, from the signed apt repository at https://mith.ro/rpi-hwid/ (the page
has the three-line setup for each suite):

```
sudo apt install python3-rpi-hwid      # provides the rpi-hwid command
```

The probe itself is one dependency-free file that runs on any Pi with
`python3` 3.5 or later, so nothing needs installing on the Pi. Send it over
ssh with the source on stdin:

```
ssh pi@host 'python3 -' < src/rpi_hwid/probe.py
ssh pi@host 'python3 - --json' < src/rpi_hwid/probe.py
```

On the Pi it wants `i2c-tools` and passwordless `sudo` (for `i2cdetect`,
`i2ctransfer`, `dtparam` and `vcgencmd`). Without them it still reports
what it can.

## Quick start

On a Pi 5 powered through a PoE splitter, with a USB Ethernet adapter:

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

On a Pi Zero W wearing Waveshare's PoE-ETH-USB-HUB-HAT bonnet, which has no
ID EEPROM and is recognised from the USB tree instead:

```
$ rpi-hwid probe
Raspberry Pi Zero W Rev 1.1  serial 000000005157f671  rev 9000c1
  header : Waveshare PoE-ETH-USB-HUB-HAT (1a40:0101 hub with RTL8152 on port 4)
  signal : power port: throttled=0x0
  power  : PoE through the Waveshare PoE-ETH-USB-HUB-HAT bonnet
  onboard: wlan   b8:27:eb:02:a3:24  brcmfmac
  usb net: 0bda:8152 Realtek USB 10/100 LAN  00:e0:4c:36:0b:0a  ethernet
```

The whole command line:

```
rpi-hwid probe [--json] [--fpga] [--jtag] [--flash]   on a Pi: what is this?
rpi-hwid fpga [--json] [--jtag] [--flash]             on a Pi: which FPGA board?
rpi-hwid collect --out DIR [-J JUMP] [--fpga] HOST…  over ssh: one JSON per host
rpi-hwid labels --data DIR --out labels.pdf           print-ready labels from that data
rpi-hwid name --netv2 DNA… | --arty SERIAL…           the derived board names
rpi-hwid revision CODE…                               decode Pi revision codes
```

```
$ rpi-hwid revision c04170 9000c1 a020d3
c04170: Raspberry Pi 5, 4 GB, Rev 1.0, BCM2712
9000c1: Raspberry Pi Zero W, 512 MB, Rev 1.1, BCM2835
a020d3: Raspberry Pi 3 Model B+, 1 GB, Rev 1.3, BCM2837
```

## The probe: what each signal proves

| signal | tells |
|---|---|
| HAT ID EEPROM at `0x50` | what the firmware read: `/proc/device-tree/hat` (official PoE HATs, Digilent Pmod HAT Adaptor, Google VoiceBonnet…) |
| HAT ID EEPROM at `0x51`–`0x57`, read off the ID bus | boards the firmware **never reads**: Waveshare's PoE M.2 HAT+ (B) puts a well-formed HAT+ EEPROM at `0x52` (product string, pid `0x6d87`, a DT atom naming `pciex1`) |
| devices on I2C bus 1 | Waveshare PoE HAT (B): SSD1306 at `0x3c` and PCF8574 at `0x20` |
| USB tree | Waveshare PoE-ETH-USB-HUB-HAT on a Zero: a Terminus `1a40:0101` hub on the root port with an RTL8152 on its port 4. That RTL8152 is reported as the Zero's wired port, not as a removable adapter |
| Pi 5 `max_current` | the firmware's USB-C verdict: 5000 after a PD contract, **3000 both for a 3 A resistor source and for no USB-C source at all** (a HAT on the GPIO 5 V pins), 1500 or 900 for a resistor source advertising that much, so 900/1500 proves an external USB-C supply |
| Pi 5 PMIC ADC | 5 V input (GPIO-fed HATs 5.1–5.4 V, splitters 4.8–5.0 V) and the RTC cell (about 3 V fitted, under 0.01 V not) |
| Pi 5 `cooling_fan` node | a fan on the Pi's own header |
| interface drivers | soldered-down (SoC Ethernet, SDIO radio, the 3B+'s LAN7800) versus removable USB adapters, which are listed with their descriptors |
| throttle flags | under-voltage now or since boot: all a 3B+, Zero or Pi 4 can say about its supply |

The verdict names the power source where the evidence allows, and says so
where it does not:

| `power_class` | meaning |
|---|---|
| `gpio-poe-hat` | a PoE HAT feeding the GPIO 5 V pins, identified by its EEPROM or I2C devices |
| `bonnet-poe` | the Waveshare PoE-ETH-USB-HUB-HAT bonnet |
| `usbc-supply` | an external supply on USB-C advertising 900 or 1500 mA: a PoE splitter or a USB-A lead |
| `usbc-pd-supply` | a USB-C supply with a PD contract |
| `ambiguous` | two sources read identically: an EEPROM-less GPIO PoE HAT on a Pi 5 and a 3 A USB-C splitter, or an EEPROM-less, I2C-less HAT on a 3B+ and any splitter |
| `undetermined` | nothing on the Pi distinguishes the source |

The switch-side 802.3af class narrows the ambiguous cases (the bonnet is
class 3, the M.2 HAT+ (B) class 4, an af-only HAT is never class 4), but
that is read from the switch, not the Pi, so it is outside this package.

## FPGA boards

`rpi_hwid.fpga` is kept apart from the Pi probe because few people have an
FPGA board on their Pi. `rpi-hwid probe --fpga` appends it; `rpi-hwid fpga`
runs it alone.

From what the Pi sees without touching the FPGA: a NeTV2 running LitePCIe
is PCIe `10ee:7024` with one 1 MiB BAR; an SQRL Acorn CLE-215+ is
`1e24:021f` (or `10ee:7011` under other gateware) with 128 KiB + 64 KiB
BARs; a Digilent Arty is its own FT2232 with a `210319…` serial. BAR sizes
come from sysfs and a BAR is never mapped, because that wedges a host.

With `--jtag`, openFPGALoader reads the IDCODE and Device DNA over the
Arty's FT2232 or the host's GPIO harness (libgpiod, pins 27:22:4:17). A
GPIO chain that answers on a host with no Arty is taken to be a NeTV2.
With `--flash`, an Arty's SPI flash is identified by JEDEC id, which
reloads the FPGA with openFPGALoader's bridge bitstream. The S25FL128S and
S25FL127S both answer `0x012018`, so the label says `S25FL128S/127S`.

```
$ rpi-hwid fpga --jtag          # a Pi 4 with an Arty A7-35T on USB
  fpga   : arty (Digilent FT2232 210319B301DE; FT2232 JTAG idcode 0x362d093 artix a7 35t), DNA 0x00628502251ea85c
$ rpi-hwid fpga                 # a Pi 5 with an Acorn on its PCIe connector
  fpga   : acorn (PCIe 1e24:021f, 128 KiB + 64 KiB BARs (SQRL Acorn CLE-215+))
```

## Collecting a fleet

`rpi-hwid collect` pushes the probe source to each host over ssh (nothing
is installed on the Pi), in parallel, and writes `<host>.json` per host.
The FPGA module is appended with `--fpga` for every host, or with
`--jtag HOST` and `--flash HOST` for the hosts that should drive JTAG. A
login banner before the JSON is skipped.

```
$ rpi-hwid collect --out data/ -J jump.example.org --fpga --jtag pi@10.21.2.16 \
      rpi5-netv2 rpiz-serial pi@10.21.2.16 pi@10.21.2.47
  rpi5-netv2: Raspberry Pi 5 Model B Rev 1.0; header bare; power usbc-supply
  rpiz-serial: Raspberry Pi Zero W Rev 1.1; header ['Waveshare PoE-ETH-USB-HUB-HAT']; power bonnet-poe
  pi@10.21.2.16: Raspberry Pi 4 Model B Rev 1.5; header ['Pmod HAT Adaptor']; power undetermined; fpga 0x00628502251ea85c
  pi@10.21.2.47: Raspberry Pi 5 Model B Rev 1.1; header ['Waveshare PoE M.2 HAT+ (B)']; power gpio-poe-hat; fpga acorn
4 of 4 host(s) written to data
```

`--users` lists the login names to try in order (default: you, then
`pi`); the one that worked is recorded in the document. A host that
cannot be reached is reported and skipped, and the exit status says so.

## The document

`rpi-hwid probe --json`, and every file the collector writes, is one JSON
object: the raw evidence as it came off the Pi, plus a `verdict` block
with a fixed-shape `summary` that everything else in the package consumes.

```json
{
  "model": "Raspberry Pi Zero W Rev 1.1",
  "serial": "000000005157f671",
  "revision": "9000c1",
  "hat_fw": null,
  "hat_eeproms": {},
  "i2c1": [],
  "usb": {"1-1": "1a40:0101", "1-1.4": "0bda:8152"},
  "interfaces": ["…"],
  "usb_net": ["…"],
  "throttled": "0x0",
  "verdict": {
    "header": ["Waveshare PoE-ETH-USB-HUB-HAT (1a40:0101 hub with RTL8152 on port 4)"],
    "power": "PoE through the Waveshare PoE-ETH-USB-HUB-HAT bonnet",
    "evidence": ["power port: throttled=0x0"],
    "summary": {
      "model": "Raspberry Pi Zero W Rev 1.1",
      "serial": "000000005157f671",
      "revision": "9000c1",
      "header": ["Waveshare PoE-ETH-USB-HUB-HAT"],
      "hat_uuid": null,
      "power_class": "bonnet-poe",
      "macs": [{"kind": "eth", "mac": "00:e0:4c:36:0b:0a"},
               {"kind": "wlan", "mac": "b8:27:eb:02:a3:24"}],
      "usb_net": [],
      "rtc_battery": null, "fan": null, "max_current_ma": null, "ext5v_v": null,
      "fpga": []
    }
  }
}
```

(Lists shortened.) The `summary` is the contract. `header` is what sits on the 40-pin header,
`macs` the soldered-down interfaces (`eth` first), `usb_net` the removable
adapters with their descriptors, `fpga` the boards the FPGA module found,
and `hat_uuid` the EEPROM's UUID when one was read. The Pi 5-only fields
are `null` elsewhere. Everything outside `verdict` is evidence, kept so a
wrong verdict can be argued with.

## Names

Raw identifiers come in near-identical clusters (Device DNAs sharing most
of their digits, Digilent serials differing in the last byte), so
`rpi-hwid name` hashes them into short, distinct words:

```
$ rpi-hwid name --netv2 0x00742c4e63b9085c --arty 210319B301DE 210319B0C238
netv2-grove  00742c4e63b9085c
arty-hawk  210319B301DE
arty-serin  210319B0C238
```

A NeTV2's name is a pure function of its DNA. Arty names are a hash
*chain* resolved against a registry, so two serials never share a word and
adding a board never renames an old one. Keep the registry as a JSON
object of serial to name and pass it with `--names registry.json` to both
`name` and `labels`.

## Labels

`rpi-hwid labels` lays out 63.5 × 38.1 mm labels, 21 to an A4 sheet (the
Avery L7160 grid), from a directory of collected documents. Print at
100 %. `--outline` draws the die-cut edges for an alignment print on plain
paper; `--start N` skips N positions on the first sheet so a partly used
sheet can be finished; `--only rpi|fpga|usb` limits the kinds; `--list`
prints what would be generated and where.

```
$ rpi-hwid labels --data data/ --list
sheet 1 row 1 col 1  arty   arty-hawk
sheet 1 row 1 col 2  acorn  Acorn CLE-215+
sheet 1 row 1 col 3  rpi    Pi 3 Model B+ 1 GB 000000004fe3e7e4
sheet 1 row 2 col 1  rpi    Pi 4 Model B 2 GB 10000000ce8e3593
sheet 1 row 2 col 2  rpi    Pi 5 1 GB c36b093f773d46b8
sheet 1 row 2 col 3  rpi    Pi 5 4 GB d88100008543dc30
sheet 1 row 3 col 1  rpi    Pi Zero W 512 MB 000000005157f671
sheet 1 row 3 col 2  usb    ASIX AX88179A f8:e4:3b:0f:c1:e6
sheet 1 row 3 col 3  usb    ASIX Elec. Corp. AX88179 00:0e:c6:82:b5:e1
$ rpi-hwid labels --data data/ --out labels.pdf
9 labels on 1 sheet -> labels.pdf
```

Every label carries only what cannot change, and every identifier that
might otherwise be typed is also a QR code. The three layouts, cropped
from a rendered sheet:

**Raspberry Pi.** The MACs are what people look for, so they are the
largest thing on the label, each with its own QR. Model, memory and
revision are decoded from the revision code, and the HAT band names what
the probe found on the header (and the EEPROM UUID when there is one). The
serial is a cross-check rather than the identity anyone uses, so it runs
up the left edge as a Code 128 barcode with the digits beside it. The
layout is always the same, so a stack of them reads at a glance.

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

The Zero W's wired port comes from the bonnet, so its MAC is printed as
the eth MAC. On a 3B+ or a Zero the wlan MAC follows from the eth MAC (the
Broadcom-OUI rule: same serial digits, XOR `55:55:55`), so it is printed
even when the radio is off. On a Pi 4 or 5 it cannot be derived, so a
disabled radio is stated as such.

**FPGA boards.** The maker and the derived name, the die, and the
immutable identifier full width with a QR: Device DNA where read, the
Digilent serial and flash part on an Arty, and a line to write the DNA on
when it has not been read yet.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/netv2.png" alt="NeTV2" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/arty.png" alt="Arty A7-35T" width="32%">
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/acorn.png" alt="Acorn CLE-215+, DNA not yet read" width="32%">
</p>

**USB network adapters.** The descriptors (USB version and speed, driver,
VID:PID) beside the MAC's QR, and the MAC itself full width along the
foot, so a dongle can be matched to a DHCP lease from across the room.

<p>
<img src="https://raw.githubusercontent.com/mithro/rpi-hwid/main/docs/examples/usb-asix.png" alt="ASIX AX88179 USB 3.0 gigabit adapter" width="49%">
</p>

The package ships the Raspberry Pi raspberry, the Alphamax and Digilent
marks (each its owner's trademark, drawn only on that maker's own hardware
to identify it) and the public-domain USB trident; see
`src/rpi_hwid/artwork/README.md` for the sources. A `--artwork DIR`
overrides any of them and may add a `netv2.svg`. A board whose maker has no
mark (SQRL) gets the name in type. The images are regenerated from the test
fixtures by `docs/examples/render.py`.

## Using it from Python

Everything on the collecting side is a frozen dataclass (`rpi_hwid.model`);
the probes emit JSON because they run on a Pi's Python 3.5.

```python
from pathlib import Path

from rpi_hwid.collect import collect, load_collected
from rpi_hwid.names import netv2_name
from rpi_hwid.revision import decode_revision

collect(["rpi5-netv2", "pi@10.21.2.47"], Path("data"), jump="jump.example.org", fpga=True)

for host, doc in load_collected(Path("data")).items():
    s = doc.summary
    rev = decode_revision(s.revision)
    print(host, rev.model, rev.memory, s.power_class, [m.mac for m in s.macs])
    for board in s.fpga:
        name = netv2_name(board.dna) if board.kind == "netv2" and board.dna else ""
        print("  ", board.kind, board.identity, name)
```

`Summary.from_dict` refuses a field it does not know, so a probe that has
grown a field is noticed when its document is loaded, not silently
dropped. `ProbeDocument.evidence` keeps the whole raw document for anything
the summary leaves out.

## Development

```
uv sync --all-extras --group dev
uv run ruff check && uv run mypy && uv run pytest
```

The test suite renders a sheet from the fixture documents and decodes
every QR on it with zxing (it needs `pdftoppm` from poppler and a
monospace font). The two probe files must stay Python 3.5-clean: the tests
refuse f-strings in them, and CI byte-compiles them with a real 3.5.

Releases are automatic: every green push to `main` publishes to PyPI and
rebuilds the apt repository. See [RELEASING.md](RELEASING.md). To build the
Debian package locally:

```
docker run --rm -v "$PWD:/src" -w /src debian:bookworm sh packaging/ci-build.sh
```

## Origin

Worked out on a fleet of Pi Zero W, 3B+, 4 and 5 hosts carrying NeTV2,
Acorn and Arty boards, powered by a mix of Waveshare PoE HATs and external
PoE splitters, in September 2026. The rules above are what those boards
showed; a board that behaves differently is a bug report.
