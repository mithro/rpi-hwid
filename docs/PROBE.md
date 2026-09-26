# The probe, in detail

What `rpi-hwid probe` reads, what each signal is worth, and the two stand-alone
modules for boards attached to the Pi. For installing and running it, see the
[README](../README.md).

## What each signal proves

| signal | tells |
|---|---|
| HAT ID EEPROM at `0x50` | what the firmware read: `/proc/device-tree/hat` (official PoE HATs, Digilent Pmod HAT Adaptor, Google VoiceBonnet…) |
| HAT ID EEPROM at `0x51`–`0x57`, read off the ID bus | boards the firmware **never reads**: Waveshare's PoE M.2 HAT+ (B) puts a well-formed HAT+ EEPROM at `0x52` (product string, pid `0x6d87`, a DT atom naming `pciex1`) |
| devices on the header's user bus | Waveshare PoE HAT (B): SSD1306 at `0x3c` and PCF8574 at `0x20`. The bus is brought up for the scan when the board has it disabled — as most of the fleet does — and put back afterwards |
| USB tree | Waveshare PoE-ETH-USB-HUB-HAT on a Zero: a Terminus `1a40:0101` hub on the root port with an RTL8152 on its port 4. That RTL8152 is reported as the Zero's wired port, not as a removable adapter |
| Pi 5 `max_current` | the firmware's USB-C verdict: 5000 after a PD contract, **3000 both for a 3 A resistor source and for no USB-C source at all** (a HAT on the GPIO 5 V pins), 1500 or 900 for a resistor source advertising that much, so 900/1500 proves an external USB-C supply |
| Pi 5 PMIC ADC | 5 V input (GPIO-fed HATs 5.1–5.4 V, splitters 4.8–5.0 V) and the RTC cell (about 3 V fitted, under 0.01 V not) |
| Pi 5 `cooling_fan` node | a fan on the Pi's own header |
| interface drivers | soldered-down (SoC Ethernet, SDIO radio, the 3B+'s LAN7800) versus removable USB adapters, which are listed with their descriptors |
| a MAC the board derives from its own serial | the port is the board's own and which one it is, whatever bus it sits on. Every Pi up to the 3B reaches Ethernet through a soldered USB chip (LAN9512/9514, `smsc95xx`) that also serves removable dongles, so the driver cannot say and the MAC can |
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

Nothing found on the header and nowhere to look are kept apart, because only
the first rules a HAT out. A bus the board declares but has disabled is brought
up for the scan and put back; one that will not come up at all — an image with
no I2C support, or an Armbian board, which needs a reboot to add an overlay — is
reported as unread rather than counted as empty:

```
  header : nothing identifiable on the header, and it was not fully read
  signal : header id and user bus could not be read (i2c-0, i2c-1): a HAT known
           only by what answers there cannot be ruled out
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
(the 0000 in its cpuinfo is not a code) and `power_class` `undetermined`, because
an H3 has no PMIC and no firmware report of what feeds it. Its 40-pin header is
probed exactly as a Pi's, though — a HAT does not know what it is plugged into —
so `header` names whatever it wears. What it has:

```
$ rpi-hwid probe
Xunlong Orange Pi PC  serial 02c000812eb7a34e
  header : Pmod HAT Adaptor (HAT EEPROM at 0x50, pid 0x0001)
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
(`dtparam`, `vcgencmd`, the bonnet rule) are skipped — an Orange Pi's two header
buses are scanned exactly as a Pi's, but only if it is already carrying them —
and the Armbian release, where the board has one, is recorded as evidence
only: the fleet's Orange Pi netboots the pool's Raspbian armhf root and so
carries none.

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

## Software-defined radios

`rpi_hwid.sdr` is a fourth stand-alone module, for the radios on a host's USB and
PCIe. `rpi-hwid probe --sdr` appends it; `rpi-hwid collect --sdr` appends it on
every host. Every radio here is normally held by a live service (OpenWebRX, the
KrakenSDR DoA software, readsb), so by default it opens none of them: it reads
sysfs, and runs `iio_attr` only against a Pluto's *network* IIO context.

- **RTL2832U** (`0bda:2838`): the EEPROM gives the USB strings and the serial
  and nothing more. A Blog V4 names itself (`RTLSDRBlog` / `Blog V4`, which
  librtlsdr checks). A Blog V3 carries the same generic strings as every other
  dongle: `Realtek`, `RTL2838UHIDIR`, serial `00000001`.
- **KrakenSDR**: five RTL2832U on one hub (a Microchip USB2517, `0424:2517`),
  serials 1000–1004. Those serials are Heimdall's convention
  (`util/kerberos_eeprom_init.sh` writes `serial=$((i+1000))`), so every
  KrakenSDR reads the same and none can be told from another.
- **ADALM-Pluto** (`0456:b673`): the USB serial is the unit's own. ADI's
  firmware sets it, the IIO `hw_serial` and the USB MACs from the kernel's
  `SPI-NOR-UniqueID`, the QSPI flash's factory unique id (`board/pluto/S23udc`).
  The network context gives the rest: `hw_model` (`PlutoSDR Rev.B
  (Z7010-AD9363A)`), the AD936x variant, the reference clock, and the tuning,
  sampling and bandwidth ranges the driver accepts.
- **Wavelet Lab usdr cards** (PCIe `10ee:7049` and the rest of the
  `usdr_pcie_uram` driver's own id table): `7049` is the LMS7002M M.2 class
  (`M2_LM7_1`), whose members, the XSDR, the XTRX and the SSDR, only the card's
  HWID register tells apart. These are Xilinx ids, so `rpi_hwid.fpga` leaves
  them alone rather than calling a radio an unknown FPGA board. The PCIe Device
  Serial Number is recorded, but on rpi-sdr-xsdr it is `00-00-00-00-12-34-56-78`,
  the Xilinx core's placeholder.

A usdr card can drop off its link without a word in dmesg. rpi-sdr-xsdr's did,
some days after the driver brought it up: still listed and bound, its command
register back to Mem- BusMaster-, the library reading HWID `ffffffff`. The
module reads the head of config space from sysfs (user-readable, nothing
opened) and reports "card not answering: reboot the host, or power-cycle it"
instead of describing a card that is not there.

`--sdr-open` (`collect --sdr-open HOST`) opens the radios nothing holds:
`fuser` must show no holder of the device node first, since OpenWebRX, readsb
and Heimdall keep theirs open while they run.

On a usdr card it runs `usdr_dm_sensors` and `usdr_flash`, both read-only.
They give the HWID (0x30 in bits 23:16 is an XSDR), the configuration flash's
JEDEC id and the images in it. The golden image's DEVID names the die, because
`usdr_flash` refuses an image built for another. The firmware ids are kept as
evidence and never printed.

The XSDR's flash is an AT25SL321 (`1f 42 16`), which has no Read Unique ID
command. Its datasheet (Renesas DS-AT25SL321-112 Rev. K, §8.41, Table 17) puts
a "128-bit ESN (Electrical Serial Number)" in a separate secured OTP area. It
is reached by Enter Secured OTP (B1h), a normal read, then Exit (C1h), and the
security register's bit 0 says whether the factory locked it. `--sdr-open`
reads it through libusdr's own espi core, in a root subprocess of its own. It
sends only that volatile mode switch (no write-enable, program or erase) and
checks afterwards that the main array reads back unchanged. rpi-sdr-xsdr's
reads `19 04 02 03 09 0e 97 69`, then eight erased bytes, with the factory
lock clear. It is the only per-unit value the card has, so the label keys on
it and names its source.

On an RTL2832U it runs `rtl_eeprom -d SERIAL`, which without a write flag only
reads. That gives the tuner librtlsdr found (an R820T2 answers as "Rafael
Micro R820T") and the EEPROM's fields, which on the fleet's six dongles are
the generic defaults throughout.

A Blog V3 carries the same generic EEPROM, so it is told by its hardware. Its
datasheet says the V3 "has direct sampling mode implemented in hardware
already", with HF diplexed off the SMA into the RTL2832U's Q branch, where
"on typical R820T RTL-SDR dongles one can enable direct sampling mode by
soldering a wire to the Q-branch pins". So for a lone R820T dongle the open
read also takes half a second from each direct-sampling input at 14 MHz,
through librtlsdr in a process of its own. A Q branch whose signal stands at
least three times clear of the I branch's ADC noise makes it a V3. On
rpi-sdr-rtlsdr-v3 the ratio was I 0.46 to Q 2.32, with Q rising with
frequency (1.14, 1.76, 2.32 at 1, 7.1 and 14 MHz) while I stayed flat. A
KrakenSDR's channels are never streamed this way: rpi-sdr-kraken negotiates
only 15 W of USB-PD and browns out under load.

```
$ rpi-hwid probe --sdr
  sdr    : krakensdr (five RTL2832U on hub 1-1 (0424:2517), serials 1000-1004)
  sdr    : pluto (USB 0456:b673 PlutoSDR (ADALM-PLUTO) at 3-1; IIO context ip:192.168.2.1)
  sdr    : usdr (PCIe 10ee:7049 at 0001:01:00.0 (usdr_pcie_uram class 4, m2_lm7_1), bound to usdr)
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

### A port a service already holds

On an fpgas.online rig the demo board's port belongs to `fpgas-tt.service`, the
site's bridge, which opens it at startup and keeps it for its whole life. Waiting
can never win it, and sharing it is worse than useless: the bridge does not hold it
exclusively, so the probe's `open` succeeds and the two readers then split the
board's answers, which looks exactly like a mute board and means writing into a
port someone else is streaming. So the module looks for a holder *before* opening
the port, and if the holder is that service it stops it for the length of the read
and starts it again after. The restart is guaranteed twice — in a `finally`, and by
a `systemd-run` timer armed before the stop for the paths a `finally` never
reaches — and the answer records what was done to the unit, so a bridge that did
not come back says so on its own line.

Stopping a service is a real interruption, so it happens only when every one of
these holds, and otherwise the probe leaves the holder alone and names it:

- the machine's device tree says it is a **Raspberry Pi**;
- the holder is not the probe itself or anything that launched it;
- the holder sits **directly** in a system service's cgroup
  (`/system.slice/<unit>.service`) — a process in a scope, such as a terminal, an
  ssh session or a tmux pane, names no unit;
- that unit is on the allowlist, which is **`fpgas-tt.service` alone**;
- `sudo -n` works, so nothing waits on a password prompt.

When the port cannot be had outright — `--no-stop-service` was given, or the
holder is a service this probe may not stop — the port is **not opened at all**
and the holder is named instead. Opening it would not fail, which is the trap: a
holder that never asked for the port exclusively does not stop anyone else
opening it, and two readers then split the board's answers. A reader that cannot
have the port to itself does not take half of it.

`--no-stop-service` (on `tinytapeout`, `probe --tinytapeout` and
`collect --tinytapeout`) turns the stopping off entirely.

These limits exist because an earlier version had fewer. It took the nearest
`.service` above the holder's cgroup, and for a process in a tmux pane that is the
user's own manager: its test suite, run on a workstation with passwordless sudo,
ran `systemctl stop user@1001.service` and ended every terminal and the tmux
server with it. The suite now fails any test that starts `sudo`, `systemctl`,
`systemd-run` or the like (`tests/conftest.py`), and a test proves that guard
works rather than trusting it.

```
$ rpi-hwid tinytapeout             # a Pi 4 with a TT06 dev kit on USB
  tt     : TT06 on demo board TT06+ (Tiny Tapeout SDK 2.0.4 on Raspberry Pi Pico with RP2040 (USB 1-1.2); chip ROM shuttle=tt06; demo board TT06+)
```

The module also carries a table of what the board cannot say: the soldermask and
silkscreen colours of both the chip carrier and the demo board for each shuttle,
the demo board revision that shipped with each kit, and the chip's page on
tinytapeout.com, for the label.
