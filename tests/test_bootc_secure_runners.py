"""Fixture contracts for Dakota's external Snosi Task 9 runners."""

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).parent.parent
INSTALLER = REPO / "test" / "bootc-secure-installer-runner.sh"
NEGATIVE = REPO / "test" / "bootc-secure-negative-runner.sh"
RECOVERY = REPO / "test" / "bootc-secure-recovery-runner.sh"
PUBLISH = REPO / "test" / "bootc-secure-update-publish.sh"
UPDATE_NEGATIVE = REPO / "test" / "bootc-secure-update-negative-runner.sh"


class TestBootcSecureRunners(unittest.TestCase):
    def run_runner(self, runner: Path, *args: str, env: dict[str, str] | None = None):
        return subprocess.run(
            ["/bin/bash", str(runner), *args],
            cwd=REPO,
            env=os.environ | (env or {}),
            text=True,
            capture_output=True,
        )

    def test_all_runners_are_shell_syntax_valid(self):
        for runner in (INSTALLER, NEGATIVE, RECOVERY, PUBLISH, UPDATE_NEGATIVE):
            result = subprocess.run(["/bin/bash", "-n", str(runner)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_requires_exact_arguments_before_touching_state(self):
        result = self.run_runner(INSTALLER, "--non-interactive")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)

    def test_installer_missing_live_artifact_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            recipe = work / "recipe.json"
            recipe.write_text("{}")
            result = self.run_runner(
                INSTALLER,
                "--non-interactive", "--iso", str(work / "missing.iso"), "--recipe", str(recipe),
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("BLOCKED:", result.stderr)

    def test_negative_rejects_unknown_case(self):
        result = self.run_runner(NEGATIVE, "--case", "not-a-case", "--profile", "cayo", "--oci-ref", "x", "--recipe", "x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown secure negative case", result.stderr)

    def test_negative_without_signed_fixture_is_blocked_not_success(self):
        result = self.run_runner(NEGATIVE, "--case", "unsigned", "--profile", "cayo", "--oci-ref", "x", "--recipe", "x")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("BLOCKED:", result.stderr)
        self.assertNotIn("rejected", result.stdout)

    def test_recovery_requires_mode_0600_path_only_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text('{"target_disk":"/dev/null"}')
            state.chmod(0o644)
            result = self.run_runner(
                RECOVERY,
                "--case", "tpm-replacement", "--profile", "cayo", "--oci-ref", "x",
                "--state", str(state), "--iso", "x", "--recipe", "x", "--recovery-key", "x",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mode 0600", result.stderr)

    def test_publisher_requires_slot_and_immutable_digest(self):
        result = self.run_runner(PUBLISH, "--profile", "cayo", "--tracking-ref", "ghcr.io/frostyard/cayo:test", "--digest-ref", "ghcr.io/frostyard/cayo:tag")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--slot", result.stderr)

    def test_publisher_missing_skopeo_is_blocked(self):
        self.assertIn("need skopeo", PUBLISH.read_text())

    def test_update_negative_is_blocked_without_live_state(self):
        result = self.run_runner(UPDATE_NEGATIVE, "--case", "unsigned", "--profile", "cayo", "--state", "/missing", "--tracking-ref", "ghcr.io/frostyard/cayo:test")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("BLOCKED:", result.stderr)
        self.assertNotIn("rejected", result.stdout)

    def test_runner_sources_forbid_bls_or_kernel_argument_mutation(self):
        for runner in (INSTALLER, NEGATIVE, RECOVERY, UPDATE_NEGATIVE):
            content = runner.read_text()
            self.assertNotIn("rd.luks.name=", content)
            self.assertNotIn("--karg", content)
            self.assertNotIn("sed -i.*loader", content)

    def test_installer_uses_live_only_recovery_copy_and_test_ssh_dropin(self):
        content = INSTALLER.read_text() + (REPO / "test/lib/bootc-secure-runner-lib.sh").read_text()
        self.assertIn("umask 077", content)
        self.assertIn("AuthorizedKeysFile", content)
        self.assertIn("99-snosi-task9-test.conf", content)
        self.assertIn("/run/snosi-task9", content)

    def test_installer_targets_exactly_one_composefs_persistent_etc(self):
        content = INSTALLER.read_text() + (REPO / "test/lib/bootc-secure-runner-lib.sh").read_text()
        self.assertIn('"$mount/state/deploy"/*/etc', content)
        self.assertIn("expected exactly one composefs persistent etc", content)
        self.assertNotIn("ostree/bootc/deploy", content)
        self.assertIn("test \"$(stat -c '%a'", content)

    def test_composefs_persistent_etc_guard_accepts_only_one_tree(self):
        library = REPO / "test/lib/bootc-secure-runner-lib.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = (
                f"source {library}; composefs_persistent_etc {root}"
            )
            zero = subprocess.run(["/bin/bash", "-c", command], text=True, capture_output=True)
            self.assertNotEqual(zero.returncode, 0)
            (root / "state/deploy/one/etc").mkdir(parents=True)
            one = subprocess.run(["/bin/bash", "-c", command], text=True, capture_output=True)
            self.assertEqual(one.returncode, 0, one.stderr)
            self.assertEqual(one.stdout.strip(), str(root / "state/deploy/one/etc"))
            (root / "state/deploy/two/etc").mkdir(parents=True)
            two = subprocess.run(["/bin/bash", "-c", command], text=True, capture_output=True)
            self.assertNotEqual(two.returncode, 0)

    def test_recovery_cases_have_distinct_causal_proofs(self):
        content = RECOVERY.read_text()
        self.assertIn("tpm-replacement)", content)
        self.assertIn("recovery-reenrollment)", content)
        self.assertIn("assert_stale_token_rejected", content)
        self.assertIn("old_token", content)
        self.assertIn("new_token", content)
        self.assertIn("--wipe-slot=tpm2", content)
        self.assertIn("cryptsetup status root", content)
        self.assertIn("exactly one LUKS device", content)
        self.assertIn('old_unavailable_proven=1\nfi', content)
        self.assertNotIn('"$CASE" != recovery-reenrollment', content)
        self.assertIn('recovery passphrase|please enter passphrase', content.lower())
        self.assertNotIn("recovery|passphrase|cryptsetup'", content)

    def test_runner_transport_and_marker_guards_are_noninteractive_and_loopback_only(self):
        library = (REPO / "test/lib/bootc-secure-runner-lib.sh").read_text()
        recovery = RECOVERY.read_text()
        self.assertIn("scp_live", library)
        # Non-interactive is now proven by key auth plus BatchMode=yes rather
        # than by supplying a password. The published secure media has sshd
        # installed but disabled and liveuser has an empty password hash, so
        # the previous `sshpass -p live liveuser@...` transport could never
        # connect; the harness injects a one-shot enabling unit as a systemd
        # credential over SMBIOS instead. Assert sshpass is GONE — reintroducing
        # it would mean either a password on user-facing media or a transport
        # that cannot work.
        self.assertNotIn("sshpass", library)
        self.assertIn('-i "$SNOSI_TASK9_LIVE_KEY"', library)
        self.assertIn("BatchMode=yes", library)
        self.assertIn("io.systemd.credential.binary:systemd.extra-unit", library)
        self.assertIn("hostfwd=tcp:127.0.0.1:", library)
        self.assertIn("stop_vm", library)
        self.assertIn("old_unavailable_proven=1", recovery)
        self.assertNotIn('printf \'BOOTC_SECURE_RECOVERY', recovery.split('old_unavailable_proven=1', 1)[0])
        self.assertIn("assert_stale_token_rejected", recovery)
        self.assertIn("/run/snosi-task9/recovery.key", INSTALLER.read_text())

    def test_state_manifest_keys_read_by_runners_are_validated(self):
        # This assertion used to be `assertIn("ssh_private_key", recovery)` --
        # a bare substring check that pinned a literal name nothing produced.
        # snosi writes ssh_key, so the test was holding the runner to a key
        # that never appears in a real manifest, and passed while the runner
        # read an empty value and bailed. Couple the reader to the validator
        # instead: every key a runner pulls out of the state manifest must be
        # one validate_state actually requires, so a rename on either side
        # fails here rather than at 03:00 in a VM.
        # Comments are stripped before the wrong-name check: both the library
        # and this test explain the ssh_key/ssh_private_key confusion in prose,
        # and that prose must not trip the guard against the name in code.
        def code(text: str) -> str:
            return "\n".join(re.sub(r"(^|\s)#.*", "", line) for line in text.splitlines())

        library = (REPO / "test/lib/bootc-secure-runner-lib.sh").read_text()
        validated = set(re.findall(r"\.([a-z_]+)\|strings", library))
        self.assertIn("ssh_key", validated)
        self.assertNotIn("ssh_private_key", code(library))

        for runner in (INSTALLER, NEGATIVE, RECOVERY, PUBLISH, UPDATE_NEGATIVE):
            source = runner.read_text()
            self.assertNotIn("ssh_private_key", code(source))
            for key in re.findall(r'json_string "\$\{?\d+\}?" ([a-z_]+)', source):
                self.assertIn(
                    key,
                    validated,
                    f"{runner.name} reads state key '{key}', which validate_state does not require",
                )

    def test_stop_vm_succeeds_on_both_graceful_paths(self):
        # stop_vm used bare `return` on both early exits, so it yielded the
        # status of the failing half of the preceding `||` -- reporting failure
        # exactly when it succeeded. Harmless in the two EXIT-trap callers,
        # fatal in the recovery runner, which calls it as a function's last
        # command under `set -e`. Assert the status directly on both paths:
        # a caller's `set -e` cares about nothing else.
        library = REPO / "test/lib/bootc-secure-runner-lib.sh"
        for name, setup in (
            ("no pid file", ""),
            ("process already gone", 'printf %s "$$" > "$W/qemu.pid"'),
        ):
            with self.subTest(path=name):
                # A pid that cannot exist beats reusing a live one: $$ is this
                # shell, so the second case writes a pid that IS running, then
                # the loop's `kill -0` must fail. Use an unused-pid stand-in.
                script = f"""
                set -Eeuo pipefail
                source {library}
                W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
                {setup.replace('$$', '2147483646')}
                stop_vm "$W"
                echo "stop_vm returned $?"
                """
                run = subprocess.run(
                    ["/bin/bash", "-c", script], capture_output=True, text=True
                )
                self.assertEqual(
                    run.returncode, 0,
                    f"stop_vm failed on the '{name}' path: {run.stderr}",
                )
                self.assertIn("stop_vm returned 0", run.stdout)

    def test_secure_repository_parser_rejects_near_miss_hosts(self):
        library = REPO / "test/lib/bootc-secure-runner-lib.sh"
        good = "source %s; same_secure_repo ghcr.io/frostyard/cayo@sha256:%s ghcr.io/frostyard/cayo:test cayo" % (library, "a" * 64)
        self.assertEqual(subprocess.run(["/bin/bash", "-c", good]).returncode, 0)
        bad = good.replace("ghcr.io/frostyard/cayo@", "evilghcr.io/frostyard/cayo@")
        self.assertNotEqual(subprocess.run(["/bin/bash", "-c", bad], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
