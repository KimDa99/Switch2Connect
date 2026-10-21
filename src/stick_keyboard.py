"""Controller keyboard (the "Stick Keyboard" mapping action).

Two layouts (CONFIG.stick_keyboard "layout"): "split" (below) and "one_stick",
a phone-style keyboard driven by either stick (see mobile_keyboard).

The left stick points into the left half of a QWERTY grid and the right stick
into the right half; by default ZR types the left-stick key and ZL the
right-stick key. Every other button's action is user-mapped (CONFIG.stick_keyboard
"buttons"); the sticks are fixed. While the mode is on every controller's input
is consumed here (buttons and sticks zeroed), so the normal mapping pipeline
releases whatever it held and stays quiet.

State is module-level because a Joy-Con pair is two Controller objects feeding
one keyboard. ponytail: one shared keyboard, per-player state if two players
ever need it at once.
"""
import logging
import threading
import time

import keyboard_output
import mobile_keyboard
from config import SWITCH_BUTTONS

LEFT_KEYS = ["12345", "QWERT", "ASDFG", "ZXCVB"]
RIGHT_KEYS = ["67890", "YUIOP", "HJKL;", "NM,./"]
_SYMBOL_TOKENS = {";": "VK_SEMICOLON", ",": "VK_COMMA", ".": "VK_PERIOD", "/": "VK_SLASH"}
# Joy-Con gates are round; the gain lets the corner keys be reached without
# bottoming the stick out exactly diagonally.
STICK_GAIN = 1.2
# One-stick layout: the highlight steps one key per tilt, then repeats while held.
# Repeats get faster with a harder tilt and with every repeat (tuning knobs).
NAV_THRESHOLD = 0.5
NAV_FULL_TILT = 0.85                 # tilt that already counts as full speed
NAV_REPEAT_DELAY = (0.22, 0.12)      # first repeat after: (light, full) tilt
NAV_REPEAT_INTERVAL = (0.12, 0.05)   # repeat interval: (light, full) tilt
NAV_ACCELERATION = 0.85              # interval multiplier per repeat at full tilt (none at light)
NAV_MIN_INTERVAL = 0.035

TYPE_LEFT = "Type Left"     # type the key under the left-stick marker
TYPE_RIGHT = "Type Right"   # type the key under the right-stick marker
NONE = "None"
# Button -> action: TYPE_LEFT / TYPE_RIGHT / NONE, or keys ("VK_A", "VK_CONTROL_L+VK_V").
# Dict order is the settings-tab order.
DEFAULT_BUTTONS = {
    "ZR": TYPE_LEFT, "ZL": TYPE_RIGHT, "R": "VK_SHIFT_L", "L": "VK_SHIFT_L",
    "A": "VK_RETURN", "B": "VK_BACK", "X": "VK_HANGUL", "Y": "VK_SPACE",
    "UP": "VK_UP", "DOWN": "VK_DOWN", "LEFT": "VK_LEFT", "RIGHT": "VK_RIGHT",
    "PLUS": NONE, "MINUS": NONE, "L_STK": NONE, "R_STK": NONE,
}
DEFAULT_SIZE = 1.0
DEFAULT_OPACITY = 0.82
_SOURCE = "stick_keyboard"
# Only these stay down while the button is held (OS auto-repeat for editing
# keys, chords for modifiers). Everything else is tapped on press: with a short
# Windows repeat delay (250 ms) an ordinary press already repeated letters and
# toggled 한/영 twice.
_HOLD_KEYS = {"VK_BACK", "VK_BACKSPACE", "VK_DELETE", "VK_UP", "VK_DOWN", "VK_LEFT", "VK_RIGHT"}
_MODIFIER_PREFIXES = ("VK_SHIFT", "VK_CONTROL", "VK_ALT", "VK_MENU", "VK_LWIN", "VK_RWIN", "VK_WIN")
logger = logging.getLogger(__name__)

_lock = threading.Lock()
active = False
left_cell = (2, 2)
right_cell = (2, 2)
left_pos = (0.0, 0.0)   # gain-clamped stick, drawn as the Joy-Con marker
right_pos = (0.0, 0.0)
shift = False
MOBILE = mobile_keyboard.Layout()   # one-stick layout state
_nav = {}            # controller -> (direction, next step time)
_owner = None        # (controller, bit) of the button that toggles the mode
_exiting = False
_prev_buttons = {}   # controller -> buttons of its previous frame
_held = {}           # (controller, bit) -> tokens pressed by that button


def settings():
    from config import CONFIG
    stored = getattr(CONFIG, "stick_keyboard", None)
    return stored if isinstance(stored, dict) else {}


def button_actions():
    return {**DEFAULT_BUTTONS, **settings().get("buttons", {})}


def one_stick():
    return settings().get("layout") == "one_stick"


def key_token(char):
    return _SYMBOL_TOKENS.get(char, f"VK_{char}")


def action_tokens(action):
    if action in (TYPE_LEFT, TYPE_RIGHT) and one_stick():
        return MOBILE.activate()
    if action == TYPE_LEFT:
        return [key_token(LEFT_KEYS[left_cell[0]][left_cell[1]])]
    if action == TYPE_RIGHT:
        return [key_token(RIGHT_KEYS[right_cell[0]][right_cell[1]])]
    if not action or action == NONE:
        return []
    return [t for t in action.split("+") if t]


def is_hold(tokens):
    return len(tokens) == 1 and (tokens[0] in _HOLD_KEYS or tokens[0].startswith(_MODIFIER_PREFIXES))


def stick_pos(stick):
    return tuple(round(max(-1.0, min(1.0, v * STICK_GAIN)), 2) for v in stick)


def grid_point(pos):
    """(row, col) as floats; the resting stick sits in the middle of D / K."""
    x, y = pos
    return min(3.99, max(0.0, 2.5 - y * 2.5)), min(4.99, (x + 1) / 2 * 5)


def stick_cell(stick):
    row, col = grid_point(stick_pos(stick))
    return int(row), int(col)


def nav_direction(stick):
    """(d_row, d_col) of a tilted stick; up is row -1."""
    x, y = stick
    return ((y < -NAV_THRESHOLD) - (y > NAV_THRESHOLD),
            (x > NAV_THRESHOLD) - (x < -NAV_THRESHOLD))


def repeat_delay(strength, repeats):
    """Seconds until the next step: shorter for a harder tilt and a longer hold."""
    t = min(1.0, max(0.0, (strength - NAV_THRESHOLD) / (NAV_FULL_TILT - NAV_THRESHOLD)))
    if repeats == 0:
        return NAV_REPEAT_DELAY[0] + (NAV_REPEAT_DELAY[1] - NAV_REPEAT_DELAY[0]) * t
    base = NAV_REPEAT_INTERVAL[0] + (NAV_REPEAT_INTERVAL[1] - NAV_REPEAT_INTERVAL[0]) * t
    acceleration = 1.0 - (1.0 - NAV_ACCELERATION) * t
    return max(NAV_MIN_INTERVAL, base * acceleration ** (repeats - 1))


def _navigate(controller, sticks, now):
    tilts = [(nav_direction(s), max(abs(s[0]), abs(s[1]))) for s in sticks]
    direction, strength = next(((d, m) for d, m in tilts if d != (0, 0)), ((0, 0), 0.0))
    last, due, repeats = _nav.get(controller, ((0, 0), 0.0, 0))
    if direction != (0, 0) and direction != last:
        MOBILE.move(*direction)                      # a fresh tilt is always one step
        repeats, due = 0, now + repeat_delay(strength, 0)
    elif direction != (0, 0) and now >= due:
        MOBILE.move(*direction)
        repeats += 1
        due = now + repeat_delay(strength, repeats)
    _nav[controller] = (direction, due, repeats)


def enter(controller, bit):
    """Turn the mode on; `bit` is the toggle button, pressed again to leave."""
    global active, _owner, _exiting
    with _lock:
        if active:
            return
        _prev_buttons.clear()
        _nav.clear()
        _owner = (controller, bit)
        _exiting = False
        active = True
        logger.info("Stick keyboard ON (toggle bit 0x%X)", bit)


def _leave_locked():
    global active, shift, _owner
    keyboard_output.release_source(_SOURCE)
    _held.clear()
    shift = False
    _owner = None
    active = False
    logger.info("Stick keyboard OFF")


def _source(holder):
    return f"{_SOURCE}:{holder[1]}:{id(holder[0])}"


def process(controller, input_data):
    """Consume one input frame while the mode is on; no-op otherwise."""
    global left_cell, right_cell, left_pos, right_pos, shift, _exiting
    if not active:
        return
    with _lock:
        if not active:
            return
        buttons = input_data.buttons
        prev = _prev_buttons.get(controller, buttons)  # no edges on a first frame
        _prev_buttons[controller] = buttons
        pressed, released = buttons & ~prev, prev & ~buttons

        is_left, is_right = controller.is_joycon_left(), controller.is_joycon_right()
        if one_stick():
            sticks = ([] if is_right else [input_data.left_stick]) +                      ([] if is_left else [input_data.right_stick])
            _navigate(controller, sticks, time.monotonic())
        if not is_right:
            left_pos = stick_pos(input_data.left_stick)
            left_cell = stick_cell(input_data.left_stick)
        if not is_left:
            right_pos = stick_pos(input_data.right_stick)
            right_cell = stick_cell(input_data.right_stick)

        owner, toggle_bit = _owner
        if controller is owner and pressed & toggle_bit:
            _exiting = True
        if _exiting:
            if controller is owner and not buttons & toggle_bit:
                _leave_locked()
        else:
            for holder, tokens in list(_held.items()):
                if holder[0] is controller and released & holder[1]:
                    for token in reversed(tokens):
                        ok = keyboard_output.key_event(token, False, _source(holder))
                        logger.info("Stick keyboard up   bit=0x%X %s ok=%s", holder[1], token, ok)
                    del _held[holder]
            if pressed:
                for name, action in button_actions().items():
                    bit = SWITCH_BUTTONS.get(name, 0)
                    if not pressed & bit or (controller is owner and bit == toggle_bit):
                        continue
                    holder = (controller, bit)
                    tokens = action_tokens(action)
                    for token in tokens:
                        ok = keyboard_output.key_event(token, True, _source(holder))
                        logger.info("Stick keyboard down %s bit=0x%X %s ok=%s mode=%s",
                                    name, bit, token, ok, keyboard_output.effective_mode())
                    if is_hold(tokens):
                        _held[holder] = tokens
                    else:
                        for token in reversed(tokens):
                            keyboard_output.key_event(token, False, _source(holder))
            shift = any(t.startswith("VK_SHIFT") for tokens in _held.values() for t in tokens)

        input_data.buttons = 0
        input_data.left_stick = (0.0, 0.0)
        input_data.right_stick = (0.0, 0.0)


_KEY_LABELS = {
    "VK_BACK": "⌫", "VK_RETURN": "⏎", "VK_SPACE": "Space", "VK_HANGUL": "한/영",
    "VK_SHIFT_L": "Shift", "VK_UP": "↑", "VK_DOWN": "↓", "VK_LEFT": "←", "VK_RIGHT": "→",
}
_BUTTON_LABELS = {"UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
                  "PLUS": "+", "MINUS": "−", "L_STK": "LS", "R_STK": "RS"}


def action_label(action):
    if action in (TYPE_LEFT, TYPE_RIGHT) and one_stick():
        return "입력"
    if action == TYPE_LEFT:
        return "◀ 키"
    if action == TYPE_RIGHT:
        return "키 ▶"
    if not action or action == NONE:
        return "—"
    return "+".join(_KEY_LABELS.get(t, t[3:] if t.startswith("VK_") else t)
                    for t in action.split("+"))


class Overlay:
    """Semi-transparent, click-through, never-focused keyboard picture.

    Shown/hidden by alpha instead of map/unmap so Windows never activates it
    and the focused text field keeps receiving the typed keys. Size and
    opacity come from CONFIG.stick_keyboard; call apply_settings() after
    changing them.
    """

    def __init__(self, root):
        import tkinter as tk
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.0)
        self.win.configure(bg="#111")
        self.canvas = tk.Canvas(self.win, bg="#111", highlightthickness=0)
        self.canvas.pack()
        self._preview_until = 0.0
        self.apply_settings()
        self.win.update_idletasks()
        self._make_click_through()
        self.root.after(30, self._poll)

    def apply_settings(self):
        conf = settings()
        size = max(0.5, min(3.0, float(conf.get("size", DEFAULT_SIZE))))
        self.opacity = max(0.2, min(1.0, float(conf.get("opacity", DEFAULT_OPACITY))))
        scale = self.root.winfo_fpixels("1i") / 96.0 * size
        self.cell = int(64 * scale)
        self.gap = int(230 * scale)
        width, height = self.cell * 10 + self.gap, self.cell * 4
        self.font = ("Malgun Gothic", max(9, int(18 * scale)), "bold")
        self.small = ("Malgun Gothic", max(7, int(11 * scale)))
        x = (self.root.winfo_screenwidth() - width) // 2
        y = self.root.winfo_screenheight() - height - int(110 * scale)
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self.canvas.config(width=width, height=height)
        self._shown = None  # force a redraw

    def preview(self, seconds=1.5):
        """Show the overlay briefly so a settings change can be seen."""
        import time
        self._preview_until = time.monotonic() + seconds
        self._shown = None

    def _make_click_through(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            GWL_EXSTYLE = -20
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # NOACTIVATE | TOOLWINDOW (no taskbar) | LAYERED | TRANSPARENT (click-through)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                  style | 0x08000000 | 0x80 | 0x80000 | 0x20)
        except Exception:
            pass

    def _poll(self):
        try:
            import time
            visible = active or time.monotonic() < self._preview_until
            state = (visible, one_stick(), MOBILE.page, MOBILE.row, MOBILE.index, MOBILE.shift,
                     left_pos, right_pos, left_cell, right_cell, shift)
            if state != self._shown:
                self._shown = state
                if visible:
                    self._draw()
                self.win.attributes("-alpha", self.opacity if visible else 0.0)
        finally:
            self.root.after(30, self._poll)

    def _draw(self):
        c, cell = self.canvas, self.cell
        c.delete("all")
        if one_stick():
            self._draw_mobile()
            legend_x = cell * 10 + self.gap / 2
        else:
            self._draw_split()
            legend_x = cell * 5 + self.gap / 2
        self._draw_legend(legend_x)

    def _draw_mobile(self):
        c, cell = self.canvas, self.cell
        upper = MOBILE.shift or shift
        for r, (offset, keys) in enumerate(MOBILE.rows()):
            for i, (spans, (label, action, _w)) in enumerate(zip(MOBILE.spans(r), keys)):
                x0, x1, y = spans[0] * cell, spans[1] * cell, r * cell
                on = (r, i) == (MOBILE.row, MOBILE.index)
                special = len(label) > 1 or action == mobile_keyboard.SHIFT
                if action == mobile_keyboard.SHIFT and MOBILE.shift:
                    fill = "#ffb300"
                else:
                    fill = "#3d7bff" if on else ("#1f1f1f" if special else "#2a2a2a")
                c.create_rectangle(x0 + 2, y + 2, x1 - 2, y + cell - 2, fill=fill, outline="#555")
                text = label.upper() if upper and label.isalpha() and len(label) == 1 else label
                c.create_text((x0 + x1) / 2, y + cell / 2, text=text, fill="white",
                              font=self.font if len(label) == 1 else self.small)
                jamo = (mobile_keyboard.SHIFT_JAMO.get(label) if upper else None) or mobile_keyboard.JAMO.get(label)
                if jamo:
                    c.create_text(x1 - cell * 0.2, y + cell * 0.22, text=jamo, fill="#9ab", font=self.small)

    def _draw_split(self):
        c, cell = self.canvas, self.cell
        for keys, selected, pos, tag, x0 in ((LEFT_KEYS, left_cell, left_pos, "L", 0),
                                             (RIGHT_KEYS, right_cell, right_pos, "R", cell * 5 + self.gap)):
            for r, row in enumerate(keys):
                for col, char in enumerate(row):
                    x, y = x0 + col * cell, r * cell
                    on = (r, col) == selected
                    c.create_rectangle(x + 2, y + 2, x + cell - 2, y + cell - 2,
                                       fill="#3d7bff" if on else "#2a2a2a", outline="#555")
                    label = char if shift or not char.isalpha() else char.lower()
                    c.create_text(x + cell / 2, y + cell / 2, text=label,
                                  fill="white", font=self.font)
            # Joy-Con marker: follows the stick continuously across its half.
            row_f, col_f = grid_point(pos)
            mx, my = x0 + col_f * cell, row_f * cell
            rad = cell * 0.22
            c.create_oval(mx - rad, my - rad, mx + rad, my + rad,
                          fill="#ff5a5a" if tag == "L" else "#4adf8a", outline="white", width=2)
            c.create_text(mx, my, text=tag, fill="white", font=self.small)

    def _draw_legend(self, mid):
        c, cell = self.canvas, self.cell
        # Legend: the current button mapping, two per line.
        actions = button_actions()
        arrows = ("UP", "DOWN", "LEFT", "RIGHT")
        plain_dpad = all(actions[b] == f"VK_{b}" for b in arrows)
        items = [f"{_BUTTON_LABELS.get(b, b)} {action_label(a)}"
                 for b, a in actions.items()
                 if a and a != NONE and not (plain_dpad and b in arrows)]
        if plain_dpad:
            items.append("십자키 화살표")
        lines = ["   ".join(items[i:i + 2]) for i in range(0, len(items), 2)]
        step = cell * 4 / (len(lines) + 1)
        for i, line in enumerate(lines):
            c.create_text(mid, step * (i + 1), text=line, font=self.small,
                          fill="#ffd54a" if shift and "Shift" in line else "#ccc")
