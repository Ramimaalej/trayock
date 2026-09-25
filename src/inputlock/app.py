"""Command-line entry point: ``inputlock``."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional

from inputlock import __version__

log = logging.getLogger("inputlock")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inputlock",
        description=(
            "Toggle keyboard/mouse input from a GNOME top-bar tray icon. "
            "While locked, all input is grabbed at the evdev level; "
            "force-unlock by holding Ctrl+Alt+Shift+L."
        ),
    )
    parser.add_argument(
        "--version", action="version", version="inputlock %s" % __version__
    )
    parser.add_argument(
        "--escape-seconds",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="hold Ctrl+Alt+Shift+L this long to force-unlock (default: 2.0)",
    )
    parser.add_argument(
        "--mode",
        choices=("both", "keyboard", "mouse"),
        default=None,
        metavar="{both,keyboard,mouse}",
        help=(
            "what a click on the icon locks (default: both); "
            "'keyboard' leaves the mouse usable, 'mouse' leaves the keyboard usable"
        ),
    )
    parser.add_argument(
        "--keyboard-only",
        action="store_true",
        help="same as --mode keyboard (grab only keyboards, tray stays clickable)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="list detected keyboard/pointer devices and exit",
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="install the launcher into ~/.local/share/applications and exit",
    )
    parser.add_argument(
        "--remove-desktop",
        action="store_true",
        help="remove the installed launcher and exit",
    )
    parser.add_argument(
        "--install-autostart",
        action="store_true",
        help="start inputlock automatically at login, then exit",
    )
    parser.add_argument(
        "--remove-autostart",
        action="store_true",
        help="stop starting inputlock at login, then exit",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return parser


def _list_devices() -> int:
    from inputlock.blocker import scan_devices

    rows = scan_devices()
    if not rows:
        print("No /dev/input/event* devices found.")
        return 1
    width = max(len(row.path) for row in rows)
    print("%-*s  %-9s %-18s %s" % (width, "DEVICE", "KIND", "ACCESS", "NAME"))
    for row in rows:
        kind = row.kind or "-"
        access = "ok" if row.readable else "PERMISSION DENIED"
        print("%-*s  %-9s %-18s %s" % (width, row.path, kind, access, row.name))

    denied = [row for row in rows if not row.readable]
    keyboards = sum(1 for row in rows if row.kind == "keyboard")
    pointers = sum(1 for row in rows if row.kind == "pointer")
    print()
    if denied:
        print(
            "%d of %d device(s) are not readable by your user."
            % (len(denied), len(rows))
        )
        print("Add your user to the 'input' group, then log out and back in:")
        print("  sudo usermod -aG input $USER")
        return 1
    print(
        "All %d device(s) accessible: %d keyboard(s), %d pointer(s)."
        % (len(rows), keyboards, pointers)
    )
    if keyboards == 0:
        print("No keyboard detected: inputlock cannot lock without one.")
        return 1
    return 0


_DESKTOP_ACTIONS = (
    "install_desktop",
    "remove_desktop",
    "install_autostart",
    "remove_autostart",
)


def _desktop_main(args: argparse.Namespace) -> int:
    from inputlock import desktop

    status = 0
    if args.install_desktop:
        try:
            print("launcher installed: %s" % desktop.install_desktop())
        except desktop.DesktopError as exc:
            print("inputlock: %s" % exc, file=sys.stderr)
            status = 1
    if args.remove_desktop:
        removed = desktop.remove_desktop()
        print("launcher removed" if removed else "launcher was not installed")
    if args.install_autostart:
        try:
            print("autostart enabled: %s" % desktop.install_autostart())
        except desktop.DesktopError as exc:
            print("inputlock: %s" % exc, file=sys.stderr)
            status = 1
    if args.remove_autostart:
        removed = desktop.remove_autostart()
        print("autostart removed" if removed else "autostart was not installed")
    return status


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.list_devices:
        return _list_devices()
    if any(getattr(args, name) for name in _DESKTOP_ACTIONS):
        return _desktop_main(args)
    if args.escape_seconds <= 0:
        parser.error("--escape-seconds must be > 0")

    from inputlock.blocker import (
        MODE_BOTH,
        MODE_KEYBOARD,
        MODE_POINTER,
        InputBlocker,
        LockError,
    )
    from inputlock.tray import TrayUnavailable, create_tray

    hint = "Esc: hold Ctrl+Alt+Shift+L %g s" % args.escape_seconds
    if args.keyboard_only:
        mode_name = MODE_KEYBOARD
    else:
        mode_name = {
            "both": MODE_BOTH,
            "keyboard": MODE_KEYBOARD,
            "mouse": MODE_POINTER,
        }.get(args.mode or "both", MODE_BOTH)

    blocker_holder = {}
    tray_holder = {}

    def on_toggle() -> None:
        blocker = blocker_holder["b"]
        tray = tray_holder["t"]
        try:
            blocker.toggle()
        except LockError as exc:
            tray.show_error(str(exc))

    def on_set_mode(mode: str) -> None:
        blocker = blocker_holder["b"]
        tray = tray_holder["t"]
        try:
            blocker.lock(mode)
        except LockError as exc:
            tray.show_error(str(exc))

    def on_quit() -> None:
        tray_holder["t"].quit()

    try:
        tray = create_tray(
            on_toggle=on_toggle, on_quit=on_quit, hint=hint, on_set_mode=on_set_mode
        )
    except TrayUnavailable as exc:
        log.error("%s", exc)
        print(
            "inputlock: no tray available.\n"
            "  Install your distro's AppIndicator library, then restart the app:\n"
            "    Fedora/RHEL/openSUSE:  sudo dnf install libayatana-appindicator-gtk3\n"
            "    Ubuntu/Debian:         sudo apt install gir1.2-ayatanaappindicator3-0.1\n"
            "    Arch:                   sudo pacman -S libayatana-appindicator\n"
            "  GNOME also needs the shell extension (once per system):\n"
            "    sudo dnf install gnome-shell-extension-appindicator\n"
            "    gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com\n"
            "  KDE/XFCE/MATE/LXQt have a tray built in - just install the library.\n"
            "  Or install the pystray fallback: pip install 'inputlock[pystray]'",
            file=sys.stderr,
        )
        return 1
    tray_holder["t"] = tray

    blocker = InputBlocker(
        on_state_change=tray.set_locked,
        on_escape=lambda: log.warning(
            "unlocked via escape combo (Ctrl+Alt+Shift+L held %g s)",
            args.escape_seconds,
        ),
        hold_seconds=args.escape_seconds,
        mode=mode_name,
    )
    blocker_holder["b"] = blocker

    tray.set_locked(False, mode_name)
    if mode_name != MODE_BOTH:
        log.info("mode: %s", mode_name)

    try:
        tray.run()
    except KeyboardInterrupt:
        pass
    finally:
        blocker.unlock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
