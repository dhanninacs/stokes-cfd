"""The theme token contract: it matches the stylesheets and the document, and checks values."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stokes.workspace.tokens import READ_ONLY, TOKENS, check_token_value, is_colour

ROOT = Path(__file__).resolve().parent.parent
SHEETS = (
    "web/src/styles.css",
    "web/src/themes/cupertino.css",
    "web/src/themes/ubuntu.css",
    "web/src/panels/panels.css",
)
CONTRACT = ROOT / "docs" / "engineering" / "theme-tokens.md"
_THEME_BLOCK = re.compile(r'^:root(?:\[data-theme\^?="[a-z-]+"\])?$')
_DECLARATION = re.compile(r"(--[a-z0-9-]+)\s*:")


def theme_tokens(css: str) -> set[str]:
    """Custom properties declared directly in ``:root`` or ``:root[data-theme...]`` blocks."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    names: set[str] = set()
    stack: list[tuple[str, int]] = []
    start = 0
    for brace in re.finditer(r"[{}]", text):
        if brace.group() == "{":
            selector = text[start : brace.start()].rsplit(";", 1)[-1].strip()
            stack.append((selector, brace.end()))
        elif stack:
            selector, body = stack.pop()
            if _THEME_BLOCK.match(selector):
                names.update(_DECLARATION.findall(text[body : brace.start()]))
        start = brace.end()
    return names


def test_the_scanner_reads_theme_blocks_only() -> None:
    css = """
    @import "x"; /* --commented: 1; */
    :root { --a: #000; --b: 1px; }
    :root[data-theme="light"] { --a: #fff; }
    :root[data-theme^="cupertino"] .glass { --local: 1; }
    .toast { --toast-accent: red; }
    @media (min-width: 1px) { :root[data-theme="cupertino"] { --c: 0; } }
    """
    assert theme_tokens(css) == {"--a", "--b", "--c"}


def test_contract_matches_the_stylesheets() -> None:
    sheets = [ROOT / sheet for sheet in SHEETS if (ROOT / sheet).is_file()]
    if not sheets:
        pytest.skip("the web/ sources are not in this checkout")
    defined = set().union(*(theme_tokens(p.read_text(encoding="utf-8")) for p in sheets))
    missing = sorted(defined - TOKENS.keys())
    stale = sorted(TOKENS.keys() - defined)
    assert not missing, (
        f"tokens in the stylesheets but not in the public contract: {missing}. Add each to "
        "stokes/workspace/tokens.py and docs/engineering/theme-tokens.md (ADR-0015 §6)."
    )
    assert not stale, (
        f"tokens in the contract that no stylesheet defines any more: {stale}. Removing or "
        "renaming a token is a contract change: update tokens.py, theme-tokens.md and its version."
    )


def test_the_contract_document_lists_every_token() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    absent = [name for name in TOKENS if f"`{name}`" not in text]
    assert not absent, f"docs/engineering/theme-tokens.md does not describe {absent}"


def test_every_token_has_a_meaning_and_a_group() -> None:
    for name, token in TOKENS.items():
        assert name.startswith("--") and token.meaning.endswith("."), name
    assert {"--tree-width", "--props-width"} == READ_ONLY
    assert all(TOKENS[n].group == "cupertino" for n in TOKENS if n.startswith("--cup-"))


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("--bg", "#0b0d10", "#0b0d10"),
        ("--panel", "rgba(18, 22, 28, 0.84)", "rgba(18, 22, 28, 0.84)"),
        ("--panel", "rgb(18 22 28 / 84%)", "rgb(18 22 28 / 84%)"),
        ("--text", "hsl(210 20% 90%)", "hsl(210 20% 90%)"),
        ("--text", "hsla(210deg, 20%, 90%, 0.5)", "hsla(210deg, 20%, 90%, 0.5)"),
        ("--text", "White", "White"),
        ("--scrim", "transparent", "transparent"),
        ("--series-1", "var(--accent)", "var(--accent)"),
        ("--accent", "#19C0A8", "#19c0a8"),
        ("--accent", "#fff", "#ffffff"),
        ("--vp-edge", "#0b1016", "#0b1016"),
        ("--radius", "12px", "12px"),
        ("--radius-sm", "0.5rem", "0.5rem"),
        ("--radius", "0", "0"),
        ("--shadow", "none", "none"),
        ("--shadow", "0 10px 30px rgba(0, 0, 0, 0.45)", "0 10px 30px rgba(0, 0, 0, 0.45)"),
        ("--focus-halo", "0 0 0 5px var(--accent-soft)", "0 0 0 5px var(--accent-soft)"),
        ("--font-mono", 'ui-monospace, "Cascadia Mono", monospace', None),
        ("--font-sans", '-apple-system, "SF Pro Text", sans-serif', None),
    ],
)
def test_token_values_accepted(name: str, value: str, expected: str | None) -> None:
    assert check_token_value(name, f"  {value} ") == (expected or value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("--bg", "url(https://example.com/a.png)"),
        ("--bg", "#12345"),
        ("--bg", "red; color: blue"),
        ("--bg", "expression(alert(1))"),
        ("--bg", 12),
        ("--bg", ""),
        ("--bg", "x" * 300),
        ("--accent", "teal"),
        ("--accent", "rgba(25, 192, 168, 0.5)"),
        ("--vp-shadow", "rgba(0, 0, 0, 0.5)"),
        ("--radius", "12"),
        ("--radius", "500px"),
        ("--radius", "-2px"),
        ("--shadow", "0"),
        ("--shadow", "0 0 0 0 0 1px red"),
        ("--shadow", "0 1px red blue"),
        ("--shadow", "0 1px rgba(0,0,0"),
        ("--font-sans", "Inter; x"),
        ("--font-sans", "url(x)"),
        ("--series-1", "var(--radius)"),
        ("--series-1", "var(--nope)"),
        ("--tree-width", "300px"),
        ("--nope", "#fff"),
    ],
)
def test_token_values_refused(name: str, value: object) -> None:
    with pytest.raises(ValueError, match=r"."):
        check_token_value(name, value)


def test_colour_recogniser() -> None:
    assert is_colour("#abcd") and is_colour("#aabbccdd") and is_colour("rebeccapurple")
    assert not is_colour("#ab") and not is_colour("rgb(1, 2)") and not is_colour("bluish")
