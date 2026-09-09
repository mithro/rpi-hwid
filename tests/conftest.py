"""Probe documents captured from real boards, trimmed to the summary the
package consumes, so the collector, names and labels can be tested without
a Pi."""

from __future__ import annotations

import copy
import json

import pytest

from rpi_hwid.model import ProbeDocument


def _doc(model, serial, revision, header, power_class, fpga, macs, usb_net, rtc, fan, mc):
    return {
        "model": model, "serial": serial, "revision": revision,
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


RAW = {
    "rpi5-netv2": PI5_NETV2,
    "pi-sw1-p10": POOL_3BPLUS,
    "pi-sw2-p16": ARTY_HOST,
    "rpiz-serial": ZERO_BONNET,
    "pi-sw2-p47": ACORN_HOST,
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
