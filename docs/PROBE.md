# The probe, in detail

What `rpi-hwid probe` reads, what each signal is worth, and the two stand-alone
modules for boards attached to the Pi. For installing and running it, see the
[README](../README.md).

## What each signal proves

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

A Pi Zero W wearing Waveshare's PoE-ETH-USB-HUB-HAT bonnet, which has no ID
EEPROM and is recognised from the USB tree instead:

```
$ rpi-hwid probe
Raspberry Pi Zero W Rev 1.1  serial 000000005157f671  rev 9000c1
  header : Waveshare PoE-ETH-USB-HUB-HAT (1a40:0101 hub with RTL8152 on port 4)
  signal : power port: throttled=0x0
  power  : PoE through the Waveshare PoE-ETH-USB-HUB-HAT bonnet
  onboard: wlan   b8:27:eb:02:a3:24  brcmfmac
  usb net: 0bda:8152 Realtek USB 10/100 LAN  00:e0:4c:36:0b:0a  ethernet
```

## What powers it

The verdict names the power source where the evidence allows, and says so where it
does not:

| `power_class` | meaning |
|---|---|
| `gpio-poe-hat` | a PoE HAT feeding the GPIO 5 V pins, identified by its EEPROM or I2C devices |
| `bonnet-poe` | the Waveshare PoE-ETH-USB-HUB-HAT bonnet |
| `usbc-supply` | an external supply on USB-C advertising 900 or 1500 mA: a PoE splitter or a USB-A lead |
| `usbc-pd-supply` | a USB-C supply with a PD contract |
| `ambiguous` | two sources read identically: an EEPROM-less GPIO PoE HAT on a Pi 5 and a 3 A USB-C splitter, or an EEPROM-less, I2C-less HAT on a 3B+ and any splitter |
| `undetermined` | nothing on the Pi distinguishes the source |

The switch-side 802.3af class narrows the ambiguous cases (the bonnet is class 3,
the M.2 HAT+ (B) class 4, an af-only HAT is never class 4), but that is read from
the switch, not the Pi, so it is outside this package.

## Orange Pi

The probe runs unchanged on the fleet's Xunlong Orange Pi PCs (Allwinner H3) and
the document keeps its shape: the board is another `model`, with `revision` empty
(the 0000 in its cpuinfo is not a code), `header` empty, and `power_class`
`undetermined`, because an H3 has no PMIC and no firmware report of what feeds it.
What it does have:

```
$ rpi-hwid probe
Xunlong Orange Pi PC  serial 02c000812eb7a34e
  header : 40-pin header not probed: no HAT ID EEPROM convention on this board
  signal : device tree: compatible xunlong,orangepi-pc allwinner,sun8i-h3; 1 GB (MemTotal 1016504 kB)
  signal : Allwinner SID 0x02c00081 0x35d04620 0x79058814 0x401c0a94 -> serial 02c000812eb7a34e
  power  : no power sensing on this board: nothing on it reports its supply
  onboard: eth    02:81:2e:b7:a3:4e  dwmac-sun8i
```

That is a run on the pool's Orange Pi PC (pi-sw2-p22, reached as `pi@10.21.2.22`
through `welland.fpgas.online`) on 2026-09-11.

The board is told from the device tree's `compatible` list. Its `serial` is the
SoC's: U-Boot builds `serial#` from the Allwinner SID e-fuses and writes it to
`/serial-number` in the device tree, where the probe reads it (cpuinfo's `Serial`
and the SID nvmem under `/sys/bus/nvmem/devices/` are read as fallbacks, U-Boot's
rule reproduced from the raw e-fuses). The eth0 MAC comes from that same serial —
`02`, the serial's fourth byte, then its last four: `02:81:2e:b7:a3:4e` from
`02c000812eb7a34e` — so it is not independent evidence. The derivation is
confirmed on hardware: the SID read out of the e-fuses reproduces the device-tree
serial exactly through U-Boot's CRC rule.

Older `sunxi_sid` kernels read those words the other way round. The Pi-only pokes
(`dtparam`, `vcgencmd`, the ID bus, the bus-1 scan, the bonnet rule) are skipped,
and the Armbian release is recorded as evidence only.

## FPGA boards

`rpi_hwid.fpga` is kept apart from the Pi probe because few people have an FPGA
board on their Pi. `rpi-hwid probe --fpga` appends it; `rpi-hwid fpga` runs it
alone.

From what the Pi sees without touching the FPGA: a NeTV2 running LitePCIe is PCIe
`10ee:7024` with one 1 MiB BAR; an SQRL Acorn CLE-215+ is `1e24:021f` (or
`10ee:7011` under other gateware) with 128 KiB + 64 KiB BARs; a Digilent Arty is
its own FT2232 with a `210319…` serial. BAR sizes come from sysfs and a BAR is
never mapped, because that wedges a host.

With `--jtag`, openFPGALoader reads the IDCODE and Device DNA over the Arty's
FT2232 or the host's GPIO harness (libgpiod, pins 27:22:4:17). A GPIO chain that
answers on a host with no Arty is taken to be a NeTV2. With `--flash`, an Arty's
SPI flash is identified by JEDEC id, which reloads the FPGA with openFPGALoader's
bridge bitstream. The S25FL128S and S25FL127S both answer `0x012018`, so the label
says `S25FL128S/127S`.

```
$ rpi-hwid fpga --jtag          # a Pi 4 with an Arty A7-35T on USB
  fpga   : arty (Digilent FT2232 210319B301DE; FT2232 JTAG idcode 0x362d093 artix a7 35t), DNA 0x00628502251ea85c
$ rpi-hwid fpga                 # a Pi 5 with an Acorn on its PCIe connector
  fpga   : acorn (PCIe 1e24:021f, 128 KiB + 64 KiB BARs (SQRL Acorn CLE-215+))
```

## Tiny Tapeout boards

`rpi_hwid.tinytapeout` is the same kind of stand-alone module for a Tiny Tapeout
demo board on the Pi's USB. `rpi-hwid probe --tinytapeout` appends it;
`rpi-hwid tinytapeout` runs it alone.

From the USB tree alone the board is only a candidate: the demo board's RP2040
(TT04 to TT08) or RP2350 (the DBv3 "ETR" boards) runs the Tiny Tapeout MicroPython
SDK, which is stock MicroPython as far as USB is concerned — `2e8a:0005`
"MicroPython" "Board in FS mode", with the RP2's flash unique id as its serial.
What makes it a Tiny Tapeout board is the SDK, so the module drives the board's raw
REPL over `/dev/ttyACM*` (`os.open` and `termios`, no pyserial) and asks the SDK
what it already holds: the chip ROM the boot cached (`shuttle=`, `repo=`,
`commit=`, present on every chip since TT05; `FPGA` on the FPGA breakout), the demo
board it detected (`TT04/TT05`, `TT06+`, `TTDBv3 [3.2]`) and its own version.

Reading the ROM afresh would drive the chip's pins, so the probe never does: where
the boot did not cache it (a custom `main.py`, say) the ROM is reported as not
cached, with the reason. Asking interrupts whatever the board is running — at
boot, nothing — but never resets it and touches no pin; every read and write has a
deadline, and an unreachable board stays a candidate. `--no-repl` stops at the USB
tree.

```
$ rpi-hwid tinytapeout             # a Pi 4 with a TT06 dev kit on USB
  tt     : TT06 on demo board TT06+ (Tiny Tapeout SDK 2.0.4 on Raspberry Pi Pico with RP2040 (USB 1-1.2); chip ROM shuttle=tt06; demo board TT06+)
```

The module also carries a table of what the board cannot say: the soldermask and
silkscreen colours of both the chip carrier and the demo board for each shuttle,
the demo board revision that shipped with each kit, and the chip's page on
tinytapeout.com, for the label.
