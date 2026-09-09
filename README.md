# rpi-hwid — Raspberry Pi hardware identity

What is this Raspberry Pi wearing, what powers it, and what is soldered to
it? `rpi-hwid` answers from the Pi itself, from evidence the firmware and
kernel already expose but nothing collects: HAT ID EEPROMs (including the
ones the firmware never reads), the Pi 5's own verdict on its USB-C supply,
the PMIC's input and RTC-cell voltages, the fan header, the USB tree, and
which network interfaces are soldered down. A separate module does the same
for an FPGA board attached to the Pi. A collector runs it over ssh across a
fleet, and a label generator turns the collected data into sticker labels
that carry only what cannot change.

```
pip install rpi-hwid            # or: uv tool install rpi-hwid[labels]

rpi-hwid probe                  # on a Pi
rpi-hwid probe --json --fpga --jtag
rpi-hwid collect --out data/ -J jump.example.org rpi5-netv2 pi@10.21.1.10
rpi-hwid labels --data data/ --out labels.pdf
rpi-hwid name --netv2 0x00742c4e63b9085c
rpi-hwid revision c04170
```

The probe is one dependency-free file that runs on any Pi with python3 3.5
or later, so it can also be sent over ssh with nothing installed there:

```
ssh pi@host 'python3 -' --json < src/rpi_hwid/probe.py
```

It needs passwordless `sudo` for `i2c-tools` and `vcgencmd`.

## What the probe reads, and what each signal proves

| signal | tells |
|---|---|
| HAT ID EEPROM at `0x50` | what the firmware read: `/proc/device-tree/hat` (official PoE HATs, Digilent Pmod HAT Adaptor, Google VoiceBonnet…) |
| HAT ID EEPROM at `0x51`–`0x57`, read off the ID bus | boards the firmware **never reads**: Waveshare's PoE M.2 HAT+ (B) puts a well-formed HAT+ EEPROM at `0x52` (product string, pid `0x6d87`, a DT atom naming `pciex1`) |
| devices on I2C bus 1 | Waveshare PoE HAT (B): SSD1306 at `0x3c` and PCF8574 at `0x20` |
| USB tree | Waveshare PoE-ETH-USB-HUB-HAT on a Zero: a Terminus `1a40:0101` hub on the root port with an RTL8152 on its port 4 |
| Pi 5 `max_current` | the firmware's USB-C verdict: 5000 after a PD contract, **3000 both for a 3 A resistor source and for no USB-C source at all** (a HAT on the GPIO 5 V pins), 1500 or 900 for a resistor source advertising that much — so 900/1500 proves an external USB-C supply |
| Pi 5 PMIC ADC | 5 V input (GPIO-fed HATs 5.1–5.4 V, splitters 4.8–5.0 V) and the RTC cell (about 3 V fitted, under 0.01 V not) |
| Pi 5 `cooling_fan` node | a fan on the Pi's own header |
| interface drivers | soldered-down (SoC Ethernet, SDIO radio, the 3B+'s LAN7800) versus removable USB adapters, which are listed with their descriptors |
| throttle flags | under-voltage now or since boot: all a 3B+, Zero or Pi 4 can say about its supply |

The verdict names the power source where the evidence allows (`gpio-poe-hat`,
`bonnet-poe`, `usbc-supply`, `usbc-pd-supply`) and says `ambiguous` or
`undetermined` where it does not: an EEPROM-less GPIO PoE HAT on a Pi 5 and
a 3 A USB-C splitter read identically, as do an EEPROM-less, I2C-less HAT on
a 3B+ and any splitter. The switch-side 802.3af class narrows those (the
bonnet is class 3, the M.2 HAT+ (B) class 4, an af-only HAT is never class 4)
but that is read from the switch, not the Pi.

## FPGA boards (`rpi_hwid.fpga`)

Kept apart from the Pi probe because few people have one. From what the Pi
sees without touching the FPGA: a NeTV2 running LitePCIe is PCIe `10ee:7024`
with one 1 MiB BAR; an SQRL Acorn CLE-215+ is `1e24:021f` (or `10ee:7011`
under other gateware) with 128 KiB + 64 KiB BARs — BAR sizes come from sysfs
and a BAR is never mapped, because that wedges a host; a Digilent Arty is its
own FT2232 with a `210319…` serial. With `--jtag`, openFPGALoader reads the
idcode and Device DNA over the Arty's FT2232 or the host's GPIO harness
(libgpiod, pins 27:22:4:17). With `--flash`, an Arty's SPI flash is
identified by JEDEC id, which reloads the FPGA with openFPGALoader's bridge;
note the S25FL128S and S25FL127S both answer `0x012018`.

## Names

`rpi-hwid name` derives short, distinct names from the immutable identifier:
a NeTV2 from its Device DNA (`netv2-grove`), an Arty from its Digilent serial
(`arty-hawk`). Both hash first, because the raw identifiers come in
near-identical clusters. Arty names are a hash *chain* against a registry
(pass `--names registry.json`), so adding a board never renames an old one.

## Labels

`rpi-hwid labels` lays out 63.5 × 38.1 mm labels, 21 to an A4 sheet (the
Avery L7160 grid), from a directory of collected documents: one per Pi
(model, memory and revision from the revision code, serial up the edge, HAT
band, a QR per soldered-down MAC), one per FPGA board (name, die, DNA or
serial with a QR, flash where read), one per removable USB network adapter
(descriptors, MAC with a QR). Print at 100 %. `--outline` draws the die-cut
edges for an alignment print on plain paper.

The package ships only artwork it may: the public-domain USB trident. A
`--artwork DIR` may add `raspberry-pi.svg`, `alphamax.png`, `digilent.png`
and `netv2.svg`, which are trademarks of their owners (the Raspberry Pi
logo is on the English Wikipedia file page; the Alphamax and Digilent marks
are their GitHub organisation avatars); without them a label uses the
maker's name in type.

## Origin

Worked out on a fleet of Pi Zero W, 3B+, 4 and 5 hosts carrying NeTV2, Acorn
and Arty boards, powered by a mix of Waveshare PoE HATs and external PoE
splitters, in September 2026. The rules above are what those boards showed;
a board that behaves differently is a bug report.
