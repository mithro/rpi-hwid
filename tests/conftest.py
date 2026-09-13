"""Probe documents captured from real boards, trimmed to the summary the
package consumes, so the collector, names and labels can be tested without
a Pi."""

from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess

import pytest

from rpi_hwid.model import ProbeDocument

# Commands that act on the machine running the tests as root, or on its
# services and logins. The probes run them on a Pi over ssh; a test must
# never start one here, whatever the code under test decides. On a
# workstation with passwordless sudo one stray "systemctl stop" is enough
# to end the user's session, every terminal and the tmux server.
PRIVILEGED = frozenset({
    "sudo", "doas", "pkexec", "su", "run0", "systemctl", "systemd-run", "loginctl",
    "shutdown", "reboot", "poweroff", "halt",
})


def _privileged(args, shell):
    """The privileged command `args` would start, else None."""
    if isinstance(args, (str, bytes)):
        text = os.fsdecode(args)
        words = shlex.split(text) if shell else [text]
    else:
        words = [os.fsdecode(a) for a in args][:1]
    for word in words:
        if os.path.basename(word) in PRIVILEGED:
            return word
    return None


@pytest.fixture(autouse=True)
def no_privileged_commands(monkeypatch):
    """Fail any test that starts sudo, systemctl and the like on this machine.

    Every subprocess helper (run, check_output, call, Popen) goes through
    subprocess.Popen, so guarding that one class guards them all. The
    failure is a pytest outcome, not an OSError, so no "never raises"
    wrapper in the code under test can swallow it.
    """
    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, args, *rest, **kwargs):
            shell = kwargs.get("shell", False)
            found = _privileged(args, shell)
            if found:
                pytest.fail(f"a test tried to run {found!r} on this machine: {args!r}")
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)
    monkeypatch.setattr(os, "system", lambda cmd: pytest.fail(
        f"a test tried to run os.system({cmd!r}) on this machine"))


def _doc(model, serial, revision, header, power_class, fpga, macs, usb_net, rtc, fan, mc,
         compatible="", memory=None):
    return {
        "model": model, "serial": serial, "revision": revision,
        "compatible": compatible.split(),
        "board": "rpi" if model.startswith("Raspberry") else "opi",
        "hat_fw": None, "hat_eeproms": {}, "header_i2c": [], "usb": {},
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

# The pool's Orange Pi PC, probed on pi-sw2-p22: no revision code, no power
# sensing, a SoC serial from U-Boot and the eth0 MAC U-Boot derives from it.
# It netboots the pool's Raspbian armhf root like the Pi rigs do, so there is
# no Armbian release to read.
#
# It wears a Digilent Pmod HAT Adaptor, and the HAT's ID EEPROM reads off the
# header exactly as it does on the Pi 4 that wears the same adaptor -- the
# uuid below is that board's, read off i2c-1 on 2026-09-13. The Orange Pi's
# header ID pins are i2c-1 where a Pi's are i2c-0; nothing else differs, so
# the two labels say the same thing. Its power class stays undetermined: a
# Pmod adaptor is not a PoE HAT and an H3 has nothing to ask.
OPI_PC = _doc(
    "Xunlong Orange Pi PC", "02c000812eb7a34e", None, ["Pmod HAT Adaptor"], "undetermined", [],
    [{"kind": "eth", "mac": "02:81:2e:b7:a3:4e"}], [], None, None, None,
    compatible="xunlong,orangepi-pc allwinner,sun8i-h3", memory="1 GB",
)
OPI_PC.update(mem_kb=1016504, cpuinfo_serial="02c000812eb7a34e", armbian=None,
              sid=["0x02c00081", "0x35d04620", "0x79058814", "0x401c0a94"],
              sid_serial="02c000812eb7a34e",
              hat_eeproms={"0x50": {"version": 1, "atoms": [],
                                    "uuid": "363bffaa-8824-a94d-7242-3c0955f9126c",
                                    "pid": "0x0001", "pver": "0x0001",
                                    "vendor": "Digilent", "product": "Pmod HAT Adaptor"}},
              header_i2c=[])
OPI_PC["verdict"]["header"] = ["Pmod HAT Adaptor (HAT EEPROM at 0x50, pid 0x0001)"]
OPI_PC["verdict"]["summary"]["hat_uuid"] = "363bffaa-8824-a94d-7242-3c0955f9126c"


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


# One of the pool's four Tiny Tapeout FPGA emulation boards, probed on
# pi-sw2-p33 on 2026-09-14: a TT demo board v3 carrying a FabricFox iCE40UP5K
# breakout instead of an ASIC, so the chip is an FPGA simulating one. Its ROM
# has no shuttle to read -- config.ini forces the string "FPGA", which the
# probe reports as chip "fpga" with shuttle None -- so the label has no
# shuttle colours and no chip page to link, the case no other fixture covers.
TT_FPGA_HOST = _doc(
    "Raspberry Pi 4 Model B Rev 1.5", "1000000085948b10", "b03115",
    ["Pmod HAT Adaptor"], "undetermined", [],
    [{"kind": "eth", "mac": "e4:5f:01:97:0e:77"}, {"kind": "wlan", "mac": "e4:5f:01:97:0e:79"}],
    [], None, None, None,
)
TT_FPGA_HOST["verdict"]["summary"]["hat_uuid"] = "38a7f86d-4c56-4649-9d9d-91eb4c0f7f50"
TT_FPGA_HOST["usb"] = {"1-1": "2109:3431", "1-1.2": "2e8a:0005"}
TT_FPGA_HOST["tinytapeout"] = {
    "usb": [{"path": "1-1.2", "id": "2e8a:0005", "manufacturer": "MicroPython",
             "product": "Board in FS mode", "serial": "4df39a7a6856f86f",
             "tty": "/dev/ttyACM0"}],
    "repl": {
        "1-1.2": {"machine": "TinyTapeout RP2350B Core with RP2350",
                  "micropython": "1.29.0-preview", "sdk": "3.1.0",
                  "sdk_revision": "baaf0a2758b475d6e099b8ef65dbd1b5667ea0aa",
                  "demoboard": "TTDBv3 [3.2]", "carrier_present": True,
                  "carrier_version": 2, "rom_forced": True, "rom_cached": True,
                  "rom": {"shuttle": "FPGA", "repo": "", "commit": ""}},
    },
}
TT_FPGA_HOST["verdict"]["tinytapeout"] = [
    {"kind": "tinytapeout", "usb": "1-1.2", "usb_serial": "4df39a7a6856f86f",
     "tty": "/dev/ttyACM0", "shuttle": None, "chip": "fpga", "repo": None, "commit": None,
     "demoboard": "TTDBv3 [3.2]", "demoboard_version": None, "sdk": "3.1.0",
     "machine": "TinyTapeout RP2350B Core with RP2350", "mcu": "RP2350", "chip_url": None,
     "how": "Tiny Tapeout SDK 3.1.0 on TinyTapeout RP2350B Core with RP2350 (USB 1-1.2); "
            "chip ROM shuttle=FPGA (forced in config.ini); demo board TTDBv3 [3.2]"},
]
TT_FPGA_HOST["verdict"]["summary"]["tinytapeout"] = [
    {"usb_serial": "4df39a7a6856f86f", "mcu": "RP2350", "shuttle": None, "chip": "fpga",
     "repo": None, "commit": None, "demoboard": "TTDBv3 [3.2]", "demoboard_version": None,
     "sdk": "3.1.0"},
]

# A Pi 5 on the IoT network carrying a Realtek RTL8811CU on USB, probed on
# 2026-09-14. Its wlanE is the only USB *wireless* adapter in the fixtures, so
# it is the one label that draws the WiFi glyph rather than an RJ45.
WIFI_HOST = _doc(
    "Raspberry Pi 5 Model B Rev 1.1", "7070c78090a6d6d8", "b04171", [], "usbc-supply", [],
    [{"kind": "eth", "mac": "88:a2:9e:45:c5:5d"}, {"kind": "wlan", "mac": "88:a2:9e:45:c5:5e"}],
    [{"iface": "wlanE", "mac": "6c:1f:f7:51:2e:a3", "driver": "rtw88_8821cu",
      "vidpid": "0bda:c811", "manufacturer": "Realtek", "product": "802.11ac NIC",
      "usb_serial": "123456", "bcd_usb": "2.00", "usb_speed": "480", "kind": "wifi"}],
    False, True, 900,
)

# A pool Pi 4 with a Linksys USB3GIGV1 on USB 3, probed on pi-sw2-p37 on
# 2026-09-14: a second USB Ethernet adapter from a different vendor to the
# ASIX, on the same r8152 driver as the Realtek parts.
LINKSYS_HOST = _doc(
    "Raspberry Pi 4 Model B Rev 1.5", "10000000613a4524", "b03115",
    ["Pmod HAT Adaptor"], "undetermined", [],
    [{"kind": "eth", "mac": "e4:5f:01:97:1f:7e"}],
    [{"iface": "eth1", "mac": "60:38:e0:e3:56:4f", "driver": "r8152",
      "vidpid": "13b1:0041", "manufacturer": "Linksys", "product": "Linksys USB3GIGV1",
      "usb_serial": "000001000000", "bcd_usb": "3.00", "usb_speed": "5000",
      "kind": "ethernet"}],
    None, None, None,
)
LINKSYS_HOST["verdict"]["summary"]["hat_uuid"] = "6e28126c-32b3-41fd-aa72-be2554bf69b1"


RAW = {
    "rpi5-netv2": PI5_NETV2,
    "pi-sw1-p10": POOL_3BPLUS,
    "pi-sw2-p16": ARTY_HOST,
    "rpiz-serial": ZERO_BONNET,
    "pi-sw2-p47": ACORN_HOST,
    "pi-sw2-p22": OPI_PC,
    "rpi4-tt": TT_HOST,
    "pi-sw2-p33": TT_FPGA_HOST,
    "pi-sw2-p37": LINKSYS_HOST,
    "rpi5-433mhz": WIFI_HOST,
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
