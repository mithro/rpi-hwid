# A label station on a borrowed laptop

Someone plugs a board into a known port, and wants a label for it. They are not
the machine's owner and will not keep the setup. This is how to give them a
laptop that prints labels for that one port, over ssh, with no other access.

The station reads the board over ssh and prints to one network printer. It
needs no repository checkout, no root on the board, and no account on the
switch.

Addresses below are placeholders: `GATEWAY_ADDRESS`, `DEVICE_ADDRESS`,
`PRINTER_URI` and `DEVICE_HOST_KEY`. Take the real ones from the site's own
notes. Keep them out of anything public.

## What you need

- A laptop running Debian or Raspberry Pi OS trixie, with a guest login.
- Network reach from the laptop to the gateway and to the printer.
- The gateway's jump account, and permission to use it.
- The board's ssh host key, so the station can recognise it.
- An account on the board that may run `python3` over ssh.
- The printer's IPP address, and sheets of label stock for its manual feed.

Nothing here is specific to one board. Any host the station can reach over the
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
sudo apt install python3-rpi-hwid
```

That gives both commands: `rpi-hwid`, which reads a host, and
`rpi-hwid-sheet`, which tracks a sheet of stickers and prints into its free
slots.

### 2. Give the station its own key

The station gets its own key, not a copy of anyone else's. Then one key can be
withdrawn without touching the rest.

```sh
ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 -C "label station"
cat ~/.ssh/id_ed25519.pub        # hand this to whoever holds the two accounts
```

The public key goes in two places: the gateway's jump account, and the account
on the board. Neither needs root.

### 3. Write the ssh config

Two entries. The first is the gateway; the second reaches the board through it,
so no jump host is named on the command line.

```sshconfig
Host gateway
    HostName GATEWAY_ADDRESS
    User GATEWAY_USER
    IdentityFile ~/.ssh/id_ed25519

Host p48
    HostName DEVICE_ADDRESS
    User DEVICE_USER
    ProxyJump gateway
    IdentityFile ~/.ssh/id_ed25519
    UserKnownHostsFile ~/.ssh/known_hosts.p48
```

`p48` is only a nickname for the port the board is plugged into. Use whatever
name the station's people already say out loud.

Then record the board's host key, so a different machine on that address is
refused:

```sh
printf '%s %s\n' DEVICE_ADDRESS DEVICE_HOST_KEY > ~/.ssh/known_hosts.p48
chmod 600 ~/.ssh/known_hosts.p48
```

A fingerprint alone will not do. `ssh` has no option that takes one, so the
file needs the whole key, and the fingerprint is for checking it by eye.

### 4. Name the printer

```sh
echo 'export RPI_HWID_PRINTER=PRINTER_URI' >> ~/.profile
export RPI_HWID_PRINTER=PRINTER_URI
```

### 5. Start a sheet

A sheet is a physical page of stickers, and its record says which stickers are
already gone. Start one when you open a fresh page.

```sh
rpi-hwid-sheet new --printer "$RPI_HWID_PRINTER"
```

It prints the sheet's id first, then a short note and the record's path. Write
that id on the paper in pencil. You give it back on every print.

### 6. One line per board

Plug the board in, wait for it to boot, then:

```sh
rpi-hwid-sheet print SHEET p48 -- --users DEVICE_USER --jtag p48
```

`SHEET` is the id from step 5, or the word `new` to start a page in the same
breath. Everything after `--` goes straight to the reader:

- `--users DEVICE_USER` logs in as that account only. Without it the station
  tries the laptop's own login name first, which fails before it falls back.
- `--jtag p48` drives JTAG on that host, for an FPGA's idcode and DNA. It also
  turns on the FPGA read, so `--fpga` adds nothing beside it.

The host and the `--jtag` name must be spelled identically. `--jtag p48` does
nothing for a host written `DEVICE_USER@p48`, because the two are matched as
plain text.

The command reads the board, shows what it found, and asks before printing.

## Check

A good run says what it read, which slots it will use, and waits for you:

```
Sheet 65D7 (L7160 · started 2026-10-01 15:42 ...), pass 1, printer PRINTER_URI
  slot  label
  1     p48/rpi/Pi 4 Model B 8 GB 1000000053f279e5
Print 1 labels on sheet 65D7 through the manual feed? [y/N] y
job 314 sent to Brother MFC-L3760CDW series.
Put sheet 65D7 in the printer's manual feed slot (one sheet; the job waits for
it), the TOP EDGE going in first.
the printer is waiting for sheet 65D7 in its manual feed slot
sheet 65D7 pass 1 printed; 19 free stickers, 0 free quarters
```

That last pause is the printer, not the command. The job sits in the queue until
a sheet goes into the manual feed slot, top edge first. Feed one sheet only.

Then check the paper against the screen. The slot numbers in the listing are the
stickers that moved, counted from the sheet's top left.

## If it fails

**It asks for a password.** The key is not on that account yet, or ssh is not
reading the config you wrote. Try `ssh -v p48 true` and read which key it
offers.

**"timed out".** The board is not up, or the gateway is unreachable. Check the
gateway alone first: `ssh gateway true`.

**A host key warning.** Stop. The address is answering with a key you did not
record. Someone else's machine may hold that address.

**"the read has nothing for p48".** The login worked but the read did not
finish. Every label must come from a fresh read, so nothing is printed. The log
path is in the message; read it.

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

When the station is no longer wanted, withdraw its key from the two accounts.
Removing the laptop's files is not enough, and leaving the key is what turns a
borrowed laptop into standing access.

See also [SHEETS.md](SHEETS.md) for sheets in general, and
[COLLECT.md](COLLECT.md) for what the reader asks a host.
