"""InputBlocker tests against a fake evdev layer (no hardware, no root)."""

import asyncio
import logging
import os
import tempfile
import time
import unittest
from unittest import mock

from evdev import ecodes

from inputlock import blocker
from inputlock.blocker import (
    InputBlocker,
    LockError,
    iter_event_paths,
    scan_devices,
)

# the blocker logs loudly on purpose; keep the test run readable
logging.getLogger("inputlock").setLevel(logging.CRITICAL)

KEYBOARD_CAPS = {ecodes.EV_KEY: {ecodes.KEY_A, ecodes.KEY_B, ecodes.KEY_SPACE}}
POINTER_CAPS = {ecodes.EV_REL: {ecodes.REL_X, ecodes.REL_Y}}

#: a harmless EV_SYN event, so the reader loop sees a realistic stream
SYN_EVENT = type("SynEvent", (), {"type": ecodes.EV_SYN, "code": 0, "value": 0})()


class FakeDevice:
    """Stand-in for evdev.InputDevice with a never-ending reader loop."""

    def __init__(self, path, caps, name="fake device", grab_error=None):
        self.path = path
        self.name = name
        self._caps = caps
        self._grab_error = grab_error
        self.grabbed = False
        self.closed = False

    def capabilities(self):
        return self._caps

    def grab(self):
        if self._grab_error is not None:
            raise self._grab_error
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        self.closed = True

    async def async_read_loop(self):
        while True:
            await asyncio.sleep(0.01)
            yield SYN_EVENT


class FakeLoop:
    """Minimal stand-in for the asyncio loop used by _rescan()."""

    def __init__(self):
        self.tasks = []

    def create_task(self, coro):
        coro.close()  # nothing will ever await it
        task = mock.Mock()
        task.done.return_value = False
        task.cancelled.return_value = False
        self.tasks.append(task)
        return task


def patch_evdev(paths, devices):
    """Patch path enumeration + device opening.

    ``paths`` is live (mutating it changes what the patched enumerator
    returns). ``devices`` maps a path to a FakeDevice, or an exception to
    raise when the device is opened.
    """

    def factory(path):
        target = devices.get(path)
        if isinstance(target, Exception):
            raise target
        if target is None:
            raise AssertionError("unexpected device open: %s" % path)
        return target

    return (
        mock.patch.object(
            blocker, "iter_event_paths", side_effect=lambda *a, **k: list(paths)
        ),
        mock.patch.object(blocker.evdev, "InputDevice", side_effect=factory),
    )


class IterEventPathsTests(unittest.TestCase):
    def test_globs_every_event_node_including_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("event0", "event10", "mouse0", "event2"):
                open(os.path.join(tmp, name), "w").close()
            self.assertEqual(
                iter_event_paths(tmp),
                [
                    os.path.join(tmp, "event0"),
                    os.path.join(tmp, "event2"),
                    os.path.join(tmp, "event10"),
                ],
            )

    def test_empty_directory_falls_back_to_evdev(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(blocker.evdev, "list_devices", return_value=["x"]):
                self.assertEqual(iter_event_paths(tmp), ["x"])


class ScanDevicesTests(unittest.TestCase):
    def test_reports_devices_we_cannot_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            denied = os.path.join(tmp, "event0")
            ok = os.path.join(tmp, "event1")
            for path in (denied, ok):
                open(path, "w").close()
            fake = FakeDevice(ok, KEYBOARD_CAPS, name="Real Keyboard")
            paths_patch, open_patch = patch_evdev(
                [denied, ok], {denied: PermissionError(13, "denied"), ok: fake}
            )
            with paths_patch, open_patch:
                rows = scan_devices(tmp, os.path.join(tmp, "sysfs"))

        self.assertEqual([row.path for row in rows], [denied, ok])
        denied_row, ok_row = rows
        self.assertFalse(denied_row.readable)
        self.assertIsNone(denied_row.kind)
        self.assertEqual(denied_row.name, "?")
        self.assertTrue(ok_row.readable)
        self.assertEqual(ok_row.kind, "keyboard")
        self.assertEqual(ok_row.name, "Real Keyboard")

    def test_denied_device_is_classified_from_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            denied = os.path.join(tmp, "event4")
            open(denied, "w").close()
            sysfs = self.write_sysfs(
                tmp,
                "event4",
                name="AT Translated Set 2 keyboard",
                key_mask=(1 << ecodes.KEY_A) | (1 << ecodes.KEY_SPACE),
            )
            paths_patch, open_patch = patch_evdev(
                [denied], {denied: PermissionError(13, "denied")}
            )
            with paths_patch, open_patch:
                rows = scan_devices(tmp, sysfs)

        row = rows[0]
        self.assertFalse(row.readable)
        self.assertEqual(row.kind, "keyboard")
        self.assertEqual(row.name, "AT Translated Set 2 keyboard")

    @staticmethod
    def write_sysfs(root, node, name=None, key_mask=0, rel_mask=0, abs_mask=0):
        """Create a fake /sys/class/input tree; returns its root."""
        base = os.path.join(root, "sysfs", node, "device")
        caps = os.path.join(base, "capabilities")
        os.makedirs(caps, exist_ok=True)
        if name is not None:
            with open(os.path.join(base, "name"), "w") as handle:
                handle.write(name + "\n")
        for filename, mask in (("key", key_mask), ("rel", rel_mask), ("abs", abs_mask)):
            with open(os.path.join(caps, filename), "w") as handle:
                handle.write("%x\n" % mask)
        return os.path.join(root, "sysfs")


class SysfsCapabilitiesTests(unittest.TestCase):
    def test_decodes_hex_bitmasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            sysfs = ScanDevicesTests.write_sysfs(
                tmp,
                "event5",
                key_mask=(1 << ecodes.KEY_A) | (1 << ecodes.KEY_SPACE),
                rel_mask=(1 << ecodes.REL_X) | (1 << ecodes.REL_Y),
            )
            caps = blocker.sysfs_capabilities("/dev/input/event5", sysfs)

        self.assertEqual(caps[ecodes.EV_KEY], {ecodes.KEY_A, ecodes.KEY_SPACE})
        self.assertEqual(caps[ecodes.EV_REL], {ecodes.REL_X, ecodes.REL_Y})

    def test_missing_sysfs_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(blocker.sysfs_capabilities("/dev/input/event0", tmp))


class LockUnlockTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(blocker, "LOOP_TICK", 0.01),
            mock.patch.object(blocker, "RESCAN_INTERVAL", 60.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_blocker(self, **kwargs):
        states = []
        blocker_obj = InputBlocker(
            on_state_change=lambda locked, mode: states.append(locked), **kwargs
        )
        self.addCleanup(blocker_obj.unlock)
        return blocker_obj, states

    def test_lock_grabs_keyboard_and_pointer_then_unlock_releases(self):
        keyboard = FakeDevice("/dev/input/event0", KEYBOARD_CAPS, "AT Keyboard")
        mouse = FakeDevice("/dev/input/event1", POINTER_CAPS, "Optical Mouse")
        paths_patch, open_patch = patch_evdev(
            [keyboard.path, mouse.path], {keyboard.path: keyboard, mouse.path: mouse}
        )
        with paths_patch, open_patch:
            blocker_obj, states = self.make_blocker()
            blocker_obj.lock()

            self.assertTrue(blocker_obj.locked)
            self.assertEqual(
                sorted(blocker_obj.devices_locked), [keyboard.path, mouse.path]
            )
            self.assertTrue(keyboard.grabbed)
            self.assertTrue(mouse.grabbed)
            blocker_obj.unlock()

        self.assertFalse(blocker_obj.locked)
        self.assertFalse(keyboard.grabbed)
        self.assertFalse(mouse.grabbed)
        self.assertTrue(keyboard.closed)
        self.assertEqual(states, [True, False])

    def test_toggle_flips_state(self):
        keyboard = FakeDevice("/dev/input/event0", KEYBOARD_CAPS)
        paths_patch, open_patch = patch_evdev([keyboard.path], {keyboard.path: keyboard})
        with paths_patch, open_patch:
            blocker_obj, states = self.make_blocker()
            self.assertTrue(blocker_obj.toggle())
            self.assertTrue(blocker_obj.locked)
            self.assertFalse(blocker_obj.toggle())
            self.assertFalse(blocker_obj.locked)
            self.assertTrue(blocker_obj.toggle())
            self.assertTrue(blocker_obj.locked)
            blocker_obj.unlock()
        self.assertEqual(states, [True, False, True, False])
        self.assertFalse(keyboard.grabbed)

    def test_unlocks_when_every_device_disappears(self):
        keyboard = FakeDevice("/dev/input/event0", KEYBOARD_CAPS)

        async def reader_ended():
            if False:  # an async generator that finishes immediately
                yield None

        keyboard.async_read_loop = reader_ended
        paths_patch, open_patch = patch_evdev([keyboard.path], {keyboard.path: keyboard})
        with paths_patch, open_patch:
            blocker_obj, states = self.make_blocker()
            blocker_obj.lock()
            deadline = time.monotonic() + 5.0
            while blocker_obj.locked and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertFalse(blocker_obj.locked)
        self.assertEqual(states, [True, False])
        self.assertFalse(keyboard.grabbed)

    def test_keyboard_only_leaves_the_mouse_usable(self):
        keyboard = FakeDevice("/dev/input/event0", KEYBOARD_CAPS)
        mouse = FakeDevice("/dev/input/event1", POINTER_CAPS)
        paths_patch, open_patch = patch_evdev(
            [keyboard.path, mouse.path], {keyboard.path: keyboard, mouse.path: mouse}
        )
        with paths_patch, open_patch:
            blocker_obj, _ = self.make_blocker(keyboard_only=True)
            blocker_obj.lock()
            self.assertEqual(blocker_obj.devices_locked, [keyboard.path])
            self.assertFalse(mouse.grabbed)
            blocker_obj.unlock()
        self.assertFalse(keyboard.grabbed)

    def test_pointer_only_devices_cannot_lock(self):
        mouse = FakeDevice("/dev/input/event1", POINTER_CAPS)
        paths_patch, open_patch = patch_evdev([mouse.path], {mouse.path: mouse})
        with paths_patch, open_patch:
            blocker_obj, states = self.make_blocker()
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock()
        self.assertIn("no keyboard device", str(ctx.exception))
        self.assertFalse(blocker_obj.locked)
        self.assertFalse(mouse.grabbed)
        self.assertEqual(states, [])

    def test_permission_denied_error_names_the_input_group(self):
        denied = "/dev/input/event0"
        paths_patch, open_patch = patch_evdev(
            [denied], {denied: PermissionError(13, "Permission denied")}
        )
        with paths_patch, open_patch:
            blocker_obj, _ = self.make_blocker()
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock()
        message = str(ctx.exception)
        self.assertIn("input' group", message)
        self.assertIn("permission denied", message)
        self.assertFalse(blocker_obj.locked)

    def test_no_devices_at_all_reports_missing_nodes(self):
        paths_patch, open_patch = patch_evdev([], {})
        with paths_patch, open_patch:
            blocker_obj, _ = self.make_blocker()
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock()
        self.assertIn("no /dev/input/event* devices", str(ctx.exception))

    def test_unlock_without_lock_is_a_noop(self):
        paths_patch, open_patch = patch_evdev([], {})
        with paths_patch, open_patch:
            blocker_obj, states = self.make_blocker()
            blocker_obj.unlock()
        self.assertEqual(states, [])

    def test_grab_busy_device_is_reported(self):
        keyboard = FakeDevice(
            "/dev/input/event0",
            KEYBOARD_CAPS,
            grab_error=OSError(16, "Device or resource busy"),
        )
        paths_patch, open_patch = patch_evdev([keyboard.path], {keyboard.path: keyboard})
        with paths_patch, open_patch:
            blocker_obj, _ = self.make_blocker()
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock()
        self.assertIn("grab failed", str(ctx.exception))


class RescanTests(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(blocker, "LOOP_TICK", 0.01),
            mock.patch.object(blocker, "RESCAN_INTERVAL", 60.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_hotplugged_keyboard_gets_grabbed_while_locked(self):
        first = FakeDevice("/dev/input/event0", KEYBOARD_CAPS)
        later = FakeDevice("/dev/input/event5", KEYBOARD_CAPS, "USB Keyboard")
        paths = [first.path]
        paths_patch, open_patch = patch_evdev(
            paths, {first.path: first, later.path: later}
        )
        with paths_patch, open_patch:
            blocker_obj = InputBlocker()
            self.addCleanup(blocker_obj.unlock)
            blocker_obj.lock()
            paths.append(later.path)
            loop = FakeLoop()
            blocker_obj._rescan(loop)

            self.assertEqual(
                sorted(blocker_obj.devices_locked), [first.path, later.path]
            )
            self.assertTrue(later.grabbed)
            self.assertEqual(len(loop.tasks), 1)
            self.assertIn(later.path, blocker_obj._readers)
            # FakeLoop tasks are not awaitable: drop just that entry so the
            # real reader thread can shut down cleanly.
            blocker_obj._readers.pop(later.path)
            blocker_obj.unlock()
        self.assertFalse(later.grabbed)


class EscapeIntegrationTests(unittest.TestCase):
    def test_escape_combo_unlocks_a_grabbed_keyboard(self):
        keyboard = FakeDevice("/dev/input/event0", KEYBOARD_CAPS)

        class Event:
            def __init__(self, code, value):
                self.type = ecodes.EV_KEY
                self.code = code
                self.value = value

        async def replay():
            combo = [
                (ecodes.KEY_LEFTCTRL, 1),
                (ecodes.KEY_LEFTALT, 1),
                (ecodes.KEY_LEFTSHIFT, 1),
                (ecodes.KEY_L, 1),
                (ecodes.KEY_L, 2),
            ]
            for code, value in combo:
                yield Event(code, value)
                await asyncio.sleep(0.001)
            while True:  # keep the reader alive after the combo was played
                await asyncio.sleep(0.05)

        keyboard.async_read_loop = replay
        seen = []
        paths_patch, open_patch = patch_evdev([keyboard.path], {keyboard.path: keyboard})
        with paths_patch, open_patch, mock.patch.object(
            blocker, "LOOP_TICK", 0.01
        ), mock.patch.object(blocker, "RESCAN_INTERVAL", 60.0):
            blocker_obj = InputBlocker(
                on_state_change=lambda locked, mode: seen.append(locked),
                on_escape=lambda: seen.append("escape"),
                hold_seconds=0.02,
            )
            self.addCleanup(blocker_obj.unlock)
            blocker_obj.lock()
            deadline = time.monotonic() + 5.0
            while blocker_obj.locked and time.monotonic() < deadline:
                time.sleep(0.01)

        self.assertFalse(blocker_obj.locked)
        self.assertEqual(seen, [True, False, "escape"])


class ModeTests(unittest.TestCase):
    """Lock modes: both / keyboard only / mouse only."""

    def setUp(self):
        for patcher in (
            mock.patch.object(blocker, "LOOP_TICK", 0.01),
            mock.patch.object(blocker, "RESCAN_INTERVAL", 60.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def devices():
        return {
            "/dev/input/event0": FakeDevice("/dev/input/event0", KEYBOARD_CAPS, "KB"),
            "/dev/input/event1": FakeDevice("/dev/input/event1", POINTER_CAPS, "Mouse"),
        }

    def make(self, paths, devices, states=None, **kwargs):
        states = [] if states is None else states
        blocker_obj = InputBlocker(
            on_state_change=lambda locked, mode: states.append((locked, mode)),
            **kwargs,
        )
        self.addCleanup(blocker_obj.unlock)
        paths_patch, open_patch = patch_evdev(paths, devices)
        return blocker_obj, states, paths_patch, open_patch

    def test_default_mode_is_both(self):
        blocker_obj, _, paths_patch, open_patch = self.make([], {})
        self.assertEqual(blocker_obj.mode, blocker.MODE_BOTH)

    def test_keyboard_only_kwarg_selects_keyboard_mode(self):
        blocker_obj, _, _, _ = self.make([], {}, keyboard_only=True)
        self.assertEqual(blocker_obj.mode, blocker.MODE_KEYBOARD)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            InputBlocker(mode="everything")

    def test_pointer_mode_grabs_only_the_mouse(self):
        devices = self.devices()
        blocker_obj, states, pp, op = self.make(list(devices), devices)
        with pp, op:
            blocker_obj.lock("pointer")
            self.assertTrue(blocker_obj.locked)
            self.assertEqual(blocker_obj.mode, blocker.MODE_POINTER)
            self.assertEqual(blocker_obj.devices_locked, ["/dev/input/event1"])
            self.assertTrue(devices["/dev/input/event1"].grabbed)
            self.assertFalse(devices["/dev/input/event0"].grabbed)
            blocker_obj.unlock()
        self.assertEqual(states, [(True, "pointer"), (False, "pointer")])

    def test_keyboard_mode_leaves_the_mouse_usable(self):
        devices = self.devices()
        blocker_obj, _, pp, op = self.make(list(devices), devices)
        with pp, op:
            blocker_obj.lock("keyboard")
            self.assertEqual(blocker_obj.devices_locked, ["/dev/input/event0"])
            blocker_obj.unlock()
        self.assertFalse(devices["/dev/input/event1"].grabbed)

    def test_switching_mode_while_locked(self):
        devices = self.devices()
        blocker_obj, _, pp, op = self.make(list(devices), devices)
        with pp, op:
            blocker_obj.lock("both")
            self.assertEqual(len(blocker_obj.devices_locked), 2)
            blocker_obj.lock("pointer")
            self.assertEqual(blocker_obj.mode, blocker.MODE_POINTER)
            self.assertEqual(blocker_obj.devices_locked, ["/dev/input/event1"])
            blocker_obj.unlock()
        self.assertFalse(devices["/dev/input/event0"].grabbed)

    def test_toggle_with_explicit_mode(self):
        devices = self.devices()
        blocker_obj, _, pp, op = self.make(list(devices), devices)
        with pp, op:
            self.assertTrue(blocker_obj.toggle("keyboard"))
            self.assertEqual(blocker_obj.devices_locked, ["/dev/input/event0"])
            self.assertFalse(blocker_obj.toggle("keyboard"))
            self.assertFalse(blocker_obj.locked)
            self.assertEqual(blocker_obj.mode, blocker.MODE_KEYBOARD)

    def test_pointer_mode_without_a_mouse_is_an_error(self):
        keyboard = FakeDevice("/dev/input/event0", KEYBOARD_CAPS)
        blocker_obj, _, pp, op = self.make([keyboard.path], {keyboard.path: keyboard})
        with pp, op:
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock("pointer")
        self.assertIn("pointer", str(ctx.exception))
        self.assertFalse(blocker_obj.locked)

    def test_both_mode_without_a_keyboard_refuses_to_lock(self):
        mouse = FakeDevice("/dev/input/event1", POINTER_CAPS)
        blocker_obj, _, pp, op = self.make([mouse.path], {mouse.path: mouse})
        with pp, op:
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock("both")
        message = str(ctx.exception)
        self.assertIn("no keyboard device", message)
        self.assertIn("no way to unlock", message)
        self.assertFalse(mouse.grabbed)

    def test_pointer_mode_watches_the_keyboard_without_grabbing_it(self):
        devices = self.devices()
        blocker_obj, _, pp, op = self.make(list(devices), devices)
        with pp, op:
            blocker_obj.lock("pointer")
            self.assertIn("/dev/input/event0", blocker_obj._watchers)
            self.assertNotIn("/dev/input/event0", blocker_obj.devices_locked)
            self.assertFalse(devices["/dev/input/event0"].grabbed)
            blocker_obj.unlock()
            self.assertEqual(blocker_obj._watchers, {})

    def test_pointer_mode_without_a_watchable_keyboard_refuses_to_lock(self):
        mouse = FakeDevice("/dev/input/event1", POINTER_CAPS)
        blocker_obj, _, pp, op = self.make([mouse.path], {mouse.path: mouse})
        with pp, op:
            with self.assertRaises(LockError) as ctx:
                blocker_obj.lock("pointer")
        self.assertIn("no way to unlock", str(ctx.exception))
        self.assertFalse(mouse.grabbed)

    def test_unknown_mode_string_raises_lock_error(self):
        blocker_obj, _, pp, op = self.make([], {})
        with pp, op:
            with self.assertRaises(LockError):
                blocker_obj.lock("banana")


class WantedKindTests(unittest.TestCase):
    def test_both_grabs_everything(self):
        self.assertTrue(blocker._wanted("keyboard", blocker.MODE_BOTH))
        self.assertTrue(blocker._wanted("pointer", blocker.MODE_BOTH))
        self.assertFalse(blocker._wanted(None, blocker.MODE_BOTH))

    def test_keyboard_mode_skips_pointers(self):
        self.assertTrue(blocker._wanted("keyboard", blocker.MODE_KEYBOARD))
        self.assertFalse(blocker._wanted("pointer", blocker.MODE_KEYBOARD))

    def test_pointer_mode_skips_keyboards(self):
        self.assertFalse(blocker._wanted("keyboard", blocker.MODE_POINTER))
        self.assertTrue(blocker._wanted("pointer", blocker.MODE_POINTER))


if __name__ == "__main__":
    unittest.main()
