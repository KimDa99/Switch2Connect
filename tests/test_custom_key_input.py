import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from controller import _chord_tap_keys, _coalesce_input_reports


class CustomKeyInputTests(unittest.TestCase):
    def test_alt_win_chords_tap_their_main_key(self):
        self.assertEqual(_chord_tap_keys(["VK_ALT_L", "VK_TAB"]), ["VK_TAB"])
        self.assertEqual(_chord_tap_keys(["VK_LWIN", "VK_D"]), ["VK_D"])
        self.assertEqual(_chord_tap_keys(["VK_SHIFT_L", "VK_W"]), [])
        self.assertEqual(_chord_tap_keys(["VK_MENU"]), [])

    def test_backlog_keeps_button_edges_and_newest_frame(self):
        processed = []
        callback = _coalesce_input_reports(lambda _s, d: processed.append(d[0]), slice(4, 8))

        def frame(n, buttons):
            return bytes([n, 0, 0, 0, buttons, 0, 0, 0])

        async def run():
            # Same-buttons stale frames are dropped; edges and the newest survive.
            for n, b in ((1, 0), (2, 0), (3, 1), (4, 1), (5, 0), (6, 0)):
                callback(None, frame(n, b))
            await asyncio.sleep(0)

        asyncio.run(run())
        self.assertEqual(processed, [1, 3, 5, 6])

    def test_failed_raw_keyboard_is_not_retried_every_key(self):
        from unittest import mock
        import config
        import keyboard_output

        manager = keyboard_output.KeyboardOutputManager()
        attempts = []
        manager.activate_raw_input = lambda save=True: attempts.append(save) and False
        with mock.patch.object(type(config.CONFIG), "keyboard_output_mode", keyboard_output.RAW_INPUT):
            for _ in range(10):
                manager.key_event("VK_NOT_A_KEY", True, "test")  # unmapped: no real keystroke
        self.assertEqual(len(attempts), 1)

    def test_without_loop_processes_directly(self):
        processed = []
        _coalesce_input_reports(lambda _s, d: processed.append(d), slice(4, 8))(None, b"x" * 8)
        self.assertEqual(len(processed), 1)


if __name__ == "__main__":
    unittest.main()
