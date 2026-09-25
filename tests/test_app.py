"""Tests for the CLI parser, device diagnosis and tray wiring (no gi/display needed)."""

import contextlib
import io
import unittest
from unittest import mock

from trayock import __version__
from trayock.app import build_parser, main


class ParserTests(unittest.TestCase):
    def parse(self, argv):
        return build_parser().parse_args(argv)

    def test_defaults(self):
        args = self.parse([])
        self.assertEqual(args.escape_seconds, 2.0)
        self.assertFalse(args.keyboard_only)
        self.assertFalse(args.list_devices)
        self.assertFalse(args.verbose)
        self.assertFalse(args.install_desktop)
        self.assertFalse(args.remove_desktop)
        self.assertFalse(args.install_autostart)
        self.assertFalse(args.remove_autostart)
        self.assertIsNone(args.mode)

    def test_mode_choices(self):
        for mode in ("both", "keyboard", "mouse"):
            self.assertEqual(self.parse(["--mode", mode]).mode, mode)
        with self.assertRaises(SystemExit):
            self.parse(["--mode", "banana"])

    def test_escape_seconds(self):
        self.assertEqual(self.parse(["--escape-seconds", "5"]).escape_seconds, 5.0)

    def test_keyboard_only_flag(self):
        self.assertTrue(self.parse(["--keyboard-only"]).keyboard_only)

    def test_version(self):
        with self.assertRaises(SystemExit) as ctx:
            self.parse(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(__version__, "0.1.0")


class TrayModuleTests(unittest.TestCase):
    def test_icon_assets_exist(self):
        from trayock import tray

        self.assertTrue((tray.ICONS_DIR / "trayock-locked.svg").is_file())
        self.assertTrue((tray.ICONS_DIR / "trayock-unlocked.svg").is_file())

    def test_create_tray_reports_missing_backends(self):
        """Without any AppIndicator library this must raise TrayUnavailable,
        not crash with an arbitrary error."""
        from trayock import tray

        try:
            tray.AppIndicatorTray(lambda: None, lambda: None)
        except Exception:
            try:
                tray.create_tray(lambda: None, lambda: None)
            except (tray.TrayUnavailable, SystemExit):
                pass
            except Exception as exc:  # pragma: no cover
                self.fail("unexpected error type: %r" % exc)

    def test_padlock_image_fallback(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow not installed")
        from trayock.tray import _padlock_image

        for locked in (True, False):
            image = _padlock_image(locked)
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.mode, "RGBA")


class ListDevicesTests(unittest.TestCase):
    def run_cli(self, rows):
        from trayock.blocker import DeviceRow

        def fake_scan():
            return [
                DeviceRow(path, name, kind, readable)
                for path, name, kind, readable in rows
            ]

        stdout = io.StringIO()
        with mock.patch("trayock.blocker.scan_devices", side_effect=fake_scan):
            with contextlib.redirect_stdout(stdout):
                status = main(["--list-devices"])
        return status, stdout.getvalue()

    def test_reports_devices_we_cannot_read(self):
        status, out = self.run_cli(
            [
                ("/dev/input/event0", "AT Translated Set 2 keyboard", "keyboard", True),
                ("/dev/input/event5", "?", None, False),
            ]
        )
        self.assertEqual(status, 1)
        self.assertIn("ok", out)
        self.assertIn("PERMISSION DENIED", out)
        self.assertIn("usermod -aG input", out)

    def test_all_good_devices(self):
        status, out = self.run_cli(
            [
                ("/dev/input/event0", "AT Translated Set 2 keyboard", "keyboard", True),
                ("/dev/input/event1", "Optical Mouse", "pointer", True),
            ]
        )
        self.assertEqual(status, 0)
        self.assertIn("2 device(s) accessible", out)
        self.assertIn("1 keyboard(s), 1 pointer(s)", out)

    def test_no_devices_is_an_error(self):
        status, out = self.run_cli([])
        self.assertEqual(status, 1)
        self.assertIn("No /dev/input/event* devices found", out)

    def test_readable_but_keyboardless_system(self):
        status, out = self.run_cli(
            [("/dev/input/event1", "Optical Mouse", "pointer", True)]
        )
        self.assertEqual(status, 1)
        self.assertIn("No keyboard detected", out)


if __name__ == "__main__":
    unittest.main()
