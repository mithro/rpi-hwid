# What an ESP32's SPI flash will tell us

Exploration of 2026-09-27, on the five ESP32s this project has read: three
ESP32-C3 SuperMinis on rpi5-433mhz (in-package flash, esptool 4.7 from
Debian) and an ESP32-CAM and an ESP32 devkit on rpi4-esp (external flash,
esptool 5.2 in `~/.venvs/esptool`). The aim: the same depth of flash facts the
FPGA boards give (JEDEC id, extended id, SFDP, unique id).

Every read here was read-only, went through the ROM loader (no stub, nothing
loaded into RAM), and ended with a hard reset; each board's application was
seen booting afterwards (Tasmota on the C3s, the camera sketch on the CAM, the
AT firmware on the devkit). Nothing was written to flash or eFuse.

The scripts are beside this file:

- `esp32_flash_explore.py PORT` — the exploration: RDID, REMS, RES, the three
  status registers, SFDP 0x00-0xFF, Read Unique ID two ways, and the first 256
  bytes of security registers 0-3.
- `esp32_uid_mosi_vs_dummy.py PORT` — why the first probe read zeroes from
  every C3's unique id.

The result is folded into `rpi_hwid.esp32` (`READ_SCRIPT`'s `spi()`), so
`rpi-hwid esp32 --read PORT` now takes all of this.

## The one trick: read the SPI controller's whole buffer

esptool's `run_spiflash_command` (loader.py, 4.7 and 5.2 alike) drives the
SPI controller's "user command" registers through the ROM's `READ_REG` /
`WRITE_REG` commands, and then returns only `W0`:

```python
if read_bits > 32:
    raise FatalError("Reading more than 32 bits back from a SPI flash "
                     "operation is unsupported")
...
status = self.read_reg(SPI_W0_REG)
```

But the controller's data buffer is sixteen words, `W0..W15`, 64 bytes, on
every ESP32 family, and `SPI_MISO_DLEN` takes any length up to that. So the
same register sequence with `MISO_DLEN = n*8-1` and sixteen `READ_REG`s of
`W0..W15` returns up to 64 bytes from any read command, with any address and
any number of dummy clocks. Anything longer that takes an address (SFDP,
security registers) is read in 64-byte pieces. No stub is needed, and the
stub adds nothing: its protocol (loader.py's command table, `0x02`-`0x14`,
`0xD0`-`0xD4`) has no generic SPI command, only the same `READ_REG` /
`WRITE_REG`.

Register offsets come from esptool's own target classes (`SPI_REG_BASE`,
`SPI_USR_OFFS`, `SPI_USR1_OFFS`, `SPI_USR2_OFFS`, `SPI_MOSI_DLEN_OFFS`,
`SPI_MISO_DLEN_OFFS`, `SPI_W0_OFFS`), so every chip esptool knows is covered.
One trap: the original ESP32 (and ESP8266) take the address in the top bits of
`SPI_ADDR`, later chips in the bottom bits. esptool 5 says so with
`SPI_ADDR_REG_MSB` (false for c2, c3, c5, c6, c61, h2, h21, h4, p4, s2, s3,
s31); esptool 4.7 has no such attribute and writes the address unshifted,
which is wrong for an original ESP32 at any non-zero address.

### Why the first probe read zeroes on every C3

The first probe skipped 0x4B's four dummy bytes by sending them as MOSI data.
On the C3 a user command with a MOSI phase returns nothing in `W0`:

```
$ python3 esp32_uid_mosi_vs_dummy.py /dev/ttyACM0      # rpi5-433mhz, esptool 4.7
esptool 4.7.0 ESP32-C3 (QFN32) (revision v0.4)
mosi-dummies 0x00000000  addr+dummy 0x19110c24  no-skip 0xffffff7f
mosi-dummies 0x00000000  addr+dummy 0x19110c24  no-skip 0xffffff7f
```

Clocking the same four bytes as a 24-bit address of zero plus eight dummy
clocks gives the id (`24 0c 11 19` little-endian). The original ESP32 does
not have this problem (the CAM and devkit answered the MOSI form). The probe
now never uses a MOSI phase.

## What each flash said

`9f` is RDID read for 64 bytes, `90` REMS (address 0), `ab` RES (three dummy
bytes), `05/35/15` the status registers, `4b raw` Read Unique ID read for 32
bytes with no dummy handling (so the first four bytes are the dummy cycles),
`4b` the same with address+dummy, 16 bytes. SFDP lines that are all `ff` are
left out.

### ESP32-C3 SuperMinis (in-package flash, eFuse FLASH_VENDOR = 1 "XMC")

```
### /dev/ttyACM0  E8:3D:C1:8C:5C:88  ESP32-C3 (QFN32) (revision v0.4)  esptool 4.7.0
9f        464016464016464016464016464016464016464016464016...   (repeats; no extended id)
90        46154615
ab        15151515
05/35/15  00 / 02 / 20
4b raw    7fffffff240c1119088539540150ffffffffffffffffffff...
4b        240c1119088539540150ffffffffffff
5a_sfdp:
  00: 53 46 44 50 06 01 02 ff 00 06 01 10 30 00 00 ff
  10: 46 00 01 04 d0 00 00 ff 84 00 01 02 c0 00 00 ff
  30: e5 20 f9 ff ff ff ff 01 44 eb 08 6b 08 3b 42 bb
  40: fe ff ff ff ff ff 00 ff ff ff 40 eb 0c 20 0f 52
  50: 10 d8 00 ff 15 32 a5 00 84 a3 13 c1 cc a1 76 35
  60: 7a 75 7a 75 f7 b3 d5 5c 19 f6 4d ff e9 10 c0 80
  c0: 00 00 f0 ff ff ff ff ff ff ff ff ff ff ff ff ff
  d0: 00 36 00 23 9f f9 77 64 00 e8 ff ff ff ff ff ff
  e0: 24 0c 11 19 08 85 39 54 01 50 ff ff ff ff ff ff
  f0: ff ff 01 ff ff 50 34 50 39 31 33 07 8d 8f 69 aa
48 @0x0000, 0x1000, 0x2000, 0x3000: all FF (256 bytes each)

### /dev/ttyACM2  44:1B:F6:2E:B3:80
4b        2c30041916712aca013affffffffffff
  e0: 2c 30 04 19 16 71 2a ca 01 3a ff ff ff ff ff ff
  f0: ff ff 01 ff ff 50 34 50 39 32 34 08 11 2e 69 aa
(everything else identical to ACM0)

### /dev/ttyACM3  E8:3D:C1:8C:3E:B8
4b        1f2b10190882f7540150ffffffffffff
  e0: 1f 2b 10 19 08 82 f7 54 01 50 ff ff ff ff ff ff
  f0: ff ff 01 ff ff 50 34 50 39 31 33 06 ac 52 69 aa
(everything else identical to ACM0)
```

Across the three, SFDP differs only at bytes 0xE0-0xE9 and 0xF9-0xFD.

**Which part.** JEDEC `46 40 16`. 0x46 is not a manufacturer in flashrom's
`include/flashchips.h`; it is ESP-IDF's
`SPI_FLASH_XMC_2` (`components/esp_mspi/include/spi_flash_defs.h`:
`#define SPI_FLASH_XMC_1 0x20`, `#define SPI_FLASH_XMC_2 0x46`), and ESP-IDF's
generic driver (`components/spi_flash/spi_flash_chip_generic.c`,
`spi_flash_chip_generic_get_caps`) says of it `// XMC-D support suspend` /
`if (chip->chip_id >> 16 == 0x46)`, and lists `0x464016`-`0x464018` among its
"XMC chips" in `spi_flash_hpm_enable.c`. The same driver tells an XMC-D that
reports 0x20 by SFDP byte 0x32 bit 3; ours is `f9`, bit 3 set. And the SFDP
header matches the XM25QH32D datasheet's SFDP table exactly -- revision 1.6,
three headers, the basic table (16 dwords at 0x30), a vendor table (4 dwords
at 0xD0) and the 4-byte-address table (0xFF84, 2 dwords at 0xC0) -- except
that the datasheet's vendor id is 20h
([XM25QH32D Rev1.2, 2024-04-08](https://www.xmcwh.com/uploads/920/XM25QH32D_Ver1.2.pdf),
"Serial Flash Discoverable Parameters (SFDP) Signature and Parameter
Identification Data Value"). So: an **XMC XM25QH32D-family die, 32 Mbit,
coded 0x46**, not the XM25QH32C (0x204016) the eFuse vendor alone suggests.
The label's flash row names it `XM25QH32D` (`esp32_micro.JEDEC_PART`);
`esp32_micro.JEDEC_VENDOR` names 0x46 XMC for any other 0x46 part.

**Unique id.** The datasheet: "The Read Unique ID Number instruction (4Bh)
is unique to each device by accessing a factory-set and read-only 128-bit
number ... Followed by a four Bytes of dummy clocks ... the 128-bit ID is
shifted out ... (data read after the 128-bit ID will always be FFh)." It
answers exactly that, with 80 bits programmed and the last six bytes `ff`.
Nothing about it needs a different command or dummy count: the zeroes of
2026-09-26 were the ESP32-C3 controller, not the flash (above).

**An undocumented copy.** The datasheet's SFDP table stops at 0xDF. At 0xE0
these parts carry the same 16 bytes as the unique id, and at 0xF5 an ASCII
string, `P4P913` / `P4P924` / `P4P913`, with three more bytes that differ per
chip -- a lot or wafer trace, by the look of it. Recorded, not relied on.

**Security registers** (48h at 0x1000, 0x2000, 0x3000, three 1024-byte OTP
registers per the datasheet): blank on all three. Status registers: SR2 = 02
(QE, quad enable, set), SR3 = 20 (drive
strength).

**eFuse** (read on 2026-09-26, `espefuse/efuse_defs/esp32c3.yaml`):
`FLASH_CAP = 1` ({1: "4M"}), `FLASH_VENDOR = 1` ({1: "XMC", 2: "GD", 3: "FM",
4: "TT", 5: "ZBIT"}), `FLASH_TEMP = 1` ({1: "105C", 2: "85C"}). The C3 has no
PSRAM fields. The chip's own 128-bit `OPTIONAL_UNIQUE_ID` is separate from, and
unrelated to, the flash's.

### ESP32-CAM (external Boya flash)

```
### /dev/ttyUSB0  a4:f0:0f:76:46:64  ESP32-D0WD-V3 (revision v3.1)  esptool 5.2.0
9f        684016684016684016684016684016684016684016684016...
90        68156815
ab        15151515
05/35/15  00 / 00 / 40
4b raw    fffffffe343738393844fa77fffcffff968f1f1134373839...
4b        343738393844fa77fffcffff968f1f11
5a_sfdp:
  00: 53 46 44 50 00 01 01 ff 00 00 01 09 30 00 00 ff
  10: 68 00 01 03 60 00 00 ff ff ff ff ff ff ff ff ff
  30: e5 20 f1 ff ff ff ff 01 44 eb 08 6b 08 3b 42 bb
  40: ee ff ff ff ff ff 00 ff ff ff 00 ff 0c 20 0f 52
  50: 10 d8 00 ff ff ff ff ff ff ff ff ff ff ff ff ff
  60: 00 36 00 27 9f e9 77 64 fc eb ff ff ff ff ff ff
48 @0x0000: 343738393844fa77fffcffff968f1f11 then FF
48 @0x1000, 0x2000, 0x3000: all FF
```

The unique id is **128 bits**, repeating after sixteen bytes; the probe of
2026-09-26 printed only its first 64. BYTe (Boya) makes two parts at
0x684016 whose SFDP headers are identical byte for byte (both datasheets'
"SFDP Signature and Parameter Identification" tables: revision 1.0, two
headers, 9 dwords at 30h, a 68h table of 3 dwords at 60h): the
[BY25Q32BS](https://www.byte-semi.com/wp-content/uploads/BY25Q32BS.pdf)
(Rev. 2.4, 7.3.5: "a factory-set read-only 64-bit number") and the
[BY25Q32ES](https://www.byte-semi.com/wp-content/uploads/BY25Q32ES.pdf)
(Rev. 2.2, 7.3.5: "a factory-set read-only 128-bit number"). 128 bits makes it
the **BY25Q32ES**, and `esp32_micro.PART_BY_UID_BITS` names it so. Reading
security register "0" (address 0, which the ES datasheet reserves for
factory SFDP storage "upon special order") returns the unique id again.

### ESP32 devkit (external GigaDevice flash)

```
### /dev/ttyUSB1  24:0a:c4:11:44:e8  ESP32-D0WDQ6 (revision v1.0)  esptool 5.2.0
9f        c84016c84016c84016c84016c84016c84016c84016c84016...
90        c815c815
ab        15151515
05/35/15  00 / 00 / 20
4b raw    ffffffff3130343531118566ffffffffffffffffc801ffff...
4b        3130343531118566ffffffffffffffff
5a_sfdp:
  00: 53 46 44 50 00 01 01 ff 00 00 01 09 30 00 00 ff
  10: c8 00 01 03 60 00 00 ff ff ff ff ff ff ff ff ff
  30: e5 20 f1 ff ff ff ff 01 44 eb 08 6b 08 3b 42 bb
  40: ee ff ff ff ff ff 00 ff ff ff 00 ff 0c 20 0f 52
  50: 10 d8 00 ff ff ff ff ff ff ff ff ff ff ff ff ff
  60: 00 36 00 27 9e f9 77 64 fc eb ff ff ff ff ff ff
48 @0x0000..0x3000: all FF
```

A **64-bit** unique id, then `ff` (and, consistently, `c8 01` at byte 16).
SFDP 1.0 with a GigaDevice (C8h) vendor table. ESP-IDF tells a GD25Q
C-series from an E-series by SFDP byte 4 ("0 means C series, 6 means E
series", `spi_flash_hpm_enable.c`); this is 0, so a C or older B. The
[GD25Q32C datasheet Rev 2.5](https://datasheet.octopart.com/GD25Q32CSIG-GigaDevice-datasheet-83806627.pdf)
has the same SFDP header (9 dwords at 30h, a C8h table of 3 dwords) and no
4Bh command at all, so the part stays "GD25Q32x", as flashrom has it
(`GIGADEVICE_GD25Q32 0x4016 /* Same as GD25Q32B */`). Its SFDP and Boya's are
the same bytes but for the vendor id and two bytes of the vendor table
(0x64-0x65: `9e f9` here, `9f e9` on the Boya).

## Summary per device

| device | JEDEC | part | SFDP | unique id | status 05/35/15 |
|---|---|---|---|---|---|
| C3 E8:3D:C1:8C:5C:88 | 464016 | XMC XM25QH32D-family (0x46-coded), in package | 1.6, 3 tables | 128 bits `240c1119088539540150ffffffffffff` | 00/02/20 |
| C3 44:1B:F6:2E:B3:80 | 464016 | same | same | `2c30041916712aca013affffffffffff` | 00/02/20 |
| C3 E8:3D:C1:8C:3E:B8 | 464016 | same | same | `1f2b10190882f7540150ffffffffffff` | 00/02/20 |
| ESP32-CAM a4:f0:0f:76:46:64 | 684016 | Boya BY25Q32ES | 1.0, 2 tables | 128 bits `343738393844fa77fffcffff968f1f11` | 00/00/40 |
| devkit 24:0a:c4:11:44:e8 | c84016 | GigaDevice GD25Q32x | 1.0, 2 tables | 64 bits `3130343531118566` | 00/00/20 |

None has an extended JEDEC id: RDID repeats its three bytes.

## A program in RAM?

Not needed for anything above, and not built. What it would take:

- **Loading** is esptool's `mem_begin` / `mem_block` / `mem_finish(entry)`
  (the ROM's MEM_BEGIN/MEM_DATA/MEM_END, 0x05/0x07/0x06): volatile, cleared
  by the reset the probe already ends with. The program then owns the
  console -- UART, or on the C3/C6/S3/H2 the USB-Serial-JTAG FIFO -- and has
  to speak something back, which is most of esptool's stub.
- **Toolchains** on arm64 Debian: RISC-V (C3, C6, H2) builds with Debian's
  `gcc-riscv64-unknown-elf` (15.3, `-march=rv32imc_zicsr -mabi=ilp32
  -nostdlib`). Xtensa LX6/LX7 (ESP32, S2, S3) has no Debian compiler: only
  `gcc-xtensa-lx106`, which is the ESP8266's core configuration and not the
  ESP32's. Espressif publishes `xtensa-esp-elf` and `riscv32-esp-elf` builds
  for aarch64-linux-gnu (crosstool-NG release `esp-16.1.0_20260609`).
- **Shipping a blob** has precedent -- esptool ships its stubs as JSON blobs
  (`targets/stub_flasher/2/*.json`, from esp-flasher-stub v0.2.0,
  Apache-2.0/MIT) -- but a Debian package of rpi-hwid would then carry
  generated code whose source and toolchain Debian cannot build for Xtensa.

The register method gives 64 bytes per command, which covers every
identifier above (a 16-byte unique id, the whole SFDP in pieces, security
registers in pieces). A RAM program would only earn its keep for a
no-address response longer than 64 bytes, for speed, or for a flash that
must be driven in QPI/OPI mode (see risks).

## PSRAM

The ESP32-CAM has an external PSRAM (its sketch prints `PSRAM OK`). It sits
on the same SPI bus behind CS1 (GPIO16, clock on GPIO17 on the D0WD), and
ESP-IDF's driver reads its id with 9Fh plus a 24-bit address (manufacturer,
known-good-die and an "EID" -- not documented as unique). Reading it from the
ROM would mean re-muxing GPIO16/17 and switching the controller to CS1: the
same register mechanism, but it touches pin configuration, so it was not
tried. On the S3 the in-package PSRAM's capacity and vendor are in eFuse
(`PSRAM_CAP` {1: "8M", 2: "2M", 3: "16M"}, `PSRAM_VENDOR` {1: "AP_3v3", 2:
"AP_1v8"}, `PSRAM_TEMP`), which the probe already takes.

## On the label, and in the document

The label, as `esp32_micro` now draws it (`docs/examples/esp32-sticker-1.png`):

- **The `flash` row**, in the package or beside it, is part and density --
  `XM25QH32D · 4 MiB`, `BY25Q32ES · 4 MiB`, `GD25Q32x · 4 MiB` -- or vendor
  and JEDEC id where no part is known (`XMC 0x464017 · 8 MiB`), as the FPGA
  flash line does. A Boya whose uid length was not measured is `BY25Q32xS`.
- **The `uid` rows** are the flash's unique id at its own length: one row
  for 64 bits, two for 128. A C3 has a second serial, its eFuse
  `OPTIONAL_UNIQUE_ID`; only one fits, and `esp32_micro.SERIALS` says which
  (the flash's, for now). The other stays in the document; a chip whose
  flash gives no uid prints its eFuse id as `eFuse`.
- The SFDP revision is not printed, as on the FPGA labels; like there, it can
  settle a part name (`labels.flash_from_jedec(..., sfdp=)` is passed it).

In the document, per device (`verdict.esp32[]`): `flash_jedec`; `flash_rdid`
(RDID's first 8 bytes, hex); `flash_status` (`{"05", "35", "15"}`, hex);
`flash_uid`, `flash_uid_bits`, `flash_uid_state` (`read` / `blank` /
`unbounded`) and `flash_uid_raw` (the 32 bytes read); `flash_sfdp` (the
revision, or `none`), `flash_sfdp_summary` (tables, density) and
`flash_sfdp_raw` (every byte). Security registers are not read by the probe:
they are user OTP, and what someone programmed there is not ours to copy
into a document.

## Risks

- **Secure download mode** refuses `READ_REG`/`WRITE_REG`; the flash steps
  would then fail one by one, recorded as step errors, with the rest of the
  read kept.
- **QPI / octal flash.** Every command here is 1-bit SPI. A part the ROM has
  put in QPI or OPI mode (octal flash on some S3 modules) will not answer
  them. None of the boards here is one; unmeasured.
- **The 64/128-bit call** looks at where `ff` or repetition begins. A
  128-bit id whose last eight bytes happened to be `ff` would be taken as
  64 bits (the value printed would be the same bytes, shorter).
- The controller registers the probe changes (USR, USR1, USR2, MOSI/MISO
  lengths) are restored after each command; `SPI_ADDR` and `W0..W15` are
  not (esptool does not either), and the reset that ends every read clears
  them.
- **esptool 4.7** has no `SPI_ADDR_REG_MSB`; the probe falls back to the chip
  name. A future chip that wants the address in the top bits and runs under
  4.7 would get it wrong; 5.x says so itself.
