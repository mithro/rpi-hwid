"""Exploration: arbitrary SPI flash commands through the ESP32 ROM loader,
with up to 64 bytes read back per command (the SPI controller's W0..W15
buffer), where esptool's run_spiflash_command returns only W0.

Read-only: every opcode sent here is a read. Resets the chip back into its
application at the end and listens to it boot.

usage: python3 flash_explore.py PORT [LISTEN_SECONDS]
"""
import json
import sys
import termios
import time

import esptool

port = sys.argv[1]
listen = float(sys.argv[2]) if len(sys.argv) > 2 else 8
out = {"port": port, "esptool": esptool.__version__, "errors": {}, "cmds": {}}

# opcodes this script may send; nothing that writes, erases or changes state
READ_ONLY = {0x9F, 0x90, 0xAB, 0x05, 0x35, 0x15, 0x5A, 0x4B, 0x48}


def spi(esp, cmd, nread=0, data=b"", addr=None, addr_len=0, dummy=0):
    """One SPI command through the USR machinery; returns nread bytes."""
    assert cmd in READ_ONLY, hex(cmd)
    assert 0 <= nread <= 64 and len(data) <= 64
    base = esp.SPI_REG_BASE
    r_cmd, r_addr = base, base + 4
    r_usr, r_usr1, r_usr2 = (base + esp.SPI_USR_OFFS, base + esp.SPI_USR1_OFFS,
                             base + esp.SPI_USR2_OFFS)
    r_w0 = base + esp.SPI_W0_OFFS
    r_mosi, r_miso = base + esp.SPI_MOSI_DLEN_OFFS, base + esp.SPI_MISO_DLEN_OFFS
    saved = {r: esp.read_reg(r) for r in (r_usr, r_usr1, r_usr2, r_mosi, r_miso)}
    try:
        flags = 1 << 31                      # USR_COMMAND
        if nread:
            flags |= 1 << 28                 # USR_MISO
        if data:
            flags |= 1 << 27                 # USR_MOSI
        if addr_len:
            flags |= 1 << 30                 # USR_ADDR
        if dummy:
            flags |= 1 << 29                 # USR_DUMMY
        esp.write_reg(r_mosi, max(len(data) * 8 - 1, 0))
        esp.write_reg(r_miso, max(nread * 8 - 1, 0))
        usr1 = saved[r_usr1] & ~((0x3F << 26) | 0xFF)
        if addr_len:
            usr1 |= (addr_len - 1) << 26
        if dummy:
            usr1 |= dummy - 1
        esp.write_reg(r_usr1, usr1)
        esp.write_reg(r_usr, flags)
        esp.write_reg(r_usr2, (7 << 28) | cmd)
        if addr_len:
            # the original ESP32 wants the address in the register's top
            # bits; later chips in its bottom ones. esptool 5 says which
            # (SPI_ADDR_REG_MSB); esptool 4.7 has no such attribute
            msb = getattr(esp, "SPI_ADDR_REG_MSB", esp.CHIP_NAME in ("ESP32", "ESP8266"))
            esp.write_reg(r_addr, addr << (32 - addr_len) if msb else addr)
        padded = data + b"\0" * (-len(data) % 4)
        for i in range(0, max(len(padded), 4 * ((nread + 3) // 4)), 4):
            word = int.from_bytes(padded[i:i + 4], "little") if i < len(padded) else 0
            esp.write_reg(r_w0 + i, word)
        esp.write_reg(r_cmd, 1 << 18)        # USR
        for _ in range(20):
            if not esp.read_reg(r_cmd) & (1 << 18):
                break
        else:
            raise RuntimeError("SPI command 0x%02x did not complete" % cmd)
        raw = b"".join(esp.read_reg(r_w0 + i).to_bytes(4, "little")
                       for i in range(0, nread, 4))
        return raw[:nread]
    finally:
        for r, v in saved.items():
            esp.write_reg(r, v)


def rec(name, fn):
    try:
        v = fn()
        out["cmds"][name] = v.hex() if isinstance(v, bytes) else v
    except Exception as exc:
        out["errors"][name] = "%s: %s" % (type(exc).__name__, exc)


mode = "default-reset" if int(esptool.__version__.split(".")[0]) >= 5 else "default_reset"
esp = None
try:
    esp = esptool.cmds.detect_chip(port, 115200, mode)
    out["chip"] = esp.get_chip_description()
    out["features"] = list(esp.get_chip_features())
    esp.flash_spi_attach(0)
    fid = esp.flash_id()
    out["esptool_flash_id"] = "0x%06x" % fid
    rec("9f_rdid_64", lambda: spi(esp, 0x9F, 64))
    rec("90_rems", lambda: spi(esp, 0x90, 4, addr=0, addr_len=24))
    rec("ab_res", lambda: spi(esp, 0xAB, 4, addr=0, addr_len=24))
    for op in (0x05, 0x35, 0x15):
        rec("%02x_status" % op, lambda op=op: spi(esp, op, 2))
    # SFDP: 5Ah, 24-bit address, 8 dummy clocks
    rec("5a_sfdp_000", lambda: b"".join(
        spi(esp, 0x5A, 64, addr=a, addr_len=24, dummy=8) for a in range(0, 256, 64)))
    # Read Unique ID 4Bh, two ways: 4 dummy bytes then data (Winbond style),
    # and the same bytes read as one run of 32 so the tail can be seen
    rec("4b_uid_raw32", lambda: spi(esp, 0x4B, 32))
    rec("4b_uid_dummy32", lambda: spi(esp, 0x4B, 16, addr=0, addr_len=24, dummy=8))
    # Security registers 48h: 24-bit address, 8 dummy clocks; register n at n<<12
    for n in range(4):
        rec("48_secreg_%d" % n, lambda n=n: b"".join(
            spi(esp, 0x48, 64, addr=(n << 12) + a, addr_len=24, dummy=8)
            for a in range(0, 256, 64)))
except Exception as exc:
    out["errors"]["connect"] = "%s: %s" % (type(exc).__name__, exc)
finally:
    ser = None
    try:
        if esp is not None:
            esp.hard_reset()
            ser = esp._port
        out["reset"] = "hard_reset"
    except Exception as exc:
        out["errors"]["reset"] = "%s: %s" % (type(exc).__name__, exc)
    if ser is not None:
        try:
            attrs = termios.tcgetattr(ser.fileno())
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(ser.fileno(), termios.TCSANOW, attrs)
            ser.timeout = 0.2
        except Exception as exc:
            out["errors"]["hupcl"] = "%s: %s" % (type(exc).__name__, exc)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < listen:
            try:
                buf += ser.read(4096)
            except Exception as exc:
                out["errors"]["listen"] = "%s: %s" % (type(exc).__name__, exc)
                break
        out["boot_after"] = buf.decode("utf-8", "replace")[-1500:]
        try:
            ser.close()
        except Exception:
            pass
    print("RESULT " + json.dumps(out))
