import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import mobile_keyboard as mk


class MobileKeyboardTests(unittest.TestCase):
    def test_rows_are_ten_keys_wide(self):
        layout = mk.Layout()
        for page in mk.PAGES:
            layout.page = page
            for row in range(4):
                start, end = layout.spans(row)[0][0], layout.spans(row)[-1][1]
                self.assertAlmostEqual(start + end, 10.0, msg=(page, row))  # centred in 10 widths

    def test_symbol_tokens(self):
        self.assertEqual(mk.char_tokens("a"), ["VK_A"])
        self.assertEqual(mk.char_tokens("7"), ["VK_7"])
        self.assertEqual(mk.char_tokens("/"), ["VK_SLASH"])
        self.assertEqual(mk.char_tokens("?"), ["VK_SHIFT_L", "VK_SLASH"])
        self.assertEqual(mk.char_tokens('"'), ["VK_SHIFT_L", "VK_APOSTROPHE"])
        for page in mk.PAGES.values():                  # every key on every page types
            for _offset, keys in page:
                for _label, action, _w in keys:
                    if len(action) == 1:
                        mk.char_tokens(action)

    def test_navigation_shift_and_pages(self):
        layout = mk.Layout()
        self.assertEqual(layout.key()[0], "g")
        layout.move(1, 0)                                # g (centre 5.0) -> v
        self.assertEqual(layout.key()[0], "v")
        layout.move(1, 0)                                # -> wide Space
        self.assertEqual(layout.key()[1], mk.SPACE)
        self.assertEqual(layout.activate(), ["VK_SPACE"])
        layout.move(0, -9)                               # far left: ?123
        self.assertEqual(layout.activate(), [])
        self.assertEqual(layout.page, "sym1")
        layout.move(-3, 0)                               # top row -> "1"
        self.assertEqual(layout.activate(), ["VK_1"])

        layout.page, layout.row, layout.index = "abc", 2, 0
        self.assertEqual(layout.activate(), [])          # Shift on
        layout.move(0, 1)                                # z
        self.assertEqual(layout.activate(), ["VK_SHIFT_L", "VK_Z"])
        self.assertEqual(layout.activate(), ["VK_Z"])    # one-shot: off again


if __name__ == "__main__":
    unittest.main()
