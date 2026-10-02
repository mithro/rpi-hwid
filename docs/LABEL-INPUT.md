# The label input document

What the labels need for one host, in a shape that something other than
rpi-hwid's own probe can write: the fpgas.online site builds it from what each
Pi sends, and renders the labels on its server. It is versioned, it is checked
field by field, and it is written one way only, so a Pi and the site that
build it for the same host write the same bytes.

`rpi-hwid labels --data DIR` reads these beside probe documents (see
[COLLECT.md](COLLECT.md)); the labels made from either are the same.

## Version 1

```json
{
 "host": "pi-sw2-p48",
 "schema": "rpi-hwid/label-input",
 "sources": {"fpga": "fpgas-verify", "serial": "registration", "header": "rpi-hwid"},
 "summary": {
  "fan": true,
  "fpga": [{"kind": "acorn", "dna": "0x0054b48664b04854", "idcode": "0x13636093",
            "flash_jedec": "0x010219", "flash_extended_id": "0x4d0180",
            "flash_uid": "edcbeececb2b2a88b04f914d2e46af90", "flash_uid_bits": 128,
            "flash_uid_state": "read"}],
  "hat_uuid": "9729525c-eeee-98e9-f348-a0720f4c16eb",
  "header": ["Waveshare PoE M.2 HAT+ (B)"],
  "macs": [{"kind": "eth", "mac": "88:a2:9e:45:85:77"}],
  "model": "Raspberry Pi 5 Model B Rev 1.1",
  "revision": "b04171",
  "rtc_battery": false,
  "serial": "0cd35697db04a4ab"
 },
 "version": 1
}
```

(The Pi 5 and Acorn at pi-sw2-p48, as a builder might send it: what it did
not get, it left out. `dumps` writes the same document with every other field
filled in, as the rules below say.)

| key | |
|---|---|
| `schema` | always `"rpi-hwid/label-input"` |
| `version` | the integer `1`. A reader refuses anything else (`2`, `1.0`, `true`, `"1"`): a version it was not taught is not read hopefully. |
| `host` | the host the labels are for, a non-empty string |
| `summary` | the facts, under the field names of `rpi_hwid.model.Summary` verbatim; its `fpga`, `tinytapeout`, `macs` and `usb_net` lists hold `FpgaBoard`, `TinyTapeoutBoard`, `Mac` and `UsbNetAdapter` records, again by their field names. [COLLECT.md](COLLECT.md#the-document) describes each field. |
| `sources` | provenance, free-form: any keys, any JSON values, not compared and read by no label. rpi-hwid writes, per summary field, who read it (`rpi-hwid`, or `fpgas-verify` for the FPGA boards); the site writes its own (its event names, say). |

The rules:

* **`null` means not read.** It is kept as it is.
* **An absent field is filled in with its default**: the value rpi-hwid's own
  probe writes when it has nothing there, `[]` for a list (`macs`, `usb_net`,
  `fpga`, `tinytapeout`, a board's `dna_sources`) and `""` for `compatible`;
  `null` for a field with no default (`model`, `serial`, `revision`, and every
  optional one). So a builder that sends only the fields it has, leaving out
  empty lists and nulls, writes the same document as the Pi.
* **`header` is the exception.** `[]` is a header that was read and has
  nothing on it (the label prints "HAT none"); `null` is a header nobody read.
  So an absent `header` is `null`, not read, and never `[]`: a Pi label whose
  header was not read is refused, never printed as "HAT none".
* **Unknown keys are refused**, at every level: a builder that renamed a field
  is told so rather than having it dropped. A builder holding more than the
  labels take (fpgas-verify's per-board dict carries `variant`, `bdf` and
  others) keeps only `label_input.PI_FIELDS`, `FPGA_FIELDS` and `TT_FIELDS`.
* **Types are checked**: strings are strings, booleans are JSON booleans,
  integers are JSON numbers. A whole number in a float field (`ext5v_v`) is
  written as a float, so `5` and `5.0` are one text. A list never holds a
  null: it lists what was read. `dmi` (a PC's) and `riscv` (a RISC-V
  board's) are checked against the keys and types the probe writes in them,
  and `dna_conflict` maps each method to the DNA it read, as strings.
* **The version** is checked by the reader. The schema says `integer`, but
  JSON Schema counts `1.0` as an integer, so only the reader refuses it.
* **Hex identifiers are lower case and `0x`-prefixed**, as the probe writes
  them.
* **A record in a list needs the fields it is built from**: an FPGA board its
  `kind`, a MAC its `kind` and `mac`, a USB adapter its `iface`, `mac`,
  `vidpid` and `kind`. Without them it is not a record, and the document is
  refused. Numbers are finite: a NaN or an infinity is refused.

## What each label needs

`label_input.missing(doc)` lists every label the document describes, each
with the fields it still needs; `rpi-hwid labels --data DIR --check` prints
the same for every document in a directory, probe documents included, and
exits 1 if any label is short. A label that is short of a field is refused,
with this list, rather than printed with a gap or a placeholder.

```python
>>> label_input.missing(doc)
{"board": ["header"], "fpga[0]": [], "usb_net[0]": []}
```

The keys are `board` (the host's own label) and `fpga[i]`, `tinytapeout[i]`
and `usb_net[i]` by position in those lists. A list that is empty or null describes no
labels, so a document of the Pi's facts alone (`--pi-only`) has no `fpga[i]`
keys until the FPGA boards are added. A board this package has no label for
has no `board` key, and an FPGA board with no label (a `fomu`, or a `tt`,
which has a Tiny Tapeout label of its own) has no `fpga[i]` key: the keys
keep the board's own position, so the next board is still `fpga[1]`.

Only the labels being made are refused: `--only fpga` does not stop on a Pi
whose header was not read, and `--check` lists only what `--only` asks for.

| label | needs | optional |
|---|---|---|
| Raspberry Pi | `model`, `serial`, `revision` (one this package can decode), `macs`, `header` (`[]` when read and bare); on a Pi 5 also `fan` and `rtc_battery` | `memory`, `hat_uuid`, `compatible`, `power_class`, `max_current_ma`, `ext5v_v` |
| Orange Pi | `serial`, `compatible`, `memory`, `macs`, `header` | the rest |
| RISC-V board | `serial`, `macs`, `riscv` | the rest |
| PC | `macs`, `dmi` (a PC's firmware may carry no serial, which its label says) | the rest |
| FPGA: Acorn, NeTV2, PCILeech, other Xilinx | `dna`, `idcode`, `flash_jedec`, `flash_uid_state` (not `blank`), and `flash_uid` when it is `read` or `flash_uid_note` when it is `none` | `flash_extended_id` and `flash_sfdp` (without them a part whose ID several parts share prints as its family, e.g. S25Fx256S), `soc_model`, `flash_uid_bits`, `flash_error`, `flash_source` |
| FPGA: Arty | as above, and `serial` (its FT2232's) | as above |
| FPGA: Cynthion | `trace_id`, `hw_rev`, `serial` | the rest |
| Tiny Tapeout | `usb_serial`, `mcu`, `chip`, `demoboard`; `shuttle` when `chip` is `asic` | `commit`, `repo`, `demoboard_version`, `sdk` |
| USB network adapter | (its record's own fields) | `driver`, `manufacturer`, `product`, `usb_serial`, `bcd_usb`, `usb_speed`, `signal` |

Some refusals are not a missing field, and still stop a label: a flash ID
that no table names, a RISC-V board whose serial and EEPROM disagree, a PC
whose serial the probe could not read. These come from the label code with
their own explanation.

The JSON Schema ships with the package, derived from the same records
(`label_input.schema_path()`, `src/rpi_hwid/label-input-v1.schema.json`).

## Writing it

Only `label_input.dumps` writes the document: checked, normalised (every field
present, filled in as above, lists not tuples), keys sorted, one-space indent,
ASCII, a trailing newline. `label_input.comparable` is the same text without
`sources`, and is what two builders of one host's document compare: who read
a field may legitimately differ between the Pi and the site, the facts may
not.

On a host, from a probe document (the full probe, which also reads the
header's user bus; it is not what the fpgas.online site's documents are
compared with):

```
$ rpi-hwid probe --json > pi-sw2-p48.json
$ rpi-hwid label-input --from pi-sw2-p48.json > labels/pi-sw2-p48.json
```

## The Pi alone: `--pi-only`

```
$ sudo rpi-hwid label-input --pi-only [--user-bus] [--host NAME]
```

The Pi's facts (model, serial, revision, memory, MACs, the HAT, the power
class, and on a Pi 5 the fan, the RTC battery and the PMIC's readings) as a
label input on stdout, with `fpga` and `tinytapeout` empty: nothing probed
them. fpgas-verify runs it once at boot, before it takes any board's lock,
and sends the summary to the fpgas.online site in its `pi-identified` event,
so it is made to be safe while a board under test is wired to the header.
`--host` defaults to this host's name.

**What the site's documents are compared with.** The site builds a host's
label input from that event and fpgas-verify's board identities. The
document on the Pi it must match, under `label_input.comparable`, is these
Pi facts plus fpgas-verify's identity of each board -- what `rpi-hwid labels
--this-host` builds -- and not `label-input --from` a full probe, which can
also read the user bus and so can differ by design.

In the event (contract 13 and 17), a field that was not read is left out --
here that is `header`, when nothing the probe may look at named a HAT -- and
the site leaves it out of what it builds, an absent `header` reading back as
not read. A scalar that was read and is none (a Pi 4's `fan` and
`rtc_battery`, a HAT with no `hat_uuid`) is sent as `-`, and a list read
empty as `[]`.

What it does, and puts back:

* **The HAT ID bus** (pins 27/28, GPIO0/1, i2c-0 on a Pi): where it is not
  already up, `modprobe i2c-dev` and `dtparam i2c_vc=on`; then a read of the
  HAT EEPROM addresses 0x50-0x57; then the dtparam is taken out and
  `modprobe -r i2c-dev`, so the host is left as it was found, even when the
  read fails. Only the probe's own dtparam is removed: `dtparam -l` before
  and after the apply finds its entry, and `dtparam -r <index>` removes that
  entry alone (a bare `dtparam -r` removes the last runtime entry, whoever
  applied it: `dtoverlay_remove()` in raspberrypi/utils
  `dtmerge/dtoverlay_main.c`). When the list shows no new entry of its own --
  the apply failed, or something else changed the list meanwhile -- it
  removes nothing. The firmware's own reading of the HAT comes from
  `/proc/device-tree/hat`.
* `vcgencmd get_throttled`, and on a Pi 5 `sudo vcgencmd pmic_read_adc`: reads.
* sysfs, procfs and the device tree: reads. On a PC (no device tree), the
  root-only DMI serials through `sudo -n cat`.

What it never does:

* **Touch the header's user bus** (pins 3/5, GPIO2/3), unless `--user-bus`
  asks: no enable, no open, no scan. An Acorn's J5 is on GPIO3, and a Pmod
  HAT's lines are on the header. A HAT known only by the devices it puts
  there (a Waveshare PoE HAT (B)) therefore goes unseen, and when nothing at
  all is found on the header, `header` is null (not read) rather than `[]`:
  no label says "HAT none" of a HAT that was never looked for.
* Probe an FPGA board, a Tiny Tapeout board or an ESP32, or stop a service.

`--user-bus` (contract 18) scans the user bus too, the same way the ID bus is
read: where it is not already up, `modprobe i2c-dev` and `dtparam
i2c_arm=on`, a quick-write scan of the addresses, then its own dtparam
removed and the module unloaded, so the host is left as it was found. With
it read, a header with nothing on it is `[]`, and the Pi label can be made.
It drives GPIO2/3, so it is for when nothing else may be using them:
fpgas-verify passes it only at boot, before any test, and only where the
setup's wiring says those pins are safe for I2C then.

## This host's labels: `labels --this-host`

```
$ sudo rpi-hwid labels --this-host [--out labels.pdf] [--list] [--input FILE] [--host NAME]
$ sudo fpgas-verify --label                     # the same, started from fpgas-verify
```

Builds this host's label input on the host and makes its labels from it: the
Pi's facts from the Pi-only probe above, and the FPGA boards from the FPGA
module with fpgas-verify's identity (see [PROBE.md](PROBE.md#fpga-boards)),
with no `--jtag`, `--flash`, `--soc` or `--force-offline` of its own. Started
by `fpgas-verify --label`, which sets `FPGAS_VERIFY_IDENTITY`, it sends nothing
to any FPGA at all. `--input` also writes the label input it built:
`rpi_hwid.this_host.label_input_document(host)` from Python.

That document is what the fpgas.online site's must match under
`label_input.comparable`, the site building it from the `pi-identified` and
`fpga-board-identified` events. A label it is short of a field for is refused
with the list (`labels --check` says the same for a directory). A Tiny Tapeout
board is not in it: reading one means taking its demo board's port from the
service using it, which this does not do.

## From Python

Everything takes and returns plain dicts, touches no hardware and starts no
process, so it is safe in a web worker.

```python
from rpi_hwid import label_input

doc = label_input.build("pi-sw2-p48", summary, sources)  # checked and normalised
doc = label_input.load(text_or_dict)                     # the same, from a document
text = label_input.dumps(doc)                            # the one serialisation
same = label_input.comparable(a) == label_input.comparable(b)
record = label_input.to_probe_document(doc)              # what the label code reads
```

`build`, `load`, `dumps` and `comparable` raise `label_input.InputError`, whose
`problems` lists every reason the document was refused, not only the first.
