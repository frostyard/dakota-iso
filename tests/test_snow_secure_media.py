"""Fail-closed source guards for the opt-in Snosi secure live medium."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).parent.parent
BUILD_ISO = REPO / "live" / "src" / "build-iso.sh"
CONTAINERFILE = REPO / "live" / "Containerfile"
INSTALL_FLATPAKS = REPO / "live" / "src" / "install-flatpaks.sh"
ISO_BUILD = REPO / "scripts" / "iso-sd-boot.sh"
BOOT_TAR_MEMBERS = REPO / "scripts" / "boot-tar-members.sh"
SQUASHFS_BUILD = REPO / "scripts" / "build-live-squashfs.sh"
CONFIGURE_LIVE = REPO / "live" / "src" / "configure-live.sh"
SNOW_IMAGES = REPO / "live" / "src" / "snow" / "images.json"
SNOW_HOOK = REPO / "live" / "src" / "snow" / "configure-live.d.sh"
SMOKE = REPO / "test" / "snow-secure-boot-smoke.sh"
SNOW_WORKFLOW = REPO / ".github" / "workflows" / "build-iso-snow.yml"


def make_boot_fixture(work: Path, secure: bool, signed: bool = True) -> tuple[Path, dict[str, str]]:
    """Build a minimal boot tar and stub external ISO tools for shell tests."""
    tree = work / "tree"
    modules = tree / "usr/lib/modules/6.1-test"
    modules.mkdir(parents=True)
    (modules / "vmlinuz").write_bytes(b"kernel")
    (modules / "initramfs.img").write_bytes(b"initrd")
    if secure:
        shim = tree / "usr/lib/shim"
        grub = tree / "usr/lib/grub/x86_64-efi-signed"
        shim.mkdir(parents=True)
        grub.mkdir(parents=True)
        (shim / "shimx64.efi.signed").write_bytes(b"shim")
        (shim / "mmx64.efi").write_bytes(b"mokmanager")
        (grub / "grubx64.efi.signed").write_bytes(b"grub")
    else:
        boot = tree / "usr/lib/systemd/boot/efi"
        boot.mkdir(parents=True)
        (boot / "systemd-bootx64.efi").write_bytes(b"systemd-boot")
    boot_tar = work / "boot.tar"
    subprocess.run(["tar", "-C", str(tree), "-cf", str(boot_tar), "."], check=True)
    squashfs = work / "root.sfs"
    squashfs.write_bytes(b"squashfs")
    tools = work / "tools"
    tools.mkdir()
    log = work / "mcopy.log"
    scripts = {
        # sbverify REPORTS rather than verifies: it exits 0 for a signed and an
        # unsigned binary alike, so only its OUTPUT distinguishes them. A stub
        # that merely exits 0 therefore models an UNSIGNED asset -- which is
        # what this stub used to be, and why the old exit-status check passed
        # its own tests while being unable to reject anything.
        "sbverify": (
            "#!/bin/sh\nprintf '%s\\n' 'signature 1' 'image signature issuers:' ' - /CN=Test'\nexit 0\n"
            if signed
            else "#!/bin/sh\necho 'No signature table present'\nexit 0\n"
        ),
        "mkfs.fat": "#!/bin/sh\nexit 0\n",
        "mmd": "#!/bin/sh\nexit 0\n",
        "mcopy": "#!/bin/sh\ncat \"$3\" >> \"$MCOPY_LOG\" 2>/dev/null || true\nexit 0\n",
        "xorriso": "#!/bin/sh\nwhile [ \"$#\" -gt 0 ]; do [ \"$1\" = -o ] && { : > \"$2\"; exit 0; }; shift; done\nexit 0\n",
        "implantisomd5": "#!/bin/sh\nexit 0\n",
        "du": "#!/bin/sh\nfor last; do :; done\nprintf '1\\t%s\\n' \"$last\"\n",
    }
    for name, content in scripts.items():
        path = tools / name
        path.write_text(content)
        path.chmod(0o755)
    env = os.environ | {
        "PATH": f"{tools}:{os.environ['PATH']}",
        "MCOPY_LOG": str(log),
        "TMPDIR": str(work),
    }
    return boot_tar, env


class TestSnowSecureMedia(unittest.TestCase):
    def test_exported_squashfs_function_derives_members_in_new_bash(self):
        source = ISO_BUILD.read_text()
        match = re.search(
            r"(?ms)^_ns_build_squashfs\(\) \{.*?^\}\nexport -f _ns_build_squashfs",
            source,
        )
        self.assertIsNotNone(match, "could not locate exported squashfs function")
        function = match.group(0)
        for secure, expected in (("0", {"./usr/lib/modules", "./usr/lib/systemd/boot/efi"}),
                                 ("1", {"./usr/lib/modules", "./usr/lib/systemd/boot/efi", "./usr/lib/shim", "./usr/lib/grub"})):
            result = subprocess.run(
                ["bash", "-u", "-c", function + "\nbash -u -c _ns_build_squashfs"],
                env=os.environ | {
                    "SECURE_SNOSI": secure,
                    "BOOT_TAR_MEMBERS_SCRIPT": str(BOOT_TAR_MEMBERS),
                    "ISO_SD_BOOT_TEST_CHILD_MEMBER_LIST": "1",
                },
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(result.stdout.splitlines()), expected)

    def test_secure_fixture_build_has_no_unbound_asset_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            boot_tar, env = make_boot_fixture(work, secure=True)
            result = subprocess.run(
                ["bash", "-u", str(BUILD_ISO), "--secure-snosi", str(boot_tar), str(work / "root.sfs"), str(work / "out.iso")],
                env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            secure_grub = (work / "mcopy.log").read_text()
            self.assertIn("set default=0", secure_grub)
            self.assertIn("set timeout=5", secure_grub)
            self.assertLess(secure_grub.index("set timeout=5"), secure_grub.index("menuentry"))
            for argument in ("root=live:LABEL=DAKOTA_LIVE", "rd.live.image", "rd.live.overlay.overlayfs=1", "nvidia-drm.modeset=1"):
                self.assertIn(argument, secure_grub)
            for forbidden in ("enforcing=0", "CDLABEL=", "root=live:/dev/sr0", "console=ttyAMA0"):
                self.assertNotIn(forbidden, secure_grub)

    def test_secure_build_refuses_an_unsigned_asset(self):
        """The guard must reject an unsigned asset, not merely a missing one.

        `sbverify --list` exits 0 whether or not a binary carries a signature,
        so gating on its exit status could never fail this way. The fixture's
        unsigned stub reproduces exactly that: it exits 0 and reports
        "No signature table present". Before the check read sbverify's output,
        this build succeeded and shipped media that firmware refuses at the
        first hop with EFI_ACCESS_DENIED.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            boot_tar, env = make_boot_fixture(work, secure=True, signed=False)
            result = subprocess.run(
                ["bash", "-u", str(BUILD_ISO), "--secure-snosi", str(boot_tar), str(work / "root.sfs"), str(work / "out.iso")],
                env=env, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0, "unsigned secure boot asset was accepted")
            self.assertIn("unsigned secure boot asset", result.stderr)
            self.assertFalse((work / "out.iso").exists(), "an ISO was produced from an unsigned asset")

    def test_generic_fixture_keeps_original_selinux_cmdline(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            boot_tar, env = make_boot_fixture(work, secure=False)
            result = subprocess.run(
                ["bash", str(BUILD_ISO), str(boot_tar), str(work / "root.sfs"), str(work / "out.iso")],
                env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("enforcing=0", (work / "mcopy.log").read_text())

    def test_boot_tar_member_selection_is_secure_snow_only(self):
        secure = subprocess.run(
            ["bash", str(BOOT_TAR_MEMBERS), "1"],
            text=True, capture_output=True,
        )
        generic = subprocess.run(
            ["bash", str(BOOT_TAR_MEMBERS), "0"],
            text=True, capture_output=True,
        )
        self.assertEqual(secure.returncode, 0, secure.stderr)
        self.assertEqual(generic.returncode, 0, generic.stderr)
        self.assertIn("./usr/lib/shim", secure.stdout)
        self.assertIn("./usr/lib/grub", secure.stdout)
        self.assertNotIn("./usr/lib/shim", generic.stdout)
        self.assertNotIn("./usr/lib/grub", generic.stdout)
        for path in (ISO_BUILD, SQUASHFS_BUILD):
            self.assertIn("boot-tar-members.sh", path.read_text(), path)

    def test_secure_mode_is_explicit_and_uses_debian_trusted_efi_chain(self):
        content = BUILD_ISO.read_text()
        for marker in (
            "--secure-snosi",
            "shimx64.efi.signed",
            "grubx64.efi.signed",
            "mmx64.efi",
            "EFI/BOOT/grubx64.efi",
            "sbverify --list",
        ):
            self.assertIn(marker, content)
        self.assertNotIn("fbx64.efi", content)
        self.assertNotIn("LIVE_ROOT=", content)
        self.assertNotIn("LIVE_RD_", content)

    def test_generic_boot_entries_preserve_selinux_exception(self):
        self.assertIn("enforcing=0", BUILD_ISO.read_text())

    def test_secure_mode_skips_generic_root_mount_override(self):
        for path in (ISO_BUILD, SQUASHFS_BUILD):
            content = path.read_text()
            self.assertIn("SECURE_SNOSI", content, path)
            self.assertIn("root-mount-spec", content, path)

    def test_secure_assembly_skips_offline_payload_embedding(self):
        """Secure media must leave payload acceptance to Fisherman's online pull."""
        for path in (SQUASHFS_BUILD, ISO_BUILD):
            content = path.read_text()
            self.assertIn(
                'SECURE_SNOSI}" != "1"',
                content,
                f"{path}: secure assembly must exclude local OCI archive imports",
            )
            self.assertIn(
                "online Cosign- and policy-verified pull",
                content,
                f"{path}: secure assembly must document the required online pull",
            )

    def test_squashfs_embedding_fixture_skips_secure_archive_and_keeps_generic_import(self):
        """Run the assembly script with stubs to exercise both embedding branches."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            mount = work / "mount"
            (mount / "usr/lib/modules").mkdir(parents=True)
            (mount / "usr/lib/systemd/boot/efi").mkdir(parents=True)
            (mount / "usr/lib/shim").mkdir(parents=True)
            (mount / "usr/lib/grub").mkdir(parents=True)
            tools = work / "tools"
            tools.mkdir()
            log = work / "commands.log"
            stubs = {
                "id": "#!/bin/sh\necho 0\n",
                "findmnt": "#!/bin/sh\nexit 1\n",
                "mksquashfs": "#!/bin/sh\ntouch \"$2\"\n",
                "podman": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LOG\"\nif [ \"$1\" = image ] && [ \"$2\" = mount ]; then printf '%s\\n' \"$MOUNT\"; fi\n",
                "buildah": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LOG\"\n[ \"$1\" = from ] && echo fixture-container\nexit 0\n",
                "skopeo": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LOG\"\nif [ \"$1\" = inspect ]; then printf '%s\\n' '{\"rootfs\":{\"diff_ids\":[\"sha256:fixture\"]}}'; fi\n",
            }
            for name, content in stubs.items():
                path = tools / name
                path.write_text(content)
                path.chmod(0o755)

            def assemble(secure: str) -> str:
                log.write_text("")
                result = subprocess.run(
                    ["bash", str(SQUASHFS_BUILD), "--oci-image", "example.invalid/payload:fixed", "fixture-live", str(work / f"{secure}.sfs"), str(work / f"{secure}.tar")],
                    env=os.environ | {
                        "PATH": f"{tools}:{os.environ['PATH']}",
                        "LOG": str(log),
                        "MOUNT": str(mount),
                        "SECURE_SNOSI": secure,
                        "SUPERISO_TMPDIR": str(work),
                    },
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return log.read_text()

            secure_log = assemble("1")
            self.assertNotIn("oci-archive:/payload.oci.tar", secure_log)
            self.assertNotIn("buildah", secure_log)
            generic_log = assemble("0")
            self.assertIn("oci-archive:/payload.oci.tar", generic_log)
            self.assertIn("containers-storage:example.invalid/payload:fixed", generic_log)

    def test_generic_assembly_retains_offline_payload_embedding(self):
        """The generic offline payload pipeline remains available unchanged."""
        for path in (SQUASHFS_BUILD, ISO_BUILD):
            content = path.read_text()
            self.assertIn("oci-archive:/payload.oci.tar", content, path)
            self.assertIn("containers-storage:${", content, path)

    def test_secure_runtime_keeps_writable_storage_and_restrictive_policy(self):
        """Secure online pulls need writable storage without accepting docker images."""
        configure_live = CONFIGURE_LIVE.read_text()
        policy = SNOW_HOOK.read_text()
        self.assertIn("mkdir -p /var/lib/containers/storage", configure_live)
        self.assertIn('"default": [{"type": "reject"}]', policy)
        for image in ("cayo", "snow", "snowfield"):
            self.assertIn(f'ghcr.io/frostyard/{image}', policy)
        self.assertNotIn('"docker": {"": [{"type":"insecureAcceptAnything"}]', policy)
        self.assertIn('"oci": {"": [{"type":"insecureAcceptAnything"}]}', policy)
        self.assertIn('"oci-archive": {"": [{"type":"insecureAcceptAnything"}]}', policy)

    def test_secure_container_installs_required_public_tools(self):
        content = CONTAINERFILE.read_text()
        for package in (
            "shim-signed",
            "grub-efi-amd64-signed",
            "shim-helpers-amd64-signed",
            "mokutil",
            "sbsigntool",
            "binutils",
            "systemd-cryptsetup",
            "cryptsetup",
        ):
            self.assertIn(package, content)

    def test_secure_inputs_are_hashed_and_never_use_rolling_release_urls(self):
        content = INSTALL_FLATPAKS.read_text()
        for marker in (
            "SECURE_SNOSI",
            "SECURE_INSTALLER_FLATPAK",
            "SECURE_INSTALLER_SHA256",
            "SECURE_FISHERMAN",
            "SECURE_FISHERMAN_SHA256",
            "SECURE_INSTALLER_URL",
            "SECURE_INSTALLER_URL_SHA256",
        ):
            self.assertIn(marker, content)
        secure_section = content.split("# ── Secure Snosi installer inputs", 1)[1]
        self.assertIn("must not use mutable release aliases", secure_section)
        self.assertIn("mkdir -p /usr/lib/snosi", secure_section)
        self.assertLess(
            secure_section.index("mkdir -p /usr/lib/snosi"),
            secure_section.index("sha256_input \"$SECURE_INSTALLER_FLATPAK\""),
        )
        self.assertIn('elif [[ -x "${SNOW_DIR}/fisherman" ]]', SNOW_HOOK.read_text())

    def test_snow_catalog_explicitly_selects_fisherman_secure_path(self):
        catalog = json.loads(SNOW_IMAGES.read_text())
        self.assertEqual(len(catalog["images"]), 3)
        for image in catalog["images"]:
            self.assertTrue(image.get("secure_install"), image["name"])
            self.assertTrue(image["imgref"].startswith("ghcr.io/frostyard/"))
            self.assertNotIn("containers-storage:", image["imgref"])

    def test_secure_policy_and_public_keys_ship_without_private_keys(self):
        content = SNOW_HOOK.read_text()
        for marker in (
            "/etc/containers/policy.json",
            "/etc/containers/registries.d/frostyard.yaml",
            "use-sigstore-attachments: true",
            "mkdir -p /usr/lib/snosi",
            "/usr/lib/snosi/cosign.pub",
            "mokutil",
            "systemd-cryptenroll",
            "cryptsetup",
        ):
            self.assertIn(marker, content)
        self.assertNotIn("mkosi.key", content)
        self.assertNotIn("pcr-signing.key", content)
        secure_block = content.split('if [[ "${SECURE_SNOSI:-0}" == "1" ]]; then', 1)[1]
        self.assertIn("policy.json", secure_block)
        self.assertIn("frostyard.yaml", secure_block)
        self.assertNotIn("ghcr.io.yaml", secure_block)

    def test_secure_boot_smoke_has_enforced_and_negative_paths(self):
        content = SMOKE.read_text()
        for marker in (
            "OVMF_CODE_4M.secboot.fd",
            "OVMF_VARS_4M.ms.fd",
            "Security Violation",
            "sbverify",
            "objdump -h",
            "dd of=",
            "signature table became unreadable after tampering",
            "Security (Policy )?Violation",
            "mcopy -o",
            'for tool in "$QEMU" sbverify objdump od dd xorriso mcopy; do',
            "^ 1",
            "od -An -tu1",
        ):
            self.assertIn(marker, content)
        self.assertNotIn("objcopy", content)
        disposable_copy = 'cp "${WORK}/efi.img" "${WORK}/unsigned-efi.img"'
        make_disposable_writable = 'chmod u+w "${WORK}/unsigned-efi.img"'
        replace_grub = 'mcopy -o -i "${WORK}/unsigned-efi.img"'
        self.assertIn(disposable_copy, content)
        self.assertIn(make_disposable_writable, content)
        self.assertLess(content.index(disposable_copy), content.index(make_disposable_writable))
        self.assertLess(content.index(make_disposable_writable), content.index(replace_grub))
        self.assertNotIn('chmod u+w "${WORK}/efi.img"', content)

    def test_snow_publisher_cannot_fall_back_to_the_generic_media_path(self):
        content = SNOW_WORKFLOW.read_text()
        self.assertIn("SECURE_SNOSI: 1", content)
        self.assertIn("--secure-snosi", content)
        self.assertIn("snow-secure-boot-smoke.sh", content)
        self.assertNotIn("continue-on-error: true", content)

    def test_snow_publisher_selects_compatible_podman_runtime(self):
        content = SNOW_WORKFLOW.read_text()
        install = content.index("- name: Install dependencies")
        runtime = content.index("- name: Configure Podman OCI runtime")
        login = content.index("- name: Log in to GHCR")
        self.assertLess(install, runtime)
        self.assertLess(runtime, login)
        for marker in (
            "/etc/containers/containers.conf.d/99-dakota-ci-runtime.conf",
            'runtime = "runc"',
            "sudo podman info --format '{{.Host.OCIRuntime.Name}}'",
            "[[ $runtime == runc ]]",
        ):
            self.assertIn(marker, content)
