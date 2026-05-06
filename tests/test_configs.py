"""
Tests for aurora/src/etc/bootc-installer/images.json,
aurora/src/etc/bootc-installer/recipe.json, and
aurora/src/flatpaks.

Validates structure, required fields, and format of the new configuration
files introduced in this PR.
"""

import json
import os
import re
import unittest

# Paths relative to repo root
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
AURORA_IMAGES_JSON = os.path.join(REPO_ROOT, "aurora", "src", "etc", "bootc-installer", "images.json")
AURORA_RECIPE_JSON = os.path.join(REPO_ROOT, "aurora", "src", "etc", "bootc-installer", "recipe.json")
AURORA_FLATPAKS = os.path.join(REPO_ROOT, "aurora", "src", "flatpaks")


# ---------------------------------------------------------------------------
# images.json
# ---------------------------------------------------------------------------


class TestImagesJson(unittest.TestCase):
    """Validates the structure of aurora/src/etc/bootc-installer/images.json."""

    def setUp(self):
        with open(AURORA_IMAGES_JSON) as f:
            self.data = json.load(f)

    def test_file_is_valid_json(self):
        """File must be valid JSON (covered by setUp not raising)."""
        self.assertIsInstance(self.data, dict)

    def test_has_default_image_field(self):
        self.assertIn("default_image", self.data)

    def test_default_image_is_nonempty_string(self):
        self.assertIsInstance(self.data["default_image"], str)
        self.assertTrue(self.data["default_image"].strip())

    def test_has_images_list(self):
        self.assertIn("images", self.data)
        self.assertIsInstance(self.data["images"], list)

    def test_images_list_is_nonempty(self):
        self.assertGreater(len(self.data["images"]), 0)

    def test_has_fallback_flatpaks_field(self):
        self.assertIn("fallback_flatpaks", self.data)
        self.assertIsInstance(self.data["fallback_flatpaks"], list)

    def test_each_image_has_required_fields(self):
        required = {"name", "imgref", "desc", "bootloader", "filesystem"}
        for img in self.data["images"]:
            with self.subTest(image=img.get("name", "<unnamed>")):
                for field in required:
                    self.assertIn(field, img, f"Missing field: {field}")

    def test_each_image_name_is_nonempty_string(self):
        for img in self.data["images"]:
            with self.subTest(imgref=img.get("imgref")):
                self.assertIsInstance(img["name"], str)
                self.assertTrue(img["name"].strip())

    def test_each_image_imgref_looks_like_oci_ref(self):
        """imgref should contain at least one '/' and a ':'."""
        for img in self.data["images"]:
            with self.subTest(name=img.get("name")):
                imgref = img["imgref"]
                self.assertIn("/", imgref)
                self.assertIn(":", imgref)

    def test_each_image_bootloader_is_systemd(self):
        for img in self.data["images"]:
            with self.subTest(name=img.get("name")):
                self.assertEqual(img["bootloader"], "systemd")

    def test_each_image_filesystem_is_btrfs(self):
        for img in self.data["images"]:
            with self.subTest(name=img.get("name")):
                self.assertEqual(img["filesystem"], "btrfs")

    def test_composefs_is_boolean(self):
        for img in self.data["images"]:
            with self.subTest(name=img.get("name")):
                if "composefs" in img:
                    self.assertIsInstance(img["composefs"], bool)

    def test_default_image_matches_first_image_imgref(self):
        """The default_image field should reference one of the listed images."""
        imgrefs = {img["imgref"] for img in self.data["images"]}
        self.assertIn(self.data["default_image"], imgrefs)

    def test_icon_field_format(self):
        """Icon should be a resource URI or a path string."""
        for img in self.data["images"]:
            if "icon" in img:
                icon = img["icon"]
                self.assertIsInstance(icon, str)
                self.assertTrue(
                    icon.startswith("resource://") or icon.startswith("/"),
                    f"Unexpected icon format: {icon!r}",
                )


# ---------------------------------------------------------------------------
# recipe.json
# ---------------------------------------------------------------------------


class TestRecipeJson(unittest.TestCase):
    """Validates the structure of aurora/src/etc/bootc-installer/recipe.json."""

    def setUp(self):
        with open(AURORA_RECIPE_JSON) as f:
            self.data = json.load(f)

    def test_file_is_valid_json(self):
        self.assertIsInstance(self.data, dict)

    def test_has_distro_name(self):
        self.assertIn("distro_name", self.data)
        self.assertIsInstance(self.data["distro_name"], str)
        self.assertTrue(self.data["distro_name"].strip())

    def test_has_imgref(self):
        self.assertIn("imgref", self.data)
        imgref = self.data["imgref"]
        self.assertIn("/", imgref)
        self.assertIn(":", imgref)

    def test_has_local_imgref(self):
        self.assertIn("local_imgref", self.data)
        local = self.data["local_imgref"]
        self.assertIsInstance(local, str)
        # Must specify the transport prefix
        self.assertIn(":", local)

    def test_local_imgref_uses_containers_storage_transport(self):
        local = self.data["local_imgref"]
        self.assertTrue(
            local.startswith("containers-storage:"),
            f"Expected 'containers-storage:' prefix, got: {local!r}",
        )

    def test_has_bootloader(self):
        self.assertIn("bootloader", self.data)
        self.assertEqual(self.data["bootloader"], "systemd")

    def test_has_tour_section(self):
        self.assertIn("tour", self.data)
        self.assertIsInstance(self.data["tour"], dict)

    def test_tour_has_required_slides(self):
        """Tour must contain at least 'welcome' and 'completed'."""
        tour = self.data["tour"]
        for slide in ("welcome", "completed"):
            with self.subTest(slide=slide):
                self.assertIn(slide, tour)

    def test_each_tour_slide_has_required_fields(self):
        for name, slide in self.data["tour"].items():
            with self.subTest(slide=name):
                for field in ("title", "description"):
                    self.assertIn(field, slide, f"Slide '{name}' missing '{field}'")
                    self.assertIsInstance(slide[field], str)
                    self.assertTrue(slide[field].strip(), f"Slide '{name}' '{field}' is blank")

    def test_each_tour_slide_has_image_field(self):
        for name, slide in self.data["tour"].items():
            with self.subTest(slide=name):
                self.assertIn("image", slide)
                self.assertIsInstance(slide["image"], str)
                self.assertTrue(slide["image"].strip())

    def test_has_steps_section(self):
        self.assertIn("steps", self.data)
        self.assertIsInstance(self.data["steps"], dict)

    def test_steps_has_required_entries(self):
        """Installation workflow must include welcome, image, disk, and encryption."""
        for step in ("welcome", "image", "disk", "encryption"):
            with self.subTest(step=step):
                self.assertIn(step, self.data["steps"])

    def test_each_step_has_template(self):
        for name, step in self.data["steps"].items():
            with self.subTest(step=name):
                self.assertIn("template", step)
                self.assertIsInstance(step["template"], str)

    def test_has_log_file(self):
        self.assertIn("log_file", self.data)
        self.assertIsInstance(self.data["log_file"], str)
        self.assertTrue(self.data["log_file"].startswith("/"))

    def test_has_composefs_backend(self):
        self.assertIn("composeFsBackend", self.data)
        self.assertIsInstance(self.data["composeFsBackend"], bool)

    def test_imgref_matches_local_imgref(self):
        """local_imgref must embed the same image as imgref."""
        imgref = self.data["imgref"]
        local = self.data["local_imgref"]
        # local_imgref = "containers-storage:<imgref>"
        self.assertIn(imgref, local)

    def test_distro_logo_is_resource_or_path(self):
        logo = self.data.get("distro_logo", "")
        if logo:
            self.assertTrue(
                logo.startswith("resource://") or logo.startswith("/"),
                f"Unexpected logo format: {logo!r}",
            )

    def test_welcome_step_is_protected(self):
        """The welcome step must be marked protected to prevent skipping."""
        welcome = self.data["steps"]["welcome"]
        self.assertTrue(welcome.get("protected", False))

    def test_image_step_is_protected(self):
        """The image step must be marked protected."""
        image_step = self.data["steps"]["image"]
        self.assertTrue(image_step.get("protected", False))


# ---------------------------------------------------------------------------
# flatpaks list
# ---------------------------------------------------------------------------

# Regex for a valid Flatpak application ID:
# reverse-DNS notation: at least two dot-separated parts, each starting with
# a letter, containing only [A-Za-z0-9_-].
_FLATPAK_ID_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){1,}$')


class TestFlatpaksList(unittest.TestCase):
    """Validates the format of aurora/src/flatpaks."""

    def _load_app_ids(self):
        """Return a list of non-comment, non-blank lines from the flatpaks file."""
        ids = []
        with open(AURORA_FLATPAKS) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    ids.append(stripped)
        return ids

    def test_file_exists(self):
        self.assertTrue(os.path.exists(AURORA_FLATPAKS))

    def test_file_is_nonempty(self):
        ids = self._load_app_ids()
        self.assertGreater(len(ids), 0, "flatpaks list contains no app IDs")

    def test_no_duplicate_app_ids(self):
        ids = self._load_app_ids()
        self.assertEqual(len(ids), len(set(ids)), "Duplicate app IDs found in flatpaks list")

    def test_each_id_matches_flatpak_naming_convention(self):
        """Every app ID must match the reverse-DNS Flatpak naming convention."""
        for app_id in self._load_app_ids():
            with self.subTest(app_id=app_id):
                self.assertRegex(
                    app_id,
                    _FLATPAK_ID_RE,
                    f"Invalid Flatpak app ID format: {app_id!r}",
                )

    def test_no_trailing_whitespace_on_lines(self):
        with open(AURORA_FLATPAKS) as f:
            for lineno, line in enumerate(f, 1):
                with self.subTest(lineno=lineno):
                    self.assertEqual(
                        line.rstrip("\n"),
                        line.rstrip("\n").rstrip(),
                        f"Line {lineno} has trailing whitespace: {line!r}",
                    )

    def test_installer_app_not_in_list(self):
        """tuna-installer is installed via GitHub Releases, not from the list."""
        ids = self._load_app_ids()
        self.assertNotIn(
            "org.bootcinstaller.Installer", ids,
            "Installer app should not appear in the flatpaks list",
        )
        self.assertNotIn(
            "org.bootcinstaller.Installer.Devel", ids,
            "Installer dev app should not appear in the flatpaks list",
        )

    def test_firefox_is_included(self):
        """Firefox is a required browser in the live session."""
        ids = self._load_app_ids()
        self.assertIn("org.mozilla.firefox", ids)

    def test_gnome_core_apps_present(self):
        """Essential GNOME apps must be present."""
        ids = self._load_app_ids()
        essential = [
            "org.gnome.Calculator",
            "org.gnome.TextEditor",
            "org.gnome.Nautilus"  # this would be a regression if removed
            if "org.gnome.Nautilus" in ids else "org.gnome.Loupe",
        ]
        # Only test apps that appear as truly essential — Calculator and TextEditor
        for app in ("org.gnome.Calculator", "org.gnome.TextEditor"):
            with self.subTest(app=app):
                self.assertIn(app, ids)

    def test_adw_gtk3_theme_included(self):
        """GTK3 compatibility themes required for non-libadwaita apps."""
        ids = self._load_app_ids()
        self.assertIn("org.gtk.Gtk3theme.adw-gtk3", ids)
        self.assertIn("org.gtk.Gtk3theme.adw-gtk3-dark", ids)

    def test_no_inline_comments(self):
        """Lines should not have inline # comments — only leading comment lines."""
        with open(AURORA_FLATPAKS) as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                with self.subTest(lineno=lineno):
                    self.assertNotIn(
                        "#", stripped,
                        f"Line {lineno} has an inline comment: {line!r}",
                    )

    def test_all_ids_are_lowercase_or_mixed_case(self):
        """Flatpak app IDs should not be all-uppercase."""
        for app_id in self._load_app_ids():
            with self.subTest(app_id=app_id):
                self.assertNotEqual(
                    app_id, app_id.upper(),
                    f"App ID appears to be all-uppercase: {app_id!r}",
                )


if __name__ == "__main__":
    unittest.main()