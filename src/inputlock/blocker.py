"""Exclusive evdev grab/ungrab of keyboards and pointers + escape combo."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import threading
import time
from collections import namedtuple
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import evdev
from evdev import ecodes

from inputlock.classify import classify

log = logging.getLogger("inputlock.blocker")

DEFAULT_INPUT_DIR = "/dev/input"
DEFAULT_SYSFS_DIR = "/sys/class/input"
RESCAN_INTERVAL = 2.0  # pick up hot-plugged devices while locked
LOOP_TICK = 0.2  # main loop granularity (escape timing, stop, rescan)

#: what to grab; "both" needs a keyboard so the escape combo stays possible
MODE_BOTH = "both"
MODE_KEYBOARD = "keyboard"
MODE_POINTER = "pointer"
MODES = (MODE_BOTH, MODE_KEYBOARD, MODE_POINTER)

#: mode name as shown in the tray menu
MODE_LABELS = {
    MODE_BOTH: "Lock keyboard + mouse",
    MODE_KEYBOARD: "Lock keyboard only",
    MODE_POINTER: "Lock mouse only",
}


def _wanted(kind: Optional[str], mode: str) -> bool:
    """Should a device of ``kind`` be grabbed for ``mode``?"""
    if kind == "keyboard":
        return mode in (MODE_BOTH, MODE_KEYBOARD)
    if kind == "pointer":
        return mode in (MODE_BOTH, MODE_POINTER)
    return False

DeviceRow = namedtuple("DeviceRow", "path name kind readable")


class LockError(RuntimeError):
    """Input could not be locked (permissions, no devices, grab failed)."""


class EscapeComboDetector:
    """Detects Ctrl+Alt+Shift+L being held continuously for ``hold_seconds``.

    Pure state machine: feed key events with :meth:`update` and call
    :meth:`tick` periodically so the combo still completes while no further
    key events arrive (held keys stop generating events).
    """

    CTRL = frozenset({ecodes.KEY_LEFTCTRL, ecodes.KEY_RIGHTCTRL})
    ALT = frozenset({ecodes.KEY_LEFTALT, ecodes.KEY_RIGHTALT})
    SHIFT = frozenset({ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT})
    L = ecodes.KEY_L

    def __init__(
        self,
        hold_seconds: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hold_seconds = hold_seconds
        self._clock = clock
        self._held: set = set()
        self._since: Optional[float] = None
        self._fired = False

    @property
    def held(self) -> frozenset:
        return frozenset(self._held)

    def reset(self) -> None:
        self._held.clear()
        self._since = None
        self._fired = False

    def active(self) -> bool:
        held = self._held
        return (
            bool(held & self.CTRL)
            and bool(held & self.ALT)
            and bool(held & self.SHIFT)
            and self.L in held
        )

    def update(self, code: int, value: int) -> bool:
        """Feed a key event (value 0=release, 1=press, 2=autorepeat).

        Returns True the moment the combo completes.
        """
        if value:
            self._held.add(code)
        else:
            self._held.discard(code)
        if self.active():
            if self._since is None:
                self._since = self._clock()
                self._fired = False
        else:
            self._since = None
            self._fired = False
        return self.tick()

    def tick(self) -> bool:
        """Returns True once when the combo has been held long enough."""
        if (
            self._since is not None
            and not self._fired
            and self._clock() - self._since >= self.hold_seconds
        ):
            self._fired = True
            return True
        return False


def _device_sort_key(path: str) -> Tuple[int, str]:
    """Sort /dev/input/event2 before /dev/input/event10."""
    digits = "".join(ch for ch in os.path.basename(path) if ch.isdigit())
    return (int(digits) if digits else -1, path)


def iter_event_paths(input_dir: str = DEFAULT_INPUT_DIR) -> List[str]:
    """Every ``event*`` node in ``input_dir``, readable or not.

    ``evdev.list_devices()`` only returns devices the caller can open, which
    hides exactly the ones a missing ``input`` group would show up on. We glob
    instead so permission problems can be reported instead of looking like
    "no devices found".
    """
    paths = sorted(glob.glob(os.path.join(input_dir, "event*")), key=_device_sort_key)
    if paths:
        return paths
    try:
        return sorted(evdev.list_devices(input_dir), key=_device_sort_key)
    except TypeError:  # older evdev without the directory argument
        return sorted(evdev.list_devices(), key=_device_sort_key)


def scan_devices(
    input_dir: str = DEFAULT_INPUT_DIR, sysfs_dir: str = DEFAULT_SYSFS_DIR
) -> List[DeviceRow]:
    """Enumerate /dev/input devices with their classification (for --list-devices)."""
    rows: List[DeviceRow] = []
    for path in iter_event_paths(input_dir):
        name, kind, readable = "?", None, False
        dev = None
        try:
            dev = evdev.InputDevice(path)
            readable = True
            name = dev.name
            kind = classify(dev.capabilities())
        except OSError:
            # cannot open it: fall back to the world-readable sysfs view so
            # the report still says *which* kind of device is denied
            name = _sysfs_name(path, sysfs_dir) or name
            kind = classify(sysfs_capabilities(path, sysfs_dir) or {})
        finally:
            if dev is not None:
                try:
                    dev.close()
                except OSError:
                    pass
        rows.append(DeviceRow(path, name, kind, readable))
    return rows


def _mask_codes(text: str) -> Set[int]:
    """Decode a kernel capability bitmask (hex words, most significant first)."""
    codes: Set[int] = set()
    for index, word in enumerate(reversed(text.split())):
        try:
            value = int(word, 16)
        except ValueError:
            continue
        base = index * 64
        bit = 0
        while value:
            if value & 1:
                codes.add(base + bit)
            value >>= 1
            bit += 1
    return codes


def sysfs_capabilities(path: str, sysfs_dir: str = DEFAULT_SYSFS_DIR) -> Optional[dict]:
    """Capabilities from ``/sys/class/input/<device>/device/capabilities``.

    Unlike the evdev node this is readable by anyone, so devices we are not
    allowed to open can still be classified as keyboard/pointer.
    """
    cap_dir = Path(sysfs_dir) / Path(path).name / "device" / "capabilities"
    caps: Dict[int, Set[int]] = {}
    for etype, filename in (
        (ecodes.EV_KEY, "key"),
        (ecodes.EV_REL, "rel"),
        (ecodes.EV_ABS, "abs"),
    ):
        try:
            codes = _mask_codes((cap_dir / filename).read_text())
        except OSError:
            return caps or None
        if codes:
            caps[etype] = codes
    return caps or None


def _sysfs_name(path: str, sysfs_dir: str = DEFAULT_SYSFS_DIR) -> Optional[str]:
    try:
        text = (Path(sysfs_dir) / Path(path).name / "device" / "name").read_text()
        return text.strip() or None
    except OSError:
        return None


class InputBlocker:
    """Grabs keyboard/pointer devices while locked; escape combo force-unlocks.

    Thread model: ``lock()``/``unlock()``/``toggle()`` are called from the
    main (GUI) thread. A daemon thread runs an asyncio loop that reads the
    grabbed devices (consuming every event) and watches for the escape combo.
    """

    def __init__(
        self,
        on_state_change: Optional[Callable[[bool, str], None]] = None,
        on_escape: Optional[Callable[[], None]] = None,
        hold_seconds: float = 2.0,
        mode: str = MODE_BOTH,
        keyboard_only: bool = False,
    ) -> None:
        if keyboard_only:
            mode = MODE_KEYBOARD
        if mode not in MODES:
            raise ValueError("unknown lock mode: %r" % (mode,))
        self._on_state_change = on_state_change
        self._on_escape = on_escape
        self._detector = EscapeComboDetector(hold_seconds)
        self._mode = mode
        self._devices: Dict[str, evdev.InputDevice] = {}
        self._watchers: Dict[str, evdev.InputDevice] = {}
        self._readers: Dict[str, "asyncio.Task"] = {}
        self._denied: set = set()
        self._locked = False
        self._escape_hit = False
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def mode(self) -> str:
        """Mode the next lock (or the current one) uses."""
        return self._mode

    @property
    def devices_locked(self) -> List[str]:
        return list(self._devices)

    @property
    def escape_combo(self) -> str:
        return "hold Ctrl+Alt+Shift+L for %.1f s" % self._detector.hold_seconds

    def lock(self, mode: Optional[str] = None) -> List[str]:
        """Grab devices for ``mode`` (defaults to the current one).

        Raises :class:`LockError` when nothing usable can be grabbed.
        """
        target = self._mode if mode is None else mode
        if target not in MODES:
            raise LockError("unknown lock mode: %r" % (mode,))
        if self._locked and target != self._mode:
            self.unlock()  # switching modes: release everything first
        self._join_previous_thread()
        with self._lock:
            if self._locked:
                return list(self._devices)
            grabbed, keyboards, pointers, errors, seen = self._grab_all(target)
            if target == MODE_POINTER:
                self._watch_keyboards()
            watched = len(self._watchers)
            if not self._lock_satisfied(target, keyboards, pointers, watched):
                self._close_all()
                raise LockError(
                    self._lock_error_message(errors, seen, target, pointers, watched)
                )
            for err in errors:
                log.warning("%s", err)
            if not grabbed:
                raise LockError("no usable input devices found")
            self._mode = target
            self._stop.clear()
            self._escape_hit = False
            self._detector.reset()
            self._locked = True
            self._thread = threading.Thread(
                target=self._run, name="inputlock-reader", daemon=True
            )
            self._thread.start()
        log.info(
            "input LOCKED [%s] (%d device%s): %s",
            target,
            len(grabbed),
            "s" if len(grabbed) != 1 else "",
            ", ".join(grabbed),
        )
        self._notify(True)
        return list(grabbed)

    def unlock(self) -> None:
        """Release all grabs (safe to call from any thread except the reader)."""
        thread = None
        with self._lock:
            if not self._locked and not self._devices and not self._watchers:
                return
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            changed = self._release_locked()
        if changed:
            log.info("input unlocked")
            self._notify(False)

    def toggle(self, mode: Optional[str] = None) -> bool:
        """Toggle the lock; returns True when now locked.

        ``mode`` selects what to lock (and switches mode when already locked).
        """
        target = self._mode if mode is None else mode
        if self._locked and target == self._mode:
            self.unlock()
            return False
        self.lock(target)
        return True

    # ----------------------------------------------------------- internals

    def _join_previous_thread(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def _open_and_grab(
        self, path: str, mode: str = MODE_BOTH
    ) -> Tuple[Optional[evdev.InputDevice], Optional[str], Optional[str]]:
        """Open+classify+grab one device. Returns (device, kind, error)."""
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            if path not in self._denied:
                log.warning(
                    "cannot open %s: permission denied "
                    "(add your user to the 'input' group, see README)",
                    path,
                )
                self._denied.add(path)
            return None, None, "%s: permission denied" % path
        except OSError as exc:
            return None, None, "%s: %s" % (path, exc)
        try:
            kind = classify(dev.capabilities())
        except OSError as exc:
            dev.close()
            return None, None, "%s: %s" % (path, exc)
        if kind is None or not _wanted(kind, mode):
            dev.close()
            return None, None, None
        try:
            dev.grab()
        except OSError as exc:
            reason = exc.strerror or str(exc)
            dev.close()
            return None, None, "%s: grab failed (%s)" % (path, reason)
        return dev, kind, None

    @staticmethod
    def _lock_satisfied(
        mode: str, keyboards: int, pointers: int, watched: int = 0
    ) -> bool:
        """Is the requested mode actually holding devices?

        "both"/"keyboard" must have a keyboard: without one there is no way to
        read the escape combo while the mouse is grabbed. "pointer" must have
        a mouse *and* a keyboard we can watch (read without grabbing) so the
        escape combo keeps working while typing does.
        """
        if mode == MODE_POINTER:
            return pointers > 0 and watched > 0
        return keyboards > 0

    def _grab_all(self, mode: str) -> Tuple[List[str], int, int, List[str], int]:
        grabbed: List[str] = []
        keyboards = 0
        pointers = 0
        errors: List[str] = []
        paths = iter_event_paths()
        for path in paths:
            if path in self._devices:
                continue
            dev, kind, error = self._open_and_grab(path, mode)
            if error:
                errors.append(error)
            if dev is None:
                continue
            self._devices[path] = dev
            grabbed.append(path)
            if kind == "keyboard":
                keyboards += 1
            elif kind == "pointer":
                pointers += 1
            log.debug("grabbed %s (%s): %s", path, kind, dev.name)
        return grabbed, keyboards, pointers, errors, len(paths)

    def _watch_keyboards(self) -> List[str]:
        """Open keyboards read-only (no grab) and return the new paths.

        Mouse-only mode must leave typing usable, but inputlock still has to
        see Ctrl+Alt+Shift+L to be able to release the mouse grab: several
        readers can share a device as long as nobody grabs it.
        """
        added: List[str] = []
        for path in iter_event_paths():
            if path in self._devices or path in self._watchers:
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            try:
                kind = classify(dev.capabilities())
            except OSError:
                dev.close()
                continue
            if kind != "keyboard":
                dev.close()
                continue
            self._watchers[path] = dev
            added.append(path)
            log.debug("watching %s for the escape combo (not grabbed)", path)
        return added

    def _close_device(self, path: str) -> None:
        dev = self._devices.pop(path, None)
        if dev is None:
            return
        try:
            dev.ungrab()
        except OSError:
            pass
        try:
            dev.close()
        except OSError:
            pass

    def _close_watcher(self, path: str) -> None:
        dev = self._watchers.pop(path, None)
        if dev is None:
            return
        try:
            dev.close()
        except OSError:
            pass

    def _close_all(self) -> None:
        for path in list(self._devices):
            self._close_device(path)
        for path in list(self._watchers):
            self._close_watcher(path)

    def _release_locked(self) -> bool:
        """Release everything; must be called with ``self._lock`` held."""
        changed = self._locked or bool(self._devices)
        self._close_all()
        self._readers.clear()
        self._locked = False
        return changed

    def _notify(self, locked: bool) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change(locked, self._mode)
        except Exception:
            log.exception("state-change callback failed")

    def _lock_error_message(
        self, errors: List[str], seen: int, mode: str, pointers: int = 0,
        watched: int = 0,
    ) -> str:
        denied = [err for err in errors if "permission denied" in err]
        if seen == 0:
            lines = [
                "Cannot lock input: no /dev/input/event* devices were found.",
                "Check the devices with:  ls -l /dev/input/event*",
            ]
        elif denied:
            lines = [
                "Cannot lock input: %d input device(s) are not accessible to your user."
                % len(denied),
                "Add yourself to the 'input' group, then log out and back in:",
                "  sudo usermod -aG input $USER",
                "Verify with:  ls -l /dev/input/event*   (expect crw-rw---- root input)",
            ]
        elif mode == MODE_POINTER and pointers:
            lines = [
                "Cannot lock the mouse: no keyboard could be read to watch the "
                "escape combo (Ctrl+Alt+Shift+L), so there would be no way to "
                "unlock.",
                "Run 'inputlock --list-devices' to see the available devices.",
            ]
        elif mode == MODE_POINTER:
            lines = [
                "Cannot lock the mouse: no pointer device could be grabbed.",
                "Run 'inputlock --list-devices' to see the available devices.",
            ]
        else:
            lines = [
                "Cannot lock input: no keyboard device could be grabbed.",
                "Are you in the 'input' group? Run:  sudo usermod -aG input $USER",
                "then log out and back in. Verify with:  ls -l /dev/input/event*",
            ]
            if mode == MODE_BOTH and pointers:
                lines.append(
                    "Note: %d pointer device(s) were found, but locking the mouse "
                    "without a keyboard would leave no way to unlock." % pointers
                )
        if errors:
            lines.append("Details: " + "; ".join(errors[:5]))
        return "\n".join(lines)

    # ------------------------------------------------------- reader thread

    def _run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception:
            log.exception("reader loop crashed")
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                changed = self._release_locked()
                escape = self._escape_hit
            if changed:
                log.info("input unlocked")
                self._notify(False)
            if escape and self._on_escape is not None:
                try:
                    self._on_escape()
                except Exception:
                    log.exception("escape callback failed")

    async def _run_async(self) -> None:
        loop = asyncio.get_running_loop()
        for path, dev in {**self._devices, **self._watchers}.items():
            if path not in self._readers:
                self._readers[path] = loop.create_task(self._reader(dev))
        last_rescan = time.monotonic()
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_rescan >= RESCAN_INTERVAL:
                    self._rescan(loop)
                    last_rescan = now
                self._collect_finished()
                if self._detector.tick():
                    self._trigger_escape()
                    break
                await asyncio.sleep(LOOP_TICK)
        finally:
            tasks = list(self._readers.values())
            self._readers.clear()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def _rescan(self, loop: "asyncio.AbstractEventLoop") -> None:
        """Grab devices plugged in after locking."""
        try:
            paths = iter_event_paths()
        except OSError:
            return
        for path in paths:
            if path in self._devices or path in self._denied:
                continue
            dev, kind, error = self._open_and_grab(path, self._mode)
            if error:
                log.warning("%s", error)
            if dev is None:
                continue
            self._devices[path] = dev
            self._readers[path] = loop.create_task(self._reader(dev))
            log.info("hot-plug: now grabbing %s (%s): %s", path, kind, dev.name)
        if self._mode == MODE_POINTER:
            for path in self._watch_keyboards():
                self._readers[path] = loop.create_task(self._reader(self._watchers[path]))
                log.info("hot-plug: now watching keyboard %s for the escape combo", path)

    def _collect_finished(self) -> None:
        for path, task in list(self._readers.items()):
            if not task.done():
                continue
            del self._readers[path]
            if not task.cancelled():
                exc = task.exception()
                if exc is not None and not isinstance(exc, OSError):
                    log.debug("reader for %s ended: %r", path, exc)
            if path in self._devices:
                log.debug("device %s disappeared, releasing it", path)
                self._close_device(path)
            elif path in self._watchers:
                self._close_watcher(path)
        if self._locked and not self._devices:
            # Nothing is grabbed any more: never report "locked" while input
            # actually reaches the desktop.
            log.warning("every grabbed device disappeared; unlocking")
            self._stop.set()

    async def _reader(self, dev: evdev.InputDevice) -> None:
        async for event in dev.async_read_loop():
            if event.type == ecodes.EV_KEY and self._detector.update(event.code, event.value):
                self._trigger_escape()
                return

    def _trigger_escape(self) -> None:
        if self._escape_hit:
            return
        self._escape_hit = True
        log.warning("escape combo detected -> unlocking")
        self._stop.set()
