import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stick_keyboard as sk
from config import SWITCH_BUTTONS as B


class Pad:
    def __init__(self, side):
        self.side = side

    def is_joycon_left(self):
        return self.side == "L"

    def is_joycon_right(self):
        return self.side == "R"


def frame(buttons=0, left=(0.0, 0.0), right=(0.0, 0.0)):
    return SimpleNamespace(buttons=buttons, left_stick=left, right_stick=right)


class StickKeyboardTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sk, "settings", lambda: {})  # defaults, not the user's config.yaml
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stick_cells(self):
        self.assertEqual(sk.stick_cell((0.0, 0.0)), (2, 2))
        self.assertEqual(sk.stick_cell((-0.71, 0.71)), (0, 0))   # round gate corner
        self.assertEqual(sk.stick_cell((0.71, -0.71)), (3, 4))
        self.assertEqual(sk.stick_cell((0.0, -0.3)), (3, 2))     # a little down reaches the bottom row
        self.assertEqual(sk.stick_cell((1.0, 1.0)), (0, 4))

    def test_navigation_speeds_up_with_tilt_and_hold(self):
        self.assertGreater(sk.repeat_delay(0.55, 0), sk.repeat_delay(1.0, 0))   # harder tilt: sooner
        self.assertGreater(sk.repeat_delay(1.0, 1), sk.repeat_delay(1.0, 8))    # longer hold: faster
        self.assertEqual(sk.repeat_delay(1.0, 100), sk.NAV_MIN_INTERVAL)
        pad, steps = Pad("R"), []
        with mock.patch.object(sk.MOBILE, "move", lambda r, c: steps.append((r, c))):
            sk._nav.clear()
            sk._navigate(pad, [(1.0, 0.0)], 0.0)       # fresh tilt: one step now
            sk._navigate(pad, [(1.0, 0.0)], 0.1)       # still inside first delay
            self.assertEqual(len(steps), 1)
            for i in range(1, 30):                     # full tilt held for ~1 s
                sk._navigate(pad, [(1.0, 0.0)], 0.1 + i * 0.033)
            fast = len(steps)
            sk._nav.clear()
            steps.clear()
            sk._navigate(pad, [(0.6, 0.0)], 0.0)       # light tilt for the same time
            for i in range(1, 33):
                sk._navigate(pad, [(0.6, 0.0)], i * 0.033)
            self.assertGreater(fast, len(steps))

    def test_mapped_chord(self):
        sk.left_cell = (0, 0)
        self.assertEqual(sk.action_tokens("VK_CONTROL_L+VK_V"), ["VK_CONTROL_L", "VK_V"])
        self.assertEqual(sk.action_tokens(sk.TYPE_LEFT), ["VK_1"])
        self.assertEqual(sk.action_tokens(sk.NONE), [])

    def test_split_typing_shift_and_exit(self):
        events = []
        left, right = Pad("L"), Pad("R")
        with mock.patch.object(sk.keyboard_output, "key_event",
                               lambda t, d, s: events.append((t, d))), \
                mock.patch.object(sk.keyboard_output, "release_source",
                                  lambda p: events.append(("release", p))):
            sk.enter(right, B["R_STK"])
            right_frame = frame(B["R_STK"])
            sk.process(right, right_frame)                         # toggle still held
            self.assertEqual(right_frame.buttons, 0)               # consumed
            sk.process(right, frame())                             # toggle released
            sk.process(left, frame(left=(-0.71, 0.71)))            # left stick -> "1"
            sk.process(right, frame(B["ZR"], right=(0.0, 0.0)))    # ZR taps left key at once
            self.assertEqual(events, [("VK_1", True), ("VK_1", False)])  # no OS auto-repeat
            sk.process(right, frame(0))
            events.clear()

            sk.process(left, frame(B["L"]))                        # L holds Shift
            sk.process(left, frame(B["L"] | B["ZL"]))              # ZL types right key "K"
            sk.process(left, frame(0))
            self.assertEqual(events, [("VK_SHIFT_L", True), ("VK_K", True),
                                      ("VK_K", False), ("VK_SHIFT_L", False)])
            events.clear()

            sk.process(right, frame(B["B"]))                       # Backspace held...
            sk.process(right, frame(B["B"] | B["R_STK"]))          # ...then exit
            self.assertTrue(sk.active)                             # until toggle released
            sk.process(right, frame(0))
            self.assertFalse(sk.active)
            self.assertEqual(events, [("VK_BACK", True), ("release", "stick_keyboard")])
            normal = frame(B["A"])
            sk.process(right, normal)
            self.assertEqual(normal.buttons, B["A"])               # off: untouched


if __name__ == "__main__":
    unittest.main()
