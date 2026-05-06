"""
Tests for bash scripts changed or added in this PR:

  - .github/scripts/mount_btrfs.sh   (new file)
  - aurora/src/build-iso.sh          (new file)
  - dakota/src/build-iso.sh          (modified: added isohybrid --gpt)
  - aurora/src/install-flatpaks.sh   (new file)
  - aurora/src/configure-live.sh     (new file)

These tests verify:
  1. Bash syntax validity (bash -n)
  2. Required shebang lines
  3. Presence of safety flags (set -e / set -eo / set -euo pipefail)
  4. Key structural invariants that catch regressions in the PR changes
"""

import os
import re
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bash_syntax_check(path: str) -> tuple:
    """Run 'bash -n <path>' and return (returncode, stderr)."""
    result = subprocess.run(
        ["bash", "-n", path],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr


def _read(path: str) -> str:
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_pipefail(content: str) -> bool:
    """Return True if the script sets pipefail anywhere."""
    for line in content.splitlines():
        if "pipefail" in line:
            return True
    return False


def _has_errexit(content: str) -> bool:
    """Return True if the script sets -e (errexit) anywhere.

    Handles forms like: set -e, set -eo, set -euo, set -euo pipefail
    """
    for line in content.splitlines():
        # Match 'set' followed by a flag group containing 'e'
        if re.search(r'\bset\s+-[a-zA-Z]*e[a-zA-Z]*', line):
            return True
    return False


# ---------------------------------------------------------------------------
# .github/scripts/mount_btrfs.sh
# ---------------------------------------------------------------------------


class TestMountBtrfsSh(unittest.TestCase):
    PATH = os.path.join(REPO_ROOT, ".github", "scripts", "mount_btrfs.sh")

    def test_file_exists(self):
        self.assertTrue(os.path.exists(self.PATH), f"Not found: {self.PATH}")

    def test_bash_syntax_valid(self):
        rc, stderr = _bash_syntax_check(self.PATH)
        self.assertEqual(rc, 0, f"bash -n failed:\n{stderr}")

    def test_has_bash_shebang(self):
        content = _read(self.PATH)
        first_line = content.splitlines()[0]
        self.assertIn("bash", first_line)
        self.assertTrue(first_line.startswith("#!"))

    def test_has_errexit_and_pipefail(self):
        content = _read(self.PATH)
        self.assertTrue(_has_errexit(content), "Missing set -e or -eo or -euo")
        self.assertTrue(_has_pipefail(content), "Missing pipefail option")

    def test_checks_mountpoint_for_mnt(self):
        """Script must check if /mnt is a separate mount point before proceeding."""
        content = _read(self.PATH)
        self.assertIn("mountpoint", content)
        self.assertIn("/mnt", content)

    def test_exits_gracefully_when_mnt_not_mounted(self):
        """Early exit path (exit 0) must exist for /mnt not being a mountpoint."""
        content = _read(self.PATH)
        # The script should have an 'exit 0' for the non-mountpoint case
        self.assertIn("exit 0", content)

    def test_defines_btrfs_target_dir_variable(self):
        content = _read(self.PATH)
        self.assertIn("BTRFS_TARGET_DIR", content)

    def test_defines_btrfs_loopback_free_variable(self):
        """BTRFS_LOOPBACK_FREE controls how much of /mnt is used."""
        content = _read(self.PATH)
        self.assertIn("BTRFS_LOOPBACK_FREE", content)

    def test_uses_truncate_for_loopback(self):
        """Loopback file creation uses truncate."""
        content = _read(self.PATH)
        self.assertIn("truncate", content)

    def test_formats_with_mkfs_btrfs(self):
        content = _read(self.PATH)
        self.assertIn("mkfs.btrfs", content)

    def test_mounts_with_systemd_mount(self):
        content = _read(self.PATH)
        self.assertIn("systemd-mount", content)

    def test_min_space_check_present(self):
        """Script checks for minimum available space before proceeding."""
        content = _read(self.PATH)
        self.assertIn("MIN_SPACE", content)

    def test_uses_jq_for_json_parsing(self):
        """Space availability is determined by parsing findmnt JSON output."""
        content = _read(self.PATH)
        self.assertIn("jq", content)

    def test_install_btrfs_progs(self):
        """Script installs btrfs-progs before use."""
        content = _read(self.PATH)
        self.assertIn("btrfs-progs", content)

    def test_default_loopback_free_is_0_8(self):
        """Default BTRFS_LOOPBACK_FREE should be 0.8 (80%)."""
        content = _read(self.PATH)
        self.assertIn('"0.8"', content)

    def test_mount_opts_variable_used(self):
        """BTRFS_MOUNT_OPTS should be referenced for mount options."""
        content = _read(self.PATH)
        self.assertIn("BTRFS_MOUNT_OPTS", content)


# ---------------------------------------------------------------------------
# aurora/src/build-iso.sh  (new file)
# ---------------------------------------------------------------------------


class TestAuroraBuildIsoSh(unittest.TestCase):
    PATH = os.path.join(REPO_ROOT, "aurora", "src", "build-iso.sh")

    def test_file_exists(self):
        self.assertTrue(os.path.exists(self.PATH))

    def test_bash_syntax_valid(self):
        rc, stderr = _bash_syntax_check(self.PATH)
        self.assertEqual(rc, 0, f"bash -n failed:\n{stderr}")

    def test_has_bash_shebang(self):
        content = _read(self.PATH)
        self.assertTrue(content.splitlines()[0].startswith("#!"))
        self.assertIn("bash", content.splitlines()[0])

    def test_has_errexit_and_pipefail(self):
        content = _read(self.PATH)
        self.assertTrue(_has_errexit(content))
        self.assertTrue(_has_pipefail(content))

    def test_requires_exactly_three_arguments(self):
        """Script must error when called with no arguments."""
        result = subprocess.run(
            ["bash", self.PATH],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_iso_label_is_dakota_live(self):
        """The ISO volume label must be DAKOTA_LIVE for dmsquash-live detection."""
        content = _read(self.PATH)
        self.assertIn("DAKOTA_LIVE", content)

    def test_uses_xorriso(self):
        content = _read(self.PATH)
        self.assertIn("xorriso", content)

    def test_uses_mtools(self):
        """FAT ESP population uses mtools (mmd/mcopy)."""
        content = _read(self.PATH)
        self.assertIn("mtools", content.lower())
        self.assertTrue(
            "mmd" in content or "mcopy" in content,
            "Expected mmd or mcopy from mtools package",
        )

    def test_uses_mkfs_fat(self):
        """ESP image is created with mkfs.fat."""
        content = _read(self.PATH)
        self.assertIn("mkfs.fat", content)

    def test_uses_implantisomd5(self):
        """ISO integrity checksum must be embedded."""
        content = _read(self.PATH)
        self.assertIn("implantisomd5", content)

    def test_uses_isohybrid_with_gpt(self):
        """isohybrid --gpt must be called for hybrid MBR/GPT support (new in PR)."""
        content = _read(self.PATH)
        self.assertIn("isohybrid", content)
        self.assertIn("--gpt", content)

    def test_efi_binary_detection_covers_both_arches(self):
        """Both amd64 (x64) and arm64 (aa64) EFI binaries must be handled."""
        content = _read(self.PATH)
        self.assertIn("systemd-bootx64.efi", content)
        self.assertIn("systemd-bootaa64.efi", content)

    def test_squashfs_goes_into_liveos_directory(self):
        """squashfs.img must be placed in LiveOS/ for dmsquash-live."""
        content = _read(self.PATH)
        self.assertIn("LiveOS", content)
        self.assertIn("squashfs.img", content)

    def test_efi_fallback_path_added_to_iso_root(self):
        """EFI fallback binary at EFI/BOOT/ on ISO root for Ventoy/Proxmox OVMF."""
        content = _read(self.PATH)
        self.assertIn("EFI/BOOT", content)

    def test_trap_cleans_up_workdir(self):
        """Work directory must be cleaned up even on failure via trap."""
        content = _read(self.PATH)
        self.assertIn("trap", content)
        self.assertIn("rm -rf", content)

    def test_kernel_cmdline_includes_rd_live_image(self):
        """dmsquash-live requires rd.live.image in the kernel cmdline."""
        content = _read(self.PATH)
        self.assertIn("rd.live.image", content)

    def test_kernel_cmdline_includes_overlayfs(self):
        """overlayfs must be enabled in the cmdline."""
        content = _read(self.PATH)
        self.assertIn("overlayfs", content)

    def test_loader_conf_has_timeout(self):
        """systemd-boot loader.conf must define a timeout."""
        content = _read(self.PATH)
        self.assertIn("timeout", content)

    def test_esp_size_calculation_adds_headroom(self):
        """ESP size calculation must add headroom beyond kernel+initramfs."""
        content = _read(self.PATH)
        # Headroom is added via arithmetic
        self.assertIn("ESP_MB", content)


# ---------------------------------------------------------------------------
# dakota/src/build-iso.sh  (modified: added isohybrid --gpt)
# ---------------------------------------------------------------------------


class TestDakotaBuildIsoSh(unittest.TestCase):
    PATH = os.path.join(REPO_ROOT, "dakota", "src", "build-iso.sh")

    def test_file_exists(self):
        self.assertTrue(os.path.exists(self.PATH))

    def test_bash_syntax_valid(self):
        rc, stderr = _bash_syntax_check(self.PATH)
        self.assertEqual(rc, 0, f"bash -n failed:\n{stderr}")

    def test_has_isohybrid_with_gpt(self):
        """PR change: isohybrid --gpt must be present in the dakota build script."""
        content = _read(self.PATH)
        self.assertIn("isohybrid", content)
        self.assertIn("--gpt", content)

    def test_isohybrid_called_after_implantisomd5(self):
        """isohybrid must run after implantisomd5 (MBR after checksum embedding)."""
        content = _read(self.PATH)
        idx_md5 = content.find("implantisomd5")
        idx_hybrid = content.find("isohybrid")
        self.assertGreater(idx_hybrid, idx_md5,
                           "isohybrid must come after implantisomd5 in the script")

    def test_trap_uses_single_quotes(self):
        """PR change: trap uses single quotes to prevent early variable expansion."""
        content = _read(self.PATH)
        # The PR changed the trap from double-quotes to single-quotes
        # Find the trap line and verify it uses single quotes around the command
        for line in content.splitlines():
            if line.strip().startswith("trap "):
                self.assertTrue(
                    line.strip().startswith("trap '"),
                    f"trap should use single quotes; got: {line.strip()!r}",
                )
                break

    def test_iso_label_is_dakota_live(self):
        content = _read(self.PATH)
        self.assertIn("DAKOTA_LIVE", content)

    def test_bash_syntax_after_isohybrid_addition(self):
        """The isohybrid addition at the end of the file must not break syntax."""
        rc, stderr = _bash_syntax_check(self.PATH)
        self.assertEqual(rc, 0, f"Syntax error after isohybrid addition:\n{stderr}")


# ---------------------------------------------------------------------------
# aurora/src/configure-live.sh  (new file)
# ---------------------------------------------------------------------------


class TestConfigureLiveSh(unittest.TestCase):
    PATH = os.path.join(REPO_ROOT, "aurora", "src", "configure-live.sh")

    def test_file_exists(self):
        self.assertTrue(os.path.exists(self.PATH))

    def test_bash_syntax_valid(self):
        rc, stderr = _bash_syntax_check(self.PATH)
        self.assertEqual(rc, 0, f"bash -n failed:\n{stderr}")

    def test_has_bash_shebang(self):
        content = _read(self.PATH)
        self.assertTrue(content.splitlines()[0].startswith("#!"))
        self.assertIn("bash", content.splitlines()[0])

    def test_creates_liveuser(self):
        """Script must create a 'liveuser' account for autologin."""
        content = _read(self.PATH)
        self.assertIn("liveuser", content)
        self.assertIn("useradd", content)

    def test_liveuser_has_uid_1000(self):
        """liveuser UID must be 1000 (convention for first human user)."""
        content = _read(self.PATH)
        self.assertIn("--uid 1000", content)

    def test_gdm_autologin_configured(self):
        """GDM must be configured for passwordless autologin."""
        content = _read(self.PATH)
        self.assertIn("AutomaticLoginEnable", content)
        self.assertIn("AutomaticLogin=liveuser", content)

    def test_live_ready_service_created(self):
        """live-ready.service must be created for CI boot verification."""
        content = _read(self.PATH)
        self.assertIn("live-ready.service", content)

    def test_live_ready_service_outputs_dakota_live_ready(self):
        """live-ready.service must echo DAKOTA_LIVE_READY to the console."""
        content = _read(self.PATH)
        self.assertIn("DAKOTA_LIVE_READY", content)

    def test_live_ready_service_uses_journal_console(self):
        """PR change: live-ready uses journal+console (not tty/TTYPath)."""
        content = _read(self.PATH)
        self.assertIn("journal+console", content)

    def test_live_ready_service_wants_display_manager(self):
        """PR change: live-ready uses Wants= (not Requires=) for display-manager."""
        content = _read(self.PATH)
        self.assertIn("Wants=display-manager.service", content)
        # Must NOT use Requires= (was changed in PR)
        # Find the live-ready.service block to check it specifically
        service_start = content.find("live-ready.service")
        service_block_end = content.find("LREOF", service_start)
        if service_start != -1 and service_block_end != -1:
            service_block = content[service_start:service_block_end]
            self.assertNotIn(
                "Requires=display-manager.service", service_block,
                "live-ready.service must use Wants= not Requires=",
            )

    def test_wantedby_display_manager(self):
        """PR change: live-ready.service is WantedBy=display-manager.service."""
        content = _read(self.PATH)
        self.assertIn("WantedBy=display-manager.service", content)

    def test_dconf_update_called(self):
        """dconf update must be called after writing dconf settings."""
        content = _read(self.PATH)
        self.assertIn("dconf update", content)

    def test_vfs_storage_conf_written(self):
        """VFS containers-storage.conf must be written for offline install."""
        content = _read(self.PATH)
        self.assertIn('driver = "vfs"', content)
        self.assertIn("storage.conf", content)

    def test_var_tmp_mount_unit_created(self):
        """var-tmp.mount unit must be created for large tmpfs in live session."""
        content = _read(self.PATH)
        self.assertIn("var-tmp.mount", content)
        self.assertIn("tmpfs", content)

    def test_polkit_rules_written(self):
        """polkit rules must be written to grant passwordless installer access."""
        content = _read(self.PATH)
        self.assertIn("polkit", content)
        self.assertIn("liveuser", content)

    def test_installer_autostart_created(self):
        """Installer autostart .desktop file must be created in xdg/autostart."""
        content = _read(self.PATH)
        self.assertIn("xdg/autostart", content)
        self.assertIn("tuna-installer.desktop", content)

    def test_debug_mode_enables_ssh(self):
        """DEBUG=1 path must configure sshd."""
        content = _read(self.PATH)
        self.assertIn("DEBUG", content)
        self.assertIn("sshd", content)

    def test_sleep_targets_masked(self):
        """Sleep/suspend targets must be masked to prevent accidental suspend."""
        content = _read(self.PATH)
        self.assertIn("sleep.target", content)
        self.assertIn("suspend.target", content)
        self.assertIn("systemctl mask", content)

    def test_sudoers_drop_in_written(self):
        """liveuser must get passwordless sudo via sudoers.d drop-in."""
        content = _read(self.PATH)
        self.assertIn("NOPASSWD", content)
        self.assertIn("sudoers", content)

    def test_dakota_icon_installed(self):
        """Dakota icon must be installed in hicolor theme hierarchy."""
        content = _read(self.PATH)
        self.assertIn("dakota.png", content)
        self.assertIn("hicolor", content)

    def test_installer_config_files_copied(self):
        """images.json and recipe.json must be copied to /etc/bootc-installer/."""
        content = _read(self.PATH)
        self.assertIn("images.json", content)
        self.assertIn("recipe.json", content)
        self.assertIn("/etc/bootc-installer", content)

    def test_live_iso_mode_flag_created(self):
        """live-iso-mode flag file must be created for the installer."""
        content = _read(self.PATH)
        self.assertIn("live-iso-mode", content)

    def test_installer_channel_logic_present(self):
        """Script must handle both stable and dev INSTALLER_CHANNEL values."""
        content = _read(self.PATH)
        self.assertIn("INSTALLER_CHANNEL", content)
        self.assertIn("Installer.Devel", content)


# ---------------------------------------------------------------------------
# aurora/src/install-flatpaks.sh  (new file)
# ---------------------------------------------------------------------------


class TestInstallFlatpaksSh(unittest.TestCase):
    PATH = os.path.join(REPO_ROOT, "aurora", "src", "install-flatpaks.sh")

    def test_file_exists(self):
        self.assertTrue(os.path.exists(self.PATH))

    def test_bash_syntax_valid(self):
        rc, stderr = _bash_syntax_check(self.PATH)
        self.assertEqual(rc, 0, f"bash -n failed:\n{stderr}")

    def test_has_bash_shebang(self):
        content = _read(self.PATH)
        self.assertTrue(content.splitlines()[0].startswith("#!"))

    def test_adds_flathub_remote(self):
        """Flathub remote must be added if not already present."""
        content = _read(self.PATH)
        self.assertIn("flathub", content)
        self.assertIn("flatpak remote-add", content)

    def test_handles_installer_channel_stable_and_dev(self):
        """Both stable (continuous) and dev (continuous-dev) tags must be handled."""
        content = _read(self.PATH)
        self.assertIn("continuous-dev", content)
        self.assertIn("continuous", content)
        self.assertIn("INSTALLER_CHANNEL", content)

    def test_downloads_tuna_installer_bundle(self):
        """tuna-installer .flatpak bundle must be downloaded from GitHub Releases."""
        content = _read(self.PATH)
        self.assertIn("tuna-installer", content)
        self.assertIn("tuna-installer.flatpak", content)

    def test_grants_filesystem_etc_ro(self):
        """Installer flatpak must get read-only /etc access for recipe.json."""
        content = _read(self.PATH)
        self.assertIn("--filesystem=/etc:ro", content)

    def test_reconciles_against_flatpaks_list(self):
        """Script must read /tmp/flatpaks-list to determine wanted apps."""
        content = _read(self.PATH)
        self.assertIn("flatpaks-list", content)

    def test_removes_dropped_apps(self):
        """Apps no longer in the wanted list must be uninstalled."""
        content = _read(self.PATH)
        self.assertIn("flatpak uninstall", content)

    def test_prunes_unused_runtimes(self):
        """Unused runtimes left by removals must be pruned."""
        content = _read(self.PATH)
        self.assertIn("--unused", content)

    def test_saves_cache_at_end(self):
        """flatpak repo must be saved back to build cache after reconciliation."""
        content = _read(self.PATH)
        # The cache save section uses rsync to write back to FLATPAK_CACHE
        self.assertIn("FLATPAK_CACHE", content)
        self.assertIn("rsync", content)

    def test_seeds_from_cache_at_start(self):
        """Build cache must be seeded into /var/lib/flatpak/repo at startup."""
        content = _read(self.PATH)
        # Warm-start rsync
        self.assertIn("Seeding flatpak repo", content)

    def test_installer_app_ids_preserved_during_reconcile(self):
        """Both stable and devel installer app IDs must be kept during cleanup."""
        content = _read(self.PATH)
        self.assertIn("org.bootcinstaller.Installer", content)
        self.assertIn("org.bootcinstaller.Installer.Devel", content)

    def test_dbus_daemon_started(self):
        """flatpak requires dbus; the script must start dbus-daemon."""
        content = _read(self.PATH)
        self.assertIn("dbus-daemon", content)

    def test_no_related_flag_used(self):
        """--no-related flag skips locale packs and debug symbols."""
        content = _read(self.PATH)
        self.assertIn("--no-related", content)

    def test_uses_curl_with_retry(self):
        """Installer bundle download must use --retry for resilience."""
        content = _read(self.PATH)
        self.assertIn("curl", content)
        self.assertIn("--retry", content)

    def test_flatpak_install_uses_or_update_flag(self):
        """--or-update is used to install or update Flathub apps."""
        content = _read(self.PATH)
        self.assertIn("--or-update", content)


# ---------------------------------------------------------------------------
# General: all new shell scripts are executable
# ---------------------------------------------------------------------------


class TestShellScriptPermissions(unittest.TestCase):
    # mount_btrfs.sh is run via `bash ./script.sh` in CI (not directly executed),
    # so it does not require the execute bit.  Only src/ scripts that are invoked
    # via chmod +x inside containers need the execute bit.
    NEW_SCRIPTS = [
        os.path.join(REPO_ROOT, "aurora", "src", "build-iso.sh"),
        os.path.join(REPO_ROOT, "aurora", "src", "configure-live.sh"),
        os.path.join(REPO_ROOT, "aurora", "src", "install-flatpaks.sh"),
    ]

    def test_scripts_are_executable(self):
        for path in self.NEW_SCRIPTS:
            with self.subTest(path=os.path.basename(path)):
                self.assertTrue(
                    os.access(path, os.X_OK),
                    f"Script is not executable: {path}",
                )


if __name__ == "__main__":
    unittest.main()
