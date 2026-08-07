# Secure Snow Live Media

`secure_snosi=1` is an explicit, fail-closed Snow-only build mode. It preserves
the generic Dakota variants and replaces only Snow's live ESP chain with
Debian-trusted `shim -> signed GRUB -> Debian-signed kernel`; MokManager is
included and `fbx64.efi` is intentionally absent.

Generic live media retains its existing `enforcing=0` kernel command line and
systemd-boot layout. Secure Snow writes only the GRUB command line, which does
not carry that generic relaxation. Its secure-only GRUB menu selects entry zero
and uses a bounded five-second timeout. Both local `iso-sd-boot` and the Snow CI
publisher use the same secure-only boot-tar member selector: shim, signed GRUB,
and MokManager are exported only when `SECURE_SNOSI=1`.

The mode accepts either reviewed local files placed in the ignored
`live/src/secure-input/` directory or immutable HTTPS release URLs. Both the
Flatpak and Fisherman require independent lowercase SHA-256 values. Local names
must be simple filenames; URLs must be HTTPS and cannot contain a rolling
`latest`, `latest-dev`, or GitHub `/releases/latest/` alias. A missing artifact,
hash, or public runtime tool stops the build.

```bash
just secure_snosi=1 \
  secure_installer_flatpak=bootc-installer-2.7.0.flatpak \
  secure_installer_sha256=<64-hex> \
  secure_fisherman=fisherman-0.2.0 \
  secure_fisherman_sha256=<64-hex> \
  iso-sd-boot snow
```

For production replace the two local-name pairs with exact versioned HTTPS URLs
and their SHA-256 values. The catalog has `secure_install: true`; Fisherman
therefore resolves the selected GHCR tag, Cosign-verifies the immutable digest,
and installs only the digest-pinned reference under the restrictive policy.
Secure media deliberately embeds no offline OCI payload. Installation requires
network access so Fisherman can resolve the selected remote image and pull it
through that policy after Cosign verification; its empty writable container
storage is reserved for this accepted pull.
The secure path does not inject `root-mount-spec=LABEL=root`, `--karg`, or
root/LUKS kernel arguments.

The Snow publisher is intentionally fail-closed until these four repository
secrets name released, immutable artifacts: `SNOW_SECURE_INSTALLER_URL`,
`SNOW_SECURE_INSTALLER_URL_SHA256`, `SNOW_SECURE_FISHERMAN_URL`, and
`SNOW_SECURE_FISHERMAN_URL_SHA256`. URLs must name exact versioned release
objects, not a GitHub `latest` redirect. Empty, mutable, or hash-mismatched
values abort before the ISO is assembled; do not restore the old channel or
gitignored local-Fisherman override to make a release pass.

The GitHub publisher configures and verifies the runner-bundled `runc` before
using Podman. This is a hosted-runner compatibility choice for Podman 5.8.4 on
runner image `20260726`; it does not alter the live environment's runtime or
container-signature policy.

Run `test/snow-secure-boot-smoke.sh output/snow-live.iso` after a fresh secure
build where `OVMF_CODE_4M.secboot.fd` and `OVMF_VARS_4M.ms.fd` are installed.
It validates the ESP signatures, boots under Microsoft-enrolled firmware, and
modifies one byte in GRUB's signed `.text` PE section in place. The harness
requires the modified PE's signature table to remain parseable, then requires
shim to report `Security Violation` or `Security Policy Violation`. The positive
firmware boot and this negative rejection are the enforcement proof; `sbverify
--list` is structural only. Missing ISO, firmware, or host tools is reported as
`BLOCKED` (exit 2); a malformed image or a failed positive/negative proof is a
failure. Full installed-system validation is Task 9.

## Task 9 runner adapters

The external Snosi Task 9 harness invokes these test-only adapters directly:
`test/bootc-secure-installer-runner.sh`,
`test/bootc-secure-recovery-runner.sh`, and
`test/bootc-secure-update-publish.sh`.

There are no negative-fixture adapters. Snosi's harness no longer requires
them, so proving the installer refuses a deliberately-broken image is not
covered here. Note this is separate from the shim `Security Violation` proof
above, which is unaffected and remains this media's enforcement evidence.

They consume the exact `SNOSI_SECURE_OVMF_*` and `SNOSI_SECURE_TPM_*` state
provided by Snosi. The installer starts only its live-media VM, invokes the
reviewed `/usr/lib/snosi/fisherman` secure recipe with the immutable source and
same-repository tracking tag, stages recovery bytes only under `/run`, and
writes a test-only SSH drop-in with the supplied public key. It never mutates
BLS entries or kernel arguments.

The publisher uses `skopeo copy --all` to move the requested immutable digest
to the tracking tag, rejects a no-op, verifies the resulting registry digest,
and emits the supplied `N+1` or `N+2` marker. Negative runners are deliberately
fail-closed: they execute a supplied causal signed fixture or mutation, or print
`BLOCKED:` and exit 2 without a success marker. The recovery adapter replaces
only its exclusively-owned stopped TPM state, re-enrolls the installed UKI PCR
public key with the recovery credential, and stops its media VM and swtpm before
returning.

The installer must locate exactly one writable composefs deployment at
`state/deploy/*/etc`; legacy `ostree/bootc/deploy` discovery is not accepted.
Before opening it, and again through `cryptsetup status root` afterwards, the
runner requires exactly one target LUKS backing device. Recovery cases are
deliberately different: `tpm-replacement` recreates TPM state and proves an
unattended installed boot reaches a recovery/stale-token failure before using
the ISO; `recovery-reenrollment` preserves TPM state and proves the old TPM
token is absent and a distinct new token exists after rotation.

Run the always-runnable fixture layer without creating a cache:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_bootc_secure_runners
```
