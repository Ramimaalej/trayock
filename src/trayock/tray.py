"""Tray icon: AppIndicator3/AyatanaAppIndicator3 on the desktop's top bar,
with a pystray (StatusNotifier/XEmbed) fallback."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from trayock.blocker import MODE_BOTH, MODE_LABELS, MODES

log = logging.getLogger("trayock.tray")

ICONS_DIR = Path(__file__).resolve().parent / "icons"
ICON_UNLOCKED = "trayock-unlocked"
ICON_LOCKED = "trayock-locked"
HINT = "Esc: hold Ctrl+Alt+Shift+L 2 s"


class TrayUnavailable(RuntimeError):
    """No tray implementation could be initialised."""


class BaseTray:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        hint: str = HINT,
        on_set_mode: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._hint = hint
        self._on_set_mode = on_set_mode
        self._locked = False
        self._mode = MODE_BOTH

    def _request_mode(self, mode: str) -> None:
        """A mode was picked in the menu: lock (or switch to) that mode."""
        if self._on_set_mode is None:
            return
        try:
            self._on_set_mode(mode)
        except Exception:
            log.exception("set-mode callback failed")

    def set_locked(self, locked: bool, mode: Optional[str] = None) -> None:
        """Update icon/tooltip/menu; may be called from any thread."""
        raise NotImplementedError

    def show_error(self, message: str) -> None:
        raise NotImplementedError

    def run(self) -> None:
        raise NotImplementedError

    def quit(self) -> None:
        raise NotImplementedError


def _load_appindicator():
    """Return ``(namespace, module)`` for whichever AppIndicator is installed.

    Fedora/Arch/openSUSE ship ``AyatanaAppIndicator3`` (libayatana-appindicator),
    Ubuntu/Debian ship ``AppIndicator3`` (libappindicator3) - the API is the
    same, only the GIR namespace differs.
    """
    import gi

    last_error: Optional[Exception] = None
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            module = getattr(__import__("gi.repository", fromlist=[namespace]), namespace)
            log.debug("using %s for the tray icon", namespace)
            return namespace, module
        except (ValueError, ImportError, AttributeError) as exc:
            last_error = exc
    raise TrayUnavailable(
        "no AppIndicator typelib found (%s); install libayatana-appindicator-gtk3 "
        "(Fedora/Arch/openSUSE) or gir1.2-ayatanaappindicator3-0.1 / "
        "gir1.2-appindicator3-0.1 (Debian/Ubuntu), or use the pystray fallback"
        % last_error
    )


class AppIndicatorTray(BaseTray):
    """Real top-bar icon via AppIndicator3/AyatanaAppIndicator3."""

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        hint: str = HINT,
        on_set_mode: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(on_toggle, on_quit, hint, on_set_mode)
        import gi

        gi.require_version("Gtk", "3.0")
        namespace, indicator = _load_appindicator()
        from gi.repository import GLib, Gtk

        self._indicator_namespace = namespace
        self._indicator = indicator
        self._GLib = GLib
        self._Gtk = Gtk
        self._syncing = False

        if not ICONS_DIR.is_dir():
            raise TrayUnavailable("icon directory missing: %s" % ICONS_DIR)

        menu = Gtk.Menu()
        self._unlock_item = Gtk.MenuItem(label="Unlock input")
        self._unlock_item.connect("activate", self._on_menu_toggle)
        menu.append(self._unlock_item)
        menu.append(Gtk.SeparatorMenuItem())

        self._mode_items = {}
        for mode in MODES:
            item = Gtk.CheckMenuItem(label=MODE_LABELS[mode])
            item.connect("activate", self._on_menu_mode, mode)
            self._mode_items[mode] = item
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        hint_item = Gtk.MenuItem(label=self._hint)
        hint_item.set_sensitive(False)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._on_menu_quit)
        menu.append(hint_item)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(quit_item)
        menu.show_all()
        self._menu = menu

        ind = self._indicator.Indicator.new(
            "trayock",
            ICON_UNLOCKED,
            self._indicator.IndicatorCategory.APPLICATION_STATUS,
        )
        ind.set_icon_theme_path(str(ICONS_DIR))
        ind.set_menu(menu)
        ind.set_title("trayock: input unlocked")
        ind.set_status(self._indicator.IndicatorStatus.ACTIVE)
        ind.connect("activate", self._on_activate)
        self._ind = ind
        self._apply_state(False, MODE_BOTH)

    # -- callbacks (main/GTK thread) ----------------------------------------

    def _on_activate(self, indicator, *args) -> None:
        """Primary activation (left click) on the top-bar icon."""
        self._on_toggle()

    def _on_menu_toggle(self, _item) -> None:
        self._on_toggle()

    def _on_menu_mode(self, item, mode: str) -> None:
        if self._syncing:  # programmatic set_active(), not a user click
            return
        self._request_mode(mode)

    def _on_menu_quit(self, _item) -> None:
        self._on_quit()

    # -- API -----------------------------------------------------------------

    def set_locked(self, locked: bool, mode: Optional[str] = None) -> None:
        self._GLib.idle_add(self._apply_state, locked, mode)

    def _apply_state(self, locked: bool, mode: Optional[str] = None) -> bool:
        self._locked = locked
        if mode is not None:
            self._mode = mode
        if locked:
            self._ind.set_icon(ICON_LOCKED)
            self._ind.set_title("trayock: LOCKED - hold Ctrl+Alt+Shift+L to unlock")
            self._unlock_item.set_label("Unlock input")
        else:
            self._ind.set_icon(ICON_UNLOCKED)
            self._ind.set_title("trayock: input unlocked")
            self._unlock_item.set_label("Input unlocked")
        self._syncing = True
        try:
            self._unlock_item.set_sensitive(locked)
            for mode_name, item in self._mode_items.items():
                item.set_active(mode_name == self._mode)
        finally:
            self._syncing = False
        return False  # run once

    def show_error(self, message: str) -> None:
        log.error("%s", message)
        self._GLib.idle_add(self._show_error_dialog, message)

    def _show_error_dialog(self, message: str) -> bool:
        dialog = self._Gtk.MessageDialog(
            None,
            self._Gtk.DialogFlags.MODAL,
            self._Gtk.MessageType.ERROR,
            self._Gtk.ButtonsType.CLOSE,
            "trayock: cannot lock input",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()
        return False

    def run(self) -> None:
        self._Gtk.main()

    def quit(self) -> None:
        self._Gtk.main_quit()


class PystrayTray(BaseTray):
    """Fallback tray via pystray (AppIndicator/StatusNotifier/XEmbed)."""

    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        hint: str = HINT,
        on_set_mode: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(on_toggle, on_quit, hint, on_set_mode)
        try:
            import pystray
            from PIL import Image  # noqa: F401  (pystray needs Pillow)
        except ImportError as exc:
            raise TrayUnavailable(
                "pystray/Pillow not installed - pip install 'trayock[pystray]'"
            ) from exc
        except Exception as exc:  # e.g. pystray probing a missing backend
            raise TrayUnavailable(
                "pystray is installed but no tray backend works here "
                "(AppIndicator3/AyatanaAppIndicator3 missing): %s" % exc
            ) from exc
        self._pystray = pystray

        self._unlock_item = pystray.MenuItem(
            lambda item: "Unlock input" if self._locked else "Input unlocked",
            lambda icon, item: self._on_toggle(),
            enabled=lambda item: self._locked,
            default=True,
        )
        self._mode_items = {
            mode: pystray.MenuItem(
                MODE_LABELS[mode],
                lambda icon, item, m=mode: self._request_mode(m),
                checked=lambda item, m=mode: self._mode == m,
                radio=True,
            )
            for mode in MODES
        }
        hint_item = pystray.MenuItem(
            self._hint, lambda _icon, _item: None, enabled=False
        )
        menu = pystray.Menu(
            self._unlock_item,
            *self._mode_items.values(),
            hint_item,
            pystray.MenuItem("Quit", lambda _icon, _item: self._on_quit()),
        )
        try:
            self._icon = pystray.Icon(
                "trayock", _padlock_image(False), "trayock: input unlocked", menu
            )
        except Exception as exc:  # backend probing can happen lazily here
            raise TrayUnavailable(
                "pystray is installed but no tray backend works here "
                "(AppIndicator3/AyatanaAppIndicator3 missing): %s" % exc
            ) from exc

    def set_locked(self, locked: bool, mode: Optional[str] = None) -> None:
        self._locked = locked
        if mode is not None:
            self._mode = mode
        try:
            self._icon.icon = _padlock_image(locked)
            self._icon.title = (
                "trayock: LOCKED - hold Ctrl+Alt+Shift+L to unlock"
                if locked
                else "trayock: input unlocked"
            )
        except Exception:
            log.exception("failed to update pystray icon")

    def show_error(self, message: str) -> None:
        log.error("%s", message)
        print("trayock: %s" % message, file=sys.stderr)

    def run(self) -> None:
        self._icon.run()

    def quit(self) -> None:
        self._icon.stop()


def _padlock_image(locked: bool):
    """Draw a small padlock icon with Pillow (used by the pystray fallback)."""
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = (192, 28, 40, 255) if locked else (38, 162, 105, 255)

    # body
    draw.rounded_rectangle((14, 30, 50, 56), radius=6, fill=accent)
    # keyhole
    draw.ellipse((29, 37, 35, 43), fill=(255, 255, 255, 255))
    draw.rectangle((31, 41, 33, 48), fill=(255, 255, 255, 255))
    # shackle
    box = (18, 12, 46, 42)
    if locked:
        draw.arc(box, start=180, end=360, fill=accent, width=6)
    else:
        # same arc rotated ~30 degrees around the right hinge -> open
        pivot = (46, 42)
        import math

        theta = math.radians(30)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        pts = []
        for deg in range(180, 361, 5):
            rad = math.radians(deg)
            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            rx = (box[2] - box[0]) / 2.0
            ry = (box[3] - box[1]) / 2.0
            x = cx + rx * math.cos(rad)
            y = cy + ry * math.sin(rad)
            dx, dy = x - pivot[0], y - pivot[1]
            pts.append(
                (
                    pivot[0] + dx * cos_t - dy * sin_t,
                    pivot[1] + dx * sin_t + dy * cos_t,
                )
            )
        draw.line(pts, fill=accent, width=6, joint="curve")
    return image


def create_tray(
    on_toggle: Callable[[], None],
    on_quit: Callable[[], None],
    hint: str = HINT,
    on_set_mode: Optional[Callable[[str], None]] = None,
) -> BaseTray:
    """Prefer the desktop's AppIndicator support; fall back to pystray."""
    try:
        return AppIndicatorTray(on_toggle, on_quit, hint, on_set_mode)
    except Exception as exc:
        log.warning("AppIndicator unavailable (%s); trying pystray", exc)
    try:
        return PystrayTray(on_toggle, on_quit, hint, on_set_mode)
    except TrayUnavailable:
        raise
    except Exception as exc:
        raise TrayUnavailable("pystray fallback failed: %s" % exc) from exc
