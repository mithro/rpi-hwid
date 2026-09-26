# Developing rpi-hwid

Contributor notes. For what the package does and how to run it, see the
[README](../README.md); for how versions reach PyPI and apt, see
[RELEASING.md](../RELEASING.md).

## Setup and gates

```sh
uv sync --all-extras --group dev
uv run ruff check && uv run mypy && uv run pytest
```

Those three are exactly what CI runs (the `test` job of
`.github/workflows/deb.yml`), across Python 3.11, 3.12 and 3.13. A green run is
what "mergeable" means.

## The probes must stay Python 3.5-clean

`src/rpi_hwid/probe.py`, `src/rpi_hwid/fpga.py` and `src/rpi_hwid/tinytapeout.py`
are pushed to the target host over ssh and run by whatever `python3` is already
there — on a Pi still on Raspbian stretch, 3.5. So they stay dependency-free,
stdlib-only single files, and they stay 3.5-grammar. The test suite refuses
f-strings in them, and a CI step byte-compiles all three under a real
`python:3.5-slim` with `-W error`. The rest of the package targets 3.11+ and may
use anything it likes.

## Tests

The suite renders a label sheet from the fixture documents in `tests/conftest.py`
and decodes every QR code on it with zxing, so it needs `pdftoppm` (poppler-utils)
and a monospace font installed.

The fixtures are probe output captured from real boards, which is why the same
documents drive the images in the README.

## Regenerating the images

```sh
uv run docs/examples/render.py    # docs/examples/*.png, the label crops in the README
uv run docs/social_preview.py     # docs/social-preview.png, 1280 x 640
```

Both read `tests/conftest.py`, so the images track the generator. `render.py` cuts
its crops out of a rendered sheet by the label grid, so they are exactly what the
printer gets.

The PNGs are byte-stable only for a given set of fonts and poppler version, so a
regeneration on another machine may differ by a few pixels. GitHub has no API for
the repository's social preview: after regenerating it, upload the file by hand
under Settings → Social preview.

## The Tiny Tapeout board data

The chip carrier and demo board colours come from the published board
spreadsheet — <https://mith.ro/tt-boards/> — not from the boards:

```sh
uv run tools/fetch_tt_boards.py           # rewrite src/rpi_hwid/tt_boards.json
uv run tools/fetch_tt_boards.py --check   # what CI runs: fail if the sheet has moved
```

Edit the spreadsheet, re-run the script, commit the JSON. The `--check` run is a
workflow of its own (`.github/workflows/tt-boards.yml`) on a weekly schedule,
deliberately not a job in `deb.yml`: a green `deb.yml` on main is what triggers the
PyPI publish, and a release should not be at the mercy of a spreadsheet edit or a
bad afternoon at docs.google.com.

The check compares the *derived* document rather than the raw CSV, because the
sheets also carry Stock and Buy Link columns that move on their own; a check that
went red when something sold out would be noise. Columns are found by their
headings, read from the sheet's two header rows together, so a column can be
added or moved without touching the script — but one that is renamed fails
loudly, naming the headings it did find.

## Building the Debian package locally

CI builds with [mithro/apt-repo-action](https://github.com/mithro/apt-repo-action)'s
`build-deb` action, which only runs in GitHub Actions. The same build by hand,
with a checkout of apt-repo-action's `main` next to this one (and `bookworm`
replaced by the suite you want):

```sh
docker run --rm -v "$PWD:/src" -v "$PWD/../apt-repo-action:/apt-repo-action:ro" -w /src \
  debian:bookworm bash -ec '
    apt-get update
    apt-get install -y --no-install-recommends \
      build-essential ca-certificates debhelper dpkg-dev fakeroot git python3
    apt-get build-dep -y ./
    git config --global --add safe.directory "*"
    python3 /apt-repo-action/scripts/deb-version.py --suite bookworm --write-changelog
    dpkg-buildpackage -us -uc -A
    mkdir -p built-debs && cp ../*.deb built-debs/'
```

The version is `git describe`'s plus the suite's `~deb<R>`
(`0.0.post190~deb12`). There is no committed `debian/changelog`: the build
writes one with just its own entry, and git ignores it. The container runs as
root, so the files the build leaves in the tree are root's.
