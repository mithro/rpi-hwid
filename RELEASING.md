# Releasing

This project is a **rolling release**. There are no manual version bumps: the
version is derived from `git describe` by `hatch-vcs` — `X.Y` at a `vX.Y`
tag, `X.Y.postN` N commits after it — and **every green push to `main`
publishes a new package** automatically:

- `.github/workflows/ci.yml` — runs the gates (ruff, mypy --strict, pytest with
  coverage) on every push and PR. A green run is what "mergeable" means, and it
  is what triggers the release workflow.
- `.github/workflows/publish-pypi.yml` — builds and uploads the wheel + sdist to
  PyPI when CI **succeeds** on `main` (`workflow_run`; a failed or cancelled CI
  run publishes nothing, and the checkout is pinned to the SHA CI validated).

Merges to `main` use `--no-ff` merge commits so history stays linear per PR.

## Tags

Tags are the only human input to the version. `v0.0` sits on the root commit
(so `git describe` works from the start of history, per the repo-setup
guidance). To cut a new series, push an annotated `vX.Y` tag on `main` (a
GitHub tag ruleset only admits `vXX.ZZZ`-shaped tags) — the next green CI run
publishes `X.Y`, and every commit after it `X.Y.postN`. Never move or delete a
tag that has been published from.

## One-time human setup

Done ONCE by a maintainer. **No secret or key is ever committed to the repo.**
Until this is done, the publish workflow runs but its upload step fails safely.

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
