# Releasing

This project is a **rolling release**. There are no manual version bumps: the
version is derived from `git describe` by `hatch-vcs` — `X.Y` at a `vX.Y`
tag, `X.Y.postN` N commits after it — and **every green push to `main`
publishes a new package** automatically:

- `.github/workflows/deb.yml` ("Debian packages") — on every push and PR, its
  `test` job runs the gates (ruff, mypy --strict, pytest with coverage, the
  python 3.5 probe check); a green run is what "mergeable" means. Then it
  builds `python3-rpi-hwid` for Debian bookworm, trixie, forky and sid with
  `mithro/apt-repo-action`'s shared `build-deb`, install-tests each, and, on
  `main` only, republishes the signed apt repository on GitHub Pages
  (https://mith.ro/rpi-hwid/). The .deb's version is the wheel's plus the
  suite: `X.Y.postN~deb12` (bookworm), `~deb13` (trixie), `~deb14` (forky),
  nothing for sid; a pull request's preview build adds `~pr<P>`.
- `.github/workflows/publish-pypi.yml` — builds and uploads the wheel + sdist to
  PyPI when "Debian packages" **succeeds** on `main` (`workflow_run`; a failed
  or cancelled run publishes nothing, and the checkout is pinned to the SHA it
  validated).

Merges to `main` use `--no-ff` merge commits so history stays linear per PR.

## Tags

Tags are the only human input to the version. `v0.0` sits on the root commit
(so `git describe` works from the start of history, per the repo-setup
guidance). To cut a new series, push an annotated `vX.Y` tag on `main` (a
GitHub tag ruleset only admits `vXX.ZZZ`-shaped tags) — the next green run
publishes `X.Y`, and every commit after it `X.Y.postN`. Never move or delete a
tag that has been published from.

## One-time human setup

Done ONCE by a maintainer. **No secret or key is ever committed to the repo.**
Until this is done, the publish workflow runs but its upload step fails safely.

### 1. PyPI trusted publishing (OIDC)

1. Create the project `rpi-hwid` on https://pypi.org, or let the first
   trusted-publisher upload create it via a *pending publisher*.
2. On PyPI, add a **Trusted Publisher** to the project with:
   - Owner: `mithro`
   - Repository: `rpi-hwid`
   - Workflow filename: `publish-pypi.yml`
   - Environment name: `pypi`
3. In the GitHub repo, the **Environment** named `pypi` exists (created at
   repo setup). No secrets needed — OIDC handles auth.

After this, the next green push to `main` uploads to PyPI. `skip-existing:
true` makes re-runs idempotent.

### 2. apt repo signing key

Each mithro apt repository signs with its own key. The private half is the
`APT_GPG_PRIVATE_KEY` repository secret; the public half is exported by the
publish workflow as `rpi-hwid.gpg` (a binary keyring) and `rpi-hwid.asc`
(armoured) at the site root; consumers install the `.gpg` as
`/etc/apt/keyrings/rpi-hwid.gpg`, as it is, with no `gpg --dearmor`.

1. Generate the key locally, unprotected so CI can sign unattended, and keep
   it in your own keyring (never in the repo):

   ```sh
   gpg --batch --gen-key <<EOF
   %no-protection
   Key-Type: RSA
   Key-Length: 4096
   Name-Real: rpi-hwid apt repo
   Name-Email: me@mith.ro
   Expire-Date: 0
   %commit
   EOF
   ```

2. Load it into the secret without writing it to disk:

   ```sh
   gpg --armor --export-secret-keys "rpi-hwid apt repo" \
     | gh secret set APT_GPG_PRIVATE_KEY --repo mithro/rpi-hwid
   ```

Until the secret is set, `mithro/apt-repo-action` refuses to publish an
unsigned repository, so the publish job fails rather than shipping something
consumers would have to trust with `[trusted=yes]`.

### 3. GitHub Pages

Settings → Pages → Source: **GitHub Actions** (`gh api repos/mithro/rpi-hwid/pages
-X POST -f build_type=workflow`). The `publish-apt` job then deploys to
https://mith.ro/rpi-hwid/ through the `github-pages` environment GitHub
creates on its own.

## Verifying a release

- PyPI: https://pypi.org/project/rpi-hwid/ shows the new `X.Y.postN`.
- apt: `sudo apt update && apt-cache policy python3-rpi-hwid` on a machine set
  up per https://mith.ro/rpi-hwid/ shows the same version.
