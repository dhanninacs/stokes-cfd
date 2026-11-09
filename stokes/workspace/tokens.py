"""The theme token contract (ADR-0015 §6): every public CSS custom property and its rules.

A custom theme is a built-in base plus overrides of these tokens. The names are a
public, versioned contract, documented in ``docs/engineering/theme-tokens.md`` and
checked against the stylesheets (``web/src/styles.css``,
``web/src/themes/cupertino.css``, ``web/src/panels/panels.css``) by
``tests/test_workspace_tokens.py``, so a token added to the CSS must be added here.

Values are validated by kind so that a theme can only say plausible CSS: colours,
box shadows, lengths and font lists. Nothing that loads a resource (``url()``),
breaks out of a declaration (``;``, braces) or references an unknown token passes.
Tokens the 3D viewport reads must be ``#rrggbb``, because its colour parser reads
only that form. Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from stokes.workspace.paths import JSON

TOKEN_CONTRACT_VERSION = 1
MAX_VALUE_CHARS = 256

TokenKind = Literal["colour", "hex", "shadow", "length", "font", "layout"]
TokenGroup = Literal[
    "surfaces",
    "text",
    "accent",
    "charts",
    "shadows",
    "viewport",
    "radii",
    "fonts",
    "cupertino",
    "layout",
]


@dataclass(frozen=True)
class Token:
    name: str
    group: TokenGroup
    kind: TokenKind
    meaning: str


def _t(name: str, group: TokenGroup, kind: TokenKind, meaning: str) -> Token:
    return Token(name, group, kind, meaning)


_TABLE: tuple[Token, ...] = (
    # surfaces
    _t("--bg", "surfaces", "colour", "Window background behind the viewport and every panel."),
    _t(
        "--bg-elev",
        "surfaces",
        "colour",
        "Raised fills inside panels: fields, key caps, value chips.",
    ),
    _t("--panel", "surfaces", "colour", "Translucent fill of floating glass panels and islands."),
    _t(
        "--panel-solid",
        "surfaces",
        "colour",
        "Opaque panel fill where translucency would hurt legibility (output drawer).",
    ),
    _t("--panel-border", "surfaces", "colour", "Hairline borders and dividers."),
    _t(
        "--panel-border-strong",
        "surfaces",
        "colour",
        "Borders that must read at a glance: menus, popovers, swatches.",
    ),
    _t(
        "--track",
        "surfaces",
        "colour",
        "Unfilled track of segmented controls and switches; quiet fills.",
    ),
    _t("--hover", "surfaces", "colour", "Fill of a row or button under the pointer."),
    _t("--active", "surfaces", "colour", "Fill of a row or button while pressed or switched on."),
    _t("--menu-bg", "surfaces", "colour", "Menus, popovers and the console's completion list."),
    _t("--tooltip-bg", "surfaces", "colour", "Tooltip background."),
    _t("--tooltip-border", "surfaces", "colour", "Tooltip border."),
    _t("--tooltip-kbd-border", "surfaces", "colour", "Outline of a key cap inside a tooltip."),
    _t(
        "--scrim",
        "surfaces",
        "colour",
        "Dimming layer behind modal dialogs and the command palette.",
    ),
    _t("--scroll-thumb", "surfaces", "colour", "Scrollbar thumb."),
    _t("--scroll-thumb-hover", "surfaces", "colour", "Scrollbar thumb under the pointer."),
    # text
    _t("--text", "text", "colour", "Primary text and icons."),
    _t(
        "--text-muted",
        "text",
        "colour",
        "Secondary text: labels, values, descriptions, chart axes.",
    ),
    _t("--text-faint", "text", "colour", "Tertiary text: placeholders, hints, disabled controls."),
    _t("--tooltip-text", "text", "colour", "Text in tooltips."),
    _t("--logo-ink", "text", "colour", "Strokes of the logo mark on its tile."),
    # accent and status
    _t(
        "--accent",
        "accent",
        "hex",
        "The one accent: selection, active stage, primary button, focus ring, 3D selection tint.",
    ),
    _t(
        "--accent-strong",
        "accent",
        "colour",
        "Accent under the pointer; first stop of accent gradients.",
    ),
    _t(
        "--accent-soft",
        "accent",
        "colour",
        "Translucent accent: selected rows, text selection, focus halo.",
    ),
    _t("--accent-text", "accent", "colour", "Text and icons on an accent fill."),
    _t("--accent-ink", "accent", "colour", "The accent as text on a panel."),
    _t("--accent-fill", "accent", "colour", "The accent as a solid fill that carries text."),
    _t("--accent-glow", "accent", "colour", "Soft glow under the primary button."),
    _t("--blue", "accent", "colour", "Information: outlet-type data, info toasts."),
    _t("--warn", "accent", "colour", "Stale status (amber)."),
    _t("--danger", "accent", "colour", "Errors, destructive actions, inlet-type data."),
    _t("--ok", "accent", "hex", "Success and up-to-date status; the view helper's Y axis."),
    _t("--blue-soft", "accent", "colour", "Translucent background behind --blue."),
    _t("--warn-soft", "accent", "colour", "Translucent background behind --warn."),
    _t(
        "--danger-soft",
        "accent",
        "colour",
        "Translucent background behind --danger: error cards and lines.",
    ),
    _t("--ok-soft", "accent", "colour", "Translucent background behind --ok."),
    # charts
    _t("--series-1", "charts", "colour", "First residual series (defaults to --accent)."),
    _t("--series-2", "charts", "colour", "Second residual series (defaults to --blue)."),
    _t("--series-3", "charts", "colour", "Third residual series (defaults to --ok)."),
    _t("--series-4", "charts", "colour", "Fourth residual series (defaults to --warn)."),
    # shadows and edges
    _t("--shadow", "shadows", "shadow", "The elevation shadow of floating panels and islands."),
    _t("--shadow-pop", "shadows", "shadow", "Higher elevation: menus, popovers, dialogs."),
    _t("--highlight", "shadows", "colour", "1 px lit top edge on glass."),
    _t("--focus-halo", "shadows", "shadow", "Soft ring outside the 2 px accent focus outline."),
    # viewport
    _t("--vp-top", "viewport", "colour", "Top of the viewport background gradient."),
    _t("--vp-bottom", "viewport", "colour", "Bottom of the viewport background gradient."),
    _t(
        "--vp-grid-minor",
        "viewport",
        "hex",
        "Minor ground-grid lines (the viewport's own default when unset).",
    ),
    _t(
        "--vp-grid-major",
        "viewport",
        "hex",
        "Major ground-grid lines (the viewport's own default when unset).",
    ),
    _t("--vp-floor", "viewport", "hex", "Faint floor disc under the part."),
    _t("--vp-shadow", "viewport", "hex", "Contact shadow under the part, at its darkest."),
    _t("--vp-edge", "viewport", "hex", "Feature-edge lines in the shaded-with-edges view mode."),
    # radii
    _t("--radius", "radii", "length", "Corner radius of panels, cards and popovers."),
    _t("--radius-sm", "radii", "length", "Corner radius of controls: buttons, fields, rows."),
    # fonts
    _t("--font-sans", "fonts", "font", "Interface text."),
    _t("--font-mono", "fonts", "font", "Identifiers, expressions and the console."),
    # Cupertino family only
    _t(
        "--cup-seg-selected",
        "cupertino",
        "colour",
        "Raised selected segment of segmented controls and stage chips.",
    ),
    _t("--cup-seg-shadow", "cupertino", "shadow", "Shadow under the selected segment."),
    _t(
        "--cup-chip-text",
        "cupertino",
        "colour",
        "Text of an idle segment (darker than --text-muted on the track).",
    ),
    _t("--cup-chip-hover", "cupertino", "colour", "Idle segment under the pointer."),
    _t("--cup-field-bg", "cupertino", "colour", "Text field background."),
    _t("--cup-field-border", "cupertino", "colour", "Text field border."),
    _t("--cup-kbd-bg", "cupertino", "colour", "Key cap background."),
    _t("--cup-kbd-border", "cupertino", "colour", "Key cap border."),
    _t("--cup-logo-top", "cupertino", "colour", "Top of the logo tile gradient."),
    _t("--cup-logo-bottom", "cupertino", "colour", "Bottom of the logo tile gradient."),
    _t(
        "--cup-select-idle",
        "cupertino",
        "colour",
        "Selected tree row while the tree is not focused.",
    ),
    _t(
        "--cup-dot-ok",
        "cupertino",
        "colour",
        "Status dot of a node that is fine (it says nothing).",
    ),
    _t(
        "--cup-light-border", "cupertino", "colour", "Outline of the traffic-light window controls."
    ),
    _t(
        "--cup-light-idle",
        "cupertino",
        "colour",
        "Traffic lights of a window that is not in front.",
    ),
    _t("--cup-light-close", "cupertino", "colour", "Close traffic light."),
    _t("--cup-light-min", "cupertino", "colour", "Minimise traffic light."),
    _t("--cup-light-zoom", "cupertino", "colour", "Zoom traffic light."),
    _t("--cup-glyph-close", "cupertino", "colour", "Glyph on the close light under the pointer."),
    _t("--cup-glyph-min", "cupertino", "colour", "Glyph on the minimise light under the pointer."),
    _t("--cup-glyph-zoom", "cupertino", "colour", "Glyph on the zoom light under the pointer."),
    _t(
        "--cup-on-accent-soft", "cupertino", "colour", "Secondary text and icons on an accent fill."
    ),
    _t("--cup-on-accent-line", "cupertino", "colour", "Outline of a key cap on an accent fill."),
    # written by the app at run time
    _t(
        "--tree-width",
        "layout",
        "layout",
        "Effective width of the left panel, written by the app on the main element.",
    ),
    _t(
        "--props-width",
        "layout",
        "layout",
        "Effective width of the right panel, written by the app on the main element.",
    ),
)

TOKENS: dict[str, Token] = {token.name: token for token in _TABLE}
_NONE = Token("", "layout", "layout", "")
READ_ONLY = frozenset(name for name, token in TOKENS.items() if token.kind == "layout")

_COLOUR_NAMES = """
    aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
    blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
    crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
    darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue
    dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
    ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
    ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
    lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen
    linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
    mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
    midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
    orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip
    peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
    saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
    slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
    wheat white whitesmoke yellow yellowgreen transparent currentcolor
"""
_NAMED_COLOURS = frozenset(_COLOUR_NAMES.split())
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?"
_HUE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:deg|grad|rad|turn)?"


def _function(name: str, first: str) -> re.Pattern[str]:
    commas = rf"\(\s*{first}\s*,\s*{_NUM}\s*,\s*{_NUM}\s*(?:,\s*{_NUM}\s*)?\)"
    spaces = rf"\(\s*{first}\s+{_NUM}\s+{_NUM}\s*(?:/\s*{_NUM}\s*)?\)"
    return re.compile(rf"^{name}a?(?:{commas}|{spaces})$", re.IGNORECASE)


_RGB = _function("rgb", _NUM)
_HSL = _function("hsl", _HUE)
_HEX_ANY = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_HEX_SHORT_OR_LONG = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_VAR = re.compile(r"^var\(\s*(--[a-z0-9-]+)\s*\)$")
_SHADOW_LENGTH = re.compile(r"^(?:0|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:px|rem|em))$")
_RADIUS = re.compile(r"^(?:0|(\d+(?:\.\d+)?)(px|rem|em))$")
_FAMILY = re.compile(r"""^(?:"[^"\\]{1,64}"|'[^'\\]{1,64}'|-?[A-Za-z][A-Za-z0-9 -]{0,63})$""")


def hex6(text: str) -> str:
    """``#abc`` and ``#AABBCC`` as ``#aabbcc``."""
    digits = text[1:].lower()
    return "#" + ("".join(c * 2 for c in digits) if len(digits) == 3 else digits)


def is_colour(text: str) -> bool:
    """A plausible CSS colour: hex, rgb()/hsl(), a named colour, or var() of a colour token."""
    value = text.strip()
    if _HEX_ANY.match(value) or _RGB.match(value) or _HSL.match(value):
        return True
    if value.lower() in _NAMED_COLOURS:
        return True
    ref = _VAR.match(value)
    if ref is None:
        return False
    return TOKENS.get(ref.group(1), _NONE).kind in ("colour", "hex")


def token_problem(name: str) -> str:
    """Why ``name`` cannot be overridden by a theme, or "" when it can."""
    if name not in TOKENS:
        return f"{name!r} is not a theme token (docs/engineering/theme-tokens.md lists them)"
    if name in READ_ONLY:
        return f"{name} is written by the app at run time; a theme cannot set it"
    return ""


def check_token_value(name: str, value: JSON) -> str:
    """The value a theme may give ``name``, normalised; raises ValueError with the reason."""
    problem = token_problem(name)
    if problem:
        raise ValueError(problem)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} takes a CSS value as a string, for example '#19c0a8'")
    text = value.strip()
    if len(text) > MAX_VALUE_CHARS:
        raise ValueError(f"{name}: a token value has at most {MAX_VALUE_CHARS} characters")
    return _CHECKS[TOKENS[name].kind](name, text)


def _colour(name: str, text: str) -> str:
    if is_colour(text):
        return text
    raise ValueError(f"{name} takes a colour (#rrggbb, rgb(), hsl(), a CSS name); got {text!r}")


def _hex(name: str, text: str) -> str:
    if _HEX_SHORT_OR_LONG.match(text):
        return hex6(text)
    raise ValueError(
        f"{name} is read by the 3D viewport, which understands only #rrggbb; got {text!r}"
    )


def _shadow(name: str, text: str) -> str:
    if text.lower() == "none":
        return text
    layers = _split(text, ",")
    if not layers or len(layers) > 8:
        raise ValueError(f"{name} takes one to eight box shadows separated by commas")
    for layer in layers:
        lengths = colours = insets = 0
        for part in _split(layer, " "):
            if part.lower() == "inset":
                insets += 1
            elif _SHADOW_LENGTH.match(part):
                lengths += 1
            elif is_colour(part):
                colours += 1
            else:
                raise ValueError(f"{name}: {part!r} is not a length, a colour or 'inset'")
        if not 2 <= lengths <= 4 or colours > 1 or insets > 1:
            raise ValueError(
                f"{name}: each shadow is two to four lengths, at most one colour "
                "and an optional inset"
            )
    return text


def _length(name: str, text: str) -> str:
    match = _RADIUS.match(text)
    if match is None:
        raise ValueError(f"{name} takes a length such as 12px or 0.75rem; got {text!r}")
    if match.group(1) is not None:
        limit = 100.0 if match.group(2) == "px" else 6.0
        if float(match.group(1)) > limit:
            raise ValueError(f"{name}: at most {limit:g}{match.group(2)}")
    return text


def _font(name: str, text: str) -> str:
    families = [family.strip() for family in text.split(",")]
    if len(families) > 16 or not all(_FAMILY.match(family) for family in families):
        raise ValueError(
            f'{name} takes a font list such as "Inter Variable", ui-sans-serif, sans-serif'
        )
    return text


def _never(name: str, text: str) -> str:
    raise ValueError(token_problem(name))


_CHECKS: dict[TokenKind, Callable[[str, str], str]] = {
    "colour": _colour,
    "hex": _hex,
    "shadow": _shadow,
    "length": _length,
    "font": _font,
    "layout": _never,
}


def _split(text: str, separator: str) -> list[str]:
    """Split at ``separator`` outside parentheses (" " means any whitespace); no empty parts."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced parentheses")
        at_separator = char.isspace() if separator == " " else char == separator
        if at_separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if depth != 0:
        raise ValueError("unbalanced parentheses")
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]
