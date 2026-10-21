"""Phone-style keyboard for the Stick Keyboard "one stick" layout.

Three pages like a phone keyboard (letters / ?123 / #+=), keys of different
widths, a one-shot Shift, and a highlight that a tilted stick moves one key at
a time. Moving up/down lands on the key under the current key's centre.
"""

SHIFT, BACK, SPACE, ENTER, HANGUL = "SHIFT", "BACK", "SPACE", "ENTER", "HANGUL"


def _k(label, action=None, width=1.0):
    return label, label if action is None else action, width


def _chars(text, width=1.0):
    return [_k(c, width=width) for c in text]


def _bottom(label, page):
    return 0.0, [_k(label, "PAGE:" + page, 1.5), _k("한/영", HANGUL), _k(","),
                 _k("Space", SPACE, 4.0), _k("."), _k("⏎", ENTER, 1.5)]


# page -> rows; a row is (left offset in key widths, [(label, action, width)]).
# Every row is 10 key-widths wide.
PAGES = {
    "abc": [
        (0.0, _chars("qwertyuiop")),
        (0.5, _chars("asdfghjkl")),
        (0.0, [_k("⇧", SHIFT, 1.5)] + _chars("zxcvbnm") + [_k("⌫", BACK, 1.5)]),
        _bottom("?123", "sym1"),
    ],
    "sym1": [
        (0.0, _chars("1234567890")),
        (0.0, _chars("-/:;()$&@\"")),
        (0.0, [_k("#+=", "PAGE:sym2", 1.5)] + _chars(".,?!'", 1.4) + [_k("⌫", BACK, 1.5)]),
        _bottom("ABC", "abc"),
    ],
    "sym2": [
        (0.0, _chars("[]{}#%^*+=")),
        (0.0, _chars("_\\|~<>`&@$")),
        (0.0, [_k("123", "PAGE:sym1", 1.5)] + _chars(".,?!'", 1.4) + [_k("⌫", BACK, 1.5)]),
        _bottom("ABC", "abc"),
    ],
}

# Dubeolsik jamo printed on the letter keys (typed through the Korean IME).
JAMO = dict(zip("qwertyuiopasdfghjklzxcvbnm", "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ"))
SHIFT_JAMO = {"q": "ㅃ", "w": "ㅉ", "e": "ㄸ", "r": "ㄲ", "t": "ㅆ", "o": "ㅒ", "p": "ㅖ"}

_SPECIAL_TOKENS = {BACK: ["VK_BACK"], SPACE: ["VK_SPACE"], ENTER: ["VK_RETURN"], HANGUL: ["VK_HANGUL"]}
_UNSHIFTED = {"-": "MINUS", "=": "EQUAL", "[": "BRACKETLEFT", "]": "BRACKETRIGHT",
              "\\": "BACKSLASH", ";": "SEMICOLON", "'": "APOSTROPHE", "`": "GRAVE",
              ",": "COMMA", ".": "PERIOD", "/": "SLASH"}
# US layout (same on Korean keyboards): shifted symbol -> its unshifted key.
_SHIFTED = dict(zip('!@#$%^&*()_+{}|:"~<>?', "1234567890-=[]\\;'`,./"))


def char_tokens(ch):
    if ch.isalnum():
        return [f"VK_{ch.upper()}"]
    if ch in _UNSHIFTED:
        return [f"VK_{_UNSHIFTED[ch]}"]
    return ["VK_SHIFT_L"] + char_tokens(_SHIFTED[ch])


class Layout:
    def __init__(self):
        self.page = "abc"
        self.row, self.index = 1, 4   # "g"
        self.shift = False

    def rows(self):
        return PAGES[self.page]

    def spans(self, row):
        offset, keys = self.rows()[row]
        spans, x = [], offset
        for _label, _action, width in keys:
            spans.append((x, x + width))
            x += width
        return spans

    def key(self):
        return self.rows()[self.row][1][self.index]

    def move(self, d_row, d_col):
        keys = self.rows()[self.row][1]
        self.index = min(len(keys) - 1, max(0, self.index + d_col))
        target = min(len(self.rows()) - 1, max(0, self.row + d_row))
        if target != self.row:
            start, end = self.spans(self.row)[self.index]
            centre = (start + end) / 2
            spans = self.spans(target)
            self.index = min(range(len(spans)),
                             key=lambda i: max(spans[i][0] - centre, centre - spans[i][1], 0))
            self.row = target

    def activate(self):
        """Press the highlighted key: returns the tokens to send (may be none)."""
        _label, action, _width = self.key()
        if action.startswith("PAGE:"):
            self.page = action[5:]
            self.shift = False
            self.index = min(self.index, len(self.rows()[self.row][1]) - 1)
            return []
        if action == SHIFT:
            self.shift = not self.shift
            return []
        if action in _SPECIAL_TOKENS:
            return list(_SPECIAL_TOKENS[action])
        tokens = char_tokens(action)
        if self.shift and action.isalpha():
            self.shift = False
            tokens = ["VK_SHIFT_L"] + tokens
        return tokens
