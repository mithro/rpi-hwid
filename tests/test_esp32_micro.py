"""ESP32 micro labels: records from documents, and the refusals."""

from __future__ import annotations

import copy
import dataclasses

import pytest

import conftest
from rpi_hwid import esp32_micro, labels, micro
from rpi_hwid.micro import Icon, MicroRow
from rpi_hwid.model import ProbeDocument

# The USB tree alone, as rpi-hwid esp32 reads it without --read.
UNREAD = {
    "transport": "usb-serial-jtag", "usb_path": "3-1.4", "vidpid": "303a:1001",
    "tty": "/dev/ttyACM3", "tty_links": [
        "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_E8:3D:C1:8C:3E:B8-if00",
        "/dev/radio-cc1101-blue", "/dev/radio-esp32-E8:3D:C1:8C:3E:B8"],
    "usb_manufacturer": "Espressif", "usb_product": "USB JTAG/serial debug unit",
    "usb_serial": "E8:3D:C1:8C:3E:B8", "bcd_device": "0101", "bridge": None,
    "mac": "e8:3d:c1:8c:3e:b8", "mac_source": "usb-serial-jtag serial",
    "chip": None, "chip_description": None, "revision": None, "package": None,
    "features": [], "crystal_mhz": None, "flash_jedec": None, "flash_uid": None,
    "efuse": {}, "read": None, "read_error": None,
}
# ...and after --read. The values are synthetic, in the shape the read returns.
READ = dict(UNREAD, chip="ESP32-C3", chip_description="ESP32-C3 (QFN32) (revision v0.4)",
            revision="v0.4", package="QFN32",
            features=["WiFi", "BLE", "Embedded Flash 4MB (XMC)"], crystal_mhz=40,
            flash_jedec="0x204016", flash_uid="0123456789abcdef",
            mac_source="usb-serial-jtag serial; efuse", read="esptool 4.7.0")


def _docs(*devices, host="rpi5-433mhz"):
    raw = copy.deepcopy(conftest.RAW["rpi5-netv2"])
    raw["verdict"]["esp32"] = [copy.deepcopy(d) for d in devices]
    return {host: ProbeDocument.from_dict(host, raw)}


def test_a_read_c3_gets_its_label():
    (lab,) = esp32_micro.micro_labels(_docs(READ))
    assert lab.host == "rpi5-433mhz"
    assert lab.title == "ESP32-C3"
    assert lab.mark == "espressif.svg"
    assert lab.icons == (Icon("wifi"), Icon("chip", "C3"))
    assert lab.subtitle == "v0.4 · QFN32 · 40 MHz xtal"
    assert lab.ident_caption == "Wi-Fi MAC"
    assert lab.ident == "e8:3d:c1:8c:3e:b8"
    assert lab.rows == (MicroRow("BT", "e8:3d:c1:8c:3e:ba", mono=True),
                        MicroRow("flash", "XMC 4 MiB in package"),
                        MicroRow("uid", "0123456789abcdef", mono=True))


def test_an_esp32_found_only_on_usb_is_fatal_and_says_how_to_read_it():
    with pytest.raises(esp32_micro.Esp32NotReadError) as e:
        esp32_micro.micro_labels(_docs(UNREAD))
    msg = str(e.value)
    assert "rpi5-433mhz" in msg
    assert "e8:3d:c1:8c:3e:b8" in msg
    assert "`rpi-hwid esp32 --read /dev/radio-cc1101-blue` on that host" in msg
    assert "`rpi-hwid collect --esp32-read rpi5-433mhz=/dev/radio-cc1101-blue rpi5-433mhz`" in msg
    assert "resets the chip" in msg
    # it is the package's one error for an unread identifier
    assert isinstance(e.value, labels.IdentifierNotReadError)


def test_a_failed_read_says_why():
    with pytest.raises(esp32_micro.Esp32NotReadError, match="Failed to connect"):
        esp32_micro.micro_labels(_docs(dict(UNREAD, read_error="esptool read failed: "
                                                                 "Failed to connect")))


@pytest.mark.parametrize("uid", ["ffffffffffffffff", "0000000000000000", "", None])
def test_a_blank_flash_uid_is_a_failed_read(uid):
    with pytest.raises(esp32_micro.Esp32NotReadError, match="failed read"):
        esp32_micro.micro_labels(_docs(dict(READ, flash_uid=uid)))


def test_the_bt_mac_adds_to_the_last_octet_only():
    assert esp32_micro.derived_mac("e8:3d:c1:8c:3e:fe", 2) == "e8:3d:c1:8c:3e:00"
    assert esp32_micro.derived_mac("44:1b:f6:2e:b3:80", 2) == "44:1b:f6:2e:b3:82"


def test_no_bt_row_for_a_chip_the_table_does_not_cover():
    d = dict(READ, chip="ESP32-S2", chip_description="ESP32-S2 (revision v0.0)",
             features=["WiFi", "Embedded Flash 4MB"])
    (lab,) = esp32_micro.micro_labels(_docs(d))
    assert [r.caption for r in lab.rows] == ["flash", "uid"]
    assert lab.icons == (Icon("wifi"), Icon("chip", "S2"))


def test_an_external_flash_is_named_from_its_jedec_id():
    d = dict(READ, chip="ESP32", chip_description="ESP32-D0WD-V3 (revision v3.1)",
             features=["WiFi", "BT", "Dual Core"], flash_jedec="0xef4016")
    (lab,) = esp32_micro.micro_labels(_docs(d))
    assert MicroRow("flash", "Winbond W25Q32xx  ·  4 MiB") in lab.rows
    assert lab.icons[-1] == Icon("chip", "32")


def test_the_label_can_be_extended_by_a_caller():
    """What the ESP32 + 433 MHz labels will do: take the plain label apart."""
    host, dev = next(esp32_micro.devices(_docs(READ)))
    base = esp32_micro.esp32_label(host, dev)
    radio = dataclasses.replace(base, icons=(Icon("antenna", "433"), *base.icons),
                                rows=(*base.rows[:2], MicroRow("radio", "CC1101")))
    assert radio.ident == base.ident
    assert radio.rows[-1] == MicroRow("radio", "CC1101")


def test_the_esp32_kind_is_found_and_printed(tmp_path):
    assert "esp32" in micro.kinds()
    docs = _docs(READ, dict(READ, mac="44:1b:f6:2e:b3:80", tty="/dev/ttyACM2",
                            tty_links=["/dev/radio-sx1278-ra02"]))
    rows = list(labels.all_labels(docs, {"esp32"}))
    assert [r[1] for r in rows] == ["micro"]
    assert "ESP32-C3 44:1b:f6:2e:b3:80" in rows[0][2]
    n, stickers, _ = micro.render_micro(esp32_micro.micro_labels(docs), tmp_path / "e.pdf")
    assert (n, stickers) == (2, 1)
