"""Why did esp32.py's Read Unique ID read zeroes on the C3? Compare the two
ways of skipping 0x4B's four dummy bytes through esptool's own
run_spiflash_command: as MOSI data (esp32.py) and as address+dummy phases.
Read-only; resets the chip back into its application."""
import sys
import termios
import time

import esptool

port = sys.argv[1]
mode = "default-reset" if int(esptool.__version__.split(".")[0]) >= 5 else "default_reset"
esp = esptool.cmds.detect_chip(port, 115200, mode)
try:
    esp.flash_spi_attach(0)
    print("esptool", esptool.__version__, esp.get_chip_description())
    for i in range(2):
        a = esp.run_spiflash_command(0x4B, data=b"\0" * 4, read_bits=32)
        b = esp.run_spiflash_command(0x4B, read_bits=32, addr=0, addr_len=24, dummy_len=8)
        c = esp.run_spiflash_command(0x4B, read_bits=32)
        print("mosi-dummies 0x%08x  addr+dummy 0x%08x  no-skip 0x%08x" % (a, b, c))
finally:
    esp.hard_reset()
    ser = esp._port
    attrs = termios.tcgetattr(ser.fileno())
    attrs[2] &= ~termios.HUPCL
    termios.tcsetattr(ser.fileno(), termios.TCSANOW, attrs)
    ser.timeout = 0.2
    buf, t0 = b"", time.time()
    while time.time() - t0 < 6:
        buf += ser.read(4096)
    print("BOOT", buf.decode("utf-8", "replace")[-300:])
    ser.close()
