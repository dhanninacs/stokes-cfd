"""Keyboard chords for the ``shortcuts`` section: parsing and the canonical spelling.

Canonical chords are modifiers in the fixed order ``Ctrl``, ``Alt``, ``Shift``,
``Meta`` followed by one key, joined with ``+``: ``Ctrl+Shift+C``, ``F2``, ``1``,
``Ctrl+,``, ``Shift+F10``. ``Ctrl`` means Control, or Command on macOS (the front
end matches ``ctrlKey || metaKey``, as its handlers always have); ``Meta`` is the
Windows or Command key itself. Input is forgiving: case does not matter, spaces
may stand for ``+`` (``Ctrl Shift Z``, as web/DESIGN.md writes chords), and the
usual aliases are understood (``Control``, ``Cmd``, ``Option``, ``Esc``, ``Del``,
``Up``). The ``+`` key itself is spelled ``Plus``.

Pure functions; standard library only.
"""

from __future__ import annotations

import re

from stokes.workspace.paths import JSON

MODIFIERS = ("Ctrl", "Alt", "Shift", "Meta")
NAMED_KEYS = (
    "Enter",
    "Escape",
    "Tab",
    "Space",
    "Backspace",
    "Delete",
    "Insert",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "ContextMenu",
    "Plus",
    *(f"F{n}" for n in range(1, 25)),
)
PUNCTUATION = "`-=[]\\;',./"
MAX_CHORD_CHARS = 64
MAX_CHORDS_PER_ACTION = 4

_MODIFIER_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "ctl": "Ctrl",
    "cmd": "Ctrl",
    "command": "Ctrl",
    "mod": "Ctrl",
    "alt": "Alt",
    "option": "Alt",
    "opt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "super": "Meta",
    "win": "Meta",
    "windows": "Meta",
}
_KEY_ALIASES = {
    **{name.lower(): name for name in NAMED_KEYS},
    "esc": "Escape",
    "return": "Enter",
    "del": "Delete",
    "ins": "Insert",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "pgup": "PageUp",
    "pgdn": "PageDown",
    "spacebar": "Space",
    "menu": "ContextMenu",
    "apps": "ContextMenu",
    "comma": ",",
    "period": ".",
    "dot": ".",
    "slash": "/",
    "backslash": "\\",
    "minus": "-",
    "equal": "=",
    "equals": "=",
    "semicolon": ";",
    "quote": "'",
    "backquote": "`",
    "bracketleft": "[",
    "bracketright": "]",
}
_SEPARATORS = re.compile(r"[+\s]+")


def normalise_chord(text: str) -> str:
    """The canonical form of one chord, or ``""`` for an empty one. Raises ValueError."""
    raw = text.strip()
    if not raw:
        return ""
    if len(raw) > MAX_CHORD_CHARS:
        raise ValueError(f"a chord has at most {MAX_CHORD_CHARS} characters")
    key: str | None = None
    if raw.endswith("+") and (len(raw) == 1 or raw[-2] in "+ "):
        key, raw = "Plus", raw[:-1].rstrip(" +")
    parts = [p for p in _SEPARATORS.split(raw) if p] if raw else []
    if key is None:
        if not parts:
            raise ValueError(f"{text!r} has no key")
        key, parts = _key(parts[-1]), parts[:-1]
    found: set[str] = set()
    for part in parts:
        modifier = _MODIFIER_ALIASES.get(part.lower())
        if modifier is None:
            raise ValueError(f"{part!r} is not a modifier (use Ctrl, Alt, Shift or Meta)")
        if modifier in found:
            raise ValueError(f"{modifier} appears twice in {text!r}")
        found.add(modifier)
    return "+".join([m for m in MODIFIERS if m in found] + [key])


def _key(token: str) -> str:
    lowered = token.lower()
    if lowered in _MODIFIER_ALIASES:
        raise ValueError(f"a chord needs a key besides the modifiers ({token!r} is a modifier)")
    if len(token) == 1:
        if token.isascii() and token.isalpha():
            return token.upper()
        if (token.isascii() and token.isdigit()) or token in PUNCTUATION:
            return token
    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]
    raise ValueError(
        f"{token!r} is not a key: use a letter, a digit, one of {PUNCTUATION} or a named key "
        "such as Enter, Escape, Delete, F2, ArrowUp, Plus"
    )


def normalise_binding(value: JSON) -> str | list[str]:
    """Canonical binding: a chord string, or a list of chords (the first is the one tooltips show).

    ``""`` and ``[]`` unbind the action. Raises ValueError with an engineer's message.
    """
    if isinstance(value, str):
        return normalise_chord(value)
    if not isinstance(value, list):
        raise ValueError("a binding is a chord string such as 'Ctrl+K' or a list of chords")
    if len(value) > MAX_CHORDS_PER_ACTION:
        raise ValueError(f"an action takes at most {MAX_CHORDS_PER_ACTION} chords")
    chords: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("every chord in a list must be a string")
        chord = normalise_chord(item)
        if not chord:
            raise ValueError("a list of chords may not contain an empty chord; use [] to unbind")
        if chord not in chords:
            chords.append(chord)
    return chords


def chords_of(binding: JSON) -> list[str]:
    """The chords a (normalised) binding holds, primary first."""
    if isinstance(binding, str):
        return [binding] if binding else []
    if isinstance(binding, list):
        return [c for c in binding if isinstance(c, str) and c]
    return []
