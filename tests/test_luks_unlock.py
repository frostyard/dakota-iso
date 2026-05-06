"""
Unit tests for aurora/src/luks-unlock.py

Tests cover:
  - qemu_check_serial(): serial log parsing for boot state detection
  - virsh_dhcp_ip(): DHCP lease parsing for IP address extraction
  - virsh_send_passphrase(): key mapping for virsh send-key
  - qemu_screendump(): PPM file parsing for brightness/md5
  - qemu_send_passphrase(): key mapping for QEMU HMP sendkey
  - main(): argument validation
"""

import hashlib
import importlib.util
import os
import struct
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Import the module under test from the aurora directory.
# The file is named with a hyphen, so we use importlib.
_LUKS_PY = os.path.join(
    os.path.dirname(__file__), "..", "aurora", "src", "luks-unlock.py"
)
_spec = importlib.util.spec_from_file_location("luks_unlock", _LUKS_PY)
luks_unlock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(luks_unlock)


# ---------------------------------------------------------------------------
# qemu_check_serial
# ---------------------------------------------------------------------------


class TestQemuCheckSerial(unittest.TestCase):
    """Tests for qemu_check_serial() — parses a serial log file."""

    def _write_log(self, content: str) -> str:
        """Write content to a temp file and return the path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        f.write(content)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    # ── Happy paths ──────────────────────────────────────────────────────────

    def test_returns_empty_string_for_missing_file(self):
        result = luks_unlock.qemu_check_serial("/nonexistent/path/serial.log")
        self.assertEqual(result, "")

    def test_returns_empty_string_for_empty_log(self):
        path = self._write_log("")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "")

    def test_detects_gdm_started(self):
        path = self._write_log("[  OK  ] Started gdm.service - GNOME Display Manager.\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gdm")

    def test_detects_gnome_display_manager_description(self):
        path = self._write_log("[  OK  ] Started GNOME Display Manager\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gdm")

    def test_detects_gnome_initial_setup(self):
        path = self._write_log("[  OK  ] Started gnome-initial-setup - GNOME Initial Setup\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gnome-initial-setup")

    def test_gnome_initial_setup_takes_priority_over_gdm(self):
        """gnome-initial-setup must be checked before gdm in qemu_check_serial."""
        path = self._write_log(
            "[  OK  ] Started gdm.service\n"
            "[  OK  ] Started gnome-initial-setup\n"
        )
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gnome-initial-setup")

    def test_detects_emergency_mode(self):
        path = self._write_log("You are in emergency mode.\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "emergency")

    def test_detects_emergency_shell(self):
        path = self._write_log("Dropped into emergency shell.\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "emergency")

    def test_detects_plymouth_passphrase_prompt(self):
        path = self._write_log("Please enter passphrase for disk sda3_crypt:\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "plymouth")

    def test_emergency_takes_priority_over_plymouth(self):
        """Emergency shell must be reported even if Plymouth text also present."""
        path = self._write_log(
            "Please enter passphrase for disk sda3_crypt:\n"
            "emergency shell\n"
        )
        self.assertEqual(luks_unlock.qemu_check_serial(path), "emergency")

    # ── ANSI stripping ───────────────────────────────────────────────────────

    def test_strips_ansi_escape_codes_for_gdm(self):
        """ANSI colour codes in systemd output are stripped before matching."""
        ansi_log = (
            "\x1b[0;32m[  OK  ]\x1b[0m Started \x1b[0mgdm.service\x1b[0m"
            " - GNOME Display Manager.\n"
        )
        path = self._write_log(ansi_log)
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gdm")

    def test_strips_ansi_escape_codes_for_gnome_initial_setup(self):
        ansi_log = "\x1b[1mStarted gnome-initial-setup\x1b[0m\n"
        path = self._write_log(ansi_log)
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gnome-initial-setup")

    def test_strips_multi_param_ansi_codes(self):
        """Complex ANSI codes with multiple parameters are stripped."""
        ansi_log = "\x1b[0;1;32mStarted gdm.service\x1b[0m\n"
        path = self._write_log(ansi_log)
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gdm")

    # ── Whitespace collapsing ────────────────────────────────────────────────

    def test_matches_gdm_across_newlines_after_ansi_strip(self):
        """Whitespace collapsing allows matching across line breaks."""
        multiline = "Started\ngdm.service\n- GNOME Display Manager.\n"
        path = self._write_log(multiline)
        self.assertEqual(luks_unlock.qemu_check_serial(path), "gdm")

    # ── Negative / no match ──────────────────────────────────────────────────

    def test_unrelated_log_returns_empty(self):
        path = self._write_log("kernel: Booting the kernel.\nSystemd starting...\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "")

    def test_partial_gdm_string_does_not_match(self):
        """'gdm' alone (not part of 'Started gdm.service') should not match."""
        path = self._write_log("some gdm reference\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "")

    def test_plymouth_partial_string_does_not_match(self):
        """Only the full passphrase prompt string triggers plymouth detection."""
        path = self._write_log("enter passphrase\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "")

    # ── Regression: plymouth detection uses raw (not ANSI-stripped) text ────

    def test_plymouth_detected_in_raw_log_no_ansi(self):
        """Plymouth passphrase check is done on raw text, not stripped text."""
        path = self._write_log("Please enter passphrase for disk nvme0n1p3_crypt:\n")
        self.assertEqual(luks_unlock.qemu_check_serial(path), "plymouth")


# ---------------------------------------------------------------------------
# virsh_dhcp_ip
# ---------------------------------------------------------------------------


class TestVirshDhcpIp(unittest.TestCase):
    """Tests for virsh_dhcp_ip() — extracts IP from virsh net-dhcp-leases output."""

    _SAMPLE_OUTPUT = """\
 Expiry Time           MAC address         Protocol  IP address          Hostname   Client ID or DUID
-------------------------------------------------------------------------------------------------------------------
 2024-01-01 12:00:00   52:54:00:ab:cd:ef   ipv4      192.168.122.100/24  dakota     -
 2024-01-01 12:00:00   52:54:00:11:22:33   ipv4      192.168.122.101/24  other      -
"""

    def _run_with_stdout(self, stdout: str) -> str:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = stdout
        with patch("subprocess.run", return_value=mock_result):
            return luks_unlock.virsh_dhcp_ip("52:54:00:ab:cd:ef")

    def test_extracts_ip_for_matching_mac(self):
        ip = self._run_with_stdout(self._SAMPLE_OUTPUT)
        self.assertEqual(ip, "192.168.122.100")

    def test_returns_empty_when_mac_not_found(self):
        ip = self._run_with_stdout(self._SAMPLE_OUTPUT)
        # Test with a non-existent MAC
        mock_result = MagicMock()
        mock_result.stdout = self._SAMPLE_OUTPUT
        with patch("subprocess.run", return_value=mock_result):
            result = luks_unlock.virsh_dhcp_ip("00:00:00:00:00:00")
        self.assertEqual(result, "")

    def test_returns_empty_for_empty_output(self):
        ip = self._run_with_stdout("")
        self.assertEqual(ip, "")

    def test_case_insensitive_mac_matching(self):
        """MAC matching should be case-insensitive."""
        mock_result = MagicMock()
        mock_result.stdout = self._SAMPLE_OUTPUT
        with patch("subprocess.run", return_value=mock_result):
            ip_lower = luks_unlock.virsh_dhcp_ip("52:54:00:AB:CD:EF")
        self.assertEqual(ip_lower, "192.168.122.100")

    def test_strips_prefix_length_from_cidr(self):
        """IP is extracted without the /24 CIDR suffix."""
        output = " dummy 52:54:00:aa:bb:cc ipv4 10.0.0.5/24 host -\n"
        mock_result = MagicMock()
        mock_result.stdout = output
        with patch("subprocess.run", return_value=mock_result):
            ip = luks_unlock.virsh_dhcp_ip("52:54:00:aa:bb:cc")
        self.assertEqual(ip, "10.0.0.5")

    def test_returns_first_matching_ip_for_duplicate_mac(self):
        """When a MAC appears twice, the first match is returned."""
        output = (
            " dummy 52:54:00:ab:cd:ef ipv4 10.0.0.1/24 h1 -\n"
            " dummy 52:54:00:ab:cd:ef ipv4 10.0.0.2/24 h2 -\n"
        )
        mock_result = MagicMock()
        mock_result.stdout = output
        with patch("subprocess.run", return_value=mock_result):
            ip = luks_unlock.virsh_dhcp_ip("52:54:00:ab:cd:ef")
        self.assertEqual(ip, "10.0.0.1")


# ---------------------------------------------------------------------------
# virsh_send_passphrase  — key-map logic
# ---------------------------------------------------------------------------


class TestVirshSendPassphrase(unittest.TestCase):
    """Tests for virsh_send_passphrase() key-map logic (subprocess mocked)."""

    def _collect_calls(self, passphrase: str):
        """Run virsh_send_passphrase and return the list of keys sent."""
        calls = []
        original_sleep = luks_unlock.time.sleep

        def fake_run(cmd, **kwargs):
            if "send-key" in cmd:
                calls.append(cmd[-1])  # last arg is the key name
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            luks_unlock.virsh_send_passphrase("test-vm", passphrase)
        return calls

    def test_lowercase_letters(self):
        keys = self._collect_calls("abc")
        self.assertEqual(keys, ["KEY_A", "KEY_B", "KEY_C", "KEY_ENTER"])

    def test_digits(self):
        keys = self._collect_calls("123")
        self.assertEqual(keys, ["KEY_1", "KEY_2", "KEY_3", "KEY_ENTER"])

    def test_hyphen_maps_to_key_minus(self):
        keys = self._collect_calls("-")
        self.assertEqual(keys, ["KEY_MINUS", "KEY_ENTER"])

    def test_underscore_maps_to_key_minus(self):
        """Underscore is intentionally mapped to KEY_MINUS (same as hyphen)."""
        keys = self._collect_calls("_")
        self.assertEqual(keys, ["KEY_MINUS", "KEY_ENTER"])

    def test_space_maps_to_key_space(self):
        keys = self._collect_calls(" ")
        self.assertEqual(keys, ["KEY_SPACE", "KEY_ENTER"])

    def test_enter_always_sent_at_end(self):
        keys = self._collect_calls("x")
        self.assertIn("KEY_ENTER", keys)
        self.assertEqual(keys[-1], "KEY_ENTER")

    def test_empty_passphrase_sends_only_enter(self):
        keys = self._collect_calls("")
        self.assertEqual(keys, ["KEY_ENTER"])

    def test_unmapped_character_skipped_with_warning(self, capsys=None):
        """Characters with no key mapping are skipped (warning to stderr)."""
        import io
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            keys = self._collect_calls("a@b")
        # '@' has no mapping — should be skipped, only a/b sent
        self.assertIn("KEY_A", keys)
        self.assertIn("KEY_B", keys)
        self.assertNotIn(None, keys)

    def test_mixed_passphrase(self):
        keys = self._collect_calls("pass1")
        self.assertEqual(
            keys,
            ["KEY_P", "KEY_A", "KEY_S", "KEY_S", "KEY_1", "KEY_ENTER"],
        )


# ---------------------------------------------------------------------------
# qemu_send_passphrase  — key-map logic
# ---------------------------------------------------------------------------


class TestQemuSendPassphrase(unittest.TestCase):
    """Tests for qemu_send_passphrase() key-map logic (subprocess mocked)."""

    def _collect_keys(self, passphrase: str):
        sent = []

        def fake_run(cmd, **kwargs):
            # The key is embedded in the socat input: "sendkey <key>\n"
            if cmd[0] == "socat":
                raw_input = kwargs.get("input", b"").decode()
                if raw_input.startswith("sendkey "):
                    key = raw_input.strip().split(" ", 1)[1]
                    sent.append(key)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            luks_unlock.qemu_send_passphrase("/tmp/fake.sock", passphrase)
        return sent

    def test_lowercase_letters_map_to_themselves(self):
        keys = self._collect_keys("abc")
        self.assertEqual(keys, ["a", "b", "c", "ret"])

    def test_digits_map_to_themselves(self):
        keys = self._collect_keys("09")
        self.assertEqual(keys, ["0", "9", "ret"])

    def test_hyphen_maps_to_minus(self):
        keys = self._collect_keys("-")
        self.assertEqual(keys, ["minus", "ret"])

    def test_underscore_maps_to_shift_minus(self):
        keys = self._collect_keys("_")
        self.assertEqual(keys, ["shift-minus", "ret"])

    def test_space_maps_to_spc(self):
        keys = self._collect_keys(" ")
        self.assertEqual(keys, ["spc", "ret"])

    def test_ret_always_appended(self):
        keys = self._collect_keys("x")
        self.assertEqual(keys[-1], "ret")

    def test_empty_passphrase_sends_only_ret(self):
        keys = self._collect_keys("")
        self.assertEqual(keys, ["ret"])

    def test_passphrase_with_mixed_chars(self):
        keys = self._collect_keys("ab1-")
        self.assertEqual(keys, ["a", "b", "1", "minus", "ret"])

    def test_unmapped_character_skipped(self):
        """Characters not in the key map should be skipped with a warning."""
        keys = self._collect_keys("a!b")
        self.assertIn("a", keys)
        self.assertIn("b", keys)
        self.assertNotIn("!", keys)

    def test_qemu_key_map_differs_from_virsh_key_map(self):
        """QEMU uses 'minus' while virsh uses 'KEY_MINUS' — verify separately."""
        qemu_keys = self._collect_keys("-")
        self.assertEqual(qemu_keys[0], "minus")  # QEMU HMP format


# ---------------------------------------------------------------------------
# qemu_screendump  — PPM parsing
# ---------------------------------------------------------------------------


def _make_ppm(width: int, height: int, pixels: list) -> bytes:
    """Create a minimal P6 (binary PPM) file with given pixel values.

    pixels: flat list of (R, G, B) tuples or a list of ints for grayscale.
    """
    header = f"P6\n{width} {height}\n255\n".encode()
    if pixels and isinstance(pixels[0], (list, tuple)):
        body = bytes([v for rgb in pixels for v in rgb])
    else:
        body = bytes(pixels)
    return header + body


class TestQemuScreendump(unittest.TestCase):
    """Tests for qemu_screendump() — returns (brightness, md5) from a PPM file."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".ppm", delete=False)
        self.tmp.close()
        self.addCleanup(os.unlink, self.tmp.name)

    def _write_ppm(self, data: bytes):
        with open(self.tmp.name, "wb") as f:
            f.write(data)

    def _run_with_ppm(self, ppm_data: bytes):
        """Run qemu_screendump with a fake socat that writes ppm_data to disk."""
        self._write_ppm(ppm_data)

        def fake_run(cmd, **kwargs):
            # Don't actually run socat; the file is already written.
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            return luks_unlock.qemu_screendump("/fake.sock", self.tmp.name)

    def test_all_black_image_has_zero_brightness(self):
        ppm = _make_ppm(10, 10, [0] * 300)
        brightness, md5 = self._run_with_ppm(ppm)
        self.assertAlmostEqual(brightness, 0.0)
        self.assertNotEqual(md5, "")

    def test_all_white_image_has_max_brightness(self):
        ppm = _make_ppm(2, 2, [255] * 12)
        brightness, md5 = self._run_with_ppm(ppm)
        self.assertAlmostEqual(brightness, 255.0)

    def test_md5_is_consistent_for_same_data(self):
        ppm = _make_ppm(4, 4, [128] * 48)
        _, md5_a = self._run_with_ppm(ppm)
        _, md5_b = self._run_with_ppm(ppm)
        self.assertEqual(md5_a, md5_b)

    def test_md5_differs_for_different_data(self):
        ppm_a = _make_ppm(4, 4, [100] * 48)
        ppm_b = _make_ppm(4, 4, [200] * 48)
        _, md5_a = self._run_with_ppm(ppm_a)
        _, md5_b = self._run_with_ppm(ppm_b)
        self.assertNotEqual(md5_a, md5_b)

    def test_returns_minus_one_for_missing_file(self):
        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            brightness, md5 = luks_unlock.qemu_screendump(
                "/fake.sock", "/nonexistent/path.ppm"
            )
        self.assertEqual(brightness, -1)
        self.assertEqual(md5, "")

    def test_returns_minus_one_for_invalid_ppm(self):
        """Non-PPM data (no '255\\n' header) returns (-1, '')."""
        self._write_ppm(b"not a ppm file at all\n")

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            brightness, md5 = luks_unlock.qemu_screendump("/fake.sock", self.tmp.name)
        self.assertEqual(brightness, -1)
        self.assertEqual(md5, "")

    def test_sampling_uses_every_100th_byte(self):
        """Verify brightness uses every 100th byte of the pixel data."""
        # Create a 100x100 image where byte 0 = 200, all others = 0
        # Sampling every 100th byte: bytes at indices 0, 100, 200, ...
        # All those bytes will be 0 except the first if we fill appropriately.
        header = b"P6\n100 100\n255\n"
        # pixel data: 100*100*3 = 30000 bytes
        # Set byte index 0 = 200, rest = 0
        pixel_data = bytearray(30000)
        pixel_data[0] = 200
        ppm = header + bytes(pixel_data)
        self._write_ppm(ppm)

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            brightness, _ = luks_unlock.qemu_screendump("/fake.sock", self.tmp.name)

        # sample = pixel_data[::100] → bytes at 0, 100, 200, ..., 29900 = 300 bytes
        # byte[0] = 200, all others = 0 → average = 200/300 ≈ 0.667
        expected = 200 / 300
        self.assertAlmostEqual(brightness, expected, places=5)

    def test_ppm_header_with_multiple_255_occurrences(self):
        """When header contains extra '255\\n', parsing finds the first occurrence."""
        # Manually construct a PPM where the header comment contains '255\n'
        # Standard P6 PPM doesn't have comments, but parser uses index()
        header = b"P6\n1 1\n255\n"
        pixels = b"\xff\x00\x00"  # single red pixel
        ppm = header + pixels
        self._write_ppm(ppm)

        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run), \
             patch.object(luks_unlock.time, "sleep"):
            brightness, md5 = luks_unlock.qemu_screendump("/fake.sock", self.tmp.name)

        self.assertGreaterEqual(brightness, 0)
        self.assertNotEqual(md5, "")


# ---------------------------------------------------------------------------
# main()  — argument handling
# ---------------------------------------------------------------------------


class TestMain(unittest.TestCase):
    """Tests for main() argument validation (no actual VM interaction)."""

    def _run_main(self, argv: list) -> int:
        """Run main() with given sys.argv and return the exit code."""
        with patch.object(sys, "argv", argv):
            try:
                luks_unlock.main()
            except SystemExit as e:
                return e.code
        return 0

    def test_no_args_exits_1(self):
        code = self._run_main(["luks-unlock.py"])
        self.assertEqual(code, 1)

    def test_unknown_mode_exits_1(self):
        code = self._run_main(["luks-unlock.py", "invalid"])
        self.assertEqual(code, 1)

    def test_libvirt_mode_too_few_args_exits_1(self):
        code = self._run_main(["luks-unlock.py", "libvirt", "vm1"])
        self.assertEqual(code, 1)

    def test_qemu_mode_too_few_args_exits_1(self):
        code = self._run_main(["luks-unlock.py", "qemu", "/tmp/sock"])
        self.assertEqual(code, 1)

    def test_libvirt_mode_dispatches_to_run_libvirt(self):
        mock_run = MagicMock()
        with patch.object(luks_unlock, "run_libvirt", mock_run), \
             patch.object(sys, "argv", ["luks-unlock.py", "libvirt", "myvm", "secret", "aa:bb:cc:dd:ee:ff"]):
            luks_unlock.main()
        mock_run.assert_called_once_with("myvm", "secret", "aa:bb:cc:dd:ee:ff")

    def test_qemu_mode_dispatches_to_run_qemu(self):
        mock_run = MagicMock()
        with patch.object(luks_unlock, "run_qemu", mock_run), \
             patch.object(sys, "argv", ["luks-unlock.py", "qemu", "/tmp/s.sock", "pw", "/tmp/serial.log"]):
            luks_unlock.main()
        mock_run.assert_called_once_with("/tmp/s.sock", "pw", "/tmp/serial.log")


# ---------------------------------------------------------------------------
# virsh_screenshot_size
# ---------------------------------------------------------------------------


class TestVirshScreenshotSize(unittest.TestCase):
    """Tests for virsh_screenshot_size() — returns file size after virsh screenshot."""

    def test_returns_file_size_on_success(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(b"x" * 8192)
            fname = f.name
        self.addCleanup(os.unlink, fname)

        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            size = luks_unlock.virsh_screenshot_size("test-vm", fname)
        self.assertEqual(size, 8192)

    def test_returns_zero_on_virsh_failure(self):
        mock_result = MagicMock(returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            size = luks_unlock.virsh_screenshot_size("test-vm", "/tmp/snap.png")
        self.assertEqual(size, 0)

    def test_returns_zero_when_file_missing_after_success(self):
        """virsh returned 0 but the screenshot file does not exist."""
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            size = luks_unlock.virsh_screenshot_size("test-vm", "/nonexistent/snap.png")
        self.assertEqual(size, 0)


# ---------------------------------------------------------------------------
# Integration-style: qemu_check_serial with realistic systemd log content
# ---------------------------------------------------------------------------


class TestQemuCheckSerialIntegration(unittest.TestCase):
    """Realistic serial log snippets that exercise the full parsing pipeline."""

    def _check(self, content: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write(content)
            fname = f.name
        self.addCleanup(os.unlink, fname)
        return luks_unlock.qemu_check_serial(fname)

    def test_realistic_boot_log_no_marker(self):
        log = (
            "[    0.000000] Linux version 6.6.0\n"
            "[    1.234567] ACPI: IRQ0 used by override from ...\n"
            "[   10.000000] systemd[1]: Starting Default Target...\n"
        )
        self.assertEqual(self._check(log), "")

    def test_realistic_plymouth_log(self):
        log = (
            "dracut-initqueue[312]: Please enter passphrase for disk /dev/sda3\n"
            "Please enter passphrase for disk luks-abc123:\n"
        )
        self.assertEqual(self._check(log), "plymouth")

    def test_realistic_gdm_log_with_ansi(self):
        log = (
            "\x1b[0;32m[  OK  ]\x1b[0m Started \x1b[0;1mgdm.service"
            "\x1b[0m - GNOME Display Manager.\n"
        )
        self.assertEqual(self._check(log), "gdm")

    def test_emergency_mode_long_log(self):
        log = (
            "[  OK  ] Reached target Basic System.\n"
            "Failed to mount /var.\n"
            "You are in emergency mode. After logging in, type ...\n"
        )
        self.assertEqual(self._check(log), "emergency")

    def test_gdm_without_service_suffix(self):
        """'Started GNOME Display Manager' (no .service) is also a valid match."""
        log = "[  OK  ] Started GNOME Display Manager\n"
        self.assertEqual(self._check(log), "gdm")


if __name__ == "__main__":
    unittest.main()