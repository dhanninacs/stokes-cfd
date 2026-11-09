"""The external-case writer: an imported case's files plus its overrides (ADR-0029 4, plan T-308).

## Responsibility

`write(files, overrides)` is the staged case: a **pure function** of the copy's bytes and the
ordered override list (rule 9). A file no override names passes through **byte-identical**. An
overridden file is changed by splicing: the reader (`stokes.compile.foam_read`) gives every entry
its byte span, and only the span of the overridden entry's values is replaced, so comments,
layout, the banner and every other entry stay exactly as the tutorial wrote them.

## Overrides

An `Override` names a file (`system/controlDict`), a key path (`("PISO", "nCorrectors")`) and a
typed value. Values are typed OpenFOAM tokens, never raw text, so an override can never smuggle in a
directive, a variable, code or a second entry:

* a number - a scalar (`0.5`);
* a boolean - the word `true` or `false`;
* a word - letters, digits and `_ . : < > , + - * /` with balanced parentheses, starting with a
  letter or `_` (`Euler`, `div(phi,U)`, `Gauss linear`'s parts are separate words);
* a list - `(...)` of the above or of lists, at most `MAX_LIST_DEPTH` deep;
* `Tokens` - several values side by side (`uniform (1 0 0)`, `Gauss linear`);
* `Scalar` from a quantity (`stokes.cases.keys`) - the SI number, replacing **only the number** of
  a dimensioned entry (`nu [0 2 -1 0 0 0 0] 1e-05;` keeps its dimension set).

A key the file lacks is added: at the end of the dictionary that should hold it, creating the
missing sub-dictionaries. A key whose value is a sub-dictionary cannot be replaced by a value
(`OverrideError`, `case.override_block`).

## Settings view

`rows(data)` flattens a file into the rows the settings editor shows: each entry's key path, its
value text (cut at `MAX_ROW_TEXT` characters, with `truncated`), and whether it is a block, an
entry or a directive.

Import rules: standard library, `stokes.errors` and `stokes.compile.foam_read`. Headless.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from stokes.compile.foam_read import (
    BlockNode,
    DictNode,
    DimsNode,
    DirectiveNode,
    Document,
    EntryNode,
    Kind,
    Node,
    Token,
    parse,
    source_text,
)
from stokes.errors import StokesError

WRITER_VERSION = "of14-external/1"
MAX_LIST_DEPTH = 4
MAX_LIST_ITEMS = 4096
MAX_WORD = 256
MAX_PATH = 16
MAX_ROW_TEXT = 200
MAX_ROWS = 5000
_INDENT = "    "

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:<>,+\-*/()]*")
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:<>,+\-*()]*|\"[^\"\\$#\n]{1,128}\"")


class OverrideError(StokesError):
    """An override cannot be applied to its file."""

    code = "case.override"


@dataclass(frozen=True)
class Scalar:
    """A number written as is: the SI value of a quantity override."""

    value: float


@dataclass(frozen=True)
class Tokens:
    """Several values side by side, such as ``uniform (1 0 0)``."""

    items: tuple[Value, ...]


type Atom = bool | int | float | str | Scalar
type Value = Atom | tuple[Value, ...] | Tokens


@dataclass(frozen=True)
class Override:
    """One recorded change: ``file``'s entry at ``path`` takes ``value``."""

    file: str
    path: tuple[str, ...]
    value: Value


def check_word(text: str) -> str:
    """``text`` when it is a word an override may write, else `OverrideError`."""
    if len(text) > MAX_WORD or not _WORD_RE.fullmatch(text) or "//" in text or "/*" in text:
        raise OverrideError(f"{text[:60]!r} is not a word", code="case.override_value")
    depth = 0
    for c in text:
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth < 0:
            break
    if depth != 0:
        raise OverrideError(f"{text[:60]!r} has unbalanced parentheses", code="case.override_value")
    return text


def check_key(text: str) -> str:
    """``text`` when it is a dictionary key an override may name (a word or a quoted name)."""
    if len(text) > MAX_WORD or not _KEY_RE.fullmatch(text):
        raise OverrideError(f"{text[:60]!r} is not a key", code="case.override_path")
    return text


def _number(value: float) -> str:
    if not math.isfinite(value):
        raise OverrideError("a number must be finite", code="case.override_value")
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def render(value: Value, depth: int = 0) -> str:
    """The OpenFOAM text of a value. Raises `OverrideError` for anything not allowed."""
    if depth > MAX_LIST_DEPTH:
        raise OverrideError("lists nest too deep", code="case.override_value")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Scalar):
        return _number(value.value)
    if isinstance(value, int | float):
        return _number(value)
    if isinstance(value, str):
        return check_word(value)
    if isinstance(value, Tokens):
        if not value.items:
            raise OverrideError("a value needs at least one token", code="case.override_value")
        return " ".join(render(item, depth) for item in value.items)
    if len(value) > MAX_LIST_ITEMS:
        raise OverrideError("the list is too long", code="case.override_value")
    return "(" + " ".join(render(item, depth + 1) for item in value) + ")"


# --------------------------------------------------------------------------------- finding entries


def _body(node: Document | DictNode | BlockNode) -> Document | DictNode:
    return node.body if isinstance(node, BlockNode) else node


def _find(container: Document | DictNode, key: str) -> EntryNode | BlockNode | None:
    """The last entry or block named ``key`` directly in ``container``: OpenFOAM's last wins."""
    found: EntryNode | BlockNode | None = None
    for item in container.items:
        if isinstance(item, EntryNode | BlockNode) and item.key.text == key:
            found = item
    return found


def locate(doc: Document, path: Sequence[str]) -> EntryNode | BlockNode | None:
    """The entry or block at ``path``, or None when any key on the way is missing."""
    container: Document | DictNode = doc
    found: EntryNode | BlockNode | None = None
    for depth, key in enumerate(path):
        found = _find(container, key)
        if found is None:
            return None
        if depth < len(path) - 1:
            if not isinstance(found, BlockNode):
                return None
            container = found.body
    return found


# ------------------------------------------------------------------------------------- splicing


def _value_span(entry: EntryNode, value: Value) -> tuple[int, int]:
    """The byte span an override replaces: all values, or only the number of a dimensioned one."""
    values = entry.values
    if not values:
        raise OverrideError(f"{entry.key.text} has no value to replace", code="case.override_path")
    last = values[-1]
    dimensioned = (
        isinstance(value, Scalar)
        and isinstance(last, Token)
        and last.kind is Kind.NUMBER
        and any(isinstance(item, DimsNode) for item in values[:-1])
    )
    if dimensioned:
        return last.start, last.end
    return values[0].start, values[-1].end


def _line_indent(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    line = text[start:offset]
    return line[: len(line) - len(line.lstrip(" \t"))]


def _new_entries(path: Sequence[str], value: str, indent: str) -> str:
    """``path[0] { path[1] { ... key value; } }`` as lines at ``indent``."""
    if len(path) == 1:
        return f"{indent}{path[0]} {value};\n"
    inner = _new_entries(path[1:], value, indent + _INDENT)
    return f"{indent}{path[0]}\n{indent}{{\n{inner}{indent}}}\n"


def _insert(text: str, doc: Document, path: Sequence[str], value: str) -> str:
    """``text`` with the missing tail of ``path`` added inside the deepest block that exists."""
    container: Document | DictNode = doc
    depth = 0
    indent = ""
    while depth < len(path) - 1:
        found = _find(container, path[depth])
        if found is None:
            break
        if not isinstance(found, BlockNode):
            raise OverrideError(
                f"{'/'.join(path[: depth + 1])} is a value, not a dictionary",
                code="case.override_path",
            )
        container = found.body
        indent = _line_indent(text, found.start) + _INDENT
        depth += 1
    lines = _new_entries(path[depth:], value, indent)
    if isinstance(container, Document):
        prefix = text if text.endswith("\n") or not text else text + "\n"
        return prefix + lines
    close = container.end - 1  # the closing brace
    head = text[:close].rstrip(" \t")  # the brace's own indent is written again after the lines
    if not head.endswith("\n"):
        head += "\n"
    return head + lines + _line_indent(text, close) + text[close:]


def apply_one(data: bytes, override: Override) -> bytes:
    """``data`` with one override applied. Raises `OverrideError` or the reader's errors."""
    if not override.path or len(override.path) > MAX_PATH:
        raise OverrideError("an override needs a key path", code="case.override_path")
    for key in override.path:
        check_key(key)
    rendered = render(override.value)
    doc = parse(data)
    text = data.decode("latin-1")
    target = locate(doc, override.path)
    if isinstance(target, BlockNode):
        raise OverrideError(
            f"{'/'.join(override.path)} is a dictionary; set the entries inside it",
            code="case.override_block",
        )
    if target is None:
        text = _insert(text, doc, override.path, rendered)
    else:
        start, end = _value_span(target, override.value)
        text = text[:start] + rendered + text[end:]
    return text.encode("latin-1")


def apply(data: bytes, overrides: Sequence[Override]) -> bytes:
    """``data`` with ``overrides`` applied in order; the input itself when there are none."""
    for override in overrides:
        data = apply_one(data, override)
    return data


def write(files: Mapping[str, bytes], overrides: Sequence[Override]) -> dict[str, bytes]:
    """The staged case: every file of ``files``, with the overrides for it applied.

    An override naming a file the case lacks is an `OverrideError` (`case.override_file`): a
    replay against a different copy must fail rather than drop a setting silently.
    """
    by_file: dict[str, list[Override]] = {}
    for override in overrides:
        if override.file not in files:
            raise OverrideError(
                f"the case has no file {override.file}",
                code="case.override_file",
                details={"file": override.file},
            )
        by_file.setdefault(override.file, []).append(override)
    return {
        name: apply(data, by_file[name]) if name in by_file else data
        for name, data in files.items()
    }


# --------------------------------------------------------------------------------- settings view


def _text(nodes: Sequence[Node]) -> tuple[str, bool]:
    shown = " ".join(" ".join(source_text(node).split()) for node in nodes)
    if len(shown) > MAX_ROW_TEXT:
        return shown[:MAX_ROW_TEXT], True
    return shown, False


def _rows(
    container: Document | DictNode,
    prefix: tuple[str, ...],
    out: list[dict[str, Any]],  # Any: JSON rows
) -> None:
    for item in container.items:
        if len(out) >= MAX_ROWS:
            return
        if isinstance(item, BlockNode):
            path = (*prefix, item.key.text)
            out.append({"path": list(path), "kind": "block", "text": "", "truncated": False})
            _rows(item.body, path, out)
        elif isinstance(item, EntryNode):
            text, truncated = _text(item.values)
            out.append(
                {
                    "path": [*prefix, item.key.text],
                    "kind": "entry",
                    "text": text,
                    "truncated": truncated,
                }
            )
        elif isinstance(item, DirectiveNode):
            text, truncated = _text(item.items)
            out.append(
                {"path": list(prefix), "kind": "directive", "text": text, "truncated": truncated}
            )


def rows(data: bytes) -> list[dict[str, Any]]:  # Any: JSON rows
    """Every entry, block and directive of a file in source order, at most `MAX_ROWS`.

    A row is `{path: [keys], kind: entry | block | directive, text, truncated}`; a directive's
    path is the dictionary it sits in.
    """
    out: list[dict[str, Any]] = []
    _rows(parse(data), (), out)
    return out


def entry_text(data: bytes, path: Sequence[str]) -> str | None:
    """The value text of the entry at ``path``, whitespace collapsed, or None when absent."""
    found = locate(parse(data), path)
    if not isinstance(found, EntryNode):
        return None
    return _text(found.values)[0]
