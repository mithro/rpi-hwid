"""x86 boards, which have no device tree: identity from DMI/SMBIOS.

The trees below are the fleet's two MinnowBoards as read on 2026-09-26 --
minnow-turbot-2, an ADI Engineering MinnowBoard Turbot, and minnow-turbot-1,
which despite its name is a CircuitCo MinnowBoard MAX (its firmware says so,
and its E3825 is the MAX's part; the Turbot's is the E3826)."""

from __future__ import annotations

import pytest

from rpi_hwid import probe


def _w(root, rel, content):
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


TURBOT_DMI = {
    "sys_vendor": "ADI", "product_name": "Minnowboard Turbot D0 PLATFORM",
    "product_version": "D0", "product_serial": "0008A209EFED",
    # The same UUID on both boards: a firmware constant, not an identity.
    "product_uuid": "00000000-6462-4524-006a-9b7737e315cf",
    "product_family": "IA Notebook", "product_sku": "To be filled by O.E.M",
    "board_vendor": "ADI", "board_name": "MinnowBoard Turbot", "board_version": "REV A",
    "board_serial": "0008A209EFED", "board_asset_tag": "To be filled by O.E.M",
    "chassis_serial": "", "chassis_vendor": "ADI", "chassis_type": "2",
    "bios_vendor": "Intel Corp.", "bios_version": "MNW2MAX1.X64.0094.R01.1612052239",
    "bios_date": "12/05/2016",
}

MAX_DMI = dict(
    TURBOT_DMI, sys_vendor="Circuitco", product_name="MinnowBoard MAX B3 PLATFORM",
    product_version="B3", product_serial="001320FE4164", board_vendor="Circuitco",
    board_name="MinnowBoard MAX", board_serial="001320FE4164", chassis_vendor="Circuitco",
    bios_version="MNW2MAX1.X64.0077.R01.1501291247", bios_date="01/29/2015",
)


def _vpd_pg80(serial):
    """A SCSI unit serial number page as the kernel serves it."""
    body = serial.encode().ljust(20)
    return bytes([0, 0x80, 0, len(body)]) + body


def _pc_tree(root, dmi, cpu, mac, mem_kb, disk, disk_serial):
    for name, value in dmi.items():
        _w(root, "/sys/class/dmi/id/" + name, value + "\n")
    _w(root, "/proc/cpuinfo", "".join(
        f"processor\t: {n}\nvendor_id\t: GenuineIntel\nmodel name\t: {cpu}\n\n"
        for n in range(2)))
    _w(root, "/proc/meminfo", f"MemTotal:        {mem_kb} kB\n")
    # the Realtek RTL8111 on the SoC's third PCIe root port
    _w(root, "/sys/class/net/eth0/address", mac + "\n")
    _w(root, "/sys/class/net/eth0/speed", "1000\n")
    _w(root, "/sys/class/net/eth0/addr_assign_type", "0\n")
    nic = root / "sys/devices/pci0000:00/0000:00:1c.2/0000:02:00.0"
    nic.mkdir(parents=True)
    (root / "sys/class/net/eth0/device").symlink_to(nic)
    (root / "sys/bus/pci/drivers/r8169").mkdir(parents=True)
    (nic / "driver").symlink_to(root / "sys/bus/pci/drivers/r8169")
    # the SATA disk: a device, with its unit serial page. sysfs has the model
    # as SCSI INQUIRY gives it, cut to 16 characters.
    blk = root / "sys/devices/pci0000:00/0000:00:13.0/ata2/host1/target1:0:0/1:0:0:0"
    _w(root, str(blk.relative_to(root)) + "/model", disk + "\n")
    _w(root, str(blk.relative_to(root)) + "/vendor", "ATA     \n")
    _w(root, str(blk.relative_to(root)) + "/vpd_pg80", _vpd_pg80(disk_serial))
    _w(root, "/sys/block/sda/size", "30867456\n")
    _w(root, "/sys/block/sda/removable", "0\n")
    (root / "sys/block/sda/device").symlink_to(blk)
    # device-mapper volumes have no device of their own and are not disks
    _w(root, "/sys/block/dm-0/size", "1000\n")
    _w(root, "/sys/bus/usb/devices/usb1/idVendor", "1d6b\n")
    _w(root, "/sys/bus/usb/devices/usb1/idProduct", "0002\n")


@pytest.fixture
def turbot(tmp_path, monkeypatch):
    _pc_tree(tmp_path, TURBOT_DMI, "Intel(R) Atom(TM) CPU  E3826  @ 1.46GHz",
             "00:08:a2:09:ef:ed", 1920488, "StorFly VSF302XC", "54812-4198")
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    calls = []

    def fake_sh(args, timeout=15):
        calls.append(args)
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    return tmp_path, calls


def test_a_turbot_is_identified_from_dmi(turbot):
    _root, calls = turbot
    d = probe.collect()
    assert d["board"] == "x86"
    assert d["model"] == "ADI MinnowBoard Turbot"
    assert d["compatible"] == []
    assert d["serial"] == "0008A209EFED"
    assert d["revision"] is None
    assert d["dmi"]["board_name"] == "MinnowBoard Turbot"
    assert d["dmi"]["product_version"] == "D0"
    assert d["dmi"]["unread"] == []
    assert d["cpu"] == {"model": "Intel(R) Atom(TM) CPU  E3826  @ 1.46GHz", "threads": 2}
    assert calls == [], "every DMI file was readable, so nothing was run"
    v = probe.verdict(d)
    s = v["summary"]
    assert s["model"] == "ADI MinnowBoard Turbot"
    assert s["serial"] == "0008A209EFED"
    assert s["memory"] == "2 GB"
    assert s["cpu"] == "Intel(R) Atom(TM) CPU  E3826  @ 1.46GHz"
    assert s["dmi"]["board_vendor"] == "ADI"
    assert s["dmi"]["unread"] == []
    assert v["header"] == ["no HAT header on this board"]
    assert "no power sensing" in v["power"]


def test_the_wired_port_is_the_one_the_firmware_serial_names(turbot):
    """The Turbot's DMI serial is its Ethernet MAC without the colons, which
    settles that the r8169 on the PCIe bus is the board's own port."""
    d = probe.collect()
    (eth,) = d["interfaces"]
    assert eth["onboard"] is True
    assert eth["kind"] == "eth"
    assert eth["signal"] == "serial-mac"
    assert probe.verdict(d)["summary"]["macs"] == [
        {"kind": "eth", "mac": "00:08:a2:09:ef:ed", "signal": "serial-mac"}]


def test_a_pci_nic_the_serial_does_not_name_is_onboard_by_bus_alone(turbot):
    root, _calls = turbot
    _w(root, "/sys/class/dmi/id/board_serial", "ABC123\n")
    _w(root, "/sys/class/dmi/id/product_serial", "ABC123\n")
    d = probe.collect()
    (eth,) = d["interfaces"]
    assert eth["onboard"] is True
    assert eth["signal"] == "pci"


def test_storage_serials_are_recorded(turbot):
    d = probe.collect()
    assert d["storage"] == [{
        "name": "sda", "model": "StorFly VSF302XC", "vendor": "ATA",
        "serial": "54812-4198", "removable": False, "size_bytes": 30867456 * 512,
        "cid": None, "mmc_type": None,
    }]
    assert any("sda StorFly VSF302XC serial 54812-4198" in e
               for e in probe.verdict(d)["evidence"])


def test_an_emmc_carries_its_cid(turbot):
    root, _calls = turbot
    card = root / "sys/devices/pci0000:00/0000:00:17.0/mmc_host/mmc0/mmc0:0001"
    for name, value in (("type", "MMC"), ("name", "DF4032"), ("serial", "0x1a2b3c4d"),
                        ("cid", "450100444634303332011a2b3c4d8e00")):
        _w(root, str(card.relative_to(root)) + "/" + name, value + "\n")
    _w(root, "/sys/block/mmcblk0/size", "61071360\n")
    _w(root, "/sys/block/mmcblk0/removable", "0\n")
    (root / "sys/block/mmcblk0/device").symlink_to(card)
    _w(root, "/sys/block/mmcblk0boot0/size", "8192\n")
    (root / "sys/block/mmcblk0boot0/device").symlink_to(card)
    emmc = next(s for s in probe.collect()["storage"] if s["name"] == "mmcblk0")
    assert emmc["cid"] == "450100444634303332011a2b3c4d8e00"
    assert emmc["serial"] == "0x1a2b3c4d"
    assert emmc["mmc_type"] == "MMC"
    assert emmc["model"] == "DF4032"
    assert [s["name"] for s in probe.collect()["storage"]] == ["mmcblk0", "sda"], \
        "the boot partitions are the same chip, not another disk"


def test_root_only_dmi_fields_are_read_through_sudo(turbot, monkeypatch):
    locked = ("board_serial", "product_serial", "product_uuid", "chassis_serial")
    real = probe.read

    def read(path):
        if any(path.endswith("/dmi/id/" + f) for f in locked):
            return None                      # -r-------- root, as on the board
        return real(path)
    monkeypatch.setattr(probe, "read", read)
    asked = []

    def sudo_read(path):
        asked.append(path)
        return TURBOT_DMI[path.rsplit("/", 1)[1]]
    monkeypatch.setattr(probe, "sudo_read", sudo_read)
    d = probe.collect()
    assert sorted(p.rsplit("/", 1)[1] for p in asked) == sorted(locked)
    assert d["serial"] == "0008A209EFED"
    assert d["dmi"]["unread"] == []


def test_a_serial_sudo_would_not_read_is_said_to_be_unread(turbot, monkeypatch):
    real = probe.read
    monkeypatch.setattr(probe, "read", lambda path: None if path.endswith(
        ("_serial", "product_uuid")) else real(path))
    monkeypatch.setattr(probe, "sudo_read", lambda path: None)
    d = probe.collect()
    assert d["serial"] is None
    assert d["dmi"]["unread"] == ["product_serial", "product_uuid", "board_serial",
                                  "chassis_serial"]
    assert any("DMI board_serial, product_serial" in e and "sudo cat" in e
               for e in probe.verdict(d)["evidence"])


@pytest.mark.parametrize("value", [
    "", "To be filled by O.E.M.", "To be filled by O.E.M", "Default string",
    "System Serial Number", "0123456789", "00000000", "Not Specified", "None",
])
def test_firmware_placeholders_are_not_serials(value):
    assert probe.dmi_value(value) is None


def test_a_max_is_named_as_a_max(tmp_path, monkeypatch):
    _pc_tree(tmp_path, MAX_DMI, "Intel(R) Atom(TM) CPU  E3825  @ 1.33GHz",
             "00:13:20:fe:41:64", 1936836, "Samsung SSD 850",
             "S33HNX0HC00253P")
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "sh", lambda args, timeout=15: "")
    d = probe.collect()
    assert d["model"] == "Circuitco MinnowBoard MAX"
    assert d["serial"] == "001320FE4164"
    assert d["interfaces"][0]["signal"] == "serial-mac"


def test_board_kind_x86():
    assert probe.board_kind("ADI MinnowBoard Turbot", [], {"board_name": "x"}) == "x86"
    assert probe.board_kind("", [], None) == "other"
    # a device tree wins over DMI: some arm64 firmware publishes both
    assert probe.board_kind("Raspberry Pi 5 Model B Rev 1.0", ["raspberrypi,5-model-b"],
                            {"board_name": "x"}) == "rpi"


def test_the_document_loads_into_the_model(turbot):
    from rpi_hwid.model import ProbeDocument

    d = probe.collect()
    d["verdict"] = probe.verdict(d)
    s = ProbeDocument.from_dict("minnow-turbot-2", d).summary
    assert s.serial == "0008A209EFED"
    assert s.cpu == "Intel(R) Atom(TM) CPU  E3826  @ 1.46GHz"
    assert s.dmi is not None
    assert s.dmi["board_name"] == "MinnowBoard Turbot"
    assert s.to_dict()["dmi"]["board_serial"] == "0008A209EFED"
