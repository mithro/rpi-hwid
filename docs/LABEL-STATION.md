# A label station on a borrowed laptop

Someone plugs a board into a known port, and wants a label for it. They are not
the machine's owner and will not keep the setup. This is how to give them a
laptop that prints labels for that one port, over ssh, with no other access.

The station reads the board over ssh and prints to one network printer. It needs
no repository checkout and no account on the switch.

Addresses below are placeholders: `GATEWAY`, `BOARD_ADDRESS`, `BOARD_USER`,
`BOARD_HOST_KEY` and `PRINTER_URI`. `board1` is a nickname for the port, and
yours will differ. Take the real values from the site's own notes, and keep
them out of anything public.

## What you need

- A laptop running Debian or Raspberry Pi OS, with a guest login and python3
  3.11 or newer.
- Network reach from the laptop to the gateway and to the printer.
- The gateway's jump account, and permission to use it.
- An account on the board that may run `python3` over ssh.
- The board's ssh host key, so the station can recognise it.
- The printer's IPP address, and label stock for its manual feed.

The board needs no root to be read at all. With passwordless `sudo` there it
reports more, because the firmware tools want it: `dtparam`, `vcgencmd` and
`modprobe i2c-dev`. Driving JTAG wants `sudo` too. Without any of it the read
still works and says less.

Nothing here is specific to one board. Any host the station reaches through the
gateway can be labelled the same way.

## Steps

### 1. Install the package

One signed apt repository, one package. Use your suite's codename in place of
`trixie`; Raspberry Pi OS uses Debian's.

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://mith.ro/rpi-hwid/rpi-hwid.gpg \
  | sudo tee /etc/apt/keyrings/rpi-hwid.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/rpi-hwid.gpg] https://mith.ro/rpi-hwid/trixie/ ./" \
  | sudo tee /etc/apt/sources.list.d/rpi-hwid.list
sudo apt update
sudo apt install python3-rpi-hwid poppler-utils
```

That gives both commands: `rpi-hwid`, which reads a host, and `rpi-hwid-sheet`,
which tracks a sheet of stickers and prints into its free slots.
`poppler-utils` is optional. Without its `pdftoppm` every run says
`preview: none (no pdftoppm)` and you cannot see the page before it prints.

### 2. Give the station its own key

The station gets its own key, in its own file, so one key can be withdrawn
without touching anyone else's.

```sh
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519_label_station -C "label station"
cat ~/.ssh/id_ed25519_label_station.pub   # hand this to whoever holds the accounts
```

Naming the file matters. Writing to the default `~/.ssh/id_ed25519` would stop
to ask about overwriting a key that is already there.

The public key goes in two places: the gateway's jump account, and the account
on the board.

### 3. Write the ssh config

Two entries. The first is the gateway; the second reaches the board through it,
so no jump host is named on the command line.

```sshconfig
Host gateway
    HostName GATEWAY
    User GATEWAY_USER
    IdentityFile ~/.ssh/id_ed25519_label_station

Host board1
    HostName BOARD_ADDRESS
    User BOARD_USER
    ProxyJump gateway
    IdentityFile ~/.ssh/id_ed25519_label_station
    UserKnownHostsFile ~/.ssh/known_hosts.board1
```

Then `chmod 600 ~/.ssh/config`.

The `User` lines serve a plain `ssh board1`. The reader does not use them: it
builds `user@host` itself from `--users`, in step 6. Keep them in step with
each other anyway, or the two will disagree later.

### 4. Record the board's host key

Pinning means the whole public key on a line, not a fingerprint. `ssh` has no
option that takes a fingerprint; a fingerprint is for checking the line by eye.

```sh
printf '%s %s\n' BOARD_ADDRESS 'ssh-ed25519 AAAAC3Nza...' \
  > ~/.ssh/known_hosts.board1
chmod 600 ~/.ssh/known_hosts.board1
ssh-keygen -lf ~/.ssh/known_hosts.board1   # compare with the fingerprint you were given
```

Write the address exactly as the config's `HostName` does. For a port other
than 22 the line begins `[BOARD_ADDRESS]:PORT`.

Do this before the first read. The reader asks ssh to accept an unknown key
silently, so a key recorded afterwards proves nothing about the first run.

### 5. Accept the gateway's key, once

The reader's ssh options do not reach the jump connection. `ssh` builds that
connection as its own command, carrying only verbosity:

```
debug1: Executing proxy command: exec ssh -vv -W '[BOARD_ADDRESS]:22' gateway
```

So the gateway's key is checked with the normal rules, which stop to ask. In a
read there is nobody to answer, and the run waits until it times out after 180
seconds. Get the question over with first:

```sh
ssh gateway true
```

Check the fingerprint it offers against the one you were given, then accept.
After that the key is in `~/.ssh/known_hosts` and no read will ask again.

### 6. Start a sheet

```sh
echo 'export RPI_HWID_PRINTER=PRINTER_URI' >> ~/.bashrc   # shape: ipp://HOST/ipp/print
export RPI_HWID_PRINTER=PRINTER_URI
rpi-hwid-sheet new --printer "$RPI_HWID_PRINTER"
```

Export it in this shell as well. A new terminal that is not a login shell reads
`~/.bashrc`, not `~/.profile`.

A sheet is a physical page of stickers, and its record says which stickers are
gone. `new` prints the sheet's id first, then a short note and the record's
path. Give that id back as `SHEET` in step 7.

The first print puts the id in the page's own margins, so pencilling it on the
paper is a safeguard, not the only copy.

### 7. One line per board

Plug the board in, wait for it to boot, then:

```sh
rpi-hwid-sheet print SHEET board1 -- --users BOARD_USER
```

`SHEET` is the id from step 6, or the word `new` to start a page in the same
breath. Everything after `--` goes straight to the reader.

`--users BOARD_USER` logs in as that account only. Without it the reader tries
the laptop's own login name first and then, literally, `pi` — so an account
called anything else is never tried.

For an FPGA board, add `--jtag board1` to read its idcode and DNA:

```sh
rpi-hwid-sheet print SHEET board1 -- --users BOARD_USER --jtag board1
```

Leave `--jtag` off for anything else. It drives JTAG on the host's header pins,
which is pointless where no FPGA is wired to them. It also turns the FPGA read
on by itself, so `--fpga` adds nothing beside it.

The host and the `--jtag` name must be spelled identically. `--jtag board1`
does nothing for a host written `BOARD_USER@board1`: the two are matched as
plain text.

`print` needs a terminal, because it asks before printing. In a script it stops
with `no terminal to ask: use --prepare`.

## Check

A good run says what it read, which slots it will use, and waits for you. The
shape, with this page's placeholders in it:

```
new sheet A1B2
  board1: Raspberry Pi 4 Model B Rev 1.4; header bare; power undetermined
1 of 1 host(s) written to ~/.local/state/rpi-hwid/reads/A1B2/2026-10-11T120000
Sheet A1B2 (L7160 · started 2026-10-11 12:00 on LAPTOP by GUEST · rpi-hwid 0.0.post348), pass 1, printer PRINTER_URI
  first pass: the sheet's id, note and registration ticks go in its margins
  slot  label
  1     board1/rpi/Pi 4 Model B 8 GB SERIAL
  data: ~/.local/state/rpi-hwid/reads/A1B2/2026-10-11T120000
  preview: ~/.local/state/rpi-hwid/plans/A1B2-p1-2026-10-11T120003/preview.png
  pdf: ~/.local/state/rpi-hwid/plans/A1B2-p1-2026-10-11T120003/pass.pdf
Print 1 label on sheet A1B2 through the manual feed? [y/N] y
job 1 sent to PRINTER.
Put sheet A1B2 in the printer's manual feed slot (one sheet; the job waits for it), the TOP EDGE going in first.
the printer is waiting for sheet A1B2 in its manual feed slot
sheet A1B2 pass 1 printed; 20 free stickers, 0 free quarters
```

Open the `preview:` file before answering `y`. It is the page as it will print.

That last pause is the printer, not the command. The job sits in the queue until
a sheet goes into the manual feed slot, top edge first. Feed one sheet only. The
command waits up to 600 seconds for the printer to take it.

Then check the paper against the screen. A sheet holds 21 stickers, numbered
from the top left, three to a row, and the listing's slot numbers are the ones
that moved.

## If it fails

**`board1: FAILED (... Permission denied ...)`, then "the read has nothing for
board1".** The key is not on that account yet, or `--users` names the wrong
one. Nothing is printed, because every label must come from a fresh read. Try
`ssh -v board1 true` and read which key it offers.

**`board1: FAILED (Host key verification failed.)`.** Stop. The address
answered with a key that is not the one in step 4. Another machine may hold
that address.

**"timed out".** The board is not up, the gateway is unreachable, or step 5 was
skipped. Check the gateway on its own: `ssh gateway true`.

**It reports less than you expect.** The account on the board has no
passwordless `sudo`, so the firmware tools were not run.

**Nothing comes out of the printer.** The job is waiting for its sheet. Put one
in the manual feed, top edge first.

**The sheet id is not found.** See below: a sheet lives on the machine that
started it.

## Next

The record of a sheet is per user, per machine, under
`~/.local/state/rpi-hwid/` (or `$XDG_STATE_HOME/rpi-hwid/`). It holds the
sheets, the reads behind each label, and the print plans.

So a sheet started elsewhere cannot be continued here. The stickers are gone
from the paper, but this machine has no record of which, and would print over
them. Start a fresh page on the station, or finish that page where it began.

When the station is no longer wanted, withdraw its key from both accounts.
Removing the laptop's files is not enough, and a key left in place is what turns
a borrowed laptop into standing access.

See also [SHEETS.md](SHEETS.md) for sheets in general, and
[COLLECT.md](COLLECT.md) for what the reader asks a host.
