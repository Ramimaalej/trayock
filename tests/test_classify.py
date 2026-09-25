"""Tests for inputlock.classify (no hardware needed)."""

import unittest

from evdev import ecodes

from inputlock.classify import classify


class ClassifyTests(unittest.TestCase):
    def test_keyboard_verbose_dict(self):
        caps = {
            ecodes.EV_KEY: {ecodes.KEY_A: "KEY_A", ecodes.KEY_POWER: "KEY_POWER"},
        }
        self.assertEqual(classify(caps), "keyboard")

    def test_keyboard_code_set(self):
        caps = {ecodes.EV_KEY: {ecodes.KEY_SPACE, ecodes.KEY_ENTER}}
        self.assertEqual(classify(caps), "keyboard")

    def test_keyboard_name_list(self):
        caps = {ecodes.EV_KEY: ["KEY_A", "KEY_LEFTSHIFT", "KEY_RIGHTCTRL"]}
        self.assertEqual(classify(caps), "keyboard")

    def test_keyboard_keyed_by_names(self):
        caps = {ecodes.EV_KEY: {"KEY_Q": None, "KEY_M": None}}
        self.assertEqual(classify(caps), "keyboard")

    def test_mouse_relative(self):
        caps = {
            ecodes.EV_KEY: {ecodes.BTN_LEFT: "BTN_LEFT", ecodes.BTN_RIGHT: "BTN_RIGHT"},
            ecodes.EV_REL: {ecodes.REL_X: "REL_X", ecodes.REL_Y: "REL_Y"},
        }
        self.assertEqual(classify(caps), "pointer")

    def test_touchpad_absolute(self):
        caps = {
            ecodes.EV_ABS: {ecodes.ABS_X: "ABS_X", ecodes.ABS_Y: "ABS_Y"},
            ecodes.EV_KEY: {ecodes.BTN_TOUCH: "BTN_TOUCH"},
        }
        self.assertEqual(classify(caps), "pointer")

    def test_touchscreen_without_relative(self):
        caps = {
            ecodes.EV_ABS: {ecodes.ABS_X: "ABS_X"},
            ecodes.EV_KEY: {ecodes.BTN_TOUCH: "BTN_TOUCH", ecodes.BTN_TOOL_FINGER: None},
        }
        self.assertEqual(classify(caps), "pointer")

    def test_power_button_is_ignored(self):
        caps = {ecodes.EV_KEY: {ecodes.KEY_POWER: "KEY_POWER"}}
        self.assertIsNone(classify(caps))

    def test_media_remote_is_ignored(self):
        caps = {
            ecodes.EV_KEY: {ecodes.KEY_VOLUMEUP: "KEY_VOLUMEUP", ecodes.KEY_PLAYPAUSE: None},
        }
        self.assertIsNone(classify(caps))

    def test_lid_switch_is_ignored(self):
        caps = {ecodes.EV_SW: {ecodes.SW_LID: "SW_LID"}}
        self.assertIsNone(classify(caps))

    def test_empty_capabilities(self):
        self.assertIsNone(classify({}))

    def test_keyboard_wins_when_device_is_hybrid(self):
        caps = {
            ecodes.EV_KEY: {ecodes.KEY_A: "KEY_A"},
            ecodes.EV_REL: {ecodes.REL_X: "REL_X"},
        }
        self.assertEqual(classify(caps), "keyboard")

    # -- shapes evdev.InputDevice.capabilities() really returns -------------

    def test_real_keyboard_is_a_list_of_codes(self):
        caps = {
            ecodes.EV_SYN: [0, 1, 3, 4],
            ecodes.EV_KEY: [1, 16, 28, 30, 44, 57, 272],
            ecodes.EV_MSC: [4],
            ecodes.EV_LED: [0, 1, 2],
        }
        self.assertEqual(classify(caps), "keyboard")

    def test_real_mouse_is_a_list_of_codes(self):
        caps = {
            ecodes.EV_SYN: [0, 1, 2, 4],
            ecodes.EV_KEY: [272, 273],
            ecodes.EV_REL: [0, 1],
            ecodes.EV_MSC: [4],
        }
        self.assertEqual(classify(caps), "pointer")

    def test_absinfo_tuples_are_unwrapped(self):
        """Axes come back as (code, AbsInfo) pairs - a crash before this fix."""
        caps = {
            ecodes.EV_KEY: [325, 330, 334],
            ecodes.EV_ABS: [
                (ecodes.ABS_X, ("value", "min", "max")),
                (ecodes.ABS_Y, ("value", "min", "max")),
            ],
        }
        self.assertEqual(classify(caps), "pointer")

    def test_unparseable_entries_are_skipped(self):
        caps = {
            ecodes.EV_KEY: [None, object(), ("KEY_A",), "KEY_SPACE"],
            ecodes.EV_ABS: [None, object()],
        }
        self.assertEqual(classify(caps), "keyboard")


if __name__ == "__main__":
    unittest.main()
