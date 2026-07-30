# Secure Snow CI OCI Runtime Design

## Problem

The first secure Snow ISO workflow run failed before consuming its pinned
installer or Fisherman artifacts. GitHub runner image `20260726` supplies
Podman 5.8.4 with `crun` as its default OCI runtime, and that runtime rejects
the generated OCI version with `unknown version specified` while building the
live container.

Snosi and Fisherman encountered the same hosted-runner regression and proved
that the runner-bundled `runc` handles these Podman builds correctly.

## Scope

Change only `.github/workflows/build-iso-snow.yml`. Other Dakota workflows,
payload selection, secure artifact pins, and R2 publication behavior remain
unchanged.

## Design

After the workflow installs Podman and before its first Podman command, add a
`Configure Podman OCI runtime` step that:

1. Creates `/etc/containers/containers.conf.d` with root-owned mode `0755`.
2. Writes a job-local drop-in selecting `runtime = "runc"` under `[engine]`.
3. Reads the effective runtime through `sudo podman info`.
4. Fails immediately unless the selected runtime is exactly `runc`.

This changes only CI's container runtime selection. It does not alter the
runtime policy shipped inside the ISO.

## Verification

Add a static assertion to `tests/test_snow_secure_media.py` requiring the Snow
publisher to select and verify `runc`. Run that focused test module and the
existing secure runner fixtures. Then rerun `build-iso-snow.yml` on `main` with
`skip_upload=true`; success requires the live container build, ISO assembly,
artifact upload, and positive/tampered-GRUB Secure Boot smoke checks to pass.

Document the hosted-runner compatibility decision in `CLAUDE.md` and
`docs/snow-secure-media.md`.
