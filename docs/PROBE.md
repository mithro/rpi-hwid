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
the first rules a HAT out. A bus the board declares but has no `/dev` node for
is brought up for the scan and put back. The cheap step comes first: a controller
already enabled in `config.txt` lacks a node only because nothing loaded
`i2c-dev`, so the probe loads that module, and applies the bus's overlay with
`dtparam` only when the module alone brings no bus up (a `dtparam` call once hung
in the kernel on a Pi 3B+ whose `i2c_arm` was on but `i2c-dev` unloaded, until it
was power-cycled). A host with `i2c-dev` in `/etc/modules` and both buses on in
`config.txt` is read with no command at all. One that will not come up at all — an image with
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

## RISC-V boards

A board is RISC-V when `/proc/cpuinfo` prints an `isa` line for its harts, which
no other architecture does. The probe runs on it unchanged and the document keeps
its shape; the summary gains a `riscv` record (the harts, and on a SiFive board
its PCB EEPROM), which is `null` on every other board. The fleet has two, SiFive
HiFive Unmatched A00s, reached as `ansible@` (key only):

```
$ ssh ansible@10.1.90.243 'python3 -' < src/rpi_hwid/probe.py
SiFive HiFive Unmatched A00  serial SF105SZ212200391
  header  : no HAT header on this board
  evidence: device tree: compatible sifive,hifive-unmatched-a00 sifive,fu740-c000 sifive,fu740; 16 GB (MemTotal 16358196 kB)
  evidence: RISC-V: 4 harts rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd, mmu sv39, uarch sifive,bullet0, mvendorid 0x489 marchid 0x8000000000000007 mimpid 0x20181004
  evidence: SiFive EEPROM: HiFive Unmatched PCB rev 3 BOM B0 serial SF105SZ212200391 MAC 70:b3:d5:92:f8:de test pass, CRC 0x9709e522 ok
  evidence: storage nvme0 WDC WDS100T2B0C-00PXH0 serial 21210J802282 firmware 211210WD
  evidence: storage mmc0:0000 SD32G serial 0xb81f9080 cid 035344534433324785b81f9080014c61
  power   : no power sensing on this board: nothing on it reports its supply
  onboard : eth    70:b3:d5:92:f8:de  macb (driver)
```

That is hifive-unmatched-1 on 2026-09-26 (Debian 13, kernel 6.12.73).

The board's identity is its PCB EEPROM, a 24c02 at 0x54 on i2c-0. The probe
reads it only on a board whose device tree names it as an Unmatched, and only
through the kernel's at24 driver: whichever nvmem the device
`/sys/bus/i2c/devices/0-0054` carries (6.12 names it `board-id0`, after the
device tree's label). at24 serves it to root alone, so a refused plain read is
tried again through `sudo -n cat`, and a read that still fails is recorded with
the command that reads it. The layout is U-Boot's
(`board/sifive/unmatched/hifive-platform-i2c-eeprom.c`) and its CRC-32 is
checked; the bytes read off both boards are exactly what that layout rebuilds
from U-Boot's own boot print-out. U-Boot also copies the serial into the device
tree's `/serial-number`, which is where `serial` comes from; the EEPROM's stands
in where the device tree has none. The NVMe and SD serials are evidence only —
a disk is not the board.

## x86 boards

A PC has no device tree, so where there is none the probe reads the firmware's
DMI/SMBIOS strings from `/sys/class/dmi/id` instead, and the board is `x86`. The
serials and the product UUID there are root-only: a plain read is tried first,
then `sudo -n cat`, and a field neither could read is listed in `dmi.unread`
rather than passed off as absent. The model is the DMI board vendor and name; the
serial the first of the board, product and chassis serials that is not a firmware
placeholder (`To be filled by O.E.M.`, `Default string` and the like). The CPU's
model name comes from cpuinfo, and every disk's serial is recorded — a SATA
disk's from VPD page 0x80, an NVMe drive's from its controller, an SD card's or
eMMC's from its CID register.

```
$ ssh tim@minnow-turbot-2 'python3 -' < src/rpi_hwid/probe.py
ADI MinnowBoard Turbot  serial 0008A209EFED
  header  : no HAT header on this board
  evidence: DMI: board ADI MinnowBoard Turbot REV A, system ADI Minnowboard Turbot D0 PLATFORM D0, BIOS MNW2MAX1.X64.0094.R01.1612052239 12/05/2016; 2 GB (MemTotal 1920488 kB)
  evidence: DMI serials: board 0008A209EFED, product 0008A209EFED, chassis ; product uuid 00000000-6462-4524-006a-9b7737e315cf
  evidence: CPU: Intel(R) Atom(TM) CPU  E3826  @ 1.46GHz, 2 threads
  evidence: storage sda StorFly VSF302XC serial 54812-4198
  power   : no power sensing on this board: nothing on it reports its supply
  onboard : eth    00:08:a2:09:ef:ed  r8169 (serial-mac)
```

That is the fleet's MinnowBoard Turbot on 2026-09-26. Its neighbour
minnow-turbot-1 is, whatever its name, a CircuitCo MinnowBoard MAX (`Circuitco
MinnowBoard MAX`, an Atom E3825 where the Turbot has an E3826). On both, the DMI
serial is the Ethernet MAC without its colons, so a wired port on the PCI bus
whose MAC it is gets the signal `serial-mac`; one the serial does not name is
still taken as the board's own, on the bus alone (`pci`). Both boards report the
same product UUID, which is therefore recorded but never used as an identity.

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
bridge bitstream. The id is reported with the RDID bytes after it and the SFDP
revision where the chip answers them, because an id is shared: the S25FL127S,
128P, 128S and 129P all answer `0x012018`, and only those later bytes tell them
apart. How the label names the part from them, and why an id that names no part
stops label generation, is in [LABELS.md](LABELS.md#the-flash-part).

Where [fpgas-verify](https://github.com/fpgas-online/fpgas.online-test-designs)
is installed (an fpgas.online host), it is asked about the boards first, with
`fpgas-verify --identify` (directly as root, else `sudo -n`), whenever there is
an FPGA on PCIe, a Digilent FT2232 on USB, or `--jtag`. It reads only what is
safe while a board is in use: IDCODE, Device DNA, and an Acorn's configuration
flash through the board's own SoC, all six RDID bytes included; what it cannot
read safely (an Arty's or NeTV2's flash) it gives from its boot report, and
says which fields those are (`from_report`). It is given 60 s, fpgas-verify
bounding its own wait for a busy board at 30.

What it reads is put on the board it describes: by PCIe slot, an Arty by its
FT2232's serial, otherwise the one board of that kind whose die (IDCODE) agrees,
on PCIe or not -- fpgas-verify finds a NeTV2 by its JTAG scan and gives no slot
-- or, for a NeTV2, the one chain the harness does not name (`jtag`). A reading
that could be more than one board is put on none, and listed under
`fpga.fpgas_verify.unplaced`; one that matches no board is added. Its values
win over the chain's, except:

* a DNA: the board's and fpgas-verify's are compared, and when they disagree
  (a wrong match, or a wrong read) neither is kept, as with the SoC's DNA
  (`dna_conflict`); a DNA already in conflict stays so;
* a field from its boot report never beats one read live here that disagrees:
  the live one stays and the two are recorded under `report_conflict`;
* a flash it could not read leaves the chain's read standing.

A Tiny Tapeout board or a Fomu it reports gets no FPGA label: the first has its
own module and label, the second none yet. The document it printed is kept
whole under `fpga.fpgas_verify.document`, as evidence: its own fields, and the
boards with no label here.

The chain is then read only for what fpgas-verify did not give. When every
board it read came with its DNA and IDCODE, and its flash where `--flash` asks
for one, `--jtag` reads nothing. Otherwise the chain is read, but with `--flash`
the flash only when some board's flash fpgas-verify did not give (by slot, an
Arty by its serial, a chain on the harness by a board found the same way): a
JTAG flash read loads a bridge in place of the running design. Without
fpgas-verify, nothing here changes.

When fpgas-verify itself runs rpi-hwid (`fpgas-verify --label`), it sets
`FPGAS_VERIFY_IDENTITY` to the identity document it has already read. Then the
FPGA module sends nothing to any FPGA -- no JTAG, no BAR, no gateware read, no
Cynthion TraceID, whatever `--jtag`, `--flash`, `--soc` or `--force-offline`
say -- because the outer run is using the board. It asks `fpgas-verify
--identify` all the same, which prints that document and touches nothing, and
keeps the variable through sudo (`sudo -n
--preserve-env=FPGAS_VERIFY_IDENTITY`; as root, it is simply inherited).

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

## ESP32s

`rpi_hwid.esp32` is a stand-alone module of the same kind, for ESP32s on the host's
USB. `rpi-hwid esp32` runs it alone, and `rpi-hwid collect --esp32` appends it.
It has two depths, because they cost very differently.

**The USB tree** is read from sysfs, and no serial port is opened. Opening one
asserts DTR and RTS, and that alone resets an ESP32 on most boards and on the
chip's own USB-Serial-JTAG. The tree gives two things:

- An ESP32 on its own USB-Serial-JTAG (`303a:1001`: the C3, C6, S3, H2 and later)
  carries its base MAC, the Wi-Fi station MAC burned into eFuse, as the USB serial
  number. That is an identifier read from the chip, with nothing sent to it.
- Behind a USB-UART bridge (CP210x, CH340, CH343/CH9102, FTDI) the chip is
  invisible. The bridge could as well be carrying a radio module or a GPS, so it is
  listed as a candidate and never as an ESP32, with the bridge's own serial where
  it has one.

**`--read PORT`** is the disruptive depth. It runs an esptool that is already on
the host, as a library in a child `python3`, so the module itself stays
stdlib-only. It uses the first interpreter that can import esptool: the host's
own, or else a virtualenv under `~/.venvs` (rpi4-esp keeps esptool 5.2 there, and
rpi5-433mhz has Debian's 4.7). Nothing is installed. esptool resets the chip into
its ROM bootloader through the port's DTR/RTS lines and asks for:

- the chip description, features and crystal;
- the base MAC;
- the SPI flash's JEDEC id and the first eight bytes of its RDID (`0x9F`), its
  status registers (`0x05`, `0x35`, `0x15`), its Read Unique ID (`0x4B`), and
  its SFDP (`0x5A`), whole;
- the eFuse fields that are not secret: MAC, custom MAC, `OPTIONAL_UNIQUE_ID`,
  the wafer, block and package versions, and the flash and PSRAM capacity and
  vendor.

The flash is asked through the SPI controller's registers, which the ROM loader
reads and writes for anyone: the same "user command" esptool's
`run_spiflash_command` sets up, but with the controller's whole 64-byte buffer
read back, where esptool returns only its first word and refuses to ask for more
than 32 bits (4.7 and 5.2 alike). No flasher stub and no program in RAM is
needed. Only read opcodes are sent. A command's dummy bytes are clocked as an
address phase and dummy cycles, never as data: on an ESP32-C3 a command with a
data-out phase reads back nothing but zeroes, which is why the first version of
this read, which did that, found no unique id on any C3.

The unique id is read as 32 bytes, twice, and the two must agree. How long it is
is the part's to say: 64 or 128 bits, followed by `0xFF` or by the id again, and
`flash_uid_bits` records which. SFDP is read whole -- the 256-byte header region
and any parameter table beyond it -- and kept as hex beside a summary of its
revision, tables and density. docs/research/esp32-flash.md has the reads.

Each step records its own error and the rest carry on, so a flash that will not
answer does not cost the chip, MAC and eFuse already read. Whatever happens, a
`finally` resets the chip back into its application. The first version had no
such guard, and it once left three nodes sitting in the ROM.

The same port stays open while the application boots, so its first lines are
kept as evidence that it came back. HUPCL is cleared first, so closing the port
does not reset the chip again, and the port is given a read timeout, so a quiet
application cannot hold the read open. Nothing is written, to flash or to eFuse,
and no key block is printed.

Only the ports named are touched: a port on the host may belong to something
else that a reset would interrupt. On `collect` the ports are named per host, as
`--esp32-read HOST=PORT`, where HOST may be written with or without its user. A
HOST that is not being collected is an error, reported before anything is
probed.

What the reads of 2026-09-26 and 27 found:

- **ESP32-C3 SuperMinis:** their in-package flash is an XMC (JEDEC `0x464016`,
  which ESP-IDF files as XMC's D series) with SFDP 1.6 and a 128-bit unique id
  whose last six bytes are `0xFF`. The first read, which sent the dummy bytes as
  data, got zeroes; the chip's own `OPTIONAL_UNIQUE_ID` is there as well.
- **An ESP32-CAM's Boya flash:** a 128-bit unique id, SFDP 1.0.
- **A devkit's GigaDevice flash:** a 64-bit unique id, SFDP 1.0.

Every board was back in its application within seconds of the read.

The devices land in the document's `verdict.esp32`, beside the summary rather than
in it; the USB tree and each read's output are kept as evidence under `esp32`.

```
$ rpi-hwid esp32                 # rpi5-433mhz: three C3 SuperMinis, a LilyGO, an E22
  esp32  : e8:3d:c1:8c:5c:88  ESP32 (chip not read)  /dev/ttyACM0 (usb-serial-jtag)
  esp32  : 44:1b:f6:2e:b3:80  ESP32 (chip not read)  /dev/ttyACM2 (usb-serial-jtag)
  esp32  : e8:3d:c1:8c:3e:b8  ESP32 (chip not read)  /dev/ttyACM3 (usb-serial-jtag)
  esp32? : CH9102 bridge 1a86:55d4 serial 591B031339 on /dev/ttyACM1; --read /dev/ttyACM1 would reset it to ask
  esp32? : CH340 bridge 1a86:7523 serial (none) on /dev/ttyUSB0; --read /dev/ttyUSB0 would reset it to ask
```

### 433 MHz radio nodes

`rpi_hwid.esp32_radio` asks a node built by
[esp32-to-433mhz](https://github.com/mithro/esp32-to-433mhz) which radio it has.
The nodes run Tasmota's `cc1101-node` build, whose driver finds the radio itself.
It is a module of the same kind as the others: stand-alone, stdlib-only and
Python 3.5-clean. `rpi-hwid esp32 --radio PORT` runs it on the host, and
`rpi-hwid collect --esp32-radio HOST=PORT` appends it after the esptool read.

The driver prints the chip and the pins it answered on only at boot:

```
CC1: CC1101 PARTNUM 0x00 VERSION 0x14, SCK=3 MISO=7 MOSI=4 CS=1 GDO0=10 GDO2=6
```

So the read resets the node once. It pulses RTS with DTR low, as esptool's hard
reset does, and HUPCL is cleared so closing the port does not reset it again. It
keeps twelve seconds of boot output, then asks `Radio`, `CcStatus` and
`SxStatus` on the console. Where the answers and the boot line both give a
value, the answers win; the pins come only from the boot line. Tasmota falls
back to its safeboot image after a run of boots that each end within ten
seconds, and the twelve-second listen outlasts that.

A board with no radio fitted is a finding, not an error. A port that answers
neither way is an error, kept on its ESP32 as `radio_error`. The radio goes on
the ESP32's entry as `verdict.esp32[].radio`, and each read's full output is kept
under `esp32.radio_reads`.

The reads `collect --esp32-radio` made on rpi5-433mhz on 2026-09-26, printed the
way `rpi-hwid esp32 --radio` prints them:

```
  radio  : /dev/radio-sx1278-ra02: SX1278 version 0x12  DIO0=6 MISO=7 MOSI=4 NSS=1 RST=10 SCK=3
  radio  : /dev/radio-cc1101-blue: CC1101 version 0x14 partnum 0x00  CS=1 GDO0=10 GDO2=6 MISO=7 MOSI=4 SCK=3
  radio  : /dev/radio-esp32-E8:3D:C1:8C:5C:88: none (firmware 15.5.0(safeboot))
```

All three nodes were back in their firmware straight after.
