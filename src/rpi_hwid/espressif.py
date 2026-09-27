"""Espressif parts: what each chip *is*, from Espressif's own documents.

The ESP32 labels (``rpi_hwid.esp32_micro``) print two kinds of fact. What
was read off the one device in hand -- its MAC, its revision, its flash and
the serials -- comes from the read. What every chip of its part number
shares -- the instruction set, the cores, the on-chip SRAM, the radios,
whether Tasmota builds for it -- comes from here, keyed by the part the
read names. This table is the one place those facts live; docs/ESPRESSIF.md
is printed from it (``python -m rpi_hwid.espressif``).

The parts are the ones on maker dev boards and in cheap IoT gear: the
ESP8266EX and ESP8285 in most Sonoff, Athom, Shelly and Tuya-rebadged plugs
and switches, the original ESP32 in its many packages, and the S2, S3, C2,
C3, C5, C6, H2 and P4.

Every value carries its source (``Family.sources``, ``Part.sources``): a
datasheet section or table, esptool's source or Tasmota's, each named in
``REFS``. Where Espressif's documents do not give a figure the field is
None and ``notes`` says why; the ESP8266's total SRAM is one that the
datasheets never state, and is taken from the SDK's linker script.

``part_for(...)`` names the part from what the probe read: esptool's chip
name, its description and features, and the eFuse fields. esptool names
most parts outright; for the S3, C2 and C3 it names the die and the part is
the die plus its in-package flash and PSRAM, which eFuse records. A chip
this table does not know is an error (``UnknownPartError``) rather than a
label with its spec strip missing: it is one row to add, with its source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- sources -------------------------------------------------------------------

ESPTOOL = "https://github.com/espressif/esptool/blob/d69fc940c698f70780231748f3a40d9b49b80fd8/"
TASMOTA = "https://github.com/arendst/Tasmota/blob/300b3bfb9a7dc9e609be067264000350b5c33d99/"
DOCS = "https://documentation.espressif.com/"
ESPRESSIF_DOCS = "https://www.espressif.com/sites/default/files/documentation/"

REFS: dict[str, str] = {
    "DS8266": ESPRESSIF_DOCS + "0a-esp8266ex_datasheet_en.pdf (ESP8266EX Datasheet v7.1)",
    "DS8285": ESPRESSIF_DOCS + "0a-esp8285_datasheet_en.pdf (ESP8285 Datasheet v2.7)",
    "SDK8266": "https://github.com/espressif/ESP8266_RTOS_SDK/blob/"
               "858c7c2eb9004691f2c736c64a23715f6ea900c4/components/esp8266/ld/esp8266.ld"
               "#L25-L43 (the ESP8266 RTOS SDK's linker memory map)",
    "DS32": ESPRESSIF_DOCS + "esp32_datasheet_en.pdf (ESP32 Series Datasheet v5.3)",
    "DS32-3.4": "https://web.archive.org/web/20201112011316id_/https://www.espressif.com/"
                "sites/default/files/documentation/esp32_datasheet_en.pdf "
                "(ESP32 Datasheet V3.4, as archived 2020-11-12)",
    "DSPICO": ESPRESSIF_DOCS + "esp32-pico_series_datasheet_en.pdf "
              "(ESP32-PICO Series Datasheet v1.3)",
    "DSS2": ESPRESSIF_DOCS + "esp32-s2_datasheet_en.pdf (ESP32-S2 Series Datasheet v1.9)",
    "DSS3": ESPRESSIF_DOCS + "esp32-s3_datasheet_en.pdf (ESP32-S3 Series Datasheet v2.2)",
    "DSC2": DOCS + "esp8684_datasheet_en.pdf (ESP8684 Series Datasheet v2.3, the "
                   "ESP32-C2 group's datasheet)",
    "DSC3": DOCS + "esp32-c3_datasheet_en.pdf (ESP32-C3 Series Datasheet v2.4)",
    "DS8685": DOCS + "esp8685_datasheet_en.pdf (ESP8685 Series Datasheet v1.6)",
    "DSC5": DOCS + "esp32-c5_datasheet_en.pdf (ESP32-C5 Series Datasheet v1.5)",
    "DSC6": DOCS + "esp32-c6_datasheet_en.pdf (ESP32-C6 Series Datasheet v1.5)",
    "DSH2": DOCS + "esp32-h2_datasheet_en.pdf (ESP32-H2 Series Datasheet v1.3)",
    "DSP4": DOCS + "esp32-p4-chip-revision-v1.3_datasheet_en.pdf "
                   "(ESP32-P4 Datasheet for chip revision v1.3, v1.2)",
    "DSP4X": DOCS + "esp32-p4_datasheet_en.pdf (ESP32-P4 Series Datasheet, "
                    "pre-release v0.7, chip revision v3.x)",
    "ULP": "https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/"
           "system/ulp.html (ESP-IDF, ULP Coprocessor Types)",
    "ESPTOOL": ESPTOOL + " (esptool at d69fc94, 2026-09-25)",
    "EFUSE": ESPTOOL + "espefuse/efuse_defs/ (espefuse's eFuse field tables)",
    "TASMOTA-OTA": "https://ota.tasmota.com/tasmota32/release/ and "
                   "https://ota.tasmota.com/tasmota/release/ (Tasmota 15.6.0 release "
                   "binaries, listed 2026-09-27)",
    "TASMOTA-ENV": TASMOTA + "platformio_tasmota_env32.ini (Tasmota's ESP32 build "
                   "environments at 300b3bf, 2026-09-26)",
    "TASMOTA-README": TASMOTA + "README.md (the chips Tasmota supports)",
    "TASMOTA-CHANGELOG": TASMOTA + "CHANGELOG.md",
    "TASMOTA-DOCS": "https://github.com/tasmota/docs/blob/"
                    "e202d2ca840513722958c0e6fd08488a0534706b/docs/ESP32.md "
                    "(tasmota.github.io/docs/ESP32)",
}

# --- the record ------------------------------------------------------------------

XTENSA, RISCV = "Xtensa", "RISC-V"

# Tasmota's support, as one of three words: "release" when its release list
# carries a binary for the chip, "experimental" when that binary is marked as
# the start of support in Tasmota's own docs or changelog, "none" otherwise.
# The label's Tasmota glyph is drawn for "release" and "experimental" alike:
# either way there is an official binary to flash.
TASMOTA_BINARY = {"release", "experimental"}


@dataclass(frozen=True)
class Family:
    """What every part of one die shares."""

    name: str
    isa: str                          # XTENSA or RISCV
    core: str                         # "Xtensa LX6", "RV32IMC"
    cores: int                        # high-performance cores
    lp_core: str | None               # the low-power coprocessor, if any
    max_mhz: int
    sram_kb: int                      # on-chip SRAM, as the datasheet totals it
    rom_kb: int | None
    rtc_sram_kb: float | None         # RTC / LP SRAM
    wifi: str | None                  # "802.11 b/g/n"
    wifi_gen: int | None              # 4, 6
    wifi_bands: str | None            # "2.4 GHz"
    bluetooth: str | None             # "Bluetooth 4.2 BR/EDR + LE"
    ieee802154: bool
    usb: str                          # "none", "Serial/JTAG", "OTG + Serial/JTAG"
    chip_uid: bool                    # eFuse holds an OPTIONAL_UNIQUE_ID
    tasmota: str                      # "release", "experimental" or "none"
    tasmota_build: str | None         # the Tasmota binary for it
    sources: Mapping[str, str]
    notes: str = ""


@dataclass(frozen=True)
class Part:
    """One orderable part: its family and what is in its package."""

    part: str
    family: Family
    flash_mb: float | None = 0        # in the package; None where eFuse cannot tell
    psram_mb: float | None = 0
    cores: int | None = None          # where the part differs from its family
    max_mhz: int | None = None
    status: str = "active"
    sources: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""
    # a variant eFuse cannot tell from its family's generic entry: listed for
    # the table, never the answer part_for() gives
    listed_only: bool = False

    @property
    def n_cores(self) -> int:
        return self.cores or self.family.cores

    @property
    def mhz(self) -> int:
        return self.max_mhz or self.family.max_mhz


# --- the families ----------------------------------------------------------------

_NONE = "no USB, 802.15.4 or other radio in the datasheet's feature list"

ESP8266 = Family(
    name="ESP8266", isa=XTENSA, core="Xtensa L106", cores=1, lp_core=None, max_mhz=160,
    sram_kb=160, rom_kb=None, rtc_sram_kb=None, wifi="802.11 b/g/n (HT20)", wifi_gen=4,
    wifi_bands="2.4 GHz", bluetooth=None, ieee802154=False, usb="none", chip_uid=False,
    tasmota="release", tasmota_build="tasmota.bin",
    sources={
        "core, cores, max_mhz": "DS8266 § 3.1.1 CPU ('Tensilica L106 32-bit RISC processor "
                                "... maximum clock speed of 160 MHz'); Table 1-1",
        "sram_kb": "SDK8266 (dram0_0_seg 96 KB + iram0_0_seg 64 KB); the datasheet, "
                   "§ 3.1.2, gives only the ~50 KB left to an application",
        "rom_kb, rtc_sram_kb": "DS8266 § 3.1.2 Memory (no size given for either)",
        "wifi": "DS8266 § 1.1 Wi-Fi Key Features; Table 1-1 (802.11 b/g/n (HT20))",
        "bluetooth, ieee802154, usb": "DS8266 § 1.1, Table 1-1: " + _NONE,
        "chip_uid": "ESPTOOL esptool/targets/esp8266.py (no eFuse unique id; "
                    "espefuse has no ESP8266 table)",
        "tasmota": "TASMOTA-OTA (tasmota.bin and its variants); TASMOTA-README",
    },
    notes="The ESP8266EX and ESP8285 datasheets state only what is left to an "
          "application (under 50 KB and 75 KB); the 160 KB total is the SDK's memory map. "
          "No Espressif document gives the mask ROM's size.")

ESP32 = Family(
    name="ESP32", isa=XTENSA, core="Xtensa LX6", cores=2, lp_core="ULP-FSM", max_mhz=240,
    sram_kb=520, rom_kb=448, rtc_sram_kb=16, wifi="802.11 b/g/n", wifi_gen=4,
    wifi_bands="2.4 GHz", bluetooth="Bluetooth 4.2 BR/EDR + LE", ieee802154=False,
    usb="none", chip_uid=False, tasmota="release", tasmota_build="tasmota32.bin",
    sources={
        "core, cores, max_mhz": "DS32 § 4.1.1 CPU ('one or two ... Xtensa 32-bit LX6', "
                                "up to 240 MHz)",
        "lp_core": "DS32 § 4.3.2 Ultra-Low-Power Coprocessor; ULP (the ULP FSM)",
        "sram_kb, rom_kb, rtc_sram_kb": "DS32 § 4.1.2 Internal Memory (448 KB ROM, 520 KB "
                                        "SRAM, 8 KB RTC FAST + 8 KB RTC SLOW)",
        "wifi": "DS32 Features > Wi-Fi ('802.11b/g/n', 2.4 GHz)",
        "bluetooth": "DS32 Features > Bluetooth; § 4.7.3",
        "ieee802154, usb": "DS32 Features, Chapter 4: " + _NONE,
        "chip_uid": "EFUSE esp32.yaml (no OPTIONAL_UNIQUE_ID field)",
        "tasmota": "TASMOTA-OTA (tasmota32.bin; tasmota32solo1.bin for a single core); "
                   "TASMOTA-DOCS",
    })

ESP32_S2 = Family(
    name="ESP32-S2", isa=XTENSA, core="Xtensa LX7", cores=1, lp_core="ULP-RISC-V + ULP-FSM",
    max_mhz=240, sram_kb=320, rom_kb=128, rtc_sram_kb=16, wifi="802.11 b/g/n", wifi_gen=4,
    wifi_bands="2.4 GHz", bluetooth=None, ieee802154=False, usb="OTG", chip_uid=True,
    tasmota="release", tasmota_build="tasmota32s2.bin",
    sources={
        "core, cores, max_mhz": "DSS2 Features > CPU and Memory ('Xtensa single-core 32-bit "
                                "LX7 microprocessor, up to 240 MHz'); § 4.1.1.1",
        "lp_core": "DSS2 Features (ULP-RISC-V and ULP-FSM coprocessors); § 4.1.1.2",
        "sram_kb, rom_kb, rtc_sram_kb": "DSS2 § 4.1.2.1 Internal Memory",
        "wifi, bluetooth, ieee802154": "DSS2 Features > Wi-Fi; cover (2.4 GHz Wi-Fi only)",
        "usb": "DSS2 Features ('Full-speed USB OTG'); § 4.2.1.11",
        "chip_uid": "EFUSE esp32s2.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA (tasmota32s2.bin, tasmota32s2cdc.bin); TASMOTA-ENV",
    })

ESP32_S3 = Family(
    name="ESP32-S3", isa=XTENSA, core="Xtensa LX7", cores=2, lp_core="ULP-RISC-V + ULP-FSM",
    max_mhz=240, sram_kb=512, rom_kb=384, rtc_sram_kb=16, wifi="802.11 b/g/n", wifi_gen=4,
    wifi_bands="2.4 GHz", bluetooth="Bluetooth LE 5", ieee802154=False,
    usb="OTG + Serial/JTAG", chip_uid=True, tasmota="release",
    tasmota_build="tasmota32s3.bin",
    sources={
        "core, cores, max_mhz": "DSS3 Features > CPU and Memory ('Xtensa dual-core 32-bit "
                                "LX7', up to 240 MHz); § 4.1.1.1",
        "lp_core": "DSS3 Features (ULP-RISC-V, ULP-FSM); § 4.1.1.3",
        "sram_kb, rom_kb, rtc_sram_kb": "DSS3 § 4.1.2.1 Internal Memory (384 KB ROM, 512 KB "
                                        "SRAM, 8 KB RTC FAST + 8 KB RTC SLOW)",
        "wifi": "DSS3 Features > Wi-Fi",
        "bluetooth": "DSS3 Features > Bluetooth ('Bluetooth 5, Bluetooth mesh'); § 4.3.3",
        "ieee802154": "DSS3 cover (Wi-Fi and Bluetooth LE only)",
        "usb": "DSS3 § 4.2.1.7 USB 2.0 OTG Full-Speed; § 4.2.1.8 USB Serial/JTAG",
        "chip_uid": "EFUSE esp32s3.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA (tasmota32s3.bin); TASMOTA-ENV",
    })

ESP32_C2 = Family(
    name="ESP32-C2", isa=RISCV, core="RV32IMAC", cores=1, lp_core=None, max_mhz=120,
    sram_kb=272, rom_kb=576, rtc_sram_kb=None, wifi="802.11 b/g/n", wifi_gen=4,
    wifi_bands="2.4 GHz", bluetooth="Bluetooth LE 5.3", ieee802154=False, usb="none",
    chip_uid=False, tasmota="experimental", tasmota_build="tasmota32c2.bin",
    sources={
        "core": "DSC2 § 4.1.1.1 ('RV32IMAC ISA')",
        "cores, max_mhz": "DSC2 Features > CPU and Memory ('single-core ... up to 120 MHz')",
        "sram_kb, rom_kb, rtc_sram_kb": "DSC2 § 4.1.2.1 Internal Memory (576 KB ROM, 272 KB "
                                        "SRAM of which 16 KB is cache; no RTC SRAM listed)",
        "wifi": "DSC2 Features > Wi-Fi ('IEEE 802.11b/g/n', 20 MHz in 2.4 GHz)",
        "bluetooth": "DSC2 Features > Bluetooth ('Bluetooth 5.3 certified')",
        "ieee802154, usb": "DSC2 cover; Features > Advanced Peripheral Interfaces: " + _NONE,
        "chip_uid": "EFUSE esp32c2.yaml (no OPTIONAL_UNIQUE_ID field)",
        "tasmota": "TASMOTA-OTA (tasmota32c2.bin); TASMOTA-CHANGELOG 13.1.0.1 ('Experimental "
                   "support' for the C2); not on TASMOTA-DOCS",
    })

ESP32_C3 = Family(
    name="ESP32-C3", isa=RISCV, core="RV32IMC", cores=1, lp_core=None, max_mhz=160,
    sram_kb=400, rom_kb=384, rtc_sram_kb=8, wifi="802.11 b/g/n", wifi_gen=4,
    wifi_bands="2.4 GHz", bluetooth="Bluetooth LE 5", ieee802154=False, usb="Serial/JTAG",
    chip_uid=True, tasmota="release", tasmota_build="tasmota32c3.bin",
    sources={
        "core, max_mhz": "DSC3 § 4.1.1.1 ('RV32IMC ISA', up to 160 MHz)",
        "cores": "DSC3 Features > CPU and Memory (single-core)",
        "sram_kb, rom_kb, rtc_sram_kb": "DSC3 § 4.1.2.1 Internal Memory (384 KB ROM, 400 KB "
                                        "SRAM of which 16 KB is cache, 8 KB RTC FAST)",
        "wifi, bluetooth": "DSC3 Features ('802.11b/g/n'; 'Bluetooth 5, Bluetooth mesh')",
        "ieee802154": "DSC3 cover (Wi-Fi and Bluetooth LE only)",
        "usb": "DSC3 Features ('Full-speed USB Serial/JTAG controller')",
        "chip_uid": "EFUSE esp32c3.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA (tasmota32c3.bin); TASMOTA-ENV; TASMOTA-DOCS",
    })

ESP32_C5 = Family(
    name="ESP32-C5", isa=RISCV, core="RV32IMAC", cores=1, lp_core="LP RISC-V", max_mhz=240,
    sram_kb=384, rom_kb=320, rtc_sram_kb=16, wifi="802.11 a/b/g/n/ac/ax", wifi_gen=6,
    wifi_bands="2.4 + 5 GHz", bluetooth="Bluetooth LE (Core 6.0 certified)",
    ieee802154=True, usb="Serial/JTAG", chip_uid=True, tasmota="release",
    tasmota_build="tasmota32c5.bin",
    sources={
        "core, lp_core": "DSC5 § 4.1.1.1, § 4.1.1.3 ('RV32IMAC ISA')",
        "cores, max_mhz, sram_kb, rom_kb, rtc_sram_kb":
            "DSC5 Features > CPU and Memory (HP 240 MHz, LP 48 MHz, 320 KB ROM, 384 KB HP "
            "SRAM, 16 KB LP SRAM)",
        "wifi": "DSC5 Features > Wi-Fi (2.4 and 5 GHz dual band, 802.11ax/ac/a/b/g/n)",
        "bluetooth": "DSC5 Features > Bluetooth ('Bluetooth Core 6.0 certified'; the cover "
                     "says Bluetooth 5 (LE))",
        "ieee802154": "DSC5 Features > IEEE 802.15.4 (Thread 1.4, Zigbee 3.0)",
        "usb": "DSC5 Features ('USB Serial/JTAG controller')",
        "chip_uid": "EFUSE esp32c5.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA (tasmota32c5.bin); TASMOTA-ENV; TASMOTA-CHANGELOG 15.0.1.3",
    })

ESP32_C6 = Family(
    name="ESP32-C6", isa=RISCV, core="RV32IMAC", cores=1, lp_core="LP RISC-V", max_mhz=160,
    sram_kb=512, rom_kb=320, rtc_sram_kb=16, wifi="802.11 b/g/n/ax", wifi_gen=6,
    wifi_bands="2.4 GHz", bluetooth="Bluetooth LE 5.3", ieee802154=True, usb="Serial/JTAG",
    chip_uid=True, tasmota="release", tasmota_build="tasmota32c6.bin",
    sources={
        "core, lp_core": "DSC6 § 4.1.1.1, § 4.1.1.3 ('RV32IMAC ISA')",
        "cores, max_mhz": "DSC6 Features > CPU and Memory (HP 160 MHz, LP 20 MHz)",
        "sram_kb, rom_kb, rtc_sram_kb": "DSC6 § 4.1.2.1 Internal Memory (320 KB ROM, 512 KB "
                                        "HP SRAM, 16 KB LP SRAM)",
        "wifi": "DSC6 cover; Features > Wi-Fi (802.11ax, 2.4 GHz)",
        "bluetooth": "DSC6 Features ('Bluetooth 5.3 certified')",
        "ieee802154": "DSC6 Features (Thread 1.3, Zigbee 3.0)",
        "usb": "DSC6 Features (USB Serial/JTAG controller)",
        "chip_uid": "EFUSE esp32c6.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA (tasmota32c6.bin); TASMOTA-ENV; TASMOTA-DOCS",
    })

ESP32_H2 = Family(
    name="ESP32-H2", isa=RISCV, core="RV32IMAC", cores=1, lp_core=None, max_mhz=96,
    sram_kb=320, rom_kb=128, rtc_sram_kb=4, wifi=None, wifi_gen=None, wifi_bands=None,
    bluetooth="Bluetooth LE 5.3", ieee802154=True, usb="Serial/JTAG", chip_uid=True,
    tasmota="none", tasmota_build=None,
    sources={
        "core, cores, max_mhz": "DSH2 § 4.1.1.1 (a single 'RV32IMAC ISA' core, up to "
                                "96 MHz)",
        "lp_core": "DSH2 § 4.1.1 (no LP CPU)",
        "sram_kb, rom_kb, rtc_sram_kb": "DSH2 § 4.1.2.1 Internal Memory (128 KB ROM, 320 KB "
                                        "HP SRAM, 4 KB LP SRAM)",
        "wifi": "DSH2 cover (Bluetooth LE and 802.15.4 only: no Wi-Fi)",
        "bluetooth": "DSH2 Features ('Bluetooth 5.3 certified')",
        "ieee802154": "DSH2 Features ('802.15.4-2015 compliant')",
        "usb": "DSH2 Features (USB Serial/JTAG controller)",
        "chip_uid": "EFUSE esp32h2.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA and TASMOTA-ENV (no ESP32-H2 binary or build: Tasmota "
                   "needs Wi-Fi)",
    })

ESP32_P4 = Family(
    name="ESP32-P4", isa=RISCV, core="RV32IMAFC", cores=2, lp_core="LP RISC-V", max_mhz=360,
    sram_kb=768, rom_kb=128, rtc_sram_kb=32, wifi=None, wifi_gen=None, wifi_bands=None,
    bluetooth=None, ieee802154=False, usb="OTG + Serial/JTAG", chip_uid=True,
    tasmota="experimental", tasmota_build="tasmota32p4.bin",
    sources={
        "core, cores, lp_core": "DSP4 § 4.1.1.1 ('RV32IMAFC'), § 4.1.1.4 (LP RV32IMAC)",
        "max_mhz": "DSP4 Features > CPU and Memory (360 MHz for chip revision v1.3; the "
                   "v3.x parts run at 400, DSP4X § 4.1.1.1)",
        "sram_kb, rom_kb, rtc_sram_kb": "DSP4 § 4.1.3.1 Internal Memory (128 KB HP ROM, 768 KB "
                                        "HP L2MEM, 32 KB LP SRAM)",
        "wifi, bluetooth, ieee802154": "DSP4 cover (no radio: Wi-Fi comes from a companion "
                                       "chip)",
        "usb": "DSP4 Features > Peripherals (USB 2.0 HS OTG, FS OTG, USB Serial/JTAG)",
        "chip_uid": "EFUSE esp32p4.yaml (OPTIONAL_UNIQUE_ID)",
        "tasmota": "TASMOTA-OTA (tasmota32p4.bin); TASMOTA-DOCS ('support in Tasmota is just "
                   "beginning')",
    })

FAMILIES = (ESP8266, ESP32, ESP32_S2, ESP32_S3, ESP32_C2, ESP32_C3, ESP32_C5, ESP32_C6,
            ESP32_H2, ESP32_P4)

# --- the parts -------------------------------------------------------------------


def _parts() -> tuple[Part, ...]:
    t11 = {"part, flash_mb, psram_mb, status": "DS32 § 1.2 Table 1-1 ESP32 Series Comparison"}
    s2 = {"part, flash_mb, psram_mb, status": "DSS2 § 1.2 Table 1-1 ESP32-S2 Series "
                                             "Comparison"}
    s3 = {"part, flash_mb, psram_mb, status": "DSS3 § 1.2 Table 1-1 ESP32-S3 Series "
                                             "Comparison"}
    c3 = {"part, flash_mb, status": "DSC3 § 1.2 Table 1-1 ESP32-C3 Series Comparison"}
    return (
        Part("ESP8266EX", ESP8266, status="NRND",
             sources={"part, status": "DS8266 cover and release notes v7.1 (NRND)",
                      "flash_mb": "DS8266 § 3.1.3 External Flash"}),
        Part("ESP8285N08", ESP8266, flash_mb=1, status="NRND",
             sources={"part, flash_mb, status": "DS8285 § 1 Table 1-1 ESP8285 family "
                                               "(1 MB, -40 to 85 °C)",
                      "family": "DS8285 § 3.1.1 CPU; Table 1-2 (the ESP8266EX core)"}),
        Part("ESP8285H16", ESP8266, flash_mb=2, status="NRND",
             sources={"part, flash_mb, status": "DS8285 § 1 Table 1-1 (2 MB, -40 to 105 °C)"}),
        Part("ESP8285N16", ESP8266, flash_mb=2, status="not in the datasheet",
             sources={"part, flash_mb": "ESPTOOL esptool/targets/esp8266.py#L98-L113 (the "
                                        "name esptool gives a 2 MB, 85 °C ESP8285)"},
             notes="esptool's name; the ESP8285 datasheet lists only the N08 and H16."),

        Part("ESP32-D0WD-V3", ESP32, sources=t11),
        Part("ESP32-D0WDR2-V3", ESP32, psram_mb=2, status="EOL", sources=t11),
        Part("ESP32-D0WDQ6-V3", ESP32, status="NRND", sources=t11),
        Part("ESP32-D0WDQ6", ESP32, status="NRND", sources=t11),
        Part("ESP32-D0WD", ESP32, status="NRND", sources=t11),
        Part("ESP32-D2WD", ESP32, flash_mb=2, max_mhz=160, status="discontinued",
             sources={"part, flash_mb": "DS32-3.4 § 7 Table 23 Ordering Information",
                      "max_mhz": "DS32-3.4 § 3.1.1 ('160 MHz for ESP32-S0WD, ESP32-D2WD "
                                 "and ESP32-U4WDH')",
                      "status": "DS32 revision history v3.7 ('Removed ESP32-D2WD')"}),
        Part("ESP32-S0WD", ESP32, cores=1, max_mhz=160, status="NRND",
             sources=dict(t11, max_mhz="DS32 § 4.1.1 CPU ('160 MHz for ESP32-S0WD')")),
        Part("ESP32-U4WDH", ESP32, flash_mb=4,
             sources=dict(t11, cores="DS32 Table 1-1 footnote 3, PCN-2021-021 "
                                           "(dual core since; earlier lots single core, "
                                           "160 MHz)")),
        Part("ESP32-PICO-D4", ESP32, flash_mb=4, status="NRND",
             sources={"part, flash_mb, psram_mb, status": "DSPICO § 1.2 Table 1"}),
        Part("ESP32-PICO-V3", ESP32, flash_mb=4,
             sources={"part, flash_mb, psram_mb": "DSPICO § 1.2 Table 1"}),
        Part("ESP32-PICO-V3-02", ESP32, flash_mb=8, psram_mb=2,
             sources={"part, flash_mb, psram_mb": "DSPICO § 1.2 Table 1"}),

        Part("ESP32-S2", ESP32_S2, sources=s2),
        Part("ESP32-S2FH2", ESP32_S2, flash_mb=2, status="EOL", sources=s2),
        Part("ESP32-S2FH4", ESP32_S2, flash_mb=4, sources=s2),
        Part("ESP32-S2FN4R2", ESP32_S2, flash_mb=4, psram_mb=2, sources=s2,
             notes="esptool prints it as 'ESP32-S2FNR2'."),
        Part("ESP32-S2R2", ESP32_S2, psram_mb=2, sources=s2),

        Part("ESP32-S3", ESP32_S3, sources=s3),
        Part("ESP32-S3FN8", ESP32_S3, flash_mb=8, sources=s3),
        Part("ESP32-S3FH4R2", ESP32_S3, flash_mb=4, psram_mb=2, sources=s3),
        Part("ESP32-S3R2", ESP32_S3, psram_mb=2, status="EOL", sources=s3,
             notes="Its replacement, the ESP32-S3RH2, has the same eFuse fields and "
                   "reads as this part."),
        Part("ESP32-S3R8", ESP32_S3, psram_mb=8, sources=s3),
        Part("ESP32-S3R8V", ESP32_S3, psram_mb=8, status="EOL", sources=s3,
             notes="Told from the S3R8 by its 1.8 V PSRAM (eFuse PSRAM_VENDOR AP_1v8)."),
        Part("ESP32-S3R16V", ESP32_S3, psram_mb=16, sources=s3),

        Part("ESP32-C2", ESP32_C2,
             sources={"part": "DSC2 cover (the C2 group's parts are the ESP8684 series); "
                              "ESPTOOL esptool/targets/esp32c2.py#L84-L87 (package 0)"},
             notes="esptool's name for package 0; the parts sold are the ESP8684s."),
        Part("ESP8684H1", ESP32_C2, flash_mb=1, status="EOL",
             sources={"part, status": "DSC2 revision history v1.5 ('Removed ESP8684H1')",
                      "flash_mb": "ESPTOOL esptool/targets/esp32c2.py#L95-L100"}),
        Part("ESP8684H2", ESP32_C2, flash_mb=2,
             sources={"part, flash_mb": "DSC2 § 1.2 Table 1 ESP8684 Series Member Comparison"}),
        Part("ESP8684H4", ESP32_C2, flash_mb=4,
             sources={"part, flash_mb": "DSC2 § 1.2 Table 1"}),

        Part("ESP32-C3", ESP32_C3, sources=c3),
        Part("ESP32-C3FN4", ESP32_C3, flash_mb=4, status="EOL", sources=c3,
             notes="Flash rated to 85 °C (eFuse FLASH_TEMP 85C)."),
        Part("ESP32-C3FH4", ESP32_C3, flash_mb=4, sources=c3,
             notes="Flash rated to 105 °C (eFuse FLASH_TEMP 105C): the SuperMini's chip."),
        Part("ESP32-C3FH4AZ", ESP32_C3, flash_mb=4, status="NRND", sources=c3,
             notes="esptool: 'ESP32-C3 AZ (QFN32)'."),
        Part("ESP32-C3FH4X", ESP32_C3, flash_mb=4, sources=c3,
             notes="Chip revision v1.1."),
        Part("ESP32-C3FH8X", ESP32_C3, flash_mb=8, sources=c3,
             notes="Chip revision v1.1."),
        Part("ESP8685H2", ESP32_C3, flash_mb=2, status="EOL",
             sources={"part, status": "DS8685 revision history v1.3 ('Removed the "
                                      "end-of-life ESP8685H2')",
                      "flash_mb": "EFUSE esp32c3.yaml FLASH_CAP"}),
        Part("ESP8685H4", ESP32_C3, flash_mb=4,
             sources={"part, flash_mb": "DS8685 § 1.2 Table 1-1"}),

        Part("ESP32-C5", ESP32_C5, flash_mb=None, psram_mb=None,
             sources={"part": "ESPTOOL esptool/targets/esp32c5.py#L122-L128"},
             notes="Every C5 reads package 0 and esptool has no map for its flash and "
                   "PSRAM fields, so a read names the family, not the part."),
        Part("ESP32-C5HR2", ESP32_C5, psram_mb=2, listed_only=True,
             sources={"part, flash_mb, psram_mb": "DSC5 § 1.2 Table 1-1"}),
        Part("ESP32-C5HR8", ESP32_C5, psram_mb=8, listed_only=True,
             sources={"part, flash_mb, psram_mb": "DSC5 § 1.2 Table 1-1"}),
        Part("ESP32-C5HF4", ESP32_C5, flash_mb=4, listed_only=True,
             sources={"part, flash_mb, psram_mb": "DSC5 § 1.2 Table 1-1"}),

        Part("ESP32-C6", ESP32_C6,
             sources={"part": "DSC6 § 1.2 Table 1-1; ESPTOOL esptool/targets/esp32c6.py"
                              "#L121-L137 ('ESP32-C6 (QFN40)')"}),
        Part("ESP32-C6FH4", ESP32_C6, flash_mb=4,
             sources={"part, flash_mb": "DSC6 § 1.2 Table 1-1"}),
        Part("ESP32-C6FH8", ESP32_C6, flash_mb=8,
             sources={"part, flash_mb": "DSC6 § 1.2 Table 1-1"}),

        Part("ESP32-H2", ESP32_H2, flash_mb=None,
             sources={"part": "ESPTOOL esptool/targets/esp32h2.py#L67-L73"},
             notes="Both parts read package 0 and esptool does not decode FLASH_CAP, so a "
                   "read names the family."),
        Part("ESP32-H2FH2S", ESP32_H2, flash_mb=2, listed_only=True,
             sources={"part, flash_mb": "DSH2 § 1.2 Table 1-1"}),
        Part("ESP32-H2FH4S", ESP32_H2, flash_mb=4, listed_only=True,
             sources={"part, flash_mb": "DSH2 § 1.2 Table 1-1"}),

        Part("ESP32-P4", ESP32_P4, psram_mb=None,
             sources={"part": "ESPTOOL esptool/targets/esp32p4.py#L160-L166"},
             notes="Every P4 reads package 0 and esptool has no map for PSRAM_CAP, so a "
                   "read names the family."),
        Part("ESP32-P4NRW16", ESP32_P4, psram_mb=16, status="EOL", listed_only=True,
             sources={"part, psram_mb": "DSP4 § 1.2 Table 1-1"}),
        Part("ESP32-P4NRW32", ESP32_P4, psram_mb=32, status="EOL", listed_only=True,
             sources={"part, psram_mb": "DSP4 § 1.2 Table 1-1"}),
        Part("ESP32-P4NRW16X", ESP32_P4, psram_mb=16, max_mhz=400, listed_only=True,
             sources={"part, psram_mb, max_mhz": "DSP4X § 1.2 Table 1-1; § 4.1.1.1"}),
    )


PARTS: tuple[Part, ...] = _parts()
BY_NAME: dict[str, Part] = {p.part: p for p in PARTS}


class UnknownPartError(ValueError):
    """A chip this table has no row for."""


# --- naming the part from a read ------------------------------------------------


def _embedded(features: tuple[str, ...] | list[str], what: str) -> tuple[int, str] | None:
    """(8, 'AP_3v3') from an 'Embedded PSRAM 8MB (AP_3v3)' feature."""
    for f in features:
        m = re.match(rf"^Embedded {what} (\d+)MB(?: \(([^)]*)\))?$", f)
        if m:
            return int(m.group(1)), m.group(2) or ""
    return None


def _efuse_int(efuse: Mapping[str, Any], name: str) -> int | None:
    v = efuse.get(name)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    return None


def _flash_temp(efuse: Mapping[str, Any]) -> str | None:
    """'H' for flash rated to 105 °C, 'N' for 85 °C: the C3's FLASH_TEMP,
    {1: 105C, 2: 85C} in espefuse's esp32c3.yaml, as a number or its name."""
    v = efuse.get("FLASH_TEMP")
    if v in (1, "105C"):
        return "H"
    if v in (2, "85C"):
        return "N"
    return None


def _revision(rev: str | None) -> tuple[int, int]:
    m = re.match(r"^v?(\d+)\.(\d+)$", rev or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def part_name(chip: str, description: str = "", features: tuple[str, ...] = (),
              efuse: Mapping[str, Any] | None = None, revision: str | None = None) -> str:
    """The part number a read names; see the module docstring."""
    efuse = efuse or {}
    flash = _embedded(features, "Flash")
    psram = _embedded(features, "PSRAM")
    fmb = flash[0] if flash else 0
    if chip == "ESP32-S2FNR2":
        return "ESP32-S2FN4R2"
    if chip == "ESP32-S3":
        pmb, vendor = psram if psram else (0, "")
        name = "ESP32-S3"
        if fmb:
            name += f"F{'N' if fmb == 8 else 'H'}{fmb}"
        if pmb:
            name += f"R{pmb}" + ("V" if vendor == "AP_1v8" else "")
        return name
    if chip == "ESP8684H":
        return f"ESP8684H{fmb}" if fmb else chip
    if chip == "ESP8685":
        return f"ESP8685H{fmb}" if fmb else chip
    if chip == "ESP32-C3":
        if not fmb:
            return "ESP32-C3"
        temp = _flash_temp(efuse)
        if temp is None:
            raise UnknownPartError(
                f"an ESP32-C3 with {fmb} MB of flash in its package, whose eFuse "
                "FLASH_TEMP was not read, is an FN or an FH part and the read cannot "
                "say which")
        name = f"ESP32-C3F{temp}{fmb}"
        if re.search(r"\bAZ\b", description):
            return name + "AZ"
        return name + ("X" if _revision(revision) >= (1, 1) else "")
    return chip


def part_for(chip: str | None, description: str = "", features: tuple[str, ...] = (),
             efuse: Mapping[str, Any] | None = None, revision: str | None = None) -> Part:
    """The table's row for a chip as the probe read it, or UnknownPartError."""
    if not chip:
        raise UnknownPartError("no chip was read")
    name = part_name(chip, description, tuple(features), efuse, revision)
    part = BY_NAME.get(name)
    if part is None or part.listed_only:
        raise UnknownPartError(
            f"{name!r} (esptool: {description or chip!r}) is not in rpi_hwid.espressif's "
            "table; add it to PARTS with the datasheet table that lists it")
    return part


# --- the table as Markdown -----------------------------------------------------


def _mb(v: float | None) -> str:
    if v is None:
        return "?"
    return "—" if not v else f"{v:g} MB"


def _kb(v: float | None) -> str:
    return "not documented" if v is None else f"{v:g} KB"


def markdown() -> str:
    """docs/ESPRESSIF.md's two tables: the families, then the parts."""
    out = ["| family | ISA | core | cores | LP core | max MHz | SRAM | ROM | RTC/LP SRAM "
           "| Wi-Fi | Bluetooth | 802.15.4 | USB | eFuse unique id | Tasmota |",
           "|" + "---|" * 15]
    for f in FAMILIES:
        wifi = (f"{f.wifi}, {f.wifi_bands} (Wi-Fi {f.wifi_gen})" if f.wifi else "—")
        tas = {"release": f"yes (`{f.tasmota_build}`)",
               "experimental": f"experimental (`{f.tasmota_build}`)",
               "none": "no"}[f.tasmota]
        out.append(
            f"| {f.name} | {f.isa} | {f.core} | {f.cores} | {f.lp_core or '—'} "
            f"| {f.max_mhz} | {_kb(f.sram_kb)} | {_kb(f.rom_kb)} | {_kb(f.rtc_sram_kb)} "
            f"| {wifi} | {f.bluetooth or '—'} | {'yes' if f.ieee802154 else '—'} "
            f"| {f.usb} | {'yes' if f.chip_uid else '—'} | {tas} |")
    out += ["", "| part | family | cores | max MHz | flash in package | PSRAM in package "
                "| status | a read names it | notes |", "|" + "---|" * 9]
    generic = {q.family.name: q.part for q in PARTS if q.part == q.family.name}
    for p in PARTS:
        read = (f"no: it reads as {generic.get(p.family.name, p.family.name)}"
                if p.listed_only else "yes")
        out.append(f"| {p.part} | {p.family.name} | {p.n_cores} | {p.mhz} "
                   f"| {_mb(p.flash_mb)} | {_mb(p.psram_mb)} | {p.status} | {read} "
                   f"| {p.notes} |")
    return "\n".join(out) + "\n"


def sources_markdown() -> str:
    """Every family's and part's citations, then the references they name."""
    out = []
    for f in FAMILIES:
        out.append(f"### {f.name}\n")
        out += [f"- **{k}**: {v}" for k, v in f.sources.items()]
        if f.notes:
            out.append(f"- *note*: {f.notes}")
        for p in PARTS:
            if p.family is f and p.sources:
                out.append(f"- **{p.part}**: " + "; ".join(
                    f"{k}: {v}" for k, v in p.sources.items()))
        out.append("")
    out.append("### References\n")
    out += [f"- **{k}**: {v}" for k, v in REFS.items()]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(markdown())
    print(sources_markdown())
