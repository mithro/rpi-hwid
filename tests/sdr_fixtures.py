"""Probe documents of the fleet's four SDR hosts, as ``rpi-hwid collect
--sdr`` wrote them on 2026-09-26, trimmed to the summary the labels read.

Kept apart from conftest.RAW so the SDR work adds no hosts to the fixtures
every other test counts; docs/examples/render.py and tests/test_sdr_labels.py
read them from here.
"""

from __future__ import annotations

import copy

from rpi_hwid.model import ProbeDocument


def _doc(model, serial, revision, memory, compatible, macs, sdr, power_class="ambiguous"):
    summary = {
        "model": model, "serial": serial, "revision": revision, "compatible": compatible,
        "memory": memory, "header": [], "hat_uuid": None, "power_class": power_class,
        "fpga": [], "macs": [{"kind": k, "mac": m, "signal": "driver"} for k, m in macs],
        "usb_net": [], "sdr": sdr, "rtc_battery": None, "fan": None,
        "max_current_ma": None, "ext5v_v": None,
    }
    return {"verdict": {"summary": summary, "sdr": sdr}}


PI5 = ("Raspberry Pi 5 Model B Rev 1.1", "raspberrypi,5-model-b brcm,bcm2712")

SDR_RAW = {
    # the ADALM-Pluto on USB, read through its network IIO context
    "rpi-sdr-pluto": _doc(
        PI5[0], "391ca359c0a2c240", "d04171", "8 GB", PI5[1],
        [("eth", "88:a2:9e:80:86:97"), ("wlan", "88:a2:9e:80:86:98")],
        [{"kind": "pluto", "vidpid": "0456:b673",
          "usb_serial": "10447354119600022000120009f61e2b82",
          "manufacturer": "Analog Devices Inc.", "product": "PlutoSDR (ADALM-PLUTO)",
          "iio_uri": "ip:192.168.2.1",
          "hw_model": "Analog Devices PlutoSDR Rev.B (Z7010-AD9363A)",
          "hw_model_variant": "1", "hw_serial": "10447354119600022000120009f61e2b82",
          "fw_version": "v0.39", "rf_chip": "ad9363a", "xo_hz": 40000000,
          "rx_lo_hz": [325000000, 3800000000], "tx_lo_hz": [325000000, 3800000000],
          "rx_rate_hz": [520833, 30720000], "tx_rate_hz": [520833, 30720000],
          "rx_bw_hz": [200000, 56000000], "tx_bw_hz": [200000, 40000000],
          "rx_channels": 1, "tx_channels": 1, "adc_bits": 12}],
        power_class="usbc-pd-supply"),
    # the XSDR on PCIe, opened with --sdr-open while nothing held it. The ESN
    # is from the same card's AT25SL321, read the same day by the reader
    # --sdr-open now runs.
    "rpi-sdr-xsdr": _doc(
        PI5[0], "bc47bd4433e401e6", "b04171", "2 GB", PI5[1],
        [("eth", "88:a2:9e:45:de:f4"), ("wlan", "88:a2:9e:45:de:f5")],
        [{"kind": "usdr", "pcie_id": "10ee:7049", "pcie_subsystem": "10ee:0007",
          "usdr_family": "m2_lm7_1", "driver": "usdr", "pcie_dsn": "00-00-00-00-12-34-56-78",
          "pcie_link": "5.0 GT/s x1 (card x2)", "usdr_hwid": "8030012d",
          "flash_jedec": "0x1f4216", "fpga_devid": "0362c093",
          "flash_uid": "19040203090e9769ffffffffffffffff", "flash_uid_state": "read",
          "flash_uid_note": "AT25SL321 secured-OTP ESN; security register 0x00: factory "
                            "lock 0, customer lock 0"}]),
    # the KrakenSDR: five RTL2832U on its own USB2517 hub, serials 1000-1004
    "rpi-sdr-kraken": _doc(
        PI5[0], "9eb82f307e52c50f", "d04171", "8 GB", PI5[1],
        [("eth", "88:a2:9e:80:87:01"), ("wlan", "88:a2:9e:80:87:02")],
        [{"kind": "krakensdr", "vidpid": "0bda:2838",
          "channel_serials": ["1000", "1001", "1002", "1003", "1004"], "hub": "0424:2517",
          "tuner": "Rafael Micro R820T"}]),
}


def sdr_docs():
    """host -> ProbeDocument, as load_collected would build them."""
    return {name: ProbeDocument.from_dict(name, copy.deepcopy(raw))
            for name, raw in SDR_RAW.items()}
