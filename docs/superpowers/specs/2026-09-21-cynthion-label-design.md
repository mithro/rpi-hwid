# Cynthion labels, and the ECP5's TraceID

A label for the Great Scott Gadgets Cynthion, and the two identifiers it is
keyed on: the ECP5 configuration flash's unique ID, which costs nothing, and
the ECP5's own TraceID, which costs the board's capture.

Measured against the production Cynthion on `rpi5-netv2.iot.welland.mithis.com`
(2026-09-21) and read against `cynthion==0.2.5` / `apollo-fpga==1.1.1`.

## Why

`rpi-hwid collect --jtag` finds three things on that host — the Pi 5, an
AX88179 dongle and the NeTV2 — and nothing at all on the Cynthion wired in
line beside them, whose TARGET side carries the NeTV2's USB. Every other FPGA
board on the fleet gets a sticker carrying an identifier that cannot change.
This one gets none.

## What can be identified, and what each costs

Three facts, with very different prices. The design follows the prices.

### 1. Configuration flash UID — free

The analyzer gateware publishes the ECP5 configuration flash's 64-bit unique
ID as the USB serial number:

    d.iSerialNumber = ECP5FlashUIDStringDescriptor     # analyzer/top.py:232

so `267125df30c460de` is already sitting in sysfs, needing no tool, no flag
and no transaction with the board. `apollo` reads the same value over JTAG
through background SPI (`apollo_fpga/ecp5.py:582`, `read_flash_uid`), which
matters because it means the identifier is the same in both modes, even though
the USB serial string is not.

This is the **primary key**: the one identifier obtainable without disturbing
anything, and therefore the only one a routine collect can be keyed on.

Caveat recorded honestly on the label and in the docstring: it identifies the
SPI-NOR configuration flash, not the ECP5 die. It is nevertheless the identity
Great Scott Gadgets' own gateware and tooling both use for the board.

### 2. ECP5 TraceID — costs the capture

The ECP5 has a true die-level equivalent of the Xilinx Device DNA, and the
earlier assumption that it did not was simply wrong. Lattice call it TraceID:
64 bits, of which the **top 8 are user-defined** (set from the bitstream's
`TRACE_ID_BINARY` preference) and the **bottom 56 are factory-programmed and
read-only**. Read by shifting `UIDCODE_PUB` (`0x19`) into an 8-bit IR and 64
bits out of the DR — structurally the same shape as the Xilinx `FUSE_DNA`
(`0x32`) read this package already performs.

`UIDCODE_PUB = 0x19` is corroborated by `adamgreig/ecpdap` (an ECP5 tool,
`read_uid()`), `ataradov/usb-sniffer`, openbmc's `lattice_cpld.h` and a dozen
MachXO drivers. `_PUB` is the public instruction: `ecpdap` issues it with no
`ISC_ENABLE`, so the read itself does not disturb a configured device.

**The read is harmless; reaching it is not.** A Cynthion's ECP5 TAP hangs off
the Apollo debug controller, not off anything the host can drive directly, and
Apollo only reaches the shared USB port by asking the gateware to stand down
(`REQUEST_APOLLO_ADV_STOP`, `apollo_fpga/__init__.py:158`). That ends the
analyzer's capture, and with the FPGA held offline the VBUS switch controls
the gateware drives are undriven, so a device on TARGET may lose bus power.
Both are acceptable on this rig and both are documented.

Because it requires that window, the TraceID is **displayed, never keyed on**.
Keying a board's name on it would make the board unnameable without taking it
offline.

**The mask is not optional.** The top 8 bits come from the bitstream, so an
unmasked TraceID would rename a board whenever its gateware was rebuilt with a
different `TRACE_ID_BINARY`. Naming and comparison use the bottom 56 bits,
exactly as `DNA_MASK = 0x1ffffffffffffff` already guards the Xilinx side.

### 3. Hardware revision and FPGA part — free

`bcdDevice` is the board revision, not a gateware version: `minor = bcdDevice &
0xFF`, `major = bcdDevice >> 8` (`apollo_fpga/__init__.py:260`), so `0104` is
r1.4 and `get_hardware_name()` renders it `Cynthion r1.4`. The FPGA part
follows from the revision — every Cynthion is `LFE5U-12F` except r0.7, which
is `LFE5U-25F` — so the part is exact without touching the board.

The analyzer *gateware's* version is deliberately not read: `GET_MINOR_VERSION`
lives on interface 0, which a running capture is streaming on.

## Detection — `rpi_hwid.fpga`, no flag

A `cynthion_devices()` beside the existing `ftdi_devices()`, scanning
`/sys/bus/usb/devices/*` for `1d50:615b` and `1d50:615c` and keeping `serial`,
`bcdDevice`, `product`, and each interface's `bInterfaceSubClass`. Pure
functions over that result decide what it means, so they can be tested without
hardware:

| subclass | meaning |
| --- | --- |
| `0x10` | analyzer gateware |
| `0x20` | Moondancer gateware |
| `0x00` | Apollo stub (shared port) or, on PID `615c`, Apollo itself |

from `cynthion/shared/usb.toml`, which exists precisely because "Cynthion
reports the same idVendor and idProduct irrespective of the gateware running".

The mode is load-bearing, not decoration: **in Apollo mode the USB serial is
the debug controller's, not the flash UID**, so it must never be recorded as
one. A board found in Apollo mode yields a label keyed on nothing until its
flash UID is read over JTAG.

This path needs no `--jtag`: a bare `rpi-hwid fpga` finds the board.

## The offline read — `--force-offline`

A new flag, named for the vocabulary `apollo` already uses. `--jtag` is
deliberately *not* reused: on a NeTV2 or an Arty `--jtag` is non-disruptive,
and a routine `collect --jtag` across the fleet must not silently end every
analyzer capture on it.

Apollo's JTAG is a pure vendor-control-request protocol — no bulk endpoints —
so it is implemented over raw `usbdevfs` in an embedded reader run as root by
the same interpreter, exactly as `PCILEECH_READER` already does for bulk. As
there, the reader only moves bytes; every decision about what they mean is
made unprivileged, where it can be tested. `USBDEVFS_CONTROL`'s ioctl number
is derived from the struct size, because it differs between a 32- and 64-bit
userland.

The sequence, and every step of it is from upstream:

| # | step | request |
| --- | --- | --- |
| 1 | find `615b` carrying a stub interface | — |
| 2 | ask the gateware to stand down | `0xF0` OUT/interface, `wIndex` = stub iface |
| 3 | wait for `615c` to enumerate (5 s) | — |
| 4 | hold the FPGA offline | `REQUEST_FORCE_FPGA_OFFLINE` `0xc1` |
| 5 | learn scan limits and quirks | `REQUEST_JTAG_GET_INFO` `0xb8`, 8 bytes in |
| 6 | start JTAG, go to RESET | `0xbf`, then `0xb5` value 0 |
| 7 | IR ← `0x19`, 8 bits | `0xb1` then `0xb3` |
| 8 | DR → 64 bits = TraceID | `0xb3` then `0xb2` |
| 9 | flash UID over background SPI | confirms the key matches iSerial |
| 10 | stop JTAG | `0xbe` |
| 11 | reconfigure from flash | `REQUEST_RECONFIGURE` `0xc0` |
| 12 | hand the shared port back | `REQUEST_ALLOW_FPGA_TAKEOVER_USB` `0xc2` |
| 13 | **verify `615b` returned, same iSerial** | ours |

Steps 11–12 are the restore, taken from `reconfigure_fpga` in apollo's CLI and
`flash_bitstream` in cynthion's `util.py`, which agree. Step 13 is this
package's own: `apollo info --force-offline` reads and simply leaves the FPGA
offline, and a tool that ends a capture to read a number must put the board
back and say whether it succeeded. If it did not, the failure is reported
loudly, along with the recovery (`apollo reconfigure`, or a power cycle).

`QUIRK_FLIP_BITS_IN_WHOLE_BYTES` from `GET_INFO` is honoured; the ECP5's IR is
8 bits at every call site in `apollo_fpga/ecp5.py`.

### What the hardware corrected, once it was run (2026-09-21)

Three things this design got wrong on paper, each found by running it on
rpi5-netv2 and none of them visible from upstream source alone.

- **`REQUEST_JTAG_GET_INFO` stalls on this firmware.** apollo's own
  `JTAGChain.__enter__` wraps that very call in `except IOError: pass`; the
  call was ported and the tolerance was not, and the first run died on it.
  A stall there means "no quirks reported", not a failure.
- **An IR scan must set the `advance_state` flag.** apollo's `_scan_data` sets
  it on the last chunk of a write (`advance_state = not bool(bits_to_scan)`)
  and its `_receive_data` never sets it at all, so the asymmetry is easy to
  miss. It is TMS on the final clock: without it the shift state is never
  left, `UIDCODE_PUB` is never latched, and the DR read returned 64 zero bits
  — which the all-ones/all-zeroes guard correctly refused to name a board
  from, so the bug surfaced as "no TraceID" rather than as a wrong one.
- **The chain returns its bytes least significant first.** Settled by a known
  answer rather than by guessing: with nothing shifted into the IR, a TAP
  reset leaves the IDCODE in the DR, and the board returned `43101121` —
  `0x21111043`, the LFE5U-12F a Cynthion r1.4 carries, reversed. Byte order
  is the one thing here that upstream source could not have settled, and
  getting it wrong would have minted a permanently wrong identifier.

Measured result: TraceID `0x1b808604604e0e` (its user byte is `0x00`, the
stock gateware having set no `TRACE_ID_BINARY`), flash uid
`267125df30c460de`, analyzer restored.

The restore held throughout, including on the run that threw: it is in a
`finally`, so the board reconfigured and came back on its own and only the
reporting was lost. That is why `RESTORED=` is now printed after the
`finally` rather than inside the read, and why a failed read still reports
whether the rig came back.

## Naming

`cynthion_name(flash_uid)` — a pure function of the flash UID, like
`netv2_name()`, with no registry: the Arty's hash chain exists only because
Digilent serials collide, and flash UIDs do not. `normalise_dna()` already
accepts bare or `0x`-prefixed hex and pads to 16, which is exactly a 64-bit
UID. A third word list, instruments, keeps neighbouring boards visually
distinct as the existing lists do.

`rpi-hwid name --cynthion <uid>` joins `--netv2` and `--arty`.

## Data model

`FpgaBoard` gains `hw_rev`, `mode` and `trace_id`; `serial` carries the flash
UID, so `identity` (`dna or serial`) keeps working untouched.

`FpgaLabel` gains `ident` and `ident_caption`, so the foot of the label states
what it is actually printing. The existing four kinds keep `ident = dna` and
the caption `Device DNA`, making their output byte-identical to today; a
Cynthion gets the flash UID under `ECP5 config flash UID`. The TraceID, when
read, takes a captioned body row like the Arty's `S/N`. The QR stays keyed on
`dna or serial`, which is already the flash UID.

`BOARD_MODEL` gains `("Great Scott Gadgets", "Cynthion")` and `mark_maker()`
the shipped GSG mark, at the Digilent triangle's 6 mm rather than the
wordmarks' 5 mm: a compact mark scaled to a wordmark's height reads as half
the size beside it.

A separate defect the first render exposed: the foot's write-it-in prompt was
fitted to the QR's width, which `"Device DNA, write it in"` just fits and
nothing longer does, so a Cynthion's came out `"ECP5 config flash UI…"` — a
caption that reads as a typo, on the one label whose whole point is the
instruction. The caption is a band of its own above the rule and now has the
full inked width.

`--only` learns to accept an FPGA sub-kind (`cynthion`, `netv2`, …) alongside
the five existing kinds, so one board's sticker can be generated alone.

## Testing

Fixtures are the descriptors actually read off `rpi5-netv2`
(`267125df30c460de`, `0104`, subclasses `10` and `00`), in the manner of the
captured `PCILEECH_HOST` and `PCILEECH_REPLY`. Covered: the verdict; the
revision decode; mode from subclass; that an Apollo-mode serial is never taken
for a flash UID; TraceID masking to 56 bits; request construction and response
parsing for the Apollo protocol as pure functions; the record, the name, the
revision→part table and a render. The `usbdevfs` shim itself stays too small
to hold a decision.

`tests/conftest.py`'s existing `no_privileged_commands` guard means no test can
start the root reader on the machine running them.

## Risks

- **The restore fails and the analyzer stays offline.** Mitigated by step 13
  and a loud report; recovery documented.
- **A device on TARGET loses bus power during the window.** Accepted for this
  rig; documented for others.
- **An unmasked TraceID renames a board on a gateware rebuild.** Prevented by
  the 56-bit mask.

## Not in this change

- The analyzer gateware's version, which cannot be read without disturbing the
  capture.
- Moondancer/Facedancer-specific identity beyond naming the mode.
- Writing anything to the board: no flashing, no bitstream, no LED patterns.
- **Flash unique IDs on the Xilinx boards** (Arty, NeTV2, Acorn) — a separate
  change, because `0x4B` is not one instruction. On Winbond parts it is Read
  Unique ID (4 dummy bytes, 8 bytes out, which is what the Cynthion's flash
  answers); on the Arty's S25FL128S it is OTPR, taking a 3-byte address, with
  a factory-programmed 128-bit ESN in the low 16 bytes of OTP region 0.
  openFPGALoader defines the opcode as `FLASH_ROTP` and never calls it. The
  right primitive is a JEDEC-manufacturer-keyed table of (opcode, address or
  dummy bytes, length), built on the `0x9F` id `--flash` already reads.

### What that separate change will have to account for

Measured on the fleet 2026-09-20 and reported by the session doing the Acorn
deployment; recorded here so it is not rediscovered the hard way.

- **`--flash`'s route does not exist on an Acorn.** The fleet's own docs record
  `openFPGALoader --write-flash` / spiOverJtag over the GPIO harness as not
  working — the bridge never toggles CCLK after configuration. spiOverJtag is
  how this package reads the Arty's flash today, and the Arty has a Digilent
  FT2232; no Acorn host at either site has an FTDI cable at all.
- **Reconfiguring over JTAG while the PCIe endpoint is enumerated is a surprise
  removal, and it crashed `pi-sw2-p47` outright on 2026-08-31.** Any such path
  must `echo 1 > /sys/bus/pci/devices/<bdf>/remove` first. This is the concrete
  form of the risk flagged when the change was scoped.
- **A route that drops nothing exists.** The LiteX SoC being deployed to these
  cards carries an `S7SPIFlash` bit-bang core, so a JEDEC `0x9F` read is an
  ordinary read-only SPI transaction over the UART bridge or BAR0 — no
  reconfiguration, no spiOverJtag, no power cycle. The part is documented as a
  Spansion S25FL256S but has never been confirmed by a JEDEC read.

Three things this package already gets wrong about Acorns, which that change
should fix rather than inherit:

- `HARNESS_PINS = "27:22:4:17"` is the NeTV2 harness and is hardcoded. An
  Acorn's JTAG comes off the card's P1 Pico-EZmate on different pins entirely:
  `2:3:4:14` on a Compute Blade, `10:9:11:8` on a Pi 5. The pins must become a
  property of the board or the host, not a constant.
- `10ee:7011` is described in `fpga.py` as the Acorn under a "default Xilinx
  id". It is not: it is RHS Research's XDMA sample image. A LiteX x1 design on
  the same card enumerates as `10ee:7021`. The two PCIe signatures identify
  *which gateware is loaded*, not which board it is loaded on.
- Only `1e24:021f` (CLE-215+) is known. The CLE-101 / LiteFury answers
  `1e24:0101` and is currently unlabelled.

And an Acorn's Device DNA *is* readable, contrary to the assumption that no
JTAG path to one exists: `pi20` reads `0x0028e5c45e304854` with
`openFPGALoader --cable libgpiod --pins 2:3:4:14 --read-dna`, which is
read-only and safe on a live endpoint. An Acorn label could therefore be keyed
on its DNA like a NeTV2's, which is the stated plan of record for tying a card
to its label. Note that openFPGALoader 0.10.0, which some hosts carry, has no
`--read-dna` at all and opens `/dev/gpiochip0` when the header is `gpiochip15`.
