"""A `#` line inside a docstring is prose, not a comment - and nothing checks prose.

**Opus found four of these by reading, with every other gate green.** A `#` inside a docstring is
legal Python, so the parser accepts it; it is not a lint error, so ruff accepts it; and it changes
no behaviour, so the tests accept it. What it does is **split a sentence in two** - and all four
were damage from a mis-targeted line edit, the mistake I made repeatedly while editing by line
number.

The pattern is a `#` line where the surrounding lines are prose. **`##` and `#!` are excluded**,
because a markdown section heading in a module docstring is deliberate and used widely here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules", "build", "dist"}


def _python_files() -> list[Path]:
    return [
        path
        for path in sorted(ROOT.rglob("*.py"))
        if not any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts)
    ]


def _docstring_comment_lines(source: str, path: Path) -> list[tuple[int, str]]:
    """Every line in a docstring that reads as a code comment.

    **`##` and `#!` are skipped**: this tree uses markdown headings in module docstrings on
    purpose, and a rule that forbade them would be a false refusal that got switched off.
    """
    tree = ast.parse(source)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        for offset, line in enumerate(doc.split("\n")):
            stripped = line.strip()
            if not stripped.startswith("#"):
                continue
            if stripped.startswith(("##", "#!")):
                continue
            # **A blank `#`** is a deliberate separator, which is readable rather than damage.
            if not stripped.lstrip("#").strip():
                continue
            out.append((getattr(node, "lineno", 0) + offset, stripped[:70]))
    return out


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: p.name)
def test_no_docstring_contains_a_code_comment(path: Path) -> None:
    """**The gate the other four gates cannot be.** Each of these read as a comment spliced into a
    sentence, which is what a bad line edit leaves behind.
    """
    found = _docstring_comment_lines(path.read_text(encoding="utf-8"), path)
    assert not found, "\n".join(f"{path.relative_to(ROOT)}:{line}: {text}" for line, text in found)
