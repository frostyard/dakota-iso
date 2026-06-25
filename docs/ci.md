# CI/CD

How the GitHub Actions workflows build, test, and publish Dakota ISOs.

## ISOs produced

Three NVIDIA-unified ISOs are built and published to R2:

| ISO | Workflow | R2 latest name | Image embedded |
|---|---|---|---|
| Dakota | `build-iso.yml` | `dakota-live-latest.iso` | `projectbluefin/dakota-nvidia:stable` |
| Bluefin | `build-iso-bluefin.yml` | `bluefin-live-latest.iso` | `projectbluefin/bluefin-nvidia:stable` |
| Bluefin LTS HWE | `build-iso-bluefin.yml` | `bluefin-lts-hwe-live-latest.iso` | `projectbluefin/bluefin-lts-hwe-nvidia:stable` |

All variants are built using the exact same `live/Containerfile` and `scripts/build-live-squashfs.sh`. The only difference is the `payload_image` passed to the build.

## Workflows

### `build-iso.yml`

Builds the `dakota` variant.

**Triggers:** Push to main, daily schedule at 03:00 UTC, `workflow_dispatch`
**Runs as:** root via `sudo`
~~**Path triggers:** `live/**`, `scripts/**`, `.github/workflows/build-iso.yml`~~ — removed; see lessons.

### Pipeline steps

1. **Free disk space** — `ublue-os/remove-unwanted-software` reclaims ~119 GB at `/var/iso-build`
2. **Install deps** — `apt-get install podman buildah skopeo mtools xorriso squashfs-tools dosfstools isomd5sum`
3. **Log in to GHCR** — `sudo podman login ghcr.io`
4. **Pull payload image** — pulls only `dakota-nvidia:stable` (the unified ISO base)
5. **Build live container** — `podman build live/ --build-arg TARGET=dakota-nvidia` → `localhost/dakota-nvidia-live:latest`
6. **Build live squashfs** — `scripts/build-live-squashfs.sh` with `SUPERISO_COMPRESSION=release` → `<target>.rootfs.sfs` + `<target>-boot.tar` (~4.5 GB dakota, ~6 GB bluefin/lts-hwe)
7. **Assemble ISO** — `live/src/build-iso.sh` → `dakota-live-<date>-<sha>.iso`
8. **Boot verification** — Boots the ISO in QEMU via UEFI and waits for `DAKOTA_LIVE_READY` on the serial console. (Fails the job if it times out).
9. **Upload to R2** — If boot verification passes, `rclone` uploads the ISO to the Cloudflare R2 `testing` bucket.
   * `dakota-live-<date>-<sha>.iso` (Permanent history)
   * `dakota-live-latest.iso` (The public pointer)

### `build-iso-bluefin.yml`

Builds the `bluefin` and `bluefin-lts-hwe` variants.

**Triggers:** Monthly schedule (1st of month at 05:00 UTC), `workflow_dispatch`
**Matrix:** `variant: [bluefin, bluefin-lts-hwe, stable, lts]`
**Outputs:**
* `bluefin-live-latest.iso`
* `bluefin-lts-hwe-live-latest.iso`
* `stable-live-latest.iso`
* `lts-live-latest.iso`

The steps are identical to `build-iso.yml` but it loops through the matrix payload images. Note the monthly trigger — the images update daily via watchtower, but ISOs are only cut monthly or on-demand.

### `test-plain-install.yml`

**E2E integration test for the unencrypted XFS/BTRFS installer flow.**

**Triggers:** PRs to main, weekly schedule, `workflow_dispatch`

### Pipeline steps

1. Ensure `ci-screenshots` branch exists
2. Free disk space
3. Install deps (adds `qemu-system-x86 ovmf socat sshpass`)
4. Configure podman storage (`configure_podman_storage.sh`)
5. Build ISO with `debug=1` and the matrix `installer_channel`
6. Boot live ISO in QEMU (daemonized) + wait for ready
7. SSH into live env, mount scratch disk over `/var/tmp`, write recipe, run `fisherman` plain install
8. Patch BLS entries on the installed disk to add serial console (`console=ttyS0`)
9. Shut down live VM
10. Boot the installed disk in QEMU
11. Wait for `dakota-plain-test login:` on serial console
12. Screenshot the QEMU framebuffer to `screenshot.png`
13. Upload screenshot as artifact and commit it to the `ci-screenshots` branch

### `test-luks-install.yml`

**E2E integration test for the LUKS encrypted btrfs installer flow.**
Reproduces [projectbluefin/dakota#270](https://github.com/projectbluefin/dakota/issues/270).

**Triggers:** PRs to main, weekly schedule, `workflow_dispatch`

### Pipeline steps

1. Ensure `ci-screenshots` branch exists
2. Free disk space
3. Install deps (adds `qemu-system-x86 ovmf socat sshpass`)
4. Configure podman storage (`configure_podman_storage.sh`)
5. Build ISO with `debug=1` and the matrix `installer_channel`
6. Boot live ISO in QEMU (daemonized) + wait for ready
7. SSH into live env, write recipe, run `fisherman` LUKS install
8. Patch BLS entries (requires unlocking the LUKS volume from the live env to modify `/boot` if on `/`)
9. Shut down live VM
10. Boot the installed disk in QEMU
11. Run `luks-unlock.py` — this script polls the QEMU monitor for the `screendump` size to drop. When the framebuffer size drops significantly, it means Plymouth has taken over the screen and is prompting for the LUKS passphrase. It then injects `testpassphrase\n` via the QEMU monitor.
12. Wait for `dakota-luks-test login:` on serial console
13. Screenshot the QEMU framebuffer to `screenshot.png`
14. Upload screenshot as artifact and commit it to the `ci-screenshots` branch

## Lessons Learned

### GitHub Actions path filters mask broken ISOs (2026-06-21)

**What failed:** ISO builds were green, but E2E tests were failing on PRs that touched the `justfile`. The `build-iso.yml` workflow was skipped entirely because of path filters (`paths: ['live/**', 'scripts/**', ...]`), so the broken `justfile` changes merged to main without the ISO build ever running.

**Why:** Path filters in GitHub Actions are an optimization that skips the workflow if none of the changed files match the filter. However, in a tightly coupled build system like `dakota-iso`, almost *any* change (justfile, Containerfile, python tests) can break the final artifact. `test-plain-install.yml` caught the failure, but because `build-iso.yml` was skipped, its required status check was satisfied (Actions treats skipped as "passed" for branch protection if the workflow name matches).

**Fix:** Removed all `paths:` filters from all workflows. If a PR is opened, the tests run. If it merges to main, the ISO builds. The cost of a redundant build is lower than the cost of a broken master branch.

### Organization Standards for GitHub Actions (2026-06-25)

**Policy: The standard is the codebase itself. Use what is in production already.**
Whenever a CI task requires a GitHub Action, your first step is to grep the organization's existing workflows to see what is already used in production. For example, Project Bluefin universally uses `ublue-os/remove-unwanted-software` to free disk space. Use the existing production standard to guarantee cross-repo consistency and prevent redundant third-party dependencies. You do not need to ask or look for a separate rules document if the codebase already shows a clear consensus.
