# Developing rpi-hwid

Contributor notes. For what the package does and how to run it, see the
[README](../README.md); for how versions reach PyPI and apt, see
[RELEASING.md](../RELEASING.md).

## Setup and gates

```sh
uv sync --all-extras --group dev
uv run ruff check && uv run mypy && uv run pytest
```

Those three are exactly what CI runs (`.github/workflows/ci.yml`), across Python
3.11, 3.12 and 3.13. A green run is what "mergeable" means.

## The probes must stay Python 3.5-clean

`src/rpi_hwid/probe.py`, `src/rpi_hwid/fpga.py` and `src/rpi_hwid/tinytapeout.py`
are pushed to the target host over ssh and run by whatever `python3` is already
there — on a Pi still on Raspbian stretch, 3.5. So they stay dependency-free,
stdlib-only single files, and they stay 3.5-grammar. The test suite refuses
f-strings in them, and a separate CI job byte-compiles all three under a real
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

## Building the Debian package locally

```sh
docker run --rm -v "$PWD:/src" -w /src debian:bookworm sh packaging/ci-build.sh
```
