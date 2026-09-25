"""Tests for the escape-combo state machine (no hardware needed)."""

import unittest

from evdev import ecodes

from inputlock.blocker import EscapeComboDetector


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class EscapeComboTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.detector = EscapeComboDetector(hold_seconds=2.0, clock=self.clock)

    def press(self, *codes: int) -> None:
        for code in codes:
            self.assertFalse(self.detector.update(code, 1))

    def release(self, *codes: int) -> None:
        for code in codes:
            self.detector.update(code, 0)

    COMBO = (
        ecodes.KEY_LEFTCTRL,
        ecodes.KEY_LEFTALT,
        ecodes.KEY_LEFTSHIFT,
        ecodes.KEY_L,
    )

    def test_fires_after_holding_two_seconds(self):
        self.press(*self.COMBO)
        self.clock.now = 1.99
        self.assertFalse(self.detector.tick())
        self.clock.now = 2.0
        self.assertTrue(self.detector.tick())
        # one-shot: does not fire again while still held
        self.clock.now = 10.0
        self.assertFalse(self.detector.tick())

    def test_partial_combo_never_fires(self):
        self.press(
            ecodes.KEY_LEFTCTRL,
            ecodes.KEY_LEFTALT,
            ecodes.KEY_L,  # shift missing
        )
        self.clock.now = 60.0
        self.assertFalse(self.detector.tick())

    def test_modifier_alone_never_fires(self):
        self.press(*self.COMBO[:3])
        self.clock.now = 60.0
        self.assertFalse(self.detector.tick())

    def test_release_restarts_the_hold(self):
        self.press(*self.COMBO)
        self.clock.now = 1.5
        self.release(ecodes.KEY_L)
        self.assertFalse(self.detector.active())
        self.clock.now = 1.6
        self.press(ecodes.KEY_L)
        self.clock.now = 3.5  # only 1.9 s since re-press
        self.assertFalse(self.detector.tick())
        self.clock.now = 3.6  # 2.0 s held continuously
        self.assertTrue(self.detector.tick())

    def test_autorepeat_does_not_restart_the_hold(self):
        self.press(*self.COMBO)
        self.clock.now = 1.0
        self.assertFalse(self.detector.update(ecodes.KEY_L, 2))  # EV_KEY repeat
        self.clock.now = 2.0
        self.assertTrue(self.detector.tick())

    def test_right_side_modifiers_count(self):
        self.press(
            ecodes.KEY_RIGHTCTRL,
            ecodes.KEY_RIGHTALT,
            ecodes.KEY_RIGHTSHIFT,
            ecodes.KEY_L,
        )
        self.clock.now = 2.0
        self.assertTrue(self.detector.tick())

    def test_completion_can_arrive_via_an_event(self):
        self.press(*self.COMBO)
        self.clock.now = 5.0
        # any subsequent event also runs the timing check
        self.assertTrue(self.detector.update(ecodes.KEY_B, 1))

    def test_reset_clears_everything(self):
        self.press(*self.COMBO)
        self.detector.reset()
        self.assertEqual(self.detector.held, frozenset())
        self.clock.now = 100.0
        self.assertFalse(self.detector.tick())
        self.assertFalse(self.detector.active())


if __name__ == "__main__":
    unittest.main()
