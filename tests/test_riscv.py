"""RISC-V device-tree boards: the probe's side (a fake FU740 tree) and the
label's side (which board, what its header says, what refuses to print).

The fleet's two RISC-V boards are SiFive HiFive Unmatched A00s. Their
identity lives in an I2C EEPROM U-Boot prints at every boot. The bytes here
were read off both boards on 2026-09-26 (`sudo od -A x -t x1
/sys/bus/nvmem/devices/board-id0/nvmem`, Debian 13, kernel 6.12.73), and
the fake sysfs tree is laid out as that kernel lays it out. They are also
exactly what U-Boot's layout rebuilds from its own boot print-out, CRC and
all -- so the decoder and U-Boot agree about every byte."""

from __future__ import annotations

import struct
import zlib

import pytest

from rpi_hwid import boards, labels, probe, riscv
from rpi_hwid.model import ProbeDocument


def sifive_eeprom_bytes(serial, mac, test_status=1, pcb=3, bom="B", variant=0, pad=256):
    """A SiFive PCB EEPROM (format v1) as U-Boot's
    board/sifive/unmatched/hifive-platform-i2c-eeprom.c lays it out: packed,
    little-endian, the CRC-32 over everything before it. `pad` is the
    24c02's 256 bytes, the rest erased (0xff)."""
    body = (b"\xf1\x5e\x50\x45" + bytes([1]) + struct.pack("<H", 2)
            + bytes([pcb, ord(bom), variant]) + serial.encode("ascii")
            + bytes([test_status]) + bytes.fromhex(mac.replace(":", "")))
    blob = body + struct.pack("<I", zlib.crc32(body))
    return blob + b"\xff" * (pad - len(blob))


# The two EEPROMs as read off the boards, 256 bytes each: 37 of data and the
# rest erased.
UNMATCHED_1 = bytes.fromhex(
    "f15e50450102000342005346313035535a323132323030333931"
    "0170b3d592f8de22e50997") + b"\xff" * 219
UNMATCHED_2 = bytes.fromhex(
    "f15e50450102000342005346313035535a323132323030353332"
    "0170b3d592f88351356c94") + b"\xff" * 219


def test_real_eeproms_are_what_u_boots_layout_rebuilds():
    """The board read and U-Boot's layout agree to the byte, CRC included:
    the CRCs U-Boot printed on 2026-07-04 (9709e522, 946c3551) are the ones
    the boards hold."""
    assert sifive_eeprom_bytes("SF105SZ212200391", "70:b3:d5:92:f8:de") == UNMATCHED_1
    assert sifive_eeprom_bytes("SF105SZ212200532", "70:b3:d5:92:f8:83") == UNMATCHED_2
    assert UNMATCHED_1[33:37] == struct.pack("<I", 0x9709E522)
    assert UNMATCHED_2[33:37] == struct.pack("<I", 0x946C3551)


def test_decode_sifive_eeprom():
    e = probe.sifive_eeprom_decode(UNMATCHED_1)
    assert e == {
        "format": 1, "product_id": "0x0002", "product": "HiFive Unmatched",
        "pcb_revision": 3, "bom_revision": "B", "bom_variant": 0,
        "serial": "SF105SZ212200391", "manuf_test_status": "pass",
        "mac": "70:b3:d5:92:f8:de", "crc": "0x9709e522", "crc_ok": True,
    }


def test_decode_refuses_what_is_not_a_sifive_eeprom():
    assert probe.sifive_eeprom_decode(b"\xff" * 256) is None
    assert probe.sifive_eeprom_decode(b"") is None
    assert probe.sifive_eeprom_decode(UNMATCHED_1[:20]) is None


def test_a_corrupt_eeprom_says_its_crc_is_wrong():
    bad = bytearray(UNMATCHED_1)
    bad[20] ^= 0x01                       # one bit of the serial
    e = probe.sifive_eeprom_decode(bytes(bad))
    assert e["crc_ok"] is False
    assert e["crc"] == "0x9709e522"


def test_riscv_cpu_from_cpuinfo():
    cpu = probe.riscv_cpu(UNMATCHED_CPUINFO)
    assert cpu == {
        "harts": 4, "isa": "rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd", "mmu": "sv39",
        "uarch": "sifive,bullet0", "mvendorid": "0x489", "marchid": "0x8000000000000007",
        "mimpid": "0x20181004",
    }
    assert probe.riscv_cpu("processor\t: 0\nmodel name\t: ARMv7 Processor rev 5 (v7l)\n") is None


# /proc/cpuinfo on hifive-unmatched-1, 2026-09-26: the four U74 application
# harts, 1-4, the booting one listed first (hart 0, the S7 monitor core, is
# not Linux's).
UNMATCHED_CPUINFO = "".join(
    f"processor\t: {n}\nhart\t\t: {hart}\n"
    "isa\t\t: rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd\n"
    "mmu\t\t: sv39\nuarch\t\t: sifive,bullet0\nmvendorid\t: 0x489\n"
    "marchid\t\t: 0x8000000000000007\nmimpid\t\t: 0x20181004\n"
    "hart isa\t: rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd\n\n"
    for n, hart in enumerate((4, 1, 2, 3)))


def _w(root, rel, content):
    path = root / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


# Where the kernel puts the board EEPROM's nvmem: under the at24 device.
EEPROM_NVMEM = "/sys/bus/i2c/devices/0-0054/board-id0/nvmem"


def _unmatched_tree(root, eeprom=UNMATCHED_1, serial="SF105SZ212200391"):
    """hifive-unmatched-1 as it read on 2026-09-26: the device tree U-Boot
    hands the kernel, the board EEPROM behind at24 on i2c-0 at 0x54 (whose
    nvmem the kernel names after the device tree's label, board-id0, and
    serves to root alone), the GEM on macb, its NVMe and its SD card."""
    _w(root, "/proc/device-tree/model", "SiFive HiFive Unmatched A00\0")
    _w(root, "/proc/device-tree/compatible",
       "sifive,hifive-unmatched-a00\0sifive,fu740-c000\0sifive,fu740\0")
    if serial is not None:
        _w(root, "/proc/device-tree/serial-number", serial + "\0")
    _w(root, "/proc/cpuinfo", UNMATCHED_CPUINFO)
    _w(root, "/proc/meminfo", "MemTotal:       16358196 kB\n")
    if eeprom is not None:
        _w(root, EEPROM_NVMEM, eeprom)
        nvmem = root / "sys/bus/nvmem/devices"
        nvmem.mkdir(parents=True, exist_ok=True)
        (nvmem / "board-id0").symlink_to(root / EEPROM_NVMEM.lstrip("/").rsplit("/", 1)[0])
    _w(root, "/sys/class/net/end0/address", "70:b3:d5:92:f8:de\n")
    gem = root / "sys/devices/platform/soc/10090000.ethernet"
    gem.mkdir(parents=True)
    (root / "sys/class/net/end0/device").symlink_to(gem)
    (root / "sys/bus/platform/drivers/macb").mkdir(parents=True)
    (gem / "driver").symlink_to(root / "sys/bus/platform/drivers/macb")
    _w(root, "/sys/class/nvme/nvme0/model", "WDC WDS100T2B0C-00PXH0                  \n")
    _w(root, "/sys/class/nvme/nvme0/serial", "21210J802282        \n")
    _w(root, "/sys/class/nvme/nvme0/firmware_rev", "211210WD\n")
    card = "/sys/class/mmc_host/mmc0/mmc0:0000"
    _w(root, card + "/name", "SD32G\n")
    _w(root, card + "/serial", "0xb81f9080\n")
    _w(root, card + "/cid", "035344534433324785b81f9080014c61\n")
    _w(root, card + "/type", "SD\n")


@pytest.fixture
def rv_root(tmp_path, monkeypatch):
    _unmatched_tree(tmp_path)
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    calls = []

    def fake_sh(args, timeout=15):
        calls.append(args)
        return ""
    monkeypatch.setattr(probe, "sh", fake_sh)
    monkeypatch.setattr(probe, "sudo_read_bytes", lambda path: calls.append(path))
    return tmp_path, calls


def test_collect_hifive_unmatched(rv_root):
    _root, calls = rv_root
    d = probe.collect()
    assert d["board"] == "riscv"
    assert d["model"] == "SiFive HiFive Unmatched A00"
    assert d["serial"] == "SF105SZ212200391"
    assert d["revision"] is None
    assert d["pi5"] is False
    assert calls == [], "the EEPROM was readable, so no sudo; nothing Pi-only either"
    rv = d["riscv"]
    assert rv["cpu"]["isa"] == "rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd"
    assert rv["eeprom"]["serial"] == "SF105SZ212200391"
    assert rv["eeprom"]["crc_ok"] is True
    assert rv["eeprom_path"] == EEPROM_NVMEM
    assert rv["eeprom_error"] is None
    assert rv["storage"] == [
        {"kind": "nvme", "name": "nvme0", "model": "WDC WDS100T2B0C-00PXH0",
         "serial": "21210J802282", "firmware": "211210WD", "cid": None},
        {"kind": "SD", "name": "mmc0:0000", "model": "SD32G", "serial": "0xb81f9080",
         "firmware": None, "cid": "035344534433324785b81f9080014c61"},
    ]
    ifaces = {i["name"]: i for i in d["interfaces"]}
    assert ifaces["end0"]["onboard"] is True
    assert ifaces["end0"]["kind"] == "eth"
    v = probe.verdict(d)
    assert v["header"] == ["no HAT header on this board"]
    assert any(e.startswith("RISC-V: 4 harts rv64imafdc") for e in v["evidence"])
    assert any("SiFive EEPROM: HiFive Unmatched PCB rev 3 BOM B0 serial SF105SZ212200391"
               in e for e in v["evidence"])
    assert any(e.startswith("storage nvme0 WDC WDS100T2B0C-00PXH0 serial 21210J802282")
               for e in v["evidence"])
    s = v["summary"]
    assert s["serial"] == "SF105SZ212200391"
    assert s["compatible"] == "sifive,hifive-unmatched-a00 sifive,fu740-c000 sifive,fu740"
    assert s["memory"] == "16 GB"
    assert s["macs"] == [{"kind": "eth", "mac": "70:b3:d5:92:f8:de", "signal": "driver"}]
    assert s["riscv"] == {
        "harts": 4, "isa": "rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd", "mmu": "sv39",
        "uarch": "sifive,bullet0", "mvendorid": "0x489", "marchid": "0x8000000000000007",
        "mimpid": "0x20181004",
        "eeprom": probe.sifive_eeprom_decode(UNMATCHED_1), "eeprom_error": None,
    }


def test_a_root_only_eeprom_is_read_through_sudo(rv_root, monkeypatch):
    root, _calls = rv_root
    path = root / EEPROM_NVMEM.lstrip("/")
    path.chmod(0o000)
    asked = []

    def sudo(p):
        asked.append(p)
        return UNMATCHED_1
    monkeypatch.setattr(probe, "sudo_read_bytes", sudo)
    try:
        d = probe.collect()
    finally:
        path.chmod(0o644)
    assert asked == [str(path)]
    assert d["riscv"]["eeprom"]["serial"] == "SF105SZ212200391"


def test_an_unreadable_eeprom_is_said_with_the_command_that_reads_it(rv_root):
    root, _calls = rv_root
    path = root / EEPROM_NVMEM.lstrip("/")
    path.chmod(0o000)
    try:
        d = probe.collect()        # sudo_read_bytes is stubbed to give nothing
    finally:
        path.chmod(0o644)
    rv = d["riscv"]
    assert rv["eeprom"] is None
    assert rv["eeprom_error"] == (
        f"could not read {EEPROM_NVMEM}, even through sudo -n: "
        f"run `sudo od -A x -t x1z {EEPROM_NVMEM}` on the host")
    assert d["serial"] == "SF105SZ212200391", "the device tree still has it"


def test_the_eeprom_serial_stands_in_for_a_device_tree_without_one(tmp_path, monkeypatch):
    _unmatched_tree(tmp_path, serial=None)
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "sh", lambda args, timeout=15: "")
    d = probe.collect()
    assert d["serial"] == "SF105SZ212200391"


def test_the_eeprom_is_only_looked_for_on_a_board_known_to_have_one(tmp_path, monkeypatch):
    """0x54 on i2c-0 is the Unmatched's EEPROM; on any other board it is
    whatever that board put there, and nothing is read from it."""
    _unmatched_tree(tmp_path)
    _w(tmp_path, "/proc/device-tree/compatible", "starfive,visionfive-2-v1.3b\0starfive,jh7110\0")
    _w(tmp_path, "/proc/device-tree/model", "StarFive VisionFive 2 v1.3B\0")
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "sh", lambda args, timeout=15: "")
    d = probe.collect()
    assert d["board"] == "riscv", "a RISC-V hart makes it a RISC-V board"
    assert d["riscv"]["eeprom"] is None
    assert d["riscv"]["eeprom_path"] is None
    assert d["riscv"]["eeprom_error"] is None


def test_an_arm_board_has_no_riscv_record(tmp_path, monkeypatch):
    _w(tmp_path, "/proc/device-tree/model", "Xunlong Orange Pi PC\0")
    _w(tmp_path, "/proc/device-tree/compatible", "xunlong,orangepi-pc\0allwinner,sun8i-h3\0")
    _w(tmp_path, "/proc/cpuinfo", "processor\t: 0\nmodel name\t: ARMv7 Processor rev 5 (v7l)\n")
    monkeypatch.setattr(probe, "ROOT", str(tmp_path))
    monkeypatch.setattr(probe, "sh", lambda args, timeout=15: "")
    d = probe.collect()
    assert d["board"] == "opi"
    assert d["riscv"] is None
    assert probe.verdict(d)["summary"]["riscv"] is None


# --- the label's side ---------------------------------------------------------

def _doc(host, raw):
    return ProbeDocument.from_dict(host, raw)


def test_identify_the_unmatched(docs):
    s = docs["hifive-unmatched-1"].summary
    assert boards.board_kind(s) == "riscv"
    ident = boards.identify(s)
    assert ident.kind == "riscv"
    assert ident.title == "HiFive Unmatched A00"
    assert ident.short == "HiFive Unmatched"
    assert ident.subtitle == "16 GB  ·  FU740  ·  dt hifive-unmatched-a00"
    assert ident.mark == "sifive.svg"
    assert ident.wired is True
    assert ident.radio is False


def test_board_record_carries_the_riscv_band(docs):
    b = labels.board_record(docs["hifive-unmatched-1"])
    assert b.kind == "riscv"
    assert b.serial == "SF105SZ212200391"
    assert b.macs == (("eth", "70:b3:d5:92:f8:de"),)
    assert b.wlan_note == "no radio"
    assert b.isa == "rv64imafdc_zicntr_zicsr_zifencei_zihpm_zca_zcd"
    assert b.riscv_line == "4 harts  ·  sv39  ·  PCB rev 3  ·  BOM B0"


def test_an_unread_serial_refuses_the_label_naming_host_and_command(docs):
    raw = docs["hifive-unmatched-1"].evidence
    raw["verdict"]["summary"]["serial"] = None
    raw["verdict"]["summary"]["riscv"]["eeprom"] = None
    raw["verdict"]["summary"]["riscv"]["eeprom_error"] = (
        f"could not read {EEPROM_NVMEM}, even through sudo -n: "
        f"run `sudo od -A x -t x1z {EEPROM_NVMEM}` on the host")
    doc = _doc("hifive-unmatched-1", raw)
    with pytest.raises(labels.IdentifierNotReadError) as exc:
        labels.board_record(doc)
    msg = str(exc.value)
    assert msg.startswith("hifive-unmatched-1:")
    assert f"sudo od -A x -t x1z {EEPROM_NVMEM}" in msg


def test_the_eeprom_and_the_device_tree_must_agree(docs):
    """Two readings of one serial that differ is not something to pick
    between on a sticker."""
    raw = docs["hifive-unmatched-1"].evidence
    raw["verdict"]["summary"]["serial"] = "SF105SZ212200532"
    with pytest.raises(ValueError, match="SF105SZ212200391"):
        labels.board_record(_doc("hifive-unmatched-1", raw))


def test_soc_name():
    assert riscv.soc_name(["sifive,hifive-unmatched-a00", "sifive,fu740-c000",
                           "sifive,fu740"]) == "FU740"
    assert riscv.soc_name(["starfive,visionfive-2-v1.3b", "starfive,jh7110"]) == "jh7110"
    assert riscv.soc_name([]) is None
