# The label contract

How three tools pass label data between them, and which tool owns which part.

| tool | repository | what it does for labels |
|---|---|---|
| fpgas-verify | [fpgas-online/fpgas.online-test-designs](https://github.com/fpgas-online/fpgas.online-test-designs) | reads each FPGA board's identity on the Pi; reports it at boot in `fpga-board-identified` events; prints it on demand with `fpgas-verify --identify` |
| rpi-hwid | this repository | owns the field names and the label input document; reads the Pi's own facts; makes the labels |
| the fpgas.online site | [fpgas-online/fpgas.online-site](https://github.com/fpgas-online/fpgas.online-site) | builds each Pi's label input document from that Pi's events, so a Pi's labels can be made without logging in to it |

```
                   boot                          on demand
fpgas-verify ── fpga-board-identified ──▶ site    fpgas-verify --identify ──▶ rpi-hwid labels --this-host
             ── pi-identified ──────────▶ site  (not sent yet: gap 2)                │
                                            │                                        ▼
                                            ▼                             label input document (Pi)
                                 label input document (site)
                         both compared with label_input.comparable(): they must be equal (gap 1)
```

Where the code does not yet do what this page says, the text points to
[Known gaps](#known-gaps) at the end.

This page describes the code on each repository's `main` as of 2026-10-03.
The detailed references are:

* fpgas-verify's identity: [docs/identity.md](https://github.com/fpgas-online/fpgas.online-test-designs/blob/main/docs/identity.md)
  and [`identity.py`](https://github.com/fpgas-online/fpgas.online-test-designs/blob/main/verify/src/fpgas_online_verify/identity.py),
  [`identify.py`](https://github.com/fpgas-online/fpgas.online-test-designs/blob/main/verify/src/fpgas_online_verify/identify.py),
  [`label.py`](https://github.com/fpgas-online/fpgas.online-test-designs/blob/main/verify/src/fpgas_online_verify/label.py).
* the label input document: [LABEL-INPUT.md](LABEL-INPUT.md), `src/rpi_hwid/label_input.py`.
* rpi-hwid's side of `--this-host`: `src/rpi_hwid/this_host.py`, and the
  fpgas-verify client in `src/rpi_hwid/fpga.py` (`identity_probe`, `identity_parse`).
* the site: [`fleet/src/fleet/hwid.py`](https://github.com/fpgas-online/fpgas.online-site/blob/main/fleet/src/fleet/hwid.py)
  (builds the document) and
  [`fleet/src/fleet/views.py`](https://github.com/fpgas-online/fpgas.online-site/blob/main/fleet/src/fleet/views.py)
  (serves it).

## Who owns what

| thing | owner | the others |
|---|---|---|
| field names (`FpgaBoard`, `TinyTapeoutBoard`, `Summary`) | rpi-hwid (`src/rpi_hwid/model.py`) | use them verbatim |
| the label input document, its checks and its one serialisation | rpi-hwid (`label_input`) | call `label_input`; never write the JSON themselves |
| what each label needs, and refusing a label short of a field | rpi-hwid (`label_input.missing`, `labels`) | |
| reading an FPGA board (JTAG, PCIe BAR, flash) | fpgas-verify | rpi-hwid asks fpgas-verify where it is installed |
| the `--identify` document and the `fpga-board-identified` event | fpgas-verify | rpi-hwid and the site read them |
| reading the Pi's facts | rpi-hwid (`label-input --pi-only`) | |
| the golden fixture `tests/data/identity-v1-acorn-p48.json` | fpgas-verify | rpi-hwid and the site keep byte-identical copies and test their readers on it |

## 1. fpgas-verify's identity

fpgas-verify builds one flat dict per board (`identity.py`; no nested
objects). The same dict is used, unchanged, in:

* the boot report, `/run/fpgas-online/verify.json` (its `identity`);
* the `fpga-board-identified` event (the dict's keys, plus `schema=fpga-identity/1`);
* the `--identify` document's `boards` list.

### Field names

| group | fields |
|---|---|
| rpi-hwid's `FpgaBoard` fields | `kind`, `serial`, `dna`, `idcode`, `flash`, `flash_jedec`, `flash_extended_id`, `flash_sfdp`, `flash_uid`, `flash_uid_bits`, `flash_uid_state`, `flash_uid_note`, `flash_error`, `flash_source`, `soc_model` |
| fpgas-verify's own (never a `FpgaBoard` name) | `board`, `variant`, `bdf`, `usb`, `identifier`, `build`, `flash_size_bytes`, `flash_status`, `flash_config`, `flash_quad`, `flash_uid_opcode`, `idcode_version`, `idcode_part_number`, `idcode_manufacturer`, `idcode_manufacturer_id`, `idcode_device` |
| a failed read | `<field>_error` (for example `dna_error`) |
| `--identify` only | `from_report` (see below) |

`kind` is one of `acorn`, `arty`, `netv2`, `tt`, `fomu`, `pcileech` (an
Acorn running the pcileech design) or `unknown-fpga`.

### Value formats

| field | format | example |
|---|---|---|
| a field that was not read | the key is absent | |
| a read that was tried and failed | `<field>_error`, a string saying why | |
| `dna` | lower case, `0x`, 16 hex digits | `0x0054b48664b04854` |
| `idcode` | lower case, `0x`, 8 hex digits, all 32 bits (version included) | `0x13636093` |
| `flash_jedec` | RDID bytes 1-3, lower case, `0x`, 6 hex digits | `0x010219` |
| `flash_extended_id` | RDID bytes 4-6, lower case, `0x`, 6 hex digits | `0x4d0180` |
| `flash_status`, `flash_config`, `flash_uid_opcode` | lower case, `0x`, 2 hex digits | `0x02` |
| `idcode_part_number` | lower case, `0x`, 4 hex digits | `0x3636` |
| `idcode_manufacturer_id` | lower case, `0x`, 3 hex digits | `0x049` |
| `flash_uid` | lower-case hex, no `0x` | `edcbeececb2b2a88b04f914d2e46af90` |
| integers (`flash_uid_bits`, `flash_size_bytes`, `idcode_version`) | JSON number in a document; decimal string in an event | `128` / `"128"` |
| booleans (`flash_quad`) | JSON boolean in a document; `"true"` / `"false"` in an event | |

### What each board kind reads

| kind | fields |
|---|---|
| `acorn`, `pcileech` | `board`, `kind`, `variant`, `bdf`, the IDCODE fields, `dna` (PCIe BAR0, else JTAG) or `dna_error`, the flash fields read over BAR0 (`flash_source` `pcie`) or `flash_error`, `soc_model`, `identifier`, `build` |
| `arty` | `board`, `kind`, `variant`, `serial` (its FT2232's), `usb`, the IDCODE fields; DNA and flash identity not yet ([gap 4](#known-gaps)) |
| `netv2` | `board`, `kind`, `variant`, the IDCODE fields, all from the JTAG scan that finds it (no `serial`, no `usb`); DNA and flash identity not yet ([gap 4](#known-gaps)) |
| `tt` | `board`, `kind`, `variant`, `serial`, `usb`; to carry `TinyTapeoutBoard`'s fields (`usb_serial`, `mcu`, `chip`, `demoboard`, `demoboard_version`, `sdk`), not yet ([gap 4](#known-gaps)) |
| `fomu` | `board`, `kind`, `variant`, `serial`, `usb` |

An Acorn whose flash does not identify itself gets
`flash_error` "the flash did not identify itself: ..." and fails its boot
check.

### The `--identify` document

Printed by `fpgas-verify --identify` (also with `--board B`), by each
`fpgas-<board>-verify --identify`, and by `fpgas-acorn-debug identify`.

```json
{
 "boards": [{"kind": "acorn", "dna": "0x0054b48664b04854", "...": "..."}],
 "identity_version": 1,
 "read_at": "2026-10-02T00:00:00+00:00",
 "schema": "fpgas-verify/identity",
 "source": "live",
 "tool": "fpgas-online-verify 0.0.post808"
}
```

| key | value |
|---|---|
| `schema` | always `"fpgas-verify/identity"` |
| `identity_version` | the integer `1` |
| `tool` | `"fpgas-online-verify <version>"` |
| `read_at` | ISO 8601, UTC, whole seconds |
| `source` | always `"live"` |
| `boards` | one dict per board, as above |

| rule | what the code does |
|---|---|
| one document | always printed, sorted keys, one-space indent, trailing newline |
| exit status | 0 when every board's label fields were read and at least one board was found; otherwise 1, with each gap on stderr. Readers read the document and ignore the exit status. |
| what is read live | only reads that do not disturb the board: IDCODE, the Acorn's DNA (BAR0, then JTAG) and its flash over BAR0 |
| what is never done | anything that loads or reconfigures a board (for example spiOverJtag on an Arty or NeTV2) |
| `from_report` | a field the live read cannot get is taken from the boot report, and its name listed in the board's `from_report`. A board matches a boot-report board only by the same `kind` and the same `serial` (Arty: FT2232 serial), `bdf` (Acorn: PCIe slot) or `dna`. An IDCODE names a part, not a board, so an IDCODE-only match is refused. Today it supplies nothing ([gap 3](#known-gaps)). |
| reasons a field stays missing | "no board-unique match in the boot report", "this board is not in the boot report", "board busy" |
| board locks | waits at most 30 s for each board's lock, one board at a time; a board whose lock it cannot get is "board busy" |

### Versions

| rule | |
|---|---|
| within version 1 | fields may be added; readers ignore keys they do not know |
| version 2 needed for | a rename, a removal, or a change of type or spelling of an existing field |
| readers | refuse any `identity_version` other than the integer `1` (not `true`, not `1.0`) |
| events | the same rule for the `fpga-identity` and `pi-identity` schemas (`pi-identity` is not sent yet: [gap 2](#known-gaps)) |
| rpi-hwid's label input | stricter: it refuses unknown keys, so a builder keeps only `label_input.FPGA_FIELDS`, `TT_FIELDS` and `PI_FIELDS` |

## 2. Nesting and soft dependencies

### `fpgas-verify --label`

1. Finds `rpi-hwid` on `PATH`. Without it, exits 2 saying how to install it.
2. Reads the identity (as `--identify`), and releases every board lock.
3. Writes the document to `/run/fpgas-online/identity-<pid>.json` (mode 0600; exits 2 if the file already exists or cannot be written).
4. Runs `rpi-hwid labels --this-host [--out F] [--list]` with `FPGAS_VERIFY_IDENTITY=<that file>`.
5. Deletes the file, also on SIGTERM.
6. Exits with rpi-hwid's exit status (128+N if rpi-hwid was killed by signal N).

### `FPGAS_VERIFY_IDENTITY`

Counts as set only when non-empty, in both tools.

| tool | when it is set |
|---|---|
| `fpgas-verify --identify` | prints the file unchanged after checking it (JSON, `schema`, `identity_version` exactly `1`, `boards` a list); takes no lock, runs no board code; a missing or bad file is exit 1, never a hardware read |
| any other `fpgas-verify` / `fpgas-<board>-verify` mode | refuses, exit 2 |
| rpi-hwid | sends nothing to any FPGA: no JTAG, no PCIe BAR, no pcileech, no Cynthion recovery |

### How rpi-hwid runs fpgas-verify

| case | command |
|---|---|
| running as root | `fpgas-verify --identify` |
| not root | `sudo -n fpgas-verify --identify` |
| not root, nested | `sudo -n --preserve-env=FPGAS_VERIFY_IDENTITY fpgas-verify --identify` |
| time allowed | 30 s per board found in sysfs plus 30 s, at least 60 s |
| when | whenever fpgas-verify is installed (`labels --this-host`), whatever sysfs shows |

### Soft dependencies

| | |
|---|---|
| fpgas-verify never imports rpi-hwid | only runs its CLI, found with `shutil.which`; a test checks no module imports it |
| fpgas-verify's deb | `Suggests: python3-rpi-hwid` |
| fpgas-verify's Python extra | `labels = ["rpi-hwid[labels]; python_version >= '3.11'"]` |
| rpi-hwid without fpgas-verify | reads FPGA boards passively from sysfs and its own FPGA module |
| the site | depends on `rpi-hwid[labels]` directly (a hard dependency) |

## 3. The event encoding

`fpga-board-identified` (one per board, `schema=fpga-identity/1`) and
`pi-identified` (`schema=pi-identity/1`; not sent yet, [gap 2](#known-gaps)) carry
flat `key=value` strings.

| value in the dict | in the event |
|---|---|
| not read | the key is left out |
| read, and none (JSON `null`) | `-` (scalars only) |
| a string | as it is |
| an integer | decimal |
| a boolean | `true` / `false` |
| a list or object (`header`, `macs`, `usb_net`: `pi-identified` fields, [gap 2](#known-gaps)) | one key; `json.dumps(v, separators=(",", ":"), sort_keys=True)`; never indexed keys like `header0` |
| a list read empty | `[]`, never `-` |

A site reading `-` for a list field treats it as not read (so the label is
refused). A float is never sent in an `fpga-board-identified` event.

## 4. Not read, and read as none

| in | not read | read, none |
|---|---|---|
| fpgas-verify's dict and events | key absent | `null` / `-` |
| the label input document | `null` | the field's default |
| `header` | `null` | `[]` (the label prints "HAT none") |

`label_input.dumps` fills an absent field with the record's default (the
value rpi-hwid's own probe writes: `[]` for a list, `""` for `compatible`,
`null` where there is none), so a builder that sends only what it has writes
the same document as the Pi. An explicit `null` stays `null`.

`header` is the exception: an absent `header` becomes `null` (not read),
never `[]`. A Pi label with a `null` header is refused (`missing()` lists
`header`); it is never printed as "HAT none".

## 5. Pi facts and the boot user-bus scan

| command | reads | never does |
|---|---|---|
| `rpi-hwid label-input --pi-only` | model, serial, revision, memory, MACs, `usb_net`, the HAT on the ID bus (GPIO0/1) and `/proc/device-tree/hat`, power class, and on a Pi 5 the fan, RTC battery and PMIC readings | touch GPIO2/3, probe an FPGA or Tiny Tapeout board, stop a service, pass `--tinytapeout` |
| `rpi-hwid label-input --pi-only --user-bus` | the same, and a scan of the header's user bus (GPIO2/3) | |

Both print a label input document (version 1) with `fpga` and
`tinytapeout` empty. Each side effect (`modprobe i2c-dev`, `dtparam`) is
listed in [LABEL-INPUT.md](LABEL-INPUT.md#the-pi-alone---pi-only) and undone.
Without `--user-bus`, a header with nothing found is `null` (not read).

| rule | |
|---|---|
| `--user-bus` | only at boot, before any test or board lock, and only where the setup's wiring says GPIO2/3 are safe for I2C then (an Acorn's J5 is on GPIO3) |
| never on demand | `labels --this-host` and `fpgas-verify --label` never scan the user bus: others may be using the board |
| `pi-identified` | the `--pi-only` summary under `Summary`'s field names, `schema=pi-identity/1`, encoded as in section 3; `macs` and `usb_net` as rpi-hwid's lists (`signal` included); `reader=none` when rpi-hwid is not installed. Not sent yet ([gap 2](#known-gaps)) |
| boot facts in `--identify` | an optional `"pi"` object: the boot `pi-identified` facts, typed JSON, with `read_at`. `labels --this-host` uses each of its fields where its own read left `null`, or left `power_class` `undetermined` or `ambiguous`; it never replaces a value it read. The fields taken are listed in `sources.pi_from_boot` (with `read_at`). |

None of this boot scan is done yet ([gap 2](#known-gaps)). rpi-hwid and the site
already read its results where present; without them, `labels --this-host`
uses its own read and the site notes "no usable pi-identified event from this Pi".

## 6. The label input document and the comparison

[LABEL-INPUT.md](LABEL-INPUT.md) is the full reference.

```json
{"schema": "rpi-hwid/label-input", "version": 1, "host": "...", "summary": {...}, "sources": {...}}
```

| rule | |
|---|---|
| written only by | `label_input.dumps`: checked, every field present, sorted keys, one-space indent, ASCII, trailing newline |
| on both sides | the Pi and the site both call it, so the same facts give the same bytes |
| `sources` | provenance: any keys, any JSON values; never read by a label |
| unknown keys | refused at every level |
| `version` | the integer `1`; anything else refused |

### The comparison

`label_input.comparable(doc)` is `dumps(doc)` without what may legitimately
differ between two reads. Both rpi-hwid and the site test with it.

| left out | why |
|---|---|
| `sources` | who read a field, not what it is |
| `ext5v_v` | the PMIC's ADC, measured on each read |
| `max_current_ma` | the USB-C current last negotiated; always left out, whether or not it was measured |
| each MAC's `signal` | which evidence settled the MAC on that read |

These stay in the document and on the labels as read.

| what the site's document is compared with | |
|---|---|
| the target | what `rpi-hwid labels --this-host` builds on the Pi: the `--pi-only` facts plus fpgas-verify's identity; the two must be equal under `comparable()` (today they differ for a board with a DNA: [gap 1](#known-gaps)) |
| not the target | `rpi-hwid label-input --from` a full probe, which may read the user bus and differs by design |

### Python API (no hardware, no subprocess, no module-global state)

| call | |
|---|---|
| `label_input.load(dict_or_text) -> dict` | checked and normalised |
| `label_input.build(host, summary, sources) -> dict` | the same, from parts |
| `label_input.dumps(dict) -> str` | the one serialisation |
| `label_input.comparable(dict) -> str` | the comparison text |
| `label_input.check(doc) -> [problems]` | every problem, not only the first |
| `label_input.missing(dict) -> {label: [fields]}` | what each label still needs |
| `labels.list_labels`, `labels.render_sheet(...) -> bytes`, `labels.render_label(...) -> bytes` | PDF rendering, safe in a web worker |

## 7. What each board kind gets

| kind | in the document | label |
|---|---|---|
| `acorn`, `arty`, `netv2` | `summary.fpga`, `FpgaBoard` fields only | FPGA label |
| `pcileech`, `unknown-fpga` | `summary.fpga`, `FpgaBoard` fields only | an FPGA label where its fields allow it |
| `tt` | `summary.tinytapeout`, `TinyTapeoutBoard` fields (`usb_serial`, `mcu`, `chip`, `demoboard`, `demoboard_version`, `sdk`, `shuttle`, `repo`, `commit`) | Tiny Tapeout label |
| `fomu` | left out | none |

| rule | rpi-hwid (`labels --this-host`) | the site |
|---|---|---|
| fpgas-verify's extra fields | dropped (keeps `FPGA_FIELDS`, `TT_FIELDS`) | dropped |
| an FPGA field read as none | dropped, so the default fills it | `-` dropped, so the default fills it |
| FPGA kinds kept | `acorn`, `arty`, `netv2`, `pcileech`, `unknown-fpga` | every kind except `tt` and `fomu` |
| `dna_sources` | `["fpgas-verify"]` when the board's `dna` came from fpgas-verify (today left `[]`: [gap 1](#known-gaps)) | `["fpgas-verify"]` when the event has a `dna` |
| a `tt` board without `usb_serial` | dropped | dropped, note "tinytapeout board without usb_serial: no label" |
| a `tt` board no longer on USB | dropped, its serial listed in `sources.tinytapeout_not_on_usb` | kept (the site cannot see USB); an expected difference |
| fpgas-verify installed but no document (sudo refused, timeout, nothing printed) | `fpga` and `tinytapeout` `null`, `sources.fpgas_verify_error` says why, `sources.fpga_sysfs` lists what sysfs saw; FPGA labels refused; `labels --this-host` exits 1 with the reason on stderr | |
| a label short of a field | refused with the list; no "print anyway" | refused with the list; the page shows what each label is missing |

Until fpgas-verify's `tt` dict carries `usb_serial` ([gap 4](#known-gaps)), both sides
drop every Tiny Tapeout board.

## 8. The site

| step | what [`hwid.py`](https://github.com/fpgas-online/fpgas.online-site/blob/main/fleet/src/fleet/hwid.py) does |
|---|---|
| Pi facts | the newest usable `pi-identified` event of the last 20; `reader=none` gives a note that rpi-hwid is not installed on the Pi |
| boards | the newest boot (of the last 5) with a version 1 `fpga-board-identified` event; an older boot gives a note naming both boots |
| schema | `fpga-identity` / `pi-identity`, major version 1; any other is ignored with a note |
| classification | by the event's `kind` only, never by its `board` key (which may be `tt@1-1.2` for a second board): `tt` to `tinytapeout`, `fomu` to no label, every other kind to `fpga` |
| an event with no `kind` | ignored, with a note |
| decoding | absent stays absent; `-` is `null`; lists and objects through `json.loads`, type-checked; `true`/`false`; integers; finite floats |
| `header` | absent becomes `null` (not read) |
| `sources` | `collected_by`, `registration`, `pi-identified`, `fpga-board-identified` |
| bad event content | never fails a page: each event (and each board) is checked with `label_input.check`; one that is refused is dropped with a note naming the event and the problem, and the next older `pi-identified` is tried |
| last guard | if the whole summary is still refused, only the registration is used, with a note |
| download | `/fleet/<serial>/rpi-hwid.json` ([`views.label_input`](https://github.com/fpgas-online/fpgas.online-site/blob/main/fleet/src/fleet/views.py)): `label_input.dumps` of the document; 409 with the problems if rpi-hwid refuses it |
| labels | rendered with `labels.render_sheet` / `labels.render_label` (not yet: [gap 5](#known-gaps)); the detail page shows `label_input.missing` |
| tests | [`tests/test_fleet_hwid_compare.py`](https://github.com/fpgas-online/fpgas.online-site/blob/main/tests/test_fleet_hwid_compare.py) compares the site's document with an rpi-hwid-built one under `comparable` |

## Known gaps

Where the code on `main` (2026-10-03) does not yet do what this page says.

| # | rule | what the code does today | being fixed in |
|---|---|---|---|
| 1 | `dna_sources` is `["fpgas-verify"]` on both sides for a DNA from fpgas-verify | rpi-hwid's `this_host.identity_boards` leaves it `[]`; the site sets `["fpgas-verify"]` (`hwid.py`). `comparable()` keeps it, so the two documents differ for any board with a DNA. The site's comparison test builds its Pi side with `fpga.merge_identity` / `fpga_summary`, which do set it, so the test does not see this. | rpi-hwid, branch `this-host-dna-sources` |
| 2 | fpgas-verify runs `rpi-hwid label-input --pi-only [--user-bus]` at boot and sends `pi-identified` (`pi-identity/1`); `--identify` carries a `"pi"` object; each setup's wiring says whether GPIO2/3 are safe for I2C | none of it: no `pi-identified` event, no `--pi-only` call, no `"pi"` object, no wiring flag | fpgas-verify ([#76](https://github.com/fpgas-online/fpgas.online-test-designs/issues/76)) |
| 3 | `--identify` takes the fields only the boot check reads (the Arty's and NeTV2's flash) from the boot report (`from_report`) | supplies nothing: the Arty/NeTV2 boot report's identity has only how the board was found and its IDCODE fields; the boot flash readback goes into the report's `state`, not its identity | fpgas-verify #76, the Arty/NeTV2 flash PR |
| 4 | each board kind carries its label fields | Arty and NeTV2: no DNA, no flash identity (so `--identify` exits 1 for them). TT: no `TinyTapeoutBoard` fields (`usb_serial`, `mcu`, `chip`, `demoboard`, `demoboard_version`, `sdk`), so both sides drop every TT board. Acorn: no `flash_sfdp`. | fpgas-verify [#110](https://github.com/fpgas-online/fpgas.online-test-designs/pull/110) (Arty/NeTV2 DNA), [#109](https://github.com/fpgas-online/fpgas.online-test-designs/pull/109) (TT facts), [#107](https://github.com/fpgas-online/fpgas.online-test-designs/pull/107) (Acorn SFDP) |
| 5 | the site renders labels with rpi-hwid's `labels` API | no `render_*` call: the site serves the label input `.json` and the list of what each label is missing | the site |
