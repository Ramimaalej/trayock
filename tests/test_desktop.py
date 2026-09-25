"""Tests for the .desktop launcher / autostart installers (isolated XDG dirs)."""

import contextlib
import io
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from trayock import desktop
from trayock.app import main


class XdgTestCase(unittest.TestCase):
    """Points XDG_DATA_HOME/XDG_CONFIG_HOME at a throwaway directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": os.path.join(self.tmp, "data"),
                "XDG_CONFIG_HOME": os.path.join(self.tmp, "config"),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def launcher(self) -> Path:
        return desktop.applications_dir() / "trayock.desktop"

    @property
    def autostart(self) -> Path:
        return desktop.autostart_dir() / "trayock.desktop"


class InstallTests(XdgTestCase):
    def test_install_desktop_writes_launcher_and_icon(self):
        path = desktop.install_desktop("/opt/bin/trayock")

        self.assertEqual(path, self.launcher)
        content = self.launcher.read_text()
        self.assertIn("Exec=/opt/bin/trayock", content)
        self.assertIn("Icon=trayock", content)
        self.assertIn("Type=Application", content)
        self.assertIn("NoDisplay=false", content)
        self.assertTrue(desktop.installed_icon().is_file())
        self.assertIn("<svg", desktop.installed_icon().read_text())

    def test_remove_desktop_deletes_launcher_and_icon(self):
        desktop.install_desktop()
        self.assertTrue(desktop.remove_desktop())
        self.assertFalse(self.launcher.exists())
        self.assertFalse(desktop.installed_icon().exists())
        self.assertFalse(desktop.remove_desktop())

    def test_autostart_is_hidden_from_app_grids(self):
        path = desktop.install_autostart("/opt/bin/trayock")

        self.assertEqual(path, self.autostart)
        content = self.autostart.read_text()
        self.assertIn("Exec=/opt/bin/trayock", content)
        self.assertIn("NoDisplay=true", content)
        self.assertIn("X-GNOME-Autostart-enabled=true", content)

    def test_remove_autostart(self):
        desktop.install_autostart()
        self.assertTrue(desktop.remove_autostart())
        self.assertFalse(self.autostart.exists())
        self.assertFalse(desktop.remove_autostart())

    def test_render_desktop_requires_the_template(self):
        with mock.patch.object(desktop, "DESKTOP_TEMPLATE", Path("/nope/nope.desktop")):
            with self.assertRaises(desktop.DesktopError):
                desktop.render_desktop("trayock")


class ResolveCommandTests(unittest.TestCase):
    def test_prefers_the_installed_script(self):
        self.assertEqual(
            desktop.resolve_command(which=lambda _name: "/usr/bin/trayock"),
            "/usr/bin/trayock",
        )

    def test_falls_back_to_argv0_when_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "trayock")
            script.write_text("#!/bin/sh\n")
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            found = desktop.resolve_command(
                str(script), which=lambda _name: None, python="/usr/bin/python3"
            )
        self.assertEqual(found, str(script.resolve()))

    def test_falls_back_to_python_m_trayock(self):
        found = desktop.resolve_command(
            "/definitely/not/a/real/binary",
            which=lambda _name: None,
            python="/usr/bin/python3",
        )
        self.assertEqual(found, "/usr/bin/python3 -m trayock")


class CliTests(XdgTestCase):
    def run_cli(self, *argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = main(list(argv))
        return status, stdout.getvalue()

    def test_install_and_remove_desktop_flags(self):
        status, out = self.run_cli("--install-desktop")
        self.assertEqual(status, 0)
        self.assertIn(str(self.launcher), out)
        self.assertTrue(self.launcher.is_file())

        status, out = self.run_cli("--remove-desktop")
        self.assertEqual(status, 0)
        self.assertFalse(self.launcher.exists())

    def test_install_and_remove_autostart_flags(self):
        status, out = self.run_cli("--install-autostart")
        self.assertEqual(status, 0)
        self.assertTrue(self.autostart.is_file())
        self.assertIn("NoDisplay=true", self.autostart.read_text())

        status, _ = self.run_cli("--remove-autostart")
        self.assertEqual(status, 0)
        self.assertFalse(self.autostart.exists())

    def test_desktop_flags_do_not_start_the_tray(self):
        # no gi/AppIndicator in this environment: reaching the tray would fail
        status, _ = self.run_cli("--remove-desktop", "--remove-autostart")
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
