# Collecting a fleet, and the document it writes

How `rpi-hwid collect` gathers a fleet over ssh, the shape of the JSON it writes,
and how to read that from Python. For what the probe itself reads, see
[PROBE.md](PROBE.md); for the labels made from this data, see
[LABELS.md](LABELS.md).

## Collecting a fleet

`rpi-hwid collect` pushes the probe source to each host over ssh (nothing is
installed on the Pi), in parallel, and writes `<host>.json` per host. The FPGA
module is appended with `--fpga` for every host, or with `--jtag HOST` and
`--flash HOST` for the hosts that should drive JTAG; the Tiny Tapeout module with
`--tinytapeout`. A login banner before the JSON is skipped.

```
$ rpi-hwid collect --out data/ -J jump.example.org --fpga --jtag pi@10.21.2.16 \
      rpi5-netv2 rpiz-serial pi@10.21.2.16 pi@10.21.2.47
  rpi5-netv2: Raspberry Pi 5 Model B Rev 1.0; header bare; power usbc-supply
  rpiz-serial: Raspberry Pi Zero W Rev 1.1; header ['Waveshare PoE-ETH-USB-HUB-HAT']; power bonnet-poe
  pi@10.21.2.16: Raspberry Pi 4 Model B Rev 1.5; header ['Pmod HAT Adaptor']; power undetermined; fpga 0x00628502251ea85c
  pi@10.21.2.47: Raspberry Pi 5 Model B Rev 1.1; header ['Waveshare PoE M.2 HAT+ (B)']; power gpio-poe-hat; fpga acorn
4 of 4 host(s) written to data
```

`--users` lists the login names to try in order (default: you, then `pi`); the one
that worked is recorded in the document. A host that cannot be reached is reported
and skipped, and the exit status says so.

## The document

`rpi-hwid probe --json`, and every file the collector writes, is one JSON object:
the raw evidence as it came off the board, plus a `verdict` block whose
fixed-shape `summary` everything else in the package consumes.

```json
{
  "model": "Raspberry Pi Zero W Rev 1.1",
  "serial": "000000005157f671",
  "revision": "9000c1",
  "hat_fw": null,
  "hat_eeproms": {},
  "i2c1": [],
  "usb": {"1-1": "1a40:0101", "1-1.4": "0bda:8152"},
  "interfaces": ["…"],
  "usb_net": ["…"],
  "throttled": "0x0",
  "verdict": {
    "header": ["Waveshare PoE-ETH-USB-HUB-HAT (1a40:0101 hub with RTL8152 on port 4)"],
    "power": "PoE through the Waveshare PoE-ETH-USB-HUB-HAT bonnet",
    "evidence": ["power port: throttled=0x0"],
    "summary": {
      "model": "Raspberry Pi Zero W Rev 1.1",
      "serial": "000000005157f671",
      "revision": "9000c1",
      "compatible": "raspberrypi,model-zero-w brcm,bcm2835",
      "memory": "512 MB",
      "header": ["Waveshare PoE-ETH-USB-HUB-HAT"],
      "hat_uuid": null,
      "power_class": "bonnet-poe",
      "macs": [{"kind": "eth", "mac": "00:e0:4c:36:0b:0a"},
               {"kind": "wlan", "mac": "b8:27:eb:02:a3:24"}],
      "usb_net": [],
      "rtc_battery": null, "fan": null, "max_current_ma": null, "ext5v_v": null,
      "fpga": []
    }
  }
}
```

(Lists shortened.) The `summary` is the contract: `header` is what sits on the
40-pin header, `macs` the soldered-down interfaces (`eth` first), `usb_net` the
removable adapters with their descriptors, `fpga` the boards the FPGA module
found, `tinytapeout` the demo boards the Tiny Tapeout module found (present only
when that module ran), `hat_uuid` the EEPROM's UUID when one was read,
`compatible` the device tree's compatible list and `memory` the fitted RAM
(MemTotal rounded up to the size that was soldered on). The Pi 5-only fields are
`null` elsewhere. Everything outside `verdict` is evidence, kept so a wrong
verdict can be argued with.

## From Python

Everything on the collecting side is a frozen dataclass (`rpi_hwid.model`); the
probes emit JSON because they run on a Pi's Python 3.5.

```python
from pathlib import Path

from rpi_hwid.boards import identify
from rpi_hwid.collect import collect, load_collected
from rpi_hwid.names import netv2_name

collect(["rpi5-netv2", "pi@10.21.2.47", "pi@10.21.2.22"], Path("data"),
        jump="jump.example.org", fpga=True)

for host, doc in load_collected(Path("data")).items():
    s = doc.summary
    board = identify(s)        # Pi: from the revision code; Orange Pi: from the device tree
    print(host, board.title, board.memory, s.power_class, [m.mac for m in s.macs])
    for board in s.fpga:
        name = netv2_name(board.dna) if board.kind == "netv2" and board.dna else ""
        print("  ", board.kind, board.identity, name)
    for tt in s.tinytapeout:
        print("  ", tt.shuttle, tt.chip, tt.demoboard, tt.usb_serial)
```

`Summary.from_dict` refuses a field it does not know, so a probe that has grown a
field is noticed when its document is loaded, not silently dropped.
`ProbeDocument.evidence` keeps the whole raw document for anything the summary
leaves out.
