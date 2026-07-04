# Spike: dakota-iso as the Snow live ISO builder

**Date:** 2026-07-03 (overnight run) · **Branch:** `feat/snow-variant` · **Verdict: SUCCESS — recommend migrating from the titanoboa fork.**

## What was tested

Can `projectbluefin/dakota-iso` replace our `frostyard/titanoboa`
`feat/bootc-installer-live` branch as the Snow live/installer ISO builder?

**Result: the full pipeline works end-to-end, verified in QEMU tonight:**

1. `just debug=1 workdir=/mnt iso-sd-boot snow` → `output/snow-live.iso`
   (3.5 GB, correct GPT ESP type GUID, hybrid MBR+GPT) — **~5 min build**
   with a warm podman cache
2. ISO boots via systemd-boot → dmsquash-live → GNOME live session; GDM
   autologin as the boot-created `snow` user; `DAKOTA_LIVE_READY` on serial
   ~10 s after QEMU start
3. bootc-installer GUI autostarts with our Snow recipe
   ("Welcome to Snow Linux") — `docs/spike/live-session-installer.png`
4. **Offline install** from the embedded containers-storage (no network):
   `fisherman` (frostyard build) → btrfs + composefs + systemd-boot on
   /dev/vda — **"Installation complete!" in 142 seconds**
5. Installed disk boots to GNOME + snow-first-setup —
   `docs/spike/installed-first-boot.png`

The offline install is the headline: titanoboa's flow pulls ~10 GB from the
registry during install (the source of the fisherman#2 ENOSPC saga); dakota-iso
embeds the squashed OCI image in the ISO's squashfs, so installs are
network-free and took 2.4 minutes in the VM.

## What the snow variant consists of

- `snow/` — variant dir: `payload_ref`/`live_target`/`live_title`/`registry`/`tag`
  (all point at `ghcr.io/frostyard/snow:latest`)
- `live/src/snow/` — `images.json` + `recipe.json` (taken verbatim from the
  verified titanoboa branch), `bootloader=systemd`, `composefs=true`,
  `cosign.pub`, empty `flatpaks` list (installer-only ISO), and
  `configure-live.d.sh` (snow-specific hook)
- `live/src/snow/fisherman` — **untracked/gitignored**: the frostyard
  fisherman build (cosign + scratch-store fixes), swapped into the installer
  Flatpak at build time exactly like the titanoboa hook did. Copied from
  `titanoboa/src/bootc-installer/fisherman`. Production path: publish a
  frostyard bootc-installer Flatpak release and point `INSTALLER_REPO` in
  `live/src/install-flatpaks.sh` at it (one-line change).

### Upstreamable patches made to dakota-iso (all generic, not snow-specific)

| File | Change |
|---|---|
| `live/src/configure-live.sh` | per-variant `recipe.json` override (mirrors `images.json`); Debian `gdm3` autologin path; Debian `ssh.service` vs `sshd.service` in debug builds; per-variant `configure-live.d.sh` hook |
| `live/src/install-flatpaks.sh` | per-variant `flatpaks` list; guard against empty list |
| `scripts/iso-sd-boot.sh` | add linuxbrew `sbin` to `build-iso.sh` PATH (`mkfs.fat`) |

### Snow hook (`live/src/snow/configure-live.d.sh`) — why each line exists

- `mask home.mount` + `HOME=/home` in `/etc/default/useradd` — Flatpak
  sandboxes get a private `/var`, so a `/var/home` user breaks the
  installer's host staging (titanoboa finding, reproduced here)
- `mask usr-local.mount` — snow binds `/var/usrlocal` over `/usr/local` at
  boot; on live media the ephemeral `/var` shadowed the
  `/usr/local/bin/fisherman` symlink (found and fixed during this spike)
- remove `org.frostyard.FirstSetup.autostart.desktop` from `/etc/skel` —
  otherwise the snow first-run wizard opens next to the installer in the
  live session (live env only; installed systems keep their first-boot flow)
- polkit rule extended to the `snow` user; passwordless sudo for it
- stages `cosign.pub`; swaps fisherman; rebrands the desktop entries

## Issues found (with resolutions)

1. **UEFI shell on first installed boot** — only when reusing the live
   session's OVMF VARS in QEMU (stale NVRAM). Fresh vars → boots fine via
   `\EFI\BOOT\BOOTX64.EFI`. Not a real-hardware concern; use fresh NVRAM in
   CI install tests.
2. **Installer button says "Install Bluefin"** — hardcoded string in the
   upstream bundle; our recipe controls the window branding but not that
   label. Fixed by the frostyard bootc-installer fork (app rebrand) when we
   publish it.
3. **First-setup wizard shows Estonian ("Tere tulemast")** on installed
   first boot — snow-first-setup locale quirk, unrelated to dakota-iso;
   worth a snosi/first-setup issue.
4. **Host tooling**: this host (snowloaded) needed brew installs of
   `xorriso`, `mtools`, `dosfstools`, `socat`, `sshpass`; OVMF extracted
   from the `debian:trixie` container into `/var/tmp/snow-iso-test/`.
   `/mnt` is a 45 GB XFS loopback (`/var/tmp/dakota-xfs-loopback.img`) —
   btrfs is slow for the VFS import.

## Production concerns to resolve before switching (none blocking)

- **`storage.conf` injected into the payload**: `iso-sd-boot.sh` writes
  `driver = "vfs"` into the payload's `/etc/containers/storage.conf` before
  embedding (composefs path). The **installed system inherits it** — bad for
  runtime podman and for `bootc-update-stage`'s podman pulls (VFS = no
  overlay, huge disk use). Verify what nbc/titanoboa installs, and either
  drop the injection for snow or ship a first-boot reset. **Most important
  follow-up.**
- The ISO pins the payload at build time — ISO must be rebuilt to track
  releases (CI schedule, like upstream's daily build).
- `flatpak_var_path: state/os/default/var` in images.json is GNOME-OS
  layout, carried over from the titanoboa config; harmless with an empty
  flatpak list but should be `var/lib/flatpak` if we ever preload flatpaks
  into installs.
- `debug=1` was used for all spike ISOs (SSH root/live access) — never ship.
- Upstream moves fast (repo is ~3 months old, alpha). Our four generic
  patches should go upstream as a PR to reduce fork drift.

## Comparison with titanoboa (why switch)

| | titanoboa fork | dakota-iso |
|---|---|---|
| bootc-installer integration | grafted via post-rootfs hook | first-class (`/etc/bootc-installer` config, channels) |
| install source | network pull (~10 GB, ENOSPC-prone) | **offline embedded store, 142 s** |
| snow support | fork branch + hook + Justfile compat patches | variant dir + one hook script |
| systemd-boot + composefs | patched in | native (Dakota's own profile) |
| upstream trajectory | uBlue legacy, our fork diverged | active successor project, same org as our installer |
| maturity | verified by us 2026-07-03 | alpha, but this spike verified E2E for snow |

## Reproduce

```bash
cd ~/projects/frostyard/dakota-iso            # branch feat/snow-variant
cp ../titanoboa/src/bootc-installer/fisherman live/src/snow/fisherman
just debug=1 workdir=/mnt iso-sd-boot snow    # ~5 min warm, ~15 min cold
bash /var/tmp/snow-iso-test/boot-test.sh output/snow-live.iso   # QEMU verify
```
