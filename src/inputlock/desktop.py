"""Install/remove the Freedesktop .desktop launcher and login autostart entry.

Everything here is plain file writing under the user's XDG directories, so it
never needs root and is easy to unit-test with a temporary HOME.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

APP_ID = "inputlock"
DESKTOP_FILENAME = "inputlock.desktop"

_PACKAGE_DIR = Path(__file__).resolve().parent
DESKTOP_TEMPLATE = _PACKAGE_DIR / "data" / DESKTOP_FILENAME
ICON_SOURCE = _PACKAGE_DIR / "icons" / "inputlock-unlocked.svg"


class DesktopError(RuntimeError):
    """A launcher/autostart file could not be installed or removed."""


def _xdg_dir(env_var: str, default: str) -> Path:
    value = os.environ.get(env_var) or default
    return Path(value).expanduser()


def applications_dir() -> Path:
    return _xdg_dir("XDG_DATA_HOME", "~/.local/share") / "applications"


def icons_dir() -> Path:
    return _xdg_dir("XDG_DATA_HOME", "~/.local/share") / "icons" / "hicolor" / "scalable" / "apps"


def autostart_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", "~/.config") / "autostart"


def resolve_command(
    argv0: Optional[str] = None,
    which=shutil.which,
    python: Optional[str] = None,
) -> str:
    """Best ``Exec=`` value: the installed script, else ``python -m inputlock``."""
    found = which(APP_ID)
    if found:
        return found
    candidate = Path(argv0 or sys.argv[0]).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    return "%s -m %s" % (python or sys.executable, APP_ID)


def render_desktop(exec_line: str, icon: str = APP_ID, no_display: bool = False) -> str:
    """Fill ``Exec=``/``Icon=``/``NoDisplay=`` in the packaged template."""
    if not DESKTOP_TEMPLATE.is_file():
        raise DesktopError("desktop template missing: %s" % DESKTOP_TEMPLATE)
    lines = []
    seen_no_display = False
    for line in DESKTOP_TEMPLATE.read_text().splitlines():
        if line.startswith("Exec="):
            line = "Exec=" + exec_line
        elif line.startswith("Icon="):
            line = "Icon=" + icon
        elif line.startswith("NoDisplay="):
            seen_no_display = True
            line = "NoDisplay=" + ("true" if no_display else "false")
        lines.append(line)
    if no_display and not seen_no_display:
        lines.append("NoDisplay=true")
    return "\n".join(lines) + "\n"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _remove(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def installed_icon() -> Path:
    return icons_dir() / (APP_ID + ".svg")


def install_desktop(exec_line: Optional[str] = None) -> Path:
    """Install the launcher into ``~/.local/share/applications`` + its icon."""
    command = exec_line or resolve_command()
    launcher = _write(applications_dir() / DESKTOP_FILENAME, render_desktop(command))
    if ICON_SOURCE.is_file():
        _write(installed_icon(), ICON_SOURCE.read_text())
    _update_icon_cache()
    return launcher


def remove_desktop() -> bool:
    """Remove the launcher and the icon we installed (best effort)."""
    removed = _remove(applications_dir() / DESKTOP_FILENAME)
    removed = _remove(installed_icon()) or removed
    if removed:
        _update_icon_cache()
    return removed


def install_autostart(exec_line: Optional[str] = None) -> Path:
    """Write ``~/.config/autostart/inputlock.desktop`` so inputlock starts at login."""
    command = exec_line or resolve_command()
    content = render_desktop(command, no_display=True)
    return _write(autostart_dir() / DESKTOP_FILENAME, content)


def remove_autostart() -> bool:
    return _remove(autostart_dir() / DESKTOP_FILENAME)


def _update_icon_cache() -> None:
    tool = shutil.which("gtk-update-icon-cache")
    if not tool:
        return
    cache_dir = icons_dir().parent.parent  # .../icons/hicolor
    import subprocess

    try:
        subprocess.run(
            [tool, "-f", "-q", str(cache_dir)],
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        pass
