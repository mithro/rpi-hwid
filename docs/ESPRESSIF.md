# Espressif parts

The ESP32 micro labels print what every chip of a part number shares from
one table, `rpi_hwid.espressif`, and this page is printed from it
(`uv run python -m rpi_hwid.espressif`; a test fails if the two drift).
It covers the Espressif chips on maker dev boards and in cheap IoT gear:
the ESP8266EX and ESP8285 inside most Sonoff, Athom, Shelly and
Tuya-rebadged plugs and switches, the original ESP32 in its packages, and
the S2, S3, C2, C3, C5, C6, H2 and P4.

Every value was read from Espressif's own documents (a datasheet section or
table), esptool's and espefuse's source, or Tasmota's; the citations follow
the tables, and the references they name are at the end. Where Espressif
gives no figure the table says "not documented" rather than guessing.

How the label uses it, and which facts come from the read instead, is in
[LABELS.md](LABELS.md#esp32).

## Which part a read names

esptool names most parts outright (`ESP32-D0WD-V3`, `ESP32-C6FH4`,
`ESP8285N08`). For the ESP32-S3, the ESP32-C3 and the ESP8684/ESP8685 it
names the die, and the part is the die plus what its package holds, which
the chip's eFuse records and esptool reports as features: `Embedded Flash
4MB (XMC)` and eFuse `FLASH_TEMP` 105C make an ESP32-C3 an **ESP32-C3FH4**
(the SuperMini's chip); `Embedded PSRAM 8MB (AP_1v8)` makes an S3 an
**ESP32-S3R8V**. The C5, H2 and P4 variants cannot be told apart this way
(esptool reads package 0 for all of them and has no map for their size
fields), so a read of one names the family, and the label's memory glyph
shows the on-chip SRAM alone.

A chip that is not in the table is an error naming the host, the MAC and
the chip, not a label with its spec strip missing: add the row, with the
datasheet table that lists the part.

## Tasmota

The Tasmota mark goes on the label of a part whose family Tasmota ships a
release binary for (ota.tasmota.com, 15.6.0): every family but the ESP32-H2,
which has no Wi-Fi. For the ESP32-C2 and the ESP32-P4 that binary is the
start of Tasmota's support ("Experimental support" in its changelog for the
C2; "support in Tasmota is just beginning" in its docs for the P4), and the
table says so. The ESP8285 runs the ESP8266 builds. A single-core ESP32
(the S0WD) takes `tasmota32solo1.bin`.

## Cores and radios on the label

The label's cores glyph prints two numbers: the application cores, then the
low-power cores beside them (`Part.core_pair`). The low-power one is the
ESP32's ULP FSM, the S2's and S3's ULP coprocessor, and the C5's, C6's and
P4's LP RISC-V core; the ESP8266, C2, C3 and H2 have none, and print 0. The
S2 and S3 have two ULP coprocessors, a RISC-V and an FSM, but their
datasheets say the two "cannot work simultaneously", so they count as one.
No part here has a processor of its own for its radio: the datasheets
describe the Wi-Fi MAC and the Bluetooth link controller as hardware, with
the protocol stacks above them running on the application cores (the
`radio_cpu` citations below).

The Wi-Fi glyph prints the bands and the 802.11 amendments from the Wi-Fi
column (`Family.wifi_band_ghz`, `Family.wifi_standards`): 2.4 GHz and b/g/n
for the ESP8266, ESP32, S2, S3, C2 and C3; 2.4 GHz and b/g/n/ax for the C6;
2.4 and 5 GHz and a/b/g/n/ac/ax for the C5.

## The families and the parts

| family | ISA | core | cores | LP core | max MHz | SRAM | ROM | RTC/LP SRAM | Wi-Fi | Bluetooth | 802.15.4 | USB | eFuse unique id | Tasmota |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ESP8266 | Xtensa | Xtensa L106 | 1 | — | 160 | 160 KB | not documented | not documented | 802.11 b/g/n (HT20), 2.4 GHz (Wi-Fi 4) | — | — | none | — | yes (`tasmota.bin`) |
| ESP32 | Xtensa | Xtensa LX6 | 2 | ULP-FSM | 240 | 520 KB | 448 KB | 16 KB | 802.11 b/g/n, 2.4 GHz (Wi-Fi 4) | Bluetooth 4.2 BR/EDR + LE | — | none | — | yes (`tasmota32.bin`) |
| ESP32-S2 | Xtensa | Xtensa LX7 | 1 | ULP-RISC-V + ULP-FSM | 240 | 320 KB | 128 KB | 16 KB | 802.11 b/g/n, 2.4 GHz (Wi-Fi 4) | — | — | OTG | yes | yes (`tasmota32s2.bin`) |
| ESP32-S3 | Xtensa | Xtensa LX7 | 2 | ULP-RISC-V + ULP-FSM | 240 | 512 KB | 384 KB | 16 KB | 802.11 b/g/n, 2.4 GHz (Wi-Fi 4) | Bluetooth LE 5 | — | OTG + Serial/JTAG | yes | yes (`tasmota32s3.bin`) |
| ESP32-C2 | RISC-V | RV32IMAC | 1 | — | 120 | 272 KB | 576 KB | not documented | 802.11 b/g/n, 2.4 GHz (Wi-Fi 4) | Bluetooth LE 5.3 | — | none | — | experimental (`tasmota32c2.bin`) |
| ESP32-C3 | RISC-V | RV32IMC | 1 | — | 160 | 400 KB | 384 KB | 8 KB | 802.11 b/g/n, 2.4 GHz (Wi-Fi 4) | Bluetooth LE 5 | — | Serial/JTAG | yes | yes (`tasmota32c3.bin`) |
| ESP32-C5 | RISC-V | RV32IMAC | 1 | LP RISC-V | 240 | 384 KB | 320 KB | 16 KB | 802.11 a/b/g/n/ac/ax, 2.4 + 5 GHz (Wi-Fi 6) | Bluetooth LE (Core 6.0 certified) | yes | Serial/JTAG | yes | yes (`tasmota32c5.bin`) |
| ESP32-C6 | RISC-V | RV32IMAC | 1 | LP RISC-V | 160 | 512 KB | 320 KB | 16 KB | 802.11 b/g/n/ax, 2.4 GHz (Wi-Fi 6) | Bluetooth LE 5.3 | yes | Serial/JTAG | yes | yes (`tasmota32c6.bin`) |
| ESP32-H2 | RISC-V | RV32IMAC | 1 | — | 96 | 320 KB | 128 KB | 4 KB | — | Bluetooth LE 5.3 | yes | Serial/JTAG | yes | no |
| ESP32-P4 | RISC-V | RV32IMAFC | 2 | LP RISC-V | 360 | 768 KB | 128 KB | 32 KB | — | — | — | OTG + Serial/JTAG | yes | experimental (`tasmota32p4.bin`) |

| part | family | cores | max MHz | flash in package | PSRAM in package | status | a read names it | notes |
|---|---|---|---|---|---|---|---|---|
| ESP8266EX | ESP8266 | 1 | 160 | — | — | NRND | yes |  |
| ESP8285N08 | ESP8266 | 1 | 160 | 1 MB | — | NRND | yes |  |
| ESP8285H16 | ESP8266 | 1 | 160 | 2 MB | — | NRND | yes |  |
| ESP8285N16 | ESP8266 | 1 | 160 | 2 MB | — | not in the datasheet | yes | esptool's name; the ESP8285 datasheet lists only the N08 and H16. |
| ESP32-D0WD-V3 | ESP32 | 2 | 240 | — | — | active | yes |  |
| ESP32-D0WDR2-V3 | ESP32 | 2 | 240 | — | 2 MB | EOL | yes |  |
| ESP32-D0WDQ6-V3 | ESP32 | 2 | 240 | — | — | NRND | yes |  |
| ESP32-D0WDQ6 | ESP32 | 2 | 240 | — | — | NRND | yes |  |
| ESP32-D0WD | ESP32 | 2 | 240 | — | — | NRND | yes |  |
| ESP32-D2WD | ESP32 | 2 | 160 | 2 MB | — | discontinued | yes |  |
| ESP32-S0WD | ESP32 | 1 | 160 | — | — | NRND | yes |  |
| ESP32-U4WDH | ESP32 | 2 | 240 | 4 MB | — | active | yes |  |
| ESP32-PICO-D4 | ESP32 | 2 | 240 | 4 MB | — | NRND | yes |  |
| ESP32-PICO-V3 | ESP32 | 2 | 240 | 4 MB | — | active | yes |  |
| ESP32-PICO-V3-02 | ESP32 | 2 | 240 | 8 MB | 2 MB | active | yes |  |
| ESP32-S2 | ESP32-S2 | 1 | 240 | — | — | active | yes |  |
| ESP32-S2FH2 | ESP32-S2 | 1 | 240 | 2 MB | — | EOL | yes |  |
| ESP32-S2FH4 | ESP32-S2 | 1 | 240 | 4 MB | — | active | yes |  |
| ESP32-S2FN4R2 | ESP32-S2 | 1 | 240 | 4 MB | 2 MB | active | yes | esptool prints it as 'ESP32-S2FNR2'. |
| ESP32-S2R2 | ESP32-S2 | 1 | 240 | — | 2 MB | active | yes |  |
| ESP32-S3 | ESP32-S3 | 2 | 240 | — | — | active | yes |  |
| ESP32-S3FN8 | ESP32-S3 | 2 | 240 | 8 MB | — | active | yes |  |
| ESP32-S3FH4R2 | ESP32-S3 | 2 | 240 | 4 MB | 2 MB | active | yes |  |
| ESP32-S3R2 | ESP32-S3 | 2 | 240 | — | 2 MB | EOL | yes | Its replacement, the ESP32-S3RH2, has the same eFuse fields and reads as this part. |
| ESP32-S3R8 | ESP32-S3 | 2 | 240 | — | 8 MB | active | yes |  |
| ESP32-S3R8V | ESP32-S3 | 2 | 240 | — | 8 MB | EOL | yes | Told from the S3R8 by its 1.8 V PSRAM (eFuse PSRAM_VENDOR AP_1v8). |
| ESP32-S3R16V | ESP32-S3 | 2 | 240 | — | 16 MB | active | yes |  |
| ESP32-C2 | ESP32-C2 | 1 | 120 | — | — | active | yes | esptool's name for package 0; the parts sold are the ESP8684s. |
| ESP8684H1 | ESP32-C2 | 1 | 120 | 1 MB | — | EOL | yes |  |
| ESP8684H2 | ESP32-C2 | 1 | 120 | 2 MB | — | active | yes |  |
| ESP8684H4 | ESP32-C2 | 1 | 120 | 4 MB | — | active | yes |  |
| ESP32-C3 | ESP32-C3 | 1 | 160 | — | — | active | yes |  |
| ESP32-C3FN4 | ESP32-C3 | 1 | 160 | 4 MB | — | EOL | yes | Flash rated to 85 °C (eFuse FLASH_TEMP 85C). |
| ESP32-C3FH4 | ESP32-C3 | 1 | 160 | 4 MB | — | active | yes | Flash rated to 105 °C (eFuse FLASH_TEMP 105C): the SuperMini's chip. |
| ESP32-C3FH4AZ | ESP32-C3 | 1 | 160 | 4 MB | — | NRND | yes | esptool: 'ESP32-C3 AZ (QFN32)'. |
| ESP32-C3FH4X | ESP32-C3 | 1 | 160 | 4 MB | — | active | yes | Chip revision v1.1. |
| ESP32-C3FH8X | ESP32-C3 | 1 | 160 | 8 MB | — | active | yes | Chip revision v1.1. |
| ESP8685H2 | ESP32-C3 | 1 | 160 | 2 MB | — | EOL | yes |  |
| ESP8685H4 | ESP32-C3 | 1 | 160 | 4 MB | — | active | yes |  |
| ESP32-C5 | ESP32-C5 | 1 | 240 | ? | ? | active | yes | Every C5 reads package 0 and esptool has no map for its flash and PSRAM fields, so a read names the family, not the part. |
| ESP32-C5HR2 | ESP32-C5 | 1 | 240 | — | 2 MB | active | no: it reads as ESP32-C5 |  |
| ESP32-C5HR8 | ESP32-C5 | 1 | 240 | — | 8 MB | active | no: it reads as ESP32-C5 |  |
| ESP32-C5HF4 | ESP32-C5 | 1 | 240 | 4 MB | — | active | no: it reads as ESP32-C5 |  |
| ESP32-C6 | ESP32-C6 | 1 | 160 | — | — | active | yes |  |
| ESP32-C6FH4 | ESP32-C6 | 1 | 160 | 4 MB | — | active | yes |  |
| ESP32-C6FH8 | ESP32-C6 | 1 | 160 | 8 MB | — | active | yes |  |
| ESP32-H2 | ESP32-H2 | 1 | 96 | ? | — | active | yes | Both parts read package 0 and esptool does not decode FLASH_CAP, so a read names the family. |
| ESP32-H2FH2S | ESP32-H2 | 1 | 96 | 2 MB | — | active | no: it reads as ESP32-H2 |  |
| ESP32-H2FH4S | ESP32-H2 | 1 | 96 | 4 MB | — | active | no: it reads as ESP32-H2 |  |
| ESP32-P4 | ESP32-P4 | 2 | 360 | — | ? | active | yes | Every P4 reads package 0 and esptool has no map for PSRAM_CAP, so a read names the family. |
| ESP32-P4NRW16 | ESP32-P4 | 2 | 360 | — | 16 MB | EOL | no: it reads as ESP32-P4 |  |
| ESP32-P4NRW32 | ESP32-P4 | 2 | 360 | — | 32 MB | EOL | no: it reads as ESP32-P4 |  |
| ESP32-P4NRW16X | ESP32-P4 | 2 | 400 | — | 16 MB | active | no: it reads as ESP32-P4 |  |

## Sources

### ESP8266

- **core, cores, max_mhz**: DS8266 § 3.1.1 CPU ('Tensilica L106 32-bit RISC processor ... maximum clock speed of 160 MHz'); Table 1-1
- **sram_kb**: SDK8266 (dram0_0_seg 96 KB + iram0_0_seg 64 KB); the datasheet, § 3.1.2, gives only the ~50 KB left to an application
- **rom_kb, rtc_sram_kb**: DS8266 § 3.1.2 Memory (no size given for either)
- **wifi**: DS8266 § 1.1 Wi-Fi Key Features; Table 1-1 (802.11 b/g/n (HT20); '802.11 n support (2.4 GHz)')
- **bluetooth, ieee802154, usb**: DS8266 § 1.1, Table 1-1: no USB, 802.15.4 or other radio in the datasheet's feature list
- **chip_uid**: ESPTOOL esptool/targets/esp8266.py (no eFuse unique id; espefuse has no ESP8266 table)
- **tasmota**: TASMOTA-OTA (tasmota.bin and its variants); TASMOTA-README
- *note*: The ESP8266EX and ESP8285 datasheets state only what is left to an application (under 50 KB and 75 KB); the 160 KB total is the SDK's memory map. No Espressif document gives the mask ROM's size.
- **ESP8266EX**: part, status: DS8266 cover and release notes v7.1 (NRND); flash_mb: DS8266 § 3.1.3 External Flash
- **ESP8285N08**: part, flash_mb, status: DS8285 § 1 Table 1-1 ESP8285 family (1 MB, -40 to 85 °C); family: DS8285 § 3.1.1 CPU; Table 1-2 (the ESP8266EX core)
- **ESP8285H16**: part, flash_mb, status: DS8285 § 1 Table 1-1 (2 MB, -40 to 105 °C)
- **ESP8285N16**: part, flash_mb: ESPTOOL esptool/targets/esp8266.py#L98-L113 (the name esptool gives a 2 MB, 85 °C ESP8285)

### ESP32

- **core, cores, max_mhz**: DS32 § 4.1.1 CPU ('one or two ... Xtensa 32-bit LX6', up to 240 MHz)
- **lp_core**: DS32 § 4.3.2 Ultra-Low-Power Coprocessor; ULP (the ULP FSM)
- **sram_kb, rom_kb, rtc_sram_kb**: DS32 § 4.1.2 Internal Memory (448 KB ROM, 520 KB SRAM, 8 KB RTC FAST + 8 KB RTC SLOW)
- **wifi**: DS32 Features > Wi-Fi ('802.11b/g/n', 2.4 GHz)
- **bluetooth**: DS32 Features > Bluetooth; § 4.7.3
- **ieee802154, usb**: DS32 Features, Chapter 4: no USB, 802.15.4 or other radio in the datasheet's feature list
- **chip_uid**: EFUSE esp32.yaml (no OPTIONAL_UNIQUE_ID field)
- **radio_cpu**: DS32 § 4.6.5 Wi-Fi MAC ('applies low-level protocol functions automatically'), § 4.7.4 Bluetooth Link Controller: hardware, no processor of the radio's own
- **tasmota**: TASMOTA-OTA (tasmota32.bin; tasmota32solo1.bin for a single core); TASMOTA-DOCS
- **ESP32-D0WD-V3**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison
- **ESP32-D0WDR2-V3**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison
- **ESP32-D0WDQ6-V3**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison
- **ESP32-D0WDQ6**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison
- **ESP32-D0WD**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison
- **ESP32-D2WD**: part, flash_mb: DS32-3.4 § 7 Table 23 Ordering Information; max_mhz: DS32-3.4 § 3.1.1 ('160 MHz for ESP32-S0WD, ESP32-D2WD and ESP32-U4WDH'); status: DS32 revision history v3.7 ('Removed ESP32-D2WD')
- **ESP32-S0WD**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison; max_mhz: DS32 § 4.1.1 CPU ('160 MHz for ESP32-S0WD')
- **ESP32-U4WDH**: part, flash_mb, psram_mb, status: DS32 § 1.2 Table 1-1 ESP32 Series Comparison; cores: DS32 Table 1-1 footnote 3, PCN-2021-021 (dual core since; earlier lots single core, 160 MHz)
- **ESP32-PICO-D4**: part, flash_mb, psram_mb, status: DSPICO § 1.2 Table 1
- **ESP32-PICO-V3**: part, flash_mb, psram_mb: DSPICO § 1.2 Table 1
- **ESP32-PICO-V3-02**: part, flash_mb, psram_mb: DSPICO § 1.2 Table 1

### ESP32-S2

- **core, cores, max_mhz**: DSS2 Features > CPU and Memory ('Xtensa single-core 32-bit LX7 microprocessor, up to 240 MHz'); § 4.1.1.1
- **lp_core**: DSS2 Features (ULP-RISC-V and ULP-FSM coprocessors); § 4.1.1.2 ('these two co-processors cannot work simultaneously': one low-power core)
- **sram_kb, rom_kb, rtc_sram_kb**: DSS2 § 4.1.2.1 Internal Memory
- **wifi, bluetooth, ieee802154**: DSS2 Features > Wi-Fi; cover (2.4 GHz Wi-Fi only)
- **usb**: DSS2 Features ('Full-speed USB OTG'); § 4.2.1.11
- **chip_uid**: EFUSE esp32s2.yaml (OPTIONAL_UNIQUE_ID)
- **tasmota**: TASMOTA-OTA (tasmota32s2.bin, tasmota32s2cdc.bin); TASMOTA-ENV
- **ESP32-S2**: part, flash_mb, psram_mb, status: DSS2 § 1.2 Table 1-1 ESP32-S2 Series Comparison
- **ESP32-S2FH2**: part, flash_mb, psram_mb, status: DSS2 § 1.2 Table 1-1 ESP32-S2 Series Comparison
- **ESP32-S2FH4**: part, flash_mb, psram_mb, status: DSS2 § 1.2 Table 1-1 ESP32-S2 Series Comparison
- **ESP32-S2FN4R2**: part, flash_mb, psram_mb, status: DSS2 § 1.2 Table 1-1 ESP32-S2 Series Comparison
- **ESP32-S2R2**: part, flash_mb, psram_mb, status: DSS2 § 1.2 Table 1-1 ESP32-S2 Series Comparison

### ESP32-S3

- **core, cores, max_mhz**: DSS3 Features > CPU and Memory ('Xtensa dual-core 32-bit LX7', up to 240 MHz); § 4.1.1.1
- **lp_core**: DSS3 Features (ULP-RISC-V, ULP-FSM); § 4.1.1.3 ('these two coprocessors cannot work simultaneously': one low-power core)
- **sram_kb, rom_kb, rtc_sram_kb**: DSS3 § 4.1.2.1 Internal Memory (384 KB ROM, 512 KB SRAM, 8 KB RTC FAST + 8 KB RTC SLOW)
- **wifi**: DSS3 Features > Wi-Fi
- **bluetooth**: DSS3 Features > Bluetooth ('Bluetooth 5, Bluetooth mesh'); § 4.3.3
- **ieee802154**: DSS3 cover (Wi-Fi and Bluetooth LE only)
- **usb**: DSS3 § 4.2.1.7 USB 2.0 OTG Full-Speed; § 4.2.1.8 USB Serial/JTAG
- **chip_uid**: EFUSE esp32s3.yaml (OPTIONAL_UNIQUE_ID)
- **tasmota**: TASMOTA-OTA (tasmota32s3.bin); TASMOTA-ENV
- **ESP32-S3**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison
- **ESP32-S3FN8**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison
- **ESP32-S3FH4R2**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison
- **ESP32-S3R2**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison
- **ESP32-S3R8**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison
- **ESP32-S3R8V**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison
- **ESP32-S3R16V**: part, flash_mb, psram_mb, status: DSS3 § 1.2 Table 1-1 ESP32-S3 Series Comparison

### ESP32-C2

- **core**: DSC2 § 4.1.1.1 ('RV32IMAC ISA')
- **cores, max_mhz**: DSC2 Features > CPU and Memory ('single-core ... up to 120 MHz')
- **sram_kb, rom_kb, rtc_sram_kb**: DSC2 § 4.1.2.1 Internal Memory (576 KB ROM, 272 KB SRAM of which 16 KB is cache; no RTC SRAM listed)
- **wifi**: DSC2 Features > Wi-Fi ('IEEE 802.11b/g/n', 20 MHz in 2.4 GHz)
- **bluetooth**: DSC2 Features > Bluetooth ('Bluetooth 5.3 certified')
- **ieee802154, usb**: DSC2 cover; Features > Advanced Peripheral Interfaces: no USB, 802.15.4 or other radio in the datasheet's feature list
- **chip_uid**: EFUSE esp32c2.yaml (no OPTIONAL_UNIQUE_ID field)
- **tasmota**: TASMOTA-OTA (tasmota32c2.bin); TASMOTA-CHANGELOG 13.1.0.1 ('Experimental support' for the C2); not on TASMOTA-DOCS
- **ESP32-C2**: part: DSC2 cover (the C2 group's parts are the ESP8684 series); ESPTOOL esptool/targets/esp32c2.py#L84-L87 (package 0)
- **ESP8684H1**: part, status: DSC2 revision history v1.5 ('Removed ESP8684H1'); flash_mb: ESPTOOL esptool/targets/esp32c2.py#L95-L100
- **ESP8684H2**: part, flash_mb: DSC2 § 1.2 Table 1 ESP8684 Series Member Comparison
- **ESP8684H4**: part, flash_mb: DSC2 § 1.2 Table 1

### ESP32-C3

- **core, max_mhz**: DSC3 § 4.1.1.1 ('RV32IMC ISA', up to 160 MHz)
- **cores**: DSC3 Features > CPU and Memory (single-core)
- **sram_kb, rom_kb, rtc_sram_kb**: DSC3 § 4.1.2.1 Internal Memory (384 KB ROM, 400 KB SRAM of which 16 KB is cache, 8 KB RTC FAST)
- **wifi, bluetooth**: DSC3 Features ('802.11b/g/n'; 'Bluetooth 5, Bluetooth mesh')
- **ieee802154**: DSC3 cover (Wi-Fi and Bluetooth LE only)
- **usb**: DSC3 Features ('Full-speed USB Serial/JTAG controller')
- **chip_uid**: EFUSE esp32c3.yaml (OPTIONAL_UNIQUE_ID)
- **tasmota**: TASMOTA-OTA (tasmota32c3.bin); TASMOTA-ENV; TASMOTA-DOCS
- **ESP32-C3**: part, flash_mb, status: DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison
- **ESP32-C3FN4**: part, flash_mb, status: DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison
- **ESP32-C3FH4**: part, flash_mb, status: DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison
- **ESP32-C3FH4AZ**: part, flash_mb, status: DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison
- **ESP32-C3FH4X**: part, flash_mb, status: DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison
- **ESP32-C3FH8X**: part, flash_mb, status: DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison
- **ESP8685H2**: part, status: DS8685 revision history v1.3 ('Removed the end-of-life ESP8685H2'); flash_mb: EFUSE esp32c3.yaml FLASH_CAP
- **ESP8685H4**: part, flash_mb: DS8685 § 1.2 Table 1-1

### ESP32-C5

- **core, lp_core**: DSC5 § 4.1.1.1, § 4.1.1.3 ('RV32IMAC ISA')
- **cores, max_mhz, sram_kb, rom_kb, rtc_sram_kb**: DSC5 Features > CPU and Memory (HP 240 MHz, LP 48 MHz, 320 KB ROM, 384 KB HP SRAM, 16 KB LP SRAM)
- **wifi**: DSC5 Features > Wi-Fi ('1T1R in 2.4 and 5 GHz dual band'; 'IEEE 802.11ax-compliant', 'IEEE 802.11ac-compliant', 'Fully compatible with IEEE 802.11a/b/g/n protocol')
- **bluetooth**: DSC5 Features > Bluetooth ('Bluetooth Core 6.0 certified'; the cover says Bluetooth 5 (LE))
- **ieee802154**: DSC5 Features > IEEE 802.15.4 (Thread 1.4, Zigbee 3.0)
- **usb**: DSC5 Features ('USB Serial/JTAG controller')
- **chip_uid**: EFUSE esp32c5.yaml (OPTIONAL_UNIQUE_ID)
- **tasmota**: TASMOTA-OTA (tasmota32c5.bin); TASMOTA-ENV; TASMOTA-CHANGELOG 15.0.1.3
- **ESP32-C5**: part: ESPTOOL esptool/targets/esp32c5.py#L122-L128
- **ESP32-C5HR2**: part, flash_mb, psram_mb: DSC5 § 1.2 Table 1-1
- **ESP32-C5HR8**: part, flash_mb, psram_mb: DSC5 § 1.2 Table 1-1
- **ESP32-C5HF4**: part, flash_mb, psram_mb: DSC5 § 1.2 Table 1-1

### ESP32-C6

- **core, lp_core**: DSC6 § 4.1.1.1, § 4.1.1.3 ('RV32IMAC ISA')
- **cores, max_mhz**: DSC6 Features > CPU and Memory (HP 160 MHz, LP 20 MHz)
- **sram_kb, rom_kb, rtc_sram_kb**: DSC6 § 4.1.2.1 Internal Memory (320 KB ROM, 512 KB HP SRAM, 16 KB LP SRAM)
- **wifi**: DSC6 cover ('2.4 GHz Wi-Fi 6 (802.11ax)'); Features > Wi-Fi ('IEEE 802.11ax-compliant', 'Fully compatible with IEEE 802.11b/g/n protocol')
- **bluetooth**: DSC6 Features ('Bluetooth 5.3 certified')
- **ieee802154**: DSC6 Features (Thread 1.3, Zigbee 3.0)
- **usb**: DSC6 Features (USB Serial/JTAG controller)
- **chip_uid**: EFUSE esp32c6.yaml (OPTIONAL_UNIQUE_ID)
- **radio_cpu**: DSC6 § 4.3.2.2 Wi-Fi MAC; § 4.3.3 ('a hardware link controller, an RF/modem block and a feature-rich software protocol stack'): no processor of the radio's own
- **tasmota**: TASMOTA-OTA (tasmota32c6.bin); TASMOTA-ENV; TASMOTA-DOCS
- **ESP32-C6**: part: DSC6 § 1.2 Table 1-1; ESPTOOL esptool/targets/esp32c6.py#L121-L137 ('ESP32-C6 (QFN40)')
- **ESP32-C6FH4**: part, flash_mb: DSC6 § 1.2 Table 1-1
- **ESP32-C6FH8**: part, flash_mb: DSC6 § 1.2 Table 1-1

### ESP32-H2

- **core, cores, max_mhz**: DSH2 § 4.1.1.1 (a single 'RV32IMAC ISA' core, up to 96 MHz)
- **lp_core**: DSH2 § 4.1.1 (no LP CPU)
- **sram_kb, rom_kb, rtc_sram_kb**: DSH2 § 4.1.2.1 Internal Memory (128 KB ROM, 320 KB HP SRAM, 4 KB LP SRAM)
- **wifi**: DSH2 cover (Bluetooth LE and 802.15.4 only: no Wi-Fi)
- **bluetooth**: DSH2 Features ('Bluetooth 5.3 certified')
- **ieee802154**: DSH2 Features ('802.15.4-2015 compliant')
- **usb**: DSH2 Features (USB Serial/JTAG controller)
- **chip_uid**: EFUSE esp32h2.yaml (OPTIONAL_UNIQUE_ID)
- **tasmota**: TASMOTA-OTA and TASMOTA-ENV (no ESP32-H2 binary or build: Tasmota needs Wi-Fi)
- **ESP32-H2**: part: ESPTOOL esptool/targets/esp32h2.py#L67-L73
- **ESP32-H2FH2S**: part, flash_mb: DSH2 § 1.2 Table 1-1
- **ESP32-H2FH4S**: part, flash_mb: DSH2 § 1.2 Table 1-1

### ESP32-P4

- **core, cores, lp_core**: DSP4 § 4.1.1.1 ('RV32IMAFC'), § 4.1.1.4 (LP RV32IMAC)
- **max_mhz**: DSP4 Features > CPU and Memory (360 MHz for chip revision v1.3; the v3.x parts run at 400, DSP4X § 4.1.1.1)
- **sram_kb, rom_kb, rtc_sram_kb**: DSP4 § 4.1.3.1 Internal Memory (128 KB HP ROM, 768 KB HP L2MEM, 32 KB LP SRAM)
- **wifi, bluetooth, ieee802154**: DSP4 cover (no radio: Wi-Fi comes from a companion chip)
- **usb**: DSP4 Features > Peripherals (USB 2.0 HS OTG, FS OTG, USB Serial/JTAG)
- **chip_uid**: EFUSE esp32p4.yaml (OPTIONAL_UNIQUE_ID)
- **tasmota**: TASMOTA-OTA (tasmota32p4.bin); TASMOTA-DOCS ('support in Tasmota is just beginning')
- **ESP32-P4**: part: ESPTOOL esptool/targets/esp32p4.py#L160-L166
- **ESP32-P4NRW16**: part, psram_mb: DSP4 § 1.2 Table 1-1
- **ESP32-P4NRW32**: part, psram_mb: DSP4 § 1.2 Table 1-1
- **ESP32-P4NRW16X**: part, psram_mb, max_mhz: DSP4X § 1.2 Table 1-1; § 4.1.1.1

### References

- **DS8266**: https://www.espressif.com/sites/default/files/documentation/0a-esp8266ex_datasheet_en.pdf (ESP8266EX Datasheet v7.1)
- **DS8285**: https://www.espressif.com/sites/default/files/documentation/0a-esp8285_datasheet_en.pdf (ESP8285 Datasheet v2.7)
- **SDK8266**: https://github.com/espressif/ESP8266_RTOS_SDK/blob/858c7c2eb9004691f2c736c64a23715f6ea900c4/components/esp8266/ld/esp8266.ld#L25-L43 (the ESP8266 RTOS SDK's linker memory map)
- **DS32**: https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf (ESP32 Series Datasheet v5.3)
- **DS32-3.4**: https://web.archive.org/web/20201112011316id_/https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf (ESP32 Datasheet V3.4, as archived 2020-11-12)
- **DSPICO**: https://www.espressif.com/sites/default/files/documentation/esp32-pico_series_datasheet_en.pdf (ESP32-PICO Series Datasheet v1.3)
- **DSS2**: https://www.espressif.com/sites/default/files/documentation/esp32-s2_datasheet_en.pdf (ESP32-S2 Series Datasheet v1.9)
- **DSS3**: https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf (ESP32-S3 Series Datasheet v2.2)
- **DSC2**: https://documentation.espressif.com/esp8684_datasheet_en.pdf (ESP8684 Series Datasheet v2.3, the ESP32-C2 group's datasheet)
- **DSC3**: https://documentation.espressif.com/esp32-c3_datasheet_en.pdf (ESP32-C3 Series Datasheet v2.4)
- **DS8685**: https://documentation.espressif.com/esp8685_datasheet_en.pdf (ESP8685 Series Datasheet v1.6)
- **DSC5**: https://documentation.espressif.com/esp32-c5_datasheet_en.pdf (ESP32-C5 Series Datasheet v1.5)
- **DSC6**: https://documentation.espressif.com/esp32-c6_datasheet_en.pdf (ESP32-C6 Series Datasheet v1.5)
- **DSH2**: https://documentation.espressif.com/esp32-h2_datasheet_en.pdf (ESP32-H2 Series Datasheet v1.3)
- **DSP4**: https://documentation.espressif.com/esp32-p4-chip-revision-v1.3_datasheet_en.pdf (ESP32-P4 Datasheet for chip revision v1.3, v1.2)
- **DSP4X**: https://documentation.espressif.com/esp32-p4_datasheet_en.pdf (ESP32-P4 Series Datasheet, pre-release v0.7, chip revision v3.x)
- **ULP**: https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/ulp.html (ESP-IDF, ULP Coprocessor Types)
- **ESPTOOL**: https://github.com/espressif/esptool/blob/d69fc940c698f70780231748f3a40d9b49b80fd8/ (esptool at d69fc94, 2026-09-25)
- **EFUSE**: https://github.com/espressif/esptool/blob/d69fc940c698f70780231748f3a40d9b49b80fd8/espefuse/efuse_defs/ (espefuse's eFuse field tables)
- **TASMOTA-OTA**: https://ota.tasmota.com/tasmota32/release/ and https://ota.tasmota.com/tasmota/release/ (Tasmota 15.6.0 release binaries, listed 2026-09-27)
- **TASMOTA-ENV**: https://github.com/arendst/Tasmota/blob/300b3bfb9a7dc9e609be067264000350b5c33d99/platformio_tasmota_env32.ini (Tasmota's ESP32 build environments at 300b3bf, 2026-09-26)
- **TASMOTA-README**: https://github.com/arendst/Tasmota/blob/300b3bfb9a7dc9e609be067264000350b5c33d99/README.md (the chips Tasmota supports)
- **TASMOTA-CHANGELOG**: https://github.com/arendst/Tasmota/blob/300b3bfb9a7dc9e609be067264000350b5c33d99/CHANGELOG.md
- **TASMOTA-DOCS**: https://github.com/tasmota/docs/blob/e202d2ca840513722958c0e6fd08488a0534706b/docs/ESP32.md (tasmota.github.io/docs/ESP32)
