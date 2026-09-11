"""Probe documents captured from real boards, trimmed to the summary the
package consumes, so the collector, names and labels can be tested without
a Pi."""

from __future__ import annotations

import copy
import json

import pytest

from rpi_hwid.model import ProbeDocument


def _doc(model, serial, revision, header, power_class, fpga, macs, usb_net, rtc, fan, mc,
         compatible="", memory=None):
    return {
        "model": model, "serial": serial, "revision": revision,
        "compatible": compatible.split(),
        "board": "rpi" if model.startswith("Raspberry") else "opi",
        "hat_fw": None, "hat_eeproms": {}, "i2c1": [], "usb": {},
        "interfaces": [{"kind": m["kind"], "mac": m["mac"], "onboard": True,
                        "name": m["kind"] + "0", "driver": "x", "usb": None, "speed": None}
                       for m in macs],
        "usb_net": usb_net, "pi5": mc is not None,
        "verdict": {
            "header": header or ["nothing identifiable on the header"],
            "power": power_class, "evidence": [], "fpga": [],
            "summary": {
                "model": model, "serial": serial, "revision": revision,
                "compatible": compatible, "memory": memory,
                "header": header, "hat_uuid": None, "power_class": power_class,
                "fpga": fpga, "macs": macs, "usb_net": usb_net,
                "rtc_battery": rtc, "fan": fan, "max_current_ma": mc, "ext5v_v": None,
            },
        },
    }


PI5_NETV2 = _doc(
    "Raspberry Pi 5 Model B Rev 1.0", "d88100008543dc30", "c04170", [], "usbc-supply",
    [{"kind": "netv2", "dna": "0x00742c4e63b9085c", "idcode": "0x3631093"}],
    [{"kind": "eth", "mac": "2c:cf:67:16:bd:98"}, {"kind": "wlan", "mac": "2c:cf:67:16:bd:99"}],
    [{"iface": "eth-netv2", "mac": "00:0e:c6:82:b5:e1", "driver": "ax88179_178a",
      "vidpid": "0b95:1790", "manufacturer": "ASIX Elec. Corp.", "product": "AX88179",
      "usb_serial": "00000000000179", "bcd_usb": "3.00", "usb_speed": "5000",
      "kind": "ethernet"}],
    False, False, 900,
)

POOL_3BPLUS = _doc(
    "Raspberry Pi 3 Model B Plus Rev 1.3", "000000004fe3e7e4", "a020d3", [], "undetermined",
    [], [{"kind": "eth", "mac": "b8:27:eb:e3:e7:e4"}], [], None, None, None,
)

ARTY_HOST = _doc(
    "Raspberry Pi 4 Model B Rev 1.5", "10000000ce8e3593", "b03115",
    ["Pmod HAT Adaptor"], "undetermined",
    [{"kind": "arty", "serial": "210319B301DE", "dna": "0x00628502251ea85c",
      "idcode": "0x362d093", "flash_jedec": "0x012018",
      "flash": "spansion S25FL128S 256 sectors size: 128Mb"}],
    [{"kind": "eth", "mac": "e4:5f:01:96:f8:a5"}, {"kind": "wlan", "mac": "e4:5f:01:96:f8:a7"}],
    [], None, None, None,
)
ARTY_HOST["verdict"]["summary"]["hat_uuid"] = "6bcd3833-3d1d-4b3e-9ab1-945c71845f3a"

ZERO_BONNET = _doc(
    "Raspberry Pi Zero W Rev 1.1", "000000005157f671", "9000c1",
    ["Waveshare PoE-ETH-USB-HUB-HAT"], "bonnet-poe", [],
    [{"kind": "eth", "mac": "00:e0:4c:36:0b:0a"}, {"kind": "wlan", "mac": "b8:27:eb:02:a3:24"}],
    [], None, None, None,
)

ACORN_HOST = _doc(
    "Raspberry Pi 5 Model B Rev 1.1", "c36b093f773d46b8", "a04171",
    ["Waveshare PoE M.2 HAT+ (B)"], "gpio-poe-hat", [{"kind": "acorn"}],
    [{"kind": "eth", "mac": "98:fe:54:13:f5:75"}], [], True, True, 3000,
)

# An Orange Pi PC on Armbian trixie, from the values captured from opi1pc-b
# on 2026-07-08: no revision code, no HAT convention, no power sensing, a
# SoC serial from U-Boot and the eth0 MAC U-Boot derives from it.
OPI_PC = _doc(
    "Xunlong Orange Pi PC", "02c00181e1ce7d46", None, [], "undetermined", [],
    [{"kind": "eth", "mac": "02:81:e1:ce:7d:46"}], [], None, None, None,
    compatible="xunlong,orangepi-pc allwinner,sun8i-h3", memory="1 GB",
)
OPI_PC.update(mem_kb=1015636, sid=None, sid_serial=None, cpuinfo_serial="02c00181e1ce7d46",
              armbian={"BOARD": "orangepipc", "BOARD_NAME": "Orange Pi PC",
                       "BOARDFAMILY": "sun8i", "LINUXFAMILY": "sunxi",
                       "VERSION": "26.8.0-trunk.170"})
OPI_PC["verdict"]["header"] = [
    "40-pin header not probed: no HAT ID EEPROM convention on this board"]


# A Pi 4 with two Tiny Tapeout demo boards on USB: a TT06 chip on its TT06+
# board (RP2040, v2 SDK) and a TTIHP25a on a DBv3 "ETR" board (RP2350, v3
# SDK), whose colours the table does not record.
TT_HOST = _doc(
    "Raspberry Pi 4 Model B Rev 1.4", "100000003a7e1c9b", "c03114", [], "undetermined", [],
    [{"kind": "eth", "mac": "dc:a6:32:8f:2b:11"}, {"kind": "wlan", "mac": "dc:a6:32:8f:2b:12"}],
    [], None, None, None,
)
TT_HOST["usb"] = {"1-1": "2109:3431", "1-1.2": "2e8a:0005", "1-1.3": "2e8a:0005"}
TT_HOST["tinytapeout"] = {
    "usb": [{"path": "1-1.2", "id": "2e8a:0005", "manufacturer": "MicroPython",
             "product": "Board in FS mode", "serial": "E6614C311B7A7A37",
             "tty": "/dev/ttyACM0"},
            {"path": "1-1.3", "id": "2e8a:0005", "manufacturer": "MicroPython",
             "product": "Board in FS mode", "serial": "E66360B8A3C1D5F2",
             "tty": "/dev/ttyACM1"}],
    "repl": {
        "1-1.2": {"machine": "Raspberry Pi Pico with RP2040", "micropython": "1.24.0",
                  "sdk": "2.0.4", "sdk_revision": None, "demoboard": "TT06+",
                  "carrier_present": True, "carrier_version": None,
                  "rom": {"shuttle": "tt06", "repo": "TinyTapeout/tinytapeout-06",
                          "commit": "0f5a1b2c"},
                  "rom_text": "shuttle=tt06\nrepo=TinyTapeout/tinytapeout-06\ncommit=0f5a1b2c\n"},
        "1-1.3": {"machine": "TinyTapeout RP2350B Core with RP2350", "micropython": "1.26.0",
                  "sdk": "3.1.1", "sdk_revision": "9c2e4d1a", "demoboard": "TTDBv3 [3.2]",
                  "carrier_present": True, "carrier_version": 1,
                  "rom": {"shuttle": "ttihp25a", "repo": "TinyTapeout/tinytapeout-ihp-25a",
                          "commit": "7b3d9e02"},
                  "rom_text": "shuttle=ttihp25a\nrepo=TinyTapeout/tinytapeout-ihp-25a\n"
                              "commit=7b3d9e02\n"},
    },
}
TT_HOST["verdict"]["tinytapeout"] = [
    {"kind": "tinytapeout", "usb": "1-1.2", "usb_serial": "E6614C311B7A7A37",
     "tty": "/dev/ttyACM0", "shuttle": "tt06", "chip": "asic",
     "repo": "TinyTapeout/tinytapeout-06", "commit": "0f5a1b2c", "demoboard": "TT06+",
     "demoboard_version": "v2.0.1", "sdk": "2.0.4", "machine": "Raspberry Pi Pico with RP2040",
     "mcu": "RP2040",
     "chip_url": "https://tinytapeout.com/chips/tt06/",
     "how": "Tiny Tapeout SDK 2.0.4 on Raspberry Pi Pico with RP2040 (USB 1-1.2); "
            "chip ROM shuttle=tt06; demo board TT06+"},
    {"kind": "tinytapeout", "usb": "1-1.3", "usb_serial": "E66360B8A3C1D5F2",
     "tty": "/dev/ttyACM1", "shuttle": "ttihp25a", "chip": "asic",
     "repo": "TinyTapeout/tinytapeout-ihp-25a", "commit": "7b3d9e02",
     "demoboard": "TTDBv3 [3.2]", "demoboard_version": None, "sdk": "3.1.1",
     "machine": "TinyTapeout RP2350B Core with RP2350", "mcu": "RP2350",
     "chip_url": "https://tinytapeout.com/chips/ttihp25a/",
     "how": "Tiny Tapeout SDK 3.1.1 on TinyTapeout RP2350B Core with RP2350 (USB 1-1.3); "
            "chip ROM shuttle=ttihp25a; demo board TTDBv3 [3.2]"},
]
TT_HOST["verdict"]["summary"]["tinytapeout"] = [
    {"usb_serial": "E6614C311B7A7A37", "mcu": "RP2040", "shuttle": "tt06", "chip": "asic",
     "repo": "TinyTapeout/tinytapeout-06", "commit": "0f5a1b2c", "demoboard": "TT06+",
     "demoboard_version": "v2.0.1", "sdk": "2.0.4"},
    {"usb_serial": "E66360B8A3C1D5F2", "mcu": "RP2350", "shuttle": "ttihp25a", "chip": "asic",
     "repo": "TinyTapeout/tinytapeout-ihp-25a", "commit": "7b3d9e02",
     "demoboard": "TTDBv3 [3.2]", "demoboard_version": None, "sdk": "3.1.1"},
]


RAW = {
    "rpi5-netv2": PI5_NETV2,
    "pi-sw1-p10": POOL_3BPLUS,
    "pi-sw2-p16": ARTY_HOST,
    "rpiz-serial": ZERO_BONNET,
    "pi-sw2-p47": ACORN_HOST,
    "opi1pc-b": OPI_PC,
    "rpi4-tt": TT_HOST,
}


@pytest.fixture
def docs():
    """host -> ProbeDocument, as load_collected would build them."""
    return {name: ProbeDocument.from_dict(name, copy.deepcopy(raw)) for name, raw in RAW.items()}


@pytest.fixture
def data_dir(tmp_path):
    for name, raw in RAW.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(raw))
    return tmp_path
